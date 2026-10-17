"""Composicao persistente do caminho LIVE binario da IQ Option.

Construir o runtime nao envia ordem. Antes de qualquer leitura remota, exige o
selo de codigo e a frase exata do proprietario. Depois, resolve a conta por
leitura, vincula sua identidade ao piloto e monta transporte, WAL, subledger e
gateway certificado como uma unica unidade reiniciavel.
"""
from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from service.iq_option_binary_transport import IqOptionBinaryMcpTransport
from service.iq_option_execution import (
    IqOptionResolvedLiveAccount,
    resolve_iq_option_live_account_async,
)
from tradingagents.brokers.ccxt_bridge import LIVE_CONFIRM_PHRASE
from tradingagents.desk.binary_agent import (
    BinaryAgentDecision,
    BinaryAgentExecutionGateway,
)
from tradingagents.desk.binary_execution import BinaryExecutionCoordinator
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.piloto import AutorizacaoDePiloto, EstadoDoPiloto


class IqOptionBinaryRuntimeError(RuntimeError):
    """A composicao LIVE falhou fechada antes de ficar disponivel."""


AccountResolver = Callable[
    [dict[str, Any]], Awaitable[IqOptionResolvedLiveAccount]
]
TransportFactory = Callable[..., Any]


def _authorization_key(authorization_id: Any) -> str:
    value = str(authorization_id or "").strip()
    if (not value or len(value) > 128
            or not all(char.isascii() and (
                char.isalnum() or char in "-_") for char in value)):
        raise IqOptionBinaryRuntimeError(
            "identificador da autorizacao invalido")
    return f"iq_option_binary_runtime::{value}"


def _validate_before_network(
        *, tenant_id: str, authorization: AutorizacaoDePiloto,
        live_code_authorized: bool, confirmation_phrase: str,
        env: dict[str, Any]) -> tuple[str, str]:
    if live_code_authorized is not True:
        raise IqOptionBinaryRuntimeError(
            "selo de codigo LIVE permanece fechado")
    if not hmac.compare_digest(
            str(confirmation_phrase or "").strip(), LIVE_CONFIRM_PHRASE):
        raise IqOptionBinaryRuntimeError(
            "frase explicita de autorizacao LIVE invalida")
    tenant = str(tenant_id or "").strip()
    if not tenant or not isinstance(authorization, AutorizacaoDePiloto):
        raise IqOptionBinaryRuntimeError(
            "piloto ou inquilino de execucao invalido")
    if authorization.tenant_id != tenant:
        raise IqOptionBinaryRuntimeError(
            "piloto pertence a outro inquilino")
    if authorization.estado is not EstadoDoPiloto.PILOTO_AUTORIZADO:
        raise IqOptionBinaryRuntimeError(
            "piloto nao esta autorizado")
    if authorization.conta_real is not True:
        raise IqOptionBinaryRuntimeError(
            "piloto nao pertence a conta REAL")
    if authorization.corretora.strip().lower() != "iq_option":
        raise IqOptionBinaryRuntimeError(
            "piloto nao pertence a IQ Option")
    token = str(env.get("IQ_OPTION_MCP_LIVE_BEARER_TOKEN") or "").strip()
    if not token:
        raise IqOptionBinaryRuntimeError(
            "credencial LIVE da IQ Option ausente")
    return tenant, token


@dataclass(repr=False)
class IqOptionBinaryRuntime:
    """Runtime fixado a um piloto e a uma conta resolvida em memoria."""

    coordinator: BinaryExecutionCoordinator
    gateway: BinaryAgentExecutionGateway
    authorization: AutorizacaoDePiloto
    account: IqOptionResolvedLiveAccount
    state_key: str

    def __repr__(self) -> str:
        return (
            "IqOptionBinaryRuntime(account=<redacted>, "
            f"authorization={self.authorization.authorization_id!r})"
        )

    def submit_payload(
            self, payload: dict[str, Any], *,
            current_authorization: AutorizacaoDePiloto | None = None):
        if (not isinstance(current_authorization, AutorizacaoDePiloto)
                or not hmac.compare_digest(
                    str(current_authorization.authorization_id or ""),
                    str(self.authorization.authorization_id or ""))
                or current_authorization.estado
                is not EstadoDoPiloto.PILOTO_AUTORIZADO):
            raise IqOptionBinaryRuntimeError(
                "autorizacao revalidada ausente ou substituida")
        decision = BinaryAgentDecision.from_payload(payload)
        return self.gateway.submit(
            decision,
            authorization=current_authorization,
            balance_id=self.account.balance_id,
            account_identity_fingerprint=(
                self.account.broker_identity_fingerprint),
        )


async def build_iq_option_binary_runtime(
        *, env: dict[str, Any], tenant_id: str,
        authorization: AutorizacaoDePiloto, ledger: Ledger | None,
        store: Any, live_code_authorized: bool, confirmation_phrase: str,
        opening_cash: Any = None,
        max_order_value: Any = None, max_daily_loss: Any = None,
        account_resolver: AccountResolver = (
            resolve_iq_option_live_account_async),
        transport_factory: TransportFactory = IqOptionBinaryMcpTransport,
        now: Callable[[], datetime] | None = None,
        ) -> IqOptionBinaryRuntime:
    """Monta o runtime sem ordem e restaura seu estado composto, se houver."""
    tenant, live_token = _validate_before_network(
        tenant_id=tenant_id,
        authorization=authorization,
        live_code_authorized=live_code_authorized,
        confirmation_phrase=confirmation_phrase,
        env=env,
    )
    if (not callable(account_resolver) or not callable(transport_factory)
            or not callable(getattr(store, "carregar", None))
            or not callable(getattr(store, "salvar", None))):
        raise IqOptionBinaryRuntimeError(
            "dependencias do runtime binario invalidas")
    state_key = _authorization_key(authorization.authorization_id)
    try:
        account = await account_resolver(dict(env))
    except IqOptionBinaryRuntimeError:
        raise
    except Exception as exc:
        raise IqOptionBinaryRuntimeError(
            f"resolucao passiva da conta falhou ({type(exc).__name__})") \
            from None
    if not isinstance(account, IqOptionResolvedLiveAccount):
        raise IqOptionBinaryRuntimeError(
            "resolvedor devolveu conta LIVE invalida")
    expected_identity = str(
        authorization.broker_identity_fingerprint or "").strip()
    observed_identity = str(
        account.broker_identity_fingerprint or "").strip()
    if (not expected_identity or not observed_identity
            or not hmac.compare_digest(expected_identity, observed_identity)):
        raise IqOptionBinaryRuntimeError(
            "identidade da conta diverge da autorizacao")
    try:
        restored = store.carregar(tenant, state_key)
        if restored is None and ledger is None:
            try:
                opening = Decimal(str(opening_cash))
            except (InvalidOperation, TypeError, ValueError):
                raise IqOptionBinaryRuntimeError(
                    "capital inicial do runtime invalido") from None
            if not opening.is_finite() or opening <= 0:
                raise IqOptionBinaryRuntimeError(
                    "capital inicial do runtime invalido")
            ledger = Ledger(opening, account.currency)
        if (restored is None and isinstance(ledger, Ledger)
                and ledger.currency != account.currency):
            raise IqOptionBinaryRuntimeError(
                "moeda do ledger diverge da conta resolvida")
        transport = transport_factory(token=live_token)

        def persist(document: dict[str, Any]) -> None:
            store.salvar(tenant, state_key, document)

        coordinator = BinaryExecutionCoordinator(
            ledger=ledger,
            tool_caller=transport,
            persist=persist,
            live_authorized=True,
            confirmation_phrase=confirmation_phrase,
            max_order_value=max_order_value,
            max_daily_loss=max_daily_loss,
            restored_state=restored,
        )
        gateway = BinaryAgentExecutionGateway(coordinator, now=now)
        if coordinator.ledger.currency != account.currency:
            raise IqOptionBinaryRuntimeError(
                "moeda restaurada diverge da conta resolvida")
    except IqOptionBinaryRuntimeError:
        raise
    except Exception as exc:
        raise IqOptionBinaryRuntimeError(
            f"montagem do runtime binario falhou ({type(exc).__name__})") \
            from None
    return IqOptionBinaryRuntime(
        coordinator=coordinator,
        gateway=gateway,
        authorization=authorization,
        account=account,
        state_key=state_key,
    )


__all__ = [
    "IqOptionBinaryRuntime",
    "IqOptionBinaryRuntimeError",
    "build_iq_option_binary_runtime",
]

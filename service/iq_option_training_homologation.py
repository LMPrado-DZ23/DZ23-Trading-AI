"""Homologação mutável da IQ Option limitada à conta de treinamento.

Este módulo não habilita LIVE. Ele aceita uma única aposta de uma unidade,
grava a intenção e o estado do executor antes do envio, reconcilia por
``position_id`` e só emite prova pública depois da baixa contábil total.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from service.iq_option_binary_transport import IqOptionBinaryMcpTransport
from service.iq_option_training import (
    IqOptionResolvedTrainingAccount,
    resolve_iq_option_training_account_async,
)
from tradingagents.brokers.iq_option_binary import (
    TRAINING_CONFIRM_PHRASE,
    BinaryAuthorizationScope,
)
from tradingagents.desk.binary_execution import (
    BinaryDeskState,
    BinaryExecutionCoordinator,
    BinarySettlementProof,
)
from tradingagents.desk.ledger import Ledger

IQ_OPTION_TRAINING_STAKE = Decimal("1")
_STATE_KEY_PREFIX = "iq_option_training_homologation::wal::"
IQ_OPTION_TRAINING_PROOF_KEY = "iq_option_training_homologation::proof"
_STATE_SCHEMA = "dz23.iq-option-training-runtime/1"
IQ_OPTION_TRAINING_PROOF_SCHEMA = "dz23.homologacao-iq-training/1"
_SHA256 = re.compile(r"[0-9a-f]{64}")


class IqOptionTrainingHomologationError(RuntimeError):
    """A homologação de treinamento falhou fechada."""


AccountResolver = Callable[
    [dict[str, Any]], Awaitable[IqOptionResolvedTrainingAccount]
]
TransportFactory = Callable[..., Any]


def _stake(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise IqOptionTrainingHomologationError(
            "valor de treinamento deve ser exatamente uma unidade")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise IqOptionTrainingHomologationError(
            "valor de treinamento deve ser exatamente uma unidade") from None
    if not parsed.is_finite() or parsed != IQ_OPTION_TRAINING_STAKE:
        raise IqOptionTrainingHomologationError(
            "valor de treinamento deve ser exatamente uma unidade")
    return parsed


def _identifier(value: Any, *, label: str, maximum: int = 128) -> str:
    text = str(value or "").strip()
    if (not text or len(text) > maximum
            or not all(char.isascii() and (
                char.isalnum() or char in "-_.") for char in text)):
        raise IqOptionTrainingHomologationError(f"{label} inválido")
    return text


def _account_fingerprint(account: IqOptionResolvedTrainingAccount) -> str:
    canonical = (
        f"{account.balance_filter}\0{account.balance_id}\0{account.currency}"
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(repr=False)
class IqOptionTrainingHomologationRuntime:
    """Runtime de uso único vinculado à credencial e ao saldo de treino."""

    coordinator: BinaryExecutionCoordinator
    account: IqOptionResolvedTrainingAccount
    tenant_id: str
    store: Any
    now: Callable[[], datetime]

    def __repr__(self) -> str:
        return "IqOptionTrainingHomologationRuntime(account=<redacted>)"

    def _existing_ids(self) -> set[str]:
        return {
            str(item.get("client_order_id") or "")
            for item in self.coordinator.snapshot_state()["intents"]
            if isinstance(item, dict)
        }

    def submit(
            self, *, client_order_id: str, instrument: str, direction: str,
            amount: Any, expiration: int,
            min_profit_percent: int):
        key = _identifier(client_order_id, label="client_order_id")
        stake = _stake(amount)
        existing = self._existing_ids()
        if existing and key not in existing:
            raise IqOptionTrainingHomologationError(
                "homologação permite uma única ordem de treinamento")
        reservation_id = f"training::{key}"
        if key not in existing:
            try:
                self.coordinator.ledger.reserve(
                    stake,
                    ref=f"iq-training:{key}",
                    reservation_id=reservation_id,
                )
            except Exception as exc:
                raise IqOptionTrainingHomologationError(
                    f"reserva de treinamento recusada ({type(exc).__name__})"
                ) from None
        try:
            return self.coordinator.submit(
                client_order_id=key,
                reservation_id=reservation_id,
                agent_id="iq-option-training-homologation",
                balance_id=self.account.balance_id,
                instrument=instrument,
                direction=direction,
                amount=stake,
                expiration=expiration,
                min_profit_percent=min_profit_percent,
            )
        except IqOptionTrainingHomologationError:
            raise
        except Exception as exc:
            raise IqOptionTrainingHomologationError(
                f"ordem de treinamento recusada ({type(exc).__name__})"
            ) from None

    def reconcile_and_certify(
            self, client_order_id: str) -> dict[str, object]:
        key = _identifier(client_order_id, label="client_order_id")
        try:
            intent = self.coordinator.intent(key)
            if intent.state is not BinaryDeskState.SETTLED:
                reconciliation = self.coordinator.reconcile(key)
                if reconciliation.state.value != "settled":
                    raise IqOptionTrainingHomologationError(
                        "resultado remoto ainda não é terminal")
                if reconciliation.profit is None:
                    raise IqOptionTrainingHomologationError(
                        "histórico remoto não informa o retorno")
                result = str(reconciliation.result or "").strip().lower()
                if result == "win":
                    plan = self.coordinator.executor.submission(key).plan
                    expected_profit = (
                        intent.amount * Decimal(plan.profit_percent) / 100
                    )
                    if reconciliation.profit != expected_profit:
                        raise IqOptionTrainingHomologationError(
                            "lucro remoto diverge do payout contratado")
                    returned = intent.amount + expected_profit
                elif result == "loss":
                    returned = Decimal("0")
                elif result == "equal":
                    returned = intent.amount
                else:
                    # O contrato observado não fornece retorno total de uma
                    # posição cancelada; não inferir reembolso parcial.
                    raise IqOptionTrainingHomologationError(
                        "retorno total de cancelamento não foi comprovado")
                if not returned.is_finite() or returned < 0:
                    raise IqOptionTrainingHomologationError(
                        "retorno remoto é incompatível com a aposta")
                intent = self.coordinator.settle_verified(
                    key,
                    BinarySettlementProof(
                        position_id=intent.position_id,
                        result=result,
                        returned_amount=returned,
                        source="iq_option_account_statement",
                        reference=f"history:{intent.position_id}",
                    ),
                )
            if (intent.state is not BinaryDeskState.SETTLED
                    or self.coordinator.ledger.binary_at_risk(
                        intent.position_id) != 0):
                raise IqOptionTrainingHomologationError(
                    "baixa contábil de treinamento não foi comprovada")
        except IqOptionTrainingHomologationError:
            raise
        except Exception as exc:
            raise IqOptionTrainingHomologationError(
                f"reconciliação de treinamento recusada ({type(exc).__name__})"
            ) from None

        instant = self.now()
        if not isinstance(instant, datetime) or instant.tzinfo is None:
            raise IqOptionTrainingHomologationError(
                "relógio da homologação inválido")
        proof = {
            "schema": IQ_OPTION_TRAINING_PROOF_SCHEMA,
            "exchange": "iq_option",
            "mode": "training",
            "filled_external_order": True,
            "reconciled": True,
            "verified_at": instant.astimezone(timezone.utc).isoformat(),
            "credential_fingerprint": self.account.credential_fingerprint,
            "external_order_fingerprint": hashlib.sha256(
                f"iq_option\0training\0{intent.position_id}".encode()
            ).hexdigest(),
        }
        try:
            self.store.salvar(
                self.tenant_id, IQ_OPTION_TRAINING_PROOF_KEY, proof)
        except Exception as exc:
            raise IqOptionTrainingHomologationError(
                f"persistência da prova recusada ({type(exc).__name__})"
            ) from None
        return dict(proof)


async def build_iq_option_training_homologation_runtime(
        *, env: dict[str, Any], tenant_id: str, store: Any,
        confirmation_phrase: str,
        account_resolver: AccountResolver = (
            resolve_iq_option_training_account_async),
        transport_factory: TransportFactory = IqOptionBinaryMcpTransport,
        now: Callable[[], datetime] | None = None,
        ) -> IqOptionTrainingHomologationRuntime:
    """Monta a homologação sem enviar ordem durante a construção."""
    if not hmac.compare_digest(
            str(confirmation_phrase or "").strip(),
            TRAINING_CONFIRM_PHRASE):
        raise IqOptionTrainingHomologationError(
            "frase explícita de treinamento inválida")
    tenant = str(tenant_id or "").strip()
    if not tenant:
        raise IqOptionTrainingHomologationError("inquilino inválido")
    if (str(env.get("TRADINGAGENTS_EXCHANGE") or "").strip().lower()
            != "iq_option"
            or str(env.get("LIVE_EXECUTION_EXCHANGE") or "").strip().lower()
            != "iq_option"
            or str(env.get("TRADINGAGENTS_BROKER_MODE") or "").strip().lower()
            != "testnet"):
        raise IqOptionTrainingHomologationError(
            "homologação exige IQ Option em modo testnet")
    token = str(env.get("IQ_OPTION_MCP_LIVE_BEARER_TOKEN") or "").strip()
    if (not token or not callable(account_resolver)
            or not callable(transport_factory)
            or not callable(getattr(store, "carregar", None))
            or not callable(getattr(store, "salvar", None))):
        raise IqOptionTrainingHomologationError(
            "dependências da homologação inválidas")
    try:
        account = await account_resolver(dict(env))
    except Exception as exc:
        raise IqOptionTrainingHomologationError(
            f"conta de treinamento não comprovada ({type(exc).__name__})"
        ) from None
    if (not isinstance(account, IqOptionResolvedTrainingAccount)
            or not _SHA256.fullmatch(account.credential_fingerprint)):
        raise IqOptionTrainingHomologationError(
            "resolvedor devolveu conta de treinamento inválida")
    binding = _account_fingerprint(account)
    state_key = _STATE_KEY_PREFIX + account.credential_fingerprint
    try:
        wrapper = store.carregar(tenant, state_key)
        restored = None
        if wrapper is not None:
            if (not isinstance(wrapper, dict)
                    or wrapper.get("schema") != _STATE_SCHEMA
                    or not hmac.compare_digest(
                        str(wrapper.get("credential_fingerprint") or ""),
                        account.credential_fingerprint)
                    or not hmac.compare_digest(
                        str(wrapper.get("account_fingerprint") or ""), binding)
                    or not isinstance(wrapper.get("coordinator"), dict)):
                raise IqOptionTrainingHomologationError(
                    "WAL pertence a outra credencial ou conta de treinamento")
            restored = wrapper["coordinator"]

        def persist(document: dict[str, Any]) -> None:
            store.salvar(tenant, state_key, {
                "schema": _STATE_SCHEMA,
                "credential_fingerprint": account.credential_fingerprint,
                "account_fingerprint": binding,
                "coordinator": document,
            })

        coordinator = BinaryExecutionCoordinator(
            ledger=None if restored is not None else Ledger(
                IQ_OPTION_TRAINING_STAKE, account.currency),
            tool_caller=transport_factory(token=token),
            persist=persist,
            live_authorized=True,
            confirmation_phrase=TRAINING_CONFIRM_PHRASE,
            max_order_value=IQ_OPTION_TRAINING_STAKE,
            max_daily_loss=IQ_OPTION_TRAINING_STAKE,
            restored_state=restored,
            authorization_scope=BinaryAuthorizationScope.TRAINING,
        )
    except IqOptionTrainingHomologationError:
        raise
    except Exception as exc:
        raise IqOptionTrainingHomologationError(
            f"montagem da homologação recusada ({type(exc).__name__})"
        ) from None
    return IqOptionTrainingHomologationRuntime(
        coordinator=coordinator,
        account=account,
        tenant_id=tenant,
        store=store,
        now=now or (lambda: datetime.now(timezone.utc)),
    )


__all__ = [
    "IQ_OPTION_TRAINING_STAKE",
    "IQ_OPTION_TRAINING_PROOF_KEY",
    "IQ_OPTION_TRAINING_PROOF_SCHEMA",
    "IqOptionTrainingHomologationError",
    "IqOptionTrainingHomologationRuntime",
    "build_iq_option_training_homologation_runtime",
]

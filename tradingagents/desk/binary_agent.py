"""Fronteira estrita entre uma decisao de agente e a execucao binaria.

Uma proposta spot (BUY/SELL) nunca e convertida em CALL/PUT. O agente precisa
declarar os campos binarios e carregar o contexto exato em que foi formado.
A autorizacao do proprietario, a conta REAL e a identidade lida da corretora
sao revalidadas antes de o coordenador receber qualquer chamada.
"""
from __future__ import annotations

import hmac
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .piloto import AutorizacaoDePiloto, EstadoDoPiloto
from .salas import ContextoDeCertificacao, SalaError


class BinaryAgentDecisionError(ValueError):
    """A decisao nao prova todos os requisitos para chegar ao executor."""


def _text(value: Any, *, label: str, maximum: int = 200) -> str:
    parsed = str(value or "").strip()
    if not parsed or len(parsed) > maximum or any(c in parsed for c in "\r\n"):
        raise BinaryAgentDecisionError(f"{label} invalido")
    return parsed


def _amount(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise BinaryAgentDecisionError("valor binario invalido")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BinaryAgentDecisionError("valor binario invalido") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise BinaryAgentDecisionError("valor binario invalido")
    return parsed


def _positive_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BinaryAgentDecisionError(f"{label} invalido")
    return value


def _context(value: Any) -> ContextoDeCertificacao:
    if not isinstance(value, dict):
        raise BinaryAgentDecisionError("contexto de certificacao invalido")
    try:
        context = ContextoDeCertificacao.de_dict(value)
    except (SalaError, TypeError, ValueError) as exc:
        raise BinaryAgentDecisionError(
            "contexto de certificacao invalido") from exc
    if context.corretora != "iq_option":
        raise BinaryAgentDecisionError(
            "contexto precisa ser da IQ Option")
    if context.mercado != "opcoes_binarias":
        raise BinaryAgentDecisionError(
            "contexto precisa ser de opcoes binarias")
    if context.modo != "paper":
        raise BinaryAgentDecisionError(
            "certificacao binaria precisa vir da escola paper")
    if (not context.conta or not context.ativo or not context.horizonte
            or context.payout is None):
        raise BinaryAgentDecisionError(
            "contexto de certificacao incompleto")
    if (not context.payout.is_finite() or context.payout <= 0
            or context.payout > 1):
        raise BinaryAgentDecisionError(
            "payout certificado invalido")
    return context


@dataclass(frozen=True)
class BinaryAgentDecision:
    """Saida binaria explicita de uma estrategia certificada."""

    client_order_id: str
    reservation_id: str
    agent_id: str
    strategy: str
    strategy_version: str
    instrument: str
    binary_direction: str
    amount: Decimal
    expiration: int
    min_profit_percent: int
    certification_context: ContextoDeCertificacao

    @classmethod
    def from_payload(cls, payload: Any) -> BinaryAgentDecision:
        if not isinstance(payload, dict):
            raise BinaryAgentDecisionError("decisao binaria invalida")
        direction = str(payload.get("binary_direction") or "").strip().lower()
        if direction not in {"call", "put"}:
            raise BinaryAgentDecisionError(
                "direcao binaria explicita deve ser call ou put")
        expiration = _positive_int(
            payload.get("expiration"), label="vencimento")
        payout = payload.get("min_profit_percent")
        if (isinstance(payout, bool) or not isinstance(payout, int)
                or not 0 <= payout <= 100):
            raise BinaryAgentDecisionError("piso de payout invalido")
        context = _context(payload.get("certification_context"))
        instrument = _text(
            payload.get("instrument"), label="instrumento").upper()
        if instrument != context.ativo.upper():
            raise BinaryAgentDecisionError(
                "instrumento diverge do contexto certificado")
        certified_percent = context.payout * Decimal("100")
        if certified_percent < payout:
            raise BinaryAgentDecisionError(
                "payout certificado esta abaixo do piso solicitado")
        return cls(
            client_order_id=_text(
                payload.get("client_order_id"), label="client_order_id",
                maximum=128),
            reservation_id=_text(
                payload.get("reservation_id"), label="reserva"),
            agent_id=_text(payload.get("agent_id"), label="agente"),
            strategy=_text(payload.get("strategy"), label="estrategia"),
            strategy_version=_text(
                payload.get("strategy_version"), label="versao"),
            instrument=instrument,
            binary_direction=direction,
            amount=_amount(payload.get("amount")),
            expiration=expiration,
            min_profit_percent=payout,
            certification_context=context,
        )


class BinaryAgentExecutionGateway:
    """Valida agente, certificacao e conta antes do coordenador duravel.

    Esta camada nao substitui o gate LIVE do coordenador. Mesmo uma decisao
    valida continua bloqueada quando ``MESA_LIVE_LIBERADA`` permanece falsa.
    """

    def __init__(
            self, coordinator: Any, *,
            now: Callable[[], datetime] | None = None) -> None:
        if not callable(getattr(coordinator, "submit", None)):
            raise BinaryAgentDecisionError("coordenador binario ausente")
        self._coordinator = coordinator
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _validate_authorization(
            self, decision: BinaryAgentDecision,
            authorization: AutorizacaoDePiloto,
            account_identity_fingerprint: str) -> None:
        if not isinstance(authorization, AutorizacaoDePiloto):
            raise BinaryAgentDecisionError("autorizacao de piloto invalida")
        if authorization.estado is not EstadoDoPiloto.PILOTO_AUTORIZADO:
            raise BinaryAgentDecisionError("agente nao esta autorizado")
        try:
            valid_until = datetime.fromisoformat(
                str(authorization.valido_ate or ""))
            current = self._now()
            if valid_until.tzinfo is None or current.tzinfo is None:
                raise ValueError("timezone ausente")
        except (TypeError, ValueError):
            raise BinaryAgentDecisionError(
                "prazo da autorizacao e invalido") from None
        if current >= valid_until:
            raise BinaryAgentDecisionError(
                "prazo da autorizacao expirou")
        if authorization.conta_real is not True:
            raise BinaryAgentDecisionError(
                "autorizacao nao pertence a uma conta REAL")
        if authorization.agent_id != decision.agent_id:
            raise BinaryAgentDecisionError("agente diverge da autorizacao")
        if authorization.estrategia != decision.strategy:
            raise BinaryAgentDecisionError(
                "estrategia diverge da autorizacao")
        if authorization.versao != decision.strategy_version:
            raise BinaryAgentDecisionError("versao diverge da autorizacao")
        if authorization.corretora.strip().lower() != "iq_option":
            raise BinaryAgentDecisionError(
                "corretora da autorizacao nao e IQ Option")
        try:
            authorized_context = _context(authorization.contexto)
        except BinaryAgentDecisionError as exc:
            raise BinaryAgentDecisionError(
                "certificacao da autorizacao e invalida") from exc
        if authorized_context.chave != decision.certification_context.chave:
            raise BinaryAgentDecisionError(
                "certificacao da decisao diverge da autorizacao")
        expected_identity = str(
            authorization.broker_identity_fingerprint or "").strip()
        observed_identity = str(account_identity_fingerprint or "").strip()
        if (not expected_identity or not observed_identity
                or not hmac.compare_digest(expected_identity,
                                           observed_identity)):
            raise BinaryAgentDecisionError(
                "identidade da conta REAL diverge da autorizacao")
        if decision.amount > authorization.capital_maximo:
            raise BinaryAgentDecisionError(
                "valor excede o capital maximo do piloto")

    def submit(
            self, decision: BinaryAgentDecision, *,
            authorization: AutorizacaoDePiloto, balance_id: int,
            account_identity_fingerprint: str):
        if not isinstance(decision, BinaryAgentDecision):
            raise BinaryAgentDecisionError("decisao binaria invalida")
        resolved_balance = _positive_int(balance_id, label="balance_id")
        self._validate_authorization(
            decision, authorization, account_identity_fingerprint)
        return self._coordinator.submit(
            client_order_id=decision.client_order_id,
            reservation_id=decision.reservation_id,
            agent_id=decision.agent_id,
            balance_id=resolved_balance,
            instrument=decision.instrument,
            direction=decision.binary_direction,
            amount=decision.amount,
            expiration=decision.expiration,
            min_profit_percent=decision.min_profit_percent,
        )


__all__ = [
    "BinaryAgentDecision",
    "BinaryAgentDecisionError",
    "BinaryAgentExecutionGateway",
]

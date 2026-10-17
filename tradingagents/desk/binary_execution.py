"""Coordenação durável do executor IQ Option com o subledger binário."""
from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from tradingagents.brokers.iq_option_binary import (
    BinaryAmbiguousSubmission,
    BinaryContractError,
    BinarySubmissionState,
    IqOptionBinaryExecutor,
)

from .ledger import Ledger, LedgerError


class BinaryCoordinatorRecoveryRequired(BinaryContractError):
    """Estado remoto ou contábil exige replay local/reconciliação."""


class BinaryDeskState(str, Enum):
    CREATED = "created"
    ACKNOWLEDGED = "acknowledged"
    OPEN = "open"
    UNKNOWN = "unknown"
    SETTLEMENT_PENDING = "settlement_pending"
    SETTLED = "settled"


@dataclass(frozen=True)
class BinarySettlementProof:
    """Prova externa de quanto voltou ao caixa para uma posição liquidada."""

    position_id: str
    result: str
    returned_amount: Decimal
    source: str
    reference: str


@dataclass
class BinaryDeskIntent:
    client_order_id: str
    reservation_id: str
    agent_id: str
    balance_id: int
    instrument: str
    direction: str
    amount: Decimal
    expiration: int
    min_profit_percent: int
    fingerprint: str
    state: BinaryDeskState = BinaryDeskState.CREATED
    position_id: str = ""
    settlement_result: str = ""
    settlement_returned_amount: Decimal | None = None
    settlement_source: str = ""
    settlement_reference: str = ""


StateWriter = Callable[[dict[str, Any]], None]


def _text(value: Any, *, label: str, maximum: int = 200) -> str:
    parsed = str(value or "").strip()
    if not parsed or len(parsed) > maximum or any(c in parsed for c in "\r\n"):
        raise BinaryCoordinatorRecoveryRequired(f"{label} inválido")
    return parsed


def _positive_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BinaryCoordinatorRecoveryRequired(f"{label} inválido")
    return value


def _amount(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise BinaryCoordinatorRecoveryRequired("valor binário inválido")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BinaryCoordinatorRecoveryRequired(
            "valor binário inválido") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise BinaryCoordinatorRecoveryRequired("valor binário inválido")
    return parsed


def _nonnegative_amount(value: Any, *, label: str) -> Decimal:
    if isinstance(value, bool):
        raise BinaryCoordinatorRecoveryRequired(f"{label} inválido")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BinaryCoordinatorRecoveryRequired(f"{label} inválido") from exc
    if not parsed.is_finite() or parsed < 0:
        raise BinaryCoordinatorRecoveryRequired(f"{label} inválido")
    return parsed


def _normalized_fields(
        *, client_order_id: Any, reservation_id: Any, agent_id: Any,
        balance_id: Any, instrument: Any, direction: Any, amount: Any,
        expiration: Any, min_profit_percent: Any) -> dict[str, Any]:
    side = str(direction or "").strip().lower()
    if side not in {"call", "put"}:
        raise BinaryCoordinatorRecoveryRequired("direção binária inválida")
    payout = min_profit_percent
    if (isinstance(payout, bool) or not isinstance(payout, int)
            or not 0 <= payout <= 100):
        raise BinaryCoordinatorRecoveryRequired("piso de payout inválido")
    return {
        "client_order_id": _text(
            client_order_id, label="client_order_id", maximum=128),
        "reservation_id": _text(reservation_id, label="reserva"),
        "agent_id": _text(agent_id, label="agente"),
        "balance_id": _positive_int(balance_id, label="balance_id"),
        "instrument": _text(instrument, label="instrumento").upper(),
        "direction": side,
        "amount": _amount(amount),
        "expiration": _positive_int(expiration, label="vencimento"),
        "min_profit_percent": payout,
    }


def _fingerprint(fields: dict[str, Any]) -> str:
    canonical = json.dumps({
        key: str(value.normalize()) if isinstance(value, Decimal) else value
        for key, value in fields.items()
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class BinaryExecutionCoordinator:
    """Une WAL remoto e contabilidade sem adaptar opções ao livro spot.

    O documento composto contém intenção, estado do executor e lançamentos.
    Um ACK pode sobreviver sem ``binary_open``; ``recover`` completa somente o
    lançamento local e nunca chama ``place_trade`` novamente.
    """

    SCHEMA = "dz23.iq-option-binary-coordinator/1"

    def __init__(
            self, *, ledger: Ledger | None, tool_caller,
            persist: StateWriter, live_authorized: bool,
            confirmation_phrase: str, max_order_value: Any,
            max_daily_loss: Any,
            restored_state: dict[str, Any] | None = None) -> None:
        if not callable(persist):
            raise BinaryCoordinatorRecoveryRequired(
                "persistência composta obrigatória ausente")
        self._persist = persist
        self._intents: dict[str, BinaryDeskIntent] = {}
        executor_state = None
        if restored_state is not None:
            self.ledger, executor_state = self._restore_composite(restored_state)
        elif isinstance(ledger, Ledger):
            self.ledger = ledger
        else:
            raise BinaryCoordinatorRecoveryRequired("subledger binário ausente")
        self.executor = IqOptionBinaryExecutor(
            tool_caller=tool_caller,
            persist=self._persist_from_executor,
            live_authorized=live_authorized,
            confirmation_phrase=confirmation_phrase,
            max_order_value=max_order_value,
            max_daily_loss=max_daily_loss,
            restored_state=executor_state,
        )

    @staticmethod
    def _intent_doc(intent: BinaryDeskIntent) -> dict[str, Any]:
        return {
            "client_order_id": intent.client_order_id,
            "reservation_id": intent.reservation_id,
            "agent_id": intent.agent_id,
            "balance_id": intent.balance_id,
            "instrument": intent.instrument,
            "direction": intent.direction,
            "amount": str(intent.amount),
            "expiration": intent.expiration,
            "min_profit_percent": intent.min_profit_percent,
            "fingerprint": intent.fingerprint,
            "state": intent.state.value,
            "position_id": intent.position_id,
            "settlement_result": intent.settlement_result,
            "settlement_returned_amount": (
                str(intent.settlement_returned_amount)
                if intent.settlement_returned_amount is not None else None),
            "settlement_source": intent.settlement_source,
            "settlement_reference": intent.settlement_reference,
        }

    def snapshot_state(
            self, executor_state: dict[str, Any] | None = None,
            ) -> dict[str, Any]:
        if executor_state is None:
            executor_state = self.executor.snapshot_state()
        return {
            "schema": self.SCHEMA,
            "currency": self.ledger.currency,
            "ledger_transactions": self.ledger.export_transactions(),
            "intents": [self._intent_doc(item)
                        for item in self._intents.values()],
            "executor": executor_state,
        }

    def _persist_from_executor(self, executor_state: dict[str, Any]) -> None:
        self._persist(self.snapshot_state(executor_state))

    def _write_state(self) -> None:
        self._persist(self.snapshot_state())

    def _restore_composite(
            self, document: dict[str, Any]) -> tuple[Ledger, dict[str, Any]]:
        if (not isinstance(document, dict)
                or document.get("schema") != self.SCHEMA
                or not isinstance(document.get("ledger_transactions"), list)
                or not isinstance(document.get("intents"), list)
                or not isinstance(document.get("executor"), dict)):
            raise BinaryCoordinatorRecoveryRequired(
                "estado composto binário inválido")
        currency = _text(document.get("currency"), label="moeda", maximum=20)
        try:
            ledger = Ledger.from_transactions(
                document["ledger_transactions"], currency=currency)
            restored: dict[str, BinaryDeskIntent] = {}
            for raw in document["intents"]:
                if not isinstance(raw, dict):
                    raise BinaryCoordinatorRecoveryRequired(
                        "intenção binária restaurada inválida")
                fields = _normalized_fields(
                    client_order_id=raw.get("client_order_id"),
                    reservation_id=raw.get("reservation_id"),
                    agent_id=raw.get("agent_id"),
                    balance_id=raw.get("balance_id"),
                    instrument=raw.get("instrument"),
                    direction=raw.get("direction"),
                    amount=raw.get("amount"),
                    expiration=raw.get("expiration"),
                    min_profit_percent=raw.get("min_profit_percent"),
                )
                expected = _fingerprint(fields)
                stored = str(raw.get("fingerprint") or "")
                if not hmac.compare_digest(expected, stored):
                    raise BinaryCoordinatorRecoveryRequired(
                        "fingerprint da intenção binária inválido")
                state = BinaryDeskState(raw.get("state"))
                position_id = str(raw.get("position_id") or "").strip()
                states_with_position = {
                    BinaryDeskState.ACKNOWLEDGED,
                    BinaryDeskState.OPEN,
                    BinaryDeskState.SETTLEMENT_PENDING,
                    BinaryDeskState.SETTLED,
                }
                if state in states_with_position and not position_id:
                    raise BinaryCoordinatorRecoveryRequired(
                        "intenção confirmada sem position_id")
                settlement_result = str(
                    raw.get("settlement_result") or "").strip().lower()
                returned_raw = raw.get("settlement_returned_amount")
                returned = (
                    _nonnegative_amount(
                        returned_raw, label="retorno total restaurado")
                    if returned_raw is not None else None
                )
                settlement_source = str(
                    raw.get("settlement_source") or "").strip()
                settlement_reference = str(
                    raw.get("settlement_reference") or "").strip()
                if state in {
                        BinaryDeskState.SETTLEMENT_PENDING,
                        BinaryDeskState.SETTLED,
                } and settlement_result not in {
                        "win", "loss", "equal", "canceled",
                }:
                    raise BinaryCoordinatorRecoveryRequired(
                        "resultado binário restaurado inválido")
                if state is BinaryDeskState.SETTLED and (
                        returned is None or not settlement_source
                        or not settlement_reference):
                    raise BinaryCoordinatorRecoveryRequired(
                        "liquidação binária restaurada sem prova")
                intent = BinaryDeskIntent(
                    **fields, fingerprint=expected, state=state,
                    position_id=position_id,
                    settlement_result=settlement_result,
                    settlement_returned_amount=returned,
                    settlement_source=settlement_source,
                    settlement_reference=settlement_reference)
                if intent.client_order_id in restored:
                    raise BinaryCoordinatorRecoveryRequired(
                        "client_order_id binário duplicado")
                restored[intent.client_order_id] = intent
        except BinaryCoordinatorRecoveryRequired:
            raise
        except (ArithmeticError, LedgerError, TypeError, ValueError) as exc:
            raise BinaryCoordinatorRecoveryRequired(
                "estado composto binário inválido") from exc
        self._intents = restored
        return ledger, document["executor"]

    def intent(self, client_order_id: str) -> BinaryDeskIntent:
        key = _text(client_order_id, label="client_order_id", maximum=128)
        try:
            return self._intents[key]
        except KeyError:
            raise BinaryCoordinatorRecoveryRequired(
                "intenção binária desconhecida") from None

    @staticmethod
    def _matches(intent: BinaryDeskIntent, fields: dict[str, Any]) -> bool:
        return hmac.compare_digest(intent.fingerprint, _fingerprint(fields))

    def _executor_submission(self, client_order_id: str):
        try:
            return self.executor.submission(client_order_id)
        except BinaryContractError:
            return None

    def _complete_open(self, intent: BinaryDeskIntent, submission) -> None:
        plan = submission.plan
        if (plan.balance_id != intent.balance_id
                or plan.instrument.upper() != intent.instrument
                or plan.direction != intent.direction
                or plan.amount != intent.amount
                or plan.expiration != intent.expiration
                or plan.profit_percent < intent.min_profit_percent
                or not submission.position_id):
            intent.state = BinaryDeskState.UNKNOWN
            with suppress(Exception):
                self._write_state()
            raise BinaryCoordinatorRecoveryRequired(
                "ACK binário diverge da intenção durável")
        intent.position_id = submission.position_id
        intent.state = BinaryDeskState.ACKNOWLEDGED
        try:
            self.ledger.binary_open(
                position_id=intent.position_id,
                stake=intent.amount,
                reservation_id=intent.reservation_id,
                instrument=intent.instrument,
                agent_id=intent.agent_id,
            )
        except (LedgerError, ValueError) as exc:
            with suppress(Exception):
                self._write_state()
            raise BinaryCoordinatorRecoveryRequired(
                "ACK remoto exige recuperação da abertura contábil "
                f"({type(exc).__name__})") from None
        intent.state = BinaryDeskState.OPEN
        try:
            self._write_state()
        except Exception as exc:
            raise BinaryCoordinatorRecoveryRequired(
                "abertura contábil concluída sem persistência composta; "
                f"replay obrigatório ({type(exc).__name__})") from None

    def submit(
            self, *, client_order_id: str, reservation_id: str,
            agent_id: str, balance_id: int, instrument: str,
            direction: str, amount: Any, expiration: int,
            min_profit_percent: int) -> BinaryDeskIntent:
        fields = _normalized_fields(
            client_order_id=client_order_id,
            reservation_id=reservation_id,
            agent_id=agent_id,
            balance_id=balance_id,
            instrument=instrument,
            direction=direction,
            amount=amount,
            expiration=expiration,
            min_profit_percent=min_profit_percent,
        )
        key = fields["client_order_id"]
        intent = self._intents.get(key)
        if intent is not None:
            if not self._matches(intent, fields):
                raise BinaryCoordinatorRecoveryRequired(
                    "client_order_id já existe com conteúdo diferente")
            if intent.state is BinaryDeskState.OPEN:
                return intent
        else:
            if self.ledger.reserved_cash(fields["reservation_id"]) < fields["amount"]:
                raise BinaryCoordinatorRecoveryRequired(
                    "reserva insuficiente para a aposta binária")
            intent = BinaryDeskIntent(
                **fields, fingerprint=_fingerprint(fields))
            self._intents[key] = intent
            try:
                self._write_state()
            except Exception as exc:
                del self._intents[key]
                raise BinaryCoordinatorRecoveryRequired(
                    "persistência da intenção falhou antes da rede; "
                    f"operação não enviada ({type(exc).__name__})") from None

        try:
            submission = self.executor.submit(
                client_order_id=key,
                balance_id=intent.balance_id,
                instrument=intent.instrument,
                direction=intent.direction,
                amount=intent.amount,
                expiration=intent.expiration,
                min_profit_percent=intent.min_profit_percent,
            )
        except BinaryAmbiguousSubmission:
            intent.state = BinaryDeskState.UNKNOWN
            with suppress(Exception):
                self._write_state()
            raise
        intent.position_id = submission.position_id
        intent.state = BinaryDeskState.ACKNOWLEDGED
        try:
            self._write_state()
        except Exception as exc:
            raise BinaryCoordinatorRecoveryRequired(
                "ACK remoto durável exige recuperação local "
                f"({type(exc).__name__})") from None
        self._complete_open(intent, submission)
        return intent

    def recover(self, client_order_id: str) -> BinaryDeskIntent:
        intent = self.intent(client_order_id)
        if intent.state is BinaryDeskState.SETTLED:
            if self.ledger.binary_at_risk(intent.position_id) != 0:
                raise BinaryCoordinatorRecoveryRequired(
                    "liquidação marcada como concluída ainda possui exposição")
            return intent
        if intent.state in {
                BinaryDeskState.OPEN,
                BinaryDeskState.SETTLEMENT_PENDING,
        } and self.ledger.binary_at_risk(intent.position_id) == intent.amount:
            # Documento composto já prova o lançamento. Não regredir porque
            # o executor possa continuar UNKNOWN depois de um ACK cuja
            # persistência isolada falhou antes da reconciliação por ID.
            return intent
        was_settlement_pending = (
            intent.state is BinaryDeskState.SETTLEMENT_PENDING)
        pending_result = intent.settlement_result
        submission = self._executor_submission(intent.client_order_id)
        if submission is None:
            if intent.state is BinaryDeskState.CREATED:
                return intent
            intent.state = BinaryDeskState.UNKNOWN
            self._write_state()
            return intent
        if submission.state in {
                BinarySubmissionState.PREPARED,
                BinarySubmissionState.UNKNOWN,
        }:
            intent.state = BinaryDeskState.UNKNOWN
            self._write_state()
            return intent
        self._complete_open(intent, submission)
        if was_settlement_pending:
            intent.state = BinaryDeskState.SETTLEMENT_PENDING
            intent.settlement_result = pending_result
            self._write_state()
        return intent

    def reconcile(self, client_order_id: str):
        """Confronta a posição por ID; nunca transforma ausência em baixa."""
        intent = self.intent(client_order_id)
        submission = self._executor_submission(intent.client_order_id)
        if submission is None or not submission.position_id:
            raise BinaryCoordinatorRecoveryRequired(
                "reconciliação exige position_id oficial")
        result = self.executor.reconcile(intent.client_order_id)
        if result.amount is not None and result.amount != intent.amount:
            intent.state = BinaryDeskState.UNKNOWN
            self._write_state()
            raise BinaryCoordinatorRecoveryRequired(
                "valor remoto diverge da intenção binária")
        if result.state.value == "unknown":
            intent.state = BinaryDeskState.UNKNOWN
            self._write_state()
            return result

        self._complete_open(intent, submission)
        if result.state.value == "settled":
            intent.state = BinaryDeskState.SETTLEMENT_PENDING
            intent.settlement_result = str(result.result or "").strip().lower()
            intent.settlement_returned_amount = None
            intent.settlement_source = ""
            intent.settlement_reference = ""
            self._write_state()
        return result

    def settle_verified(
            self, client_order_id: str,
            proof: BinarySettlementProof) -> BinaryDeskIntent:
        """Baixa a exposição só com retorno total ligado a fonte externa."""
        if not isinstance(proof, BinarySettlementProof):
            raise BinaryCoordinatorRecoveryRequired(
                "prova de retorno total inválida")
        intent = self.intent(client_order_id)
        position_id = _text(proof.position_id, label="posição")
        result = str(proof.result or "").strip().lower()
        returned = _nonnegative_amount(
            proof.returned_amount, label="retorno total")
        source = str(proof.source or "").strip()
        reference = _text(proof.reference, label="referência da prova")
        if source != "iq_option_account_statement":
            raise BinaryCoordinatorRecoveryRequired(
                "fonte da prova de retorno total não homologada")
        if position_id != intent.position_id:
            raise BinaryCoordinatorRecoveryRequired(
                "posição da prova não corresponde à intenção")
        if result != intent.settlement_result:
            raise BinaryCoordinatorRecoveryRequired(
                "resultado da prova não corresponde à reconciliação")
        if intent.state is BinaryDeskState.SETTLED:
            same = (
                intent.settlement_returned_amount == returned
                and intent.settlement_source == source
                and intent.settlement_reference == reference
            )
            if not same:
                raise BinaryCoordinatorRecoveryRequired(
                    "liquidação já possui outra prova")
            return intent
        if intent.state is not BinaryDeskState.SETTLEMENT_PENDING:
            raise BinaryCoordinatorRecoveryRequired(
                "liquidação exige resultado remoto reconciliado")
        try:
            self.ledger.binary_settle(
                position_id=position_id,
                result=result,
                returned_amount=returned,
            )
        except (LedgerError, ValueError) as exc:
            raise BinaryCoordinatorRecoveryRequired(
                f"prova de retorno incompatível ({type(exc).__name__})") from None
        intent.state = BinaryDeskState.SETTLED
        intent.settlement_returned_amount = returned
        intent.settlement_source = source
        intent.settlement_reference = reference
        try:
            self._write_state()
        except Exception as exc:
            raise BinaryCoordinatorRecoveryRequired(
                "liquidação contábil concluída sem persistência composta; "
                f"replay obrigatório ({type(exc).__name__})") from None
        return intent


__all__ = [
    "BinaryCoordinatorRecoveryRequired",
    "BinaryDeskIntent",
    "BinaryDeskState",
    "BinaryExecutionCoordinator",
    "BinarySettlementProof",
]

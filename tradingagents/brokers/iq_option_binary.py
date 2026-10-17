"""Contrato e executor fail-closed para opções binárias da IQ Option.

O planejamento e a reconciliação permanecem puros. O executor mutável recebe
um transporte injetado, fixa a única chamada ``place_trade`` e exige selo de
código, frase do proprietário, limites e persistência durável. Como o provedor
não recebe uma chave idempotente do DZ23, PREPARED/UNKNOWN nunca é reenviado.
A sessão MCP concreta vive em ``service.iq_option_binary_transport``.
"""
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

from tradingagents.brokers.ccxt_bridge import LIVE_CONFIRM_PHRASE


class BinaryContractError(RuntimeError):
    """Entrada ou resposta incompatível com o contrato binário seguro."""


class BinaryExecutionSecurityError(BinaryContractError):
    """Uma trava local obrigatória para mutação não foi comprovada."""


class BinaryAmbiguousSubmission(BinaryContractError):
    """A operação pode existir no provedor e portanto não pode ser reenviada."""


class BinaryReconciliationState(str, Enum):
    OPEN = "open"
    SETTLED = "settled"
    UNKNOWN = "unknown"


class BinarySubmissionState(str, Enum):
    PREPARED = "prepared"
    ACKNOWLEDGED = "acknowledged"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BinaryTradePlan:
    """Argumentos exatos que uma autorização futura poderia enviar."""

    balance_id: int
    asset_id: int
    instrument: str
    direction: str
    amount: Decimal
    profit_percent: int
    expiration: int

    @property
    def arguments(self) -> dict[str, int | float | str]:
        return {
            "balance_id": self.balance_id,
            "asset_id": self.asset_id,
            "direction": self.direction,
            "amount": float(self.amount),
            "profit_percent": self.profit_percent,
            "expired": self.expiration,
        }


@dataclass(frozen=True)
class BinaryReconciliation:
    """Leitura canônica; UNKNOWN jamais significa que a operação não existe."""

    state: BinaryReconciliationState
    position_id: str
    amount: Decimal | None = None
    result: str | None = None
    profit: Decimal | None = None


@dataclass
class BinarySubmission:
    """Identidade durável do único envio permitido para uma intenção."""

    client_order_id: str
    request_fingerprint: str
    plan: BinaryTradePlan
    state: BinarySubmissionState = BinarySubmissionState.PREPARED
    position_id: str = ""


def _decimal(value: Any, *, label: str, positive: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise BinaryContractError(f"{label} inválido")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BinaryContractError(f"{label} inválido") from exc
    if not parsed.is_finite() or (positive and parsed <= 0):
        raise BinaryContractError(f"{label} inválido")
    return parsed


def _positive_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BinaryContractError(f"{label} inválido")
    return value


def _instrument_key(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(char for char in value.upper() if char.isalnum())


def _asset_symbol(asset: dict[str, Any]) -> str:
    for key in ("symbol", "asset", "name"):
        value = asset.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return ""


def _list(payload: Any, key: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
        raise BinaryContractError("resposta binária inválida")
    items = payload[key]
    if not all(isinstance(item, dict) for item in items):
        raise BinaryContractError("resposta binária inválida")
    return items


def plan_binary_trade(
        assets_payload: dict[str, Any], *, balance_id: int, instrument: str,
        direction: str, amount: Any, expiration: int,
        min_profit_percent: int) -> BinaryTradePlan:
    """Seleciona somente valores que a IQ Option acabou de oferecer.

    Não escolhe um vencimento aproximado, não arredonda a aposta e não aceita
    payout abaixo do piso da estratégia. Qualquer ambiguidade recusa o plano.
    """
    account = _positive_int(balance_id, label="balance_id")
    requested_asset = _instrument_key(instrument)
    if not requested_asset:
        raise BinaryContractError("instrumento inválido")
    side = str(direction or "").strip().lower()
    if side not in {"call", "put"}:
        raise BinaryContractError("direção binária inválida")
    stake = _decimal(amount, label="valor da aposta", positive=True)
    expiry = _positive_int(expiration, label="vencimento")
    if (isinstance(min_profit_percent, bool)
            or not isinstance(min_profit_percent, int)
            or not 0 <= min_profit_percent <= 100):
        raise BinaryContractError("piso de payout inválido")

    matches = [
        asset for asset in _list(assets_payload, "assets")
        if _instrument_key(_asset_symbol(asset)) == requested_asset
    ]
    if len(matches) != 1:
        raise BinaryContractError(
            "a resposta precisa identificar um ativo único")
    asset = matches[0]
    asset_id = _positive_int(asset.get("asset_id"), label="asset_id")
    if asset.get("is_open") is not True:
        raise BinaryContractError("ativo está fechado")

    minimum = _decimal(
        asset.get("min_amount"), label="limites de valor", positive=True)
    maximum = _decimal(
        asset.get("max_amount"), label="limites de valor", positive=True)
    if minimum > maximum or not minimum <= stake <= maximum:
        raise BinaryContractError("valor fora dos limites oferecidos")

    payout = asset.get("profit_percent")
    if (isinstance(payout, bool) or not isinstance(payout, int)
            or not 0 <= payout <= 100 or payout < min_profit_percent):
        raise BinaryContractError("payout oferecido abaixo do mínimo")

    expirations = asset.get("expirations")
    if (not isinstance(expirations, list)
            or any(isinstance(item, bool) or not isinstance(item, int)
                   or item <= 0 for item in expirations)
            or expiry not in expirations):
        raise BinaryContractError("vencimento não foi oferecido")

    return BinaryTradePlan(
        balance_id=account, asset_id=asset_id,
        instrument=_asset_symbol(asset), direction=side, amount=stake,
        profit_percent=payout, expiration=expiry,
    )


def _matching_positions(payload: dict[str, Any], position_id: str) -> list[dict[str, Any]]:
    return [
        item for item in _list(payload, "positions")
        if str(item.get("position_id") or "").strip() == position_id
    ]


def reconcile_binary_trade(
        *, position_id: str, positions_payload: dict[str, Any],
        history_payload: dict[str, Any]) -> BinaryReconciliation:
    """Concilia por ``position_id`` sem transformar ausência em cancelamento."""
    target = str(position_id or "").strip()
    if not target:
        raise BinaryContractError("position_id inválido")
    opened = _matching_positions(positions_payload, target)
    settled = _matching_positions(history_payload, target)
    if len(opened) > 1 or len(settled) > 1 or (opened and settled):
        raise BinaryContractError("identidade de posição duplicada")
    if opened:
        amount = _decimal(
            opened[0].get("amount"), label="resposta binária", positive=True)
        return BinaryReconciliation(
            BinaryReconciliationState.OPEN, target, amount=amount)
    if settled:
        item = settled[0]
        result = str(item.get("result") or "").strip().lower()
        if result not in {"win", "loss", "equal", "canceled"}:
            raise BinaryContractError("resultado binário inválido")
        amount = _decimal(
            item.get("amount"), label="resposta binária", positive=True)
        profit = _decimal(item.get("profit"), label="resposta binária")
        return BinaryReconciliation(
            BinaryReconciliationState.SETTLED, target, amount=amount,
            result=result, profit=profit)
    return BinaryReconciliation(BinaryReconciliationState.UNKNOWN, target)


ToolCaller = Callable[[str, dict[str, Any]], dict[str, Any]]
StateWriter = Callable[[dict[str, Any]], None]


def _request_fingerprint(
        *, balance_id: int, instrument: str, direction: str, amount: Any,
        expiration: int, min_profit_percent: int) -> str:
    """Identidade canônica da solicitação, calculável sem consultar a rede."""
    account = _positive_int(balance_id, label="balance_id")
    symbol = _instrument_key(instrument)
    if not symbol:
        raise BinaryContractError("instrumento inválido")
    side = str(direction or "").strip().lower()
    if side not in {"call", "put"}:
        raise BinaryContractError("direção binária inválida")
    stake = _decimal(amount, label="valor da aposta", positive=True)
    expiry = _positive_int(expiration, label="vencimento")
    if (isinstance(min_profit_percent, bool)
            or not isinstance(min_profit_percent, int)
            or not 0 <= min_profit_percent <= 100):
        raise BinaryContractError("piso de payout inválido")
    canonical = json.dumps({
        "balance_id": account,
        "instrument": symbol,
        "direction": side,
        "amount": str(stake.normalize()),
        "expiration": expiry,
        "min_profit_percent": min_profit_percent,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class IqOptionBinaryExecutor:
    """Fronteira mutável da IQ Option, selada e fail-closed.

    O chamador de ferramentas é injetado e normaliza respostas MCP para
    dicionários. O executor fixa os nomes e argumentos das ferramentas; uma
    intenção PREPARED/UNKNOWN jamais é reenviada porque ``place_trade`` não
    oferece uma chave de idempotência do cliente.
    """

    SCHEMA = "dz23.iq-option-binary-executor/1"

    def __init__(
            self, *, tool_caller: ToolCaller, persist: StateWriter,
            live_authorized: bool, confirmation_phrase: str,
            max_order_value: Any, max_daily_loss: Any,
            restored_state: dict[str, Any] | None = None) -> None:
        if live_authorized is not True:
            raise BinaryExecutionSecurityError(
                "selo de codigo LIVE não autorizado")
        if not hmac.compare_digest(
                str(confirmation_phrase or "").strip(), LIVE_CONFIRM_PHRASE):
            raise BinaryExecutionSecurityError(
                "frase explícita de autorização LIVE inválida")
        if not callable(tool_caller):
            raise BinaryExecutionSecurityError(
                "transporte MCP de execução ausente")
        if not callable(persist):
            raise BinaryExecutionSecurityError(
                "persistencia durável obrigatória ausente")
        try:
            self.max_order_value = _decimal(
                max_order_value, label="teto por ordem", positive=True)
            self.max_daily_loss = _decimal(
                max_daily_loss, label="teto de perda diaria", positive=True)
        except BinaryContractError as exc:
            raise BinaryExecutionSecurityError(str(exc)) from None
        self.current_daily_loss = Decimal("0")
        self._call_tool = tool_caller
        self._persist = persist
        self._submissions: dict[str, BinarySubmission] = {}
        if restored_state is not None:
            self.restore_state(restored_state)

    @staticmethod
    def _client_order_id(value: Any) -> str:
        client_order_id = str(value or "").strip()
        if not client_order_id or len(client_order_id) > 128:
            raise BinaryContractError("client_order_id inválido")
        return client_order_id

    def set_daily_loss(self, value: Any) -> None:
        loss = _decimal(value, label="perda diaria")
        if loss < 0:
            raise BinaryExecutionSecurityError("perda diaria inválida")
        self.current_daily_loss = loss

    def submission(self, client_order_id: str) -> BinarySubmission:
        key = self._client_order_id(client_order_id)
        try:
            return self._submissions[key]
        except KeyError:
            raise BinaryContractError("intenção binária desconhecida") from None

    @staticmethod
    def _plan_doc(plan: BinaryTradePlan) -> dict[str, Any]:
        return {
            "balance_id": plan.balance_id,
            "asset_id": plan.asset_id,
            "instrument": plan.instrument,
            "direction": plan.direction,
            "amount": str(plan.amount),
            "profit_percent": plan.profit_percent,
            "expiration": plan.expiration,
        }

    def snapshot_state(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "current_daily_loss": str(self.current_daily_loss),
            "submissions": [{
                "client_order_id": item.client_order_id,
                "request_fingerprint": item.request_fingerprint,
                "state": item.state.value,
                "position_id": item.position_id,
                "plan": self._plan_doc(item.plan),
            } for item in self._submissions.values()],
        }

    @staticmethod
    def _plan_from_doc(document: Any) -> BinaryTradePlan:
        if not isinstance(document, dict):
            raise BinaryExecutionSecurityError(
                "estado binário restaurado inválido")
        instrument = str(document.get("instrument") or "").strip().upper()
        direction = str(document.get("direction") or "").strip().lower()
        payout = document.get("profit_percent")
        if (not instrument or direction not in {"call", "put"}
                or isinstance(payout, bool) or not isinstance(payout, int)
                or not 0 <= payout <= 100):
            raise BinaryExecutionSecurityError(
                "estado binário restaurado inválido")
        return BinaryTradePlan(
            balance_id=_positive_int(
                document.get("balance_id"), label="balance_id"),
            asset_id=_positive_int(document.get("asset_id"), label="asset_id"),
            instrument=instrument,
            direction=direction,
            amount=_decimal(
                document.get("amount"), label="valor da aposta", positive=True),
            profit_percent=payout,
            expiration=_positive_int(
                document.get("expiration"), label="vencimento"),
        )

    def restore_state(self, document: dict[str, Any]) -> None:
        if (not isinstance(document, dict)
                or document.get("schema") != self.SCHEMA
                or not isinstance(document.get("submissions"), list)):
            raise BinaryExecutionSecurityError(
                "estado binário restaurado inválido")
        restored: dict[str, BinarySubmission] = {}
        try:
            self.set_daily_loss(document.get("current_daily_loss", "0"))
            for raw in document["submissions"]:
                if not isinstance(raw, dict):
                    raise BinaryExecutionSecurityError(
                        "estado binário restaurado inválido")
                key = self._client_order_id(raw.get("client_order_id"))
                fingerprint_value = str(
                    raw.get("request_fingerprint") or "").strip()
                if len(fingerprint_value) != 64:
                    raise BinaryExecutionSecurityError(
                        "estado binário restaurado inválido")
                state = BinarySubmissionState(raw.get("state"))
                position_id = str(raw.get("position_id") or "").strip()
                if state is BinarySubmissionState.ACKNOWLEDGED and not position_id:
                    raise BinaryExecutionSecurityError(
                        "estado binário restaurado inválido")
                if key in restored:
                    raise BinaryExecutionSecurityError(
                        "estado binário restaurado possui identidade duplicada")
                restored[key] = BinarySubmission(
                    client_order_id=key,
                    request_fingerprint=fingerprint_value,
                    plan=self._plan_from_doc(raw.get("plan")),
                    state=state,
                    position_id=position_id,
                )
        except BinaryContractError:
            raise
        except (TypeError, ValueError) as exc:
            raise BinaryExecutionSecurityError(
                "estado binário restaurado inválido") from exc
        self._submissions = restored

    def _write_state(self) -> None:
        self._persist(self.snapshot_state())

    def _existing(self, key: str, fingerprint_value: str) -> BinarySubmission | None:
        item = self._submissions.get(key)
        if item is None:
            return None
        if not hmac.compare_digest(item.request_fingerprint, fingerprint_value):
            raise BinaryContractError(
                "client_order_id já existe com conteudo diferente")
        if item.state is BinarySubmissionState.ACKNOWLEDGED:
            return item
        raise BinaryAmbiguousSubmission(
            f"intenção em estado {item.state.value.upper()}; "
            "reconciliacao obrigatória, reenvio recusado")

    def submit(
            self, *, client_order_id: str, balance_id: int, instrument: str,
            direction: str, amount: Any, expiration: int,
            min_profit_percent: int) -> BinarySubmission:
        key = self._client_order_id(client_order_id)
        fingerprint_value = _request_fingerprint(
            balance_id=balance_id, instrument=instrument, direction=direction,
            amount=amount, expiration=expiration,
            min_profit_percent=min_profit_percent)
        existing = self._existing(key, fingerprint_value)
        if existing is not None:
            return existing

        stake = _decimal(amount, label="valor da aposta", positive=True)
        if stake > self.max_order_value:
            raise BinaryExecutionSecurityError(
                "valor excede o teto por ordem LIVE")
        if self.current_daily_loss + stake > self.max_daily_loss:
            raise BinaryExecutionSecurityError(
                "risco máximo excede o teto de perda diaria LIVE")

        try:
            assets_payload = self._call_tool(
                "list_assets", {"only_enabled": True})
        except Exception as exc:
            raise BinaryContractError(
                f"consulta fresca de ativos falhou ({type(exc).__name__})") from None
        plan = plan_binary_trade(
            assets_payload, balance_id=balance_id, instrument=instrument,
            direction=direction, amount=amount, expiration=expiration,
            min_profit_percent=min_profit_percent)
        item = BinarySubmission(
            client_order_id=key,
            request_fingerprint=fingerprint_value,
            plan=plan,
        )
        self._submissions[key] = item
        try:
            self._write_state()
        except Exception as exc:
            del self._submissions[key]
            raise BinaryExecutionSecurityError(
                "persistencia falhou antes do envio; operação não enviada "
                f"({type(exc).__name__})") from None

        try:
            response = self._call_tool("place_trade", plan.arguments)
        except Exception:
            item.state = BinarySubmissionState.UNKNOWN
            with suppress(Exception):
                self._write_state()
            raise BinaryAmbiguousSubmission(
                "envio sem resposta conclusiva; reconciliacao obrigatória e "
                "reenvio recusado") from None

        position_id = ""
        if isinstance(response, dict):
            raw_position_id = response.get("position_id")
            if (not isinstance(raw_position_id, bool)
                    and isinstance(raw_position_id, (str, int))):
                position_id = str(raw_position_id).strip()
        if not position_id:
            item.state = BinarySubmissionState.UNKNOWN
            with suppress(Exception):
                self._write_state()
            raise BinaryAmbiguousSubmission(
                "resposta de envio sem position_id verificável; "
                "reconciliacao obrigatória")

        item.position_id = position_id
        item.state = BinarySubmissionState.ACKNOWLEDGED
        try:
            self._write_state()
        except Exception as exc:
            item.state = BinarySubmissionState.UNKNOWN
            raise BinaryAmbiguousSubmission(
                "ordem aceita sem confirmacao duravel local; reconciliacao "
                f"obrigatória ({type(exc).__name__})") from None
        return item

    def reconcile(self, client_order_id: str) -> BinaryReconciliation:
        item = self.submission(client_order_id)
        if not item.position_id:
            raise BinaryAmbiguousSubmission(
                "intenção ambígua sem position_id; não é seguro inventar "
                "correspondência por fingerprint")
        try:
            positions = self._call_tool(
                "list_positions", {"balance_id": item.plan.balance_id})
            history = self._call_tool(
                "get_trade_history", {"skip": 0, "limit": 100})
        except Exception as exc:
            raise BinaryContractError(
                f"reconciliacao binária falhou ({type(exc).__name__})") from None
        return reconcile_binary_trade(
            position_id=item.position_id,
            positions_payload=positions,
            history_payload=history,
        )


__all__ = [
    "BinaryAmbiguousSubmission",
    "BinaryContractError",
    "BinaryExecutionSecurityError",
    "BinaryReconciliation",
    "BinaryReconciliationState",
    "BinarySubmission",
    "BinarySubmissionState",
    "BinaryTradePlan",
    "IqOptionBinaryExecutor",
    "plan_binary_trade",
    "reconcile_binary_trade",
]

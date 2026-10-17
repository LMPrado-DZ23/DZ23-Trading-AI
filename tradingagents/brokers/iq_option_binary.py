"""Contrato canônico para opções binárias da IQ Option.

Este módulo é deliberadamente puro: transforma respostas já obtidas por
ferramentas oficiais de leitura em um plano exato e em um estado de
reconciliação. Ele não abre sessão MCP e não contém uma função que chame
``place_trade``. Assim, preparar e testar o contrato não cria uma segunda
porta de envio enquanto o livro binário ainda não foi homologado.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any


class BinaryContractError(RuntimeError):
    """Entrada ou resposta incompatível com o contrato binário seguro."""


class BinaryReconciliationState(str, Enum):
    OPEN = "open"
    SETTLED = "settled"
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


__all__ = [
    "BinaryContractError",
    "BinaryReconciliation",
    "BinaryReconciliationState",
    "BinaryTradePlan",
    "plan_binary_trade",
    "reconcile_binary_trade",
]

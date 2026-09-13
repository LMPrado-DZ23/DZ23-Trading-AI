from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from .enums import DecisionStatus, Environment, OrderStatus, RiskStatus, Side, StrategyStatus

SCHEMA_VERSION = "1.0"


def _require(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} deve ser uma string não vazia")
    return value


@dataclass(frozen=True, slots=True)
class Evidence:
    evidence_id: str
    source_id: str
    source_type: str
    instrument_id: str
    content_hash: str
    observed_at: datetime
    available_at: datetime | None
    valid_until: datetime | None = None
    provenance: str = ""
    payload_reference: str = ""

    def __post_init__(self) -> None:
        for name in ("evidence_id", "source_id", "source_type", "instrument_id", "content_hash"):
            _require(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class Instrument:
    instrument_id: str
    symbol: str
    asset_class: str
    venue: str
    quote_currency: str
    base_currency: str = ""
    contract_size: Decimal = Decimal("1")
    tick_size: Decimal = Decimal("0.00001")
    min_quantity: Decimal = Decimal("0.00001")
    price_precision: int = 8
    quantity_precision: int = 8
    trading_session: str = "24x7"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    snapshot_id: str
    instrument_id: str
    venue: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume: Decimal
    spread: Decimal
    observed_at: datetime
    available_at: datetime
    source_id: str
    sequence: int
    timezone: str = "UTC"

    def __post_init__(self) -> None:
        if self.bid < 0 or self.ask < 0 or self.last < 0:
            raise ValueError("preços não podem ser negativos")
        if self.ask and self.bid and self.ask < self.bid:
            raise ValueError("ask não pode ser menor que bid")
        if self.sequence < 0:
            raise ValueError("sequence deve ser não-negativo")


@dataclass(frozen=True, slots=True)
class Signal:
    signal_id: str
    strategy_id: str
    agent_id: str
    desk_id: str
    instrument_id: str
    side: Side
    regime: str
    confidence: Decimal
    thesis: str
    evidence_ids: tuple[str, ...]
    created_at: datetime
    expires_at: datetime
    status: str = "ACTIVE"

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence deve estar entre 0 e 1")
        if not self.evidence_ids:
            raise ValueError("signal exige ao menos uma evidência")


@dataclass(frozen=True, slots=True)
class TradeDecision:
    decision_id: str
    run_id: str
    strategy_id: str
    desk_id: str
    instrument_id: str
    venue: str
    account_id: str
    side: Side
    order_type: str
    entry_price_reference: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    risk_amount: Decimal
    risk_pct_equity: Decimal
    quantity: Decimal
    time_in_force: str
    signal_expiry: datetime
    confidence: Decimal
    regime: str
    evidence_ids: tuple[str, ...]
    agent_votes: tuple[dict[str, Any], ...]
    created_at: datetime
    status: DecisionStatus = DecisionStatus.PROPOSED
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("decision_id", "run_id", "strategy_id", "desk_id", "instrument_id", "venue", "account_id"):
            _require(getattr(self, name), name)
        if self.entry_price_reference <= 0 or self.stop_loss <= 0 or self.take_profit <= 0:
            raise ValueError("preço, stop e alvo devem ser positivos")
        if self.quantity <= 0 or self.risk_amount < 0 or not 0 <= self.risk_pct_equity <= 1:
            raise ValueError("quantidade/risco inválidos")
        if not self.evidence_ids:
            raise ValueError("decisão exige evidências")


@dataclass(frozen=True, slots=True)
class RiskDecision:
    risk_decision_id: str
    decision_id: str
    status: RiskStatus
    approved_quantity: Decimal
    approved_risk_amount: Decimal
    reason_codes: tuple[str, ...]
    limits_checked: tuple[str, ...]
    portfolio_snapshot_id: str
    engine_version: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class OrderRequest:
    client_order_id: str
    decision_id: str
    run_id: str
    strategy_id: str
    account_id: str
    instrument_id: str
    venue: str
    side: Side
    order_type: str
    quantity: Decimal
    price: Decimal | None
    stop_loss: Decimal
    take_profit: Decimal
    environment: Environment = Environment.PAPER
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("client_order_id", "decision_id", "run_id", "strategy_id", "account_id", "instrument_id", "venue"):
            _require(getattr(self, name), name)
        if self.quantity <= 0 or self.stop_loss <= 0 or self.take_profit <= 0:
            raise ValueError("ordem exige quantidade e níveis positivos")
        if self.environment is Environment.LIVE:
            raise ValueError("LIVE está bloqueado nesta versão")


@dataclass(frozen=True, slots=True)
class OrderAck:
    order_id: str
    client_order_id: str
    status: OrderStatus
    accepted_quantity: Decimal
    filled_quantity: Decimal
    average_price: Decimal | None
    created_at: datetime
    message: str = ""


@dataclass(frozen=True, slots=True)
class Position:
    position_id: str
    account_id: str
    instrument_id: str
    side: Side
    quantity: Decimal
    average_price: Decimal
    unrealized_pnl: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    snapshot_id: str
    account_id: str
    equity: Decimal
    balance: Decimal
    used_margin: Decimal
    available_margin: Decimal
    daily_pnl: Decimal
    drawdown_pct: Decimal
    gross_exposure: Decimal
    net_exposure: Decimal
    positions: tuple[Position, ...] = ()
    captured_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Strategy:
    strategy_id: str
    name: str
    version: str
    desk_id: str
    status: StrategyStatus
    artifact_hash: str
    metadata: dict[str, Any] = field(default_factory=dict)

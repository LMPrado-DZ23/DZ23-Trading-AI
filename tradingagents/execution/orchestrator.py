"""The explicit Brain -> deterministic sizing -> gated broker bridge."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from tradingagents.portfolio import (
    MarketRiskSnapshot,
    PortfolioConstraints,
    PortfolioSnapshot,
)

from .engine import ExecutionEngine
from .gates import prepare_execution
from .models import ExecutionIntent, ExecutionReceipt


class ExecutionGraph(Protocol):
    last_telemetry: dict[str, Any] | None

    def propagate(self, company_name: str, trade_date: str, **kwargs: Any): ...


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    signal: str
    intent: ExecutionIntent
    receipt: ExecutionReceipt


class TradingExecutionOrchestrator:
    def __init__(self, graph: ExecutionGraph, engine: ExecutionEngine) -> None:
        self.graph = graph
        self.engine = engine

    def run(
        self,
        ticker: str,
        trade_date: str,
        *,
        mode: Literal["shadow", "paper"],
        portfolio_snapshot: PortfolioSnapshot,
        market_risk: MarketRiskSnapshot,
        portfolio_constraints: PortfolioConstraints | None = None,
        asset_type: str = "stock",
        decision_at: datetime | None = None,
        now: datetime | None = None,
    ) -> ExecutionOutcome:
        final_state, signal = self.graph.propagate(
            ticker,
            trade_date,
            asset_type=asset_type,
            decision_at=decision_at,
            run_mode=mode,
            portfolio_snapshot=portfolio_snapshot,
            market_risk=market_risk,
            portfolio_constraints=portfolio_constraints,
        )
        intent = prepare_execution(
            final_state,
            self.graph.last_telemetry,
            mode=mode,
            now=now,
        )
        receipt = self.engine.execute(intent)
        return ExecutionOutcome(signal=signal, intent=intent, receipt=receipt)

"""Multi-session backtest with close-time decisions and raw next-open fills."""

from __future__ import annotations

import json
import math
import os
import statistics
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any

from tradingagents.agents.utils.rating import RATING_REVIEW, RATINGS_5_TIER
from tradingagents.memory import (
    MarketRegime,
    RegimeMemoryStore,
    RegimeObservation,
    RegimeSnapshot,
    classify_regime,
)
from tradingagents.portfolio import (
    MarketRiskSnapshot,
    PortfolioConstraints,
    PortfolioSnapshot,
    Position,
    size_position,
)
from tradingagents.portfolio.models import decimal_value
from tradingagents.runtime import RunContext, build_runtime_config

from .data import PriceBar, PriceSeries

BACKTEST_SCHEMA_VERSION = "tradingagents/backtest/1"
_BPS = Decimal("10000")


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_cash: Decimal = Decimal("100000")
    commission_bps: Decimal = Decimal("1")
    slippage_bps: Decimal = Decimal("2")
    volatility_lookback: int = 20
    minimum_volatility_observations: int = 10
    annualization_sessions: int = 252
    minimum_annualized_volatility: Decimal = Decimal("0.01")
    regime_lookback: int = 20
    memory_horizon_sessions: int = 5

    def __post_init__(self) -> None:
        for name in (
            "initial_cash",
            "commission_bps",
            "slippage_bps",
            "minimum_annualized_volatility",
        ):
            object.__setattr__(self, name, decimal_value(getattr(self, name), name))
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if not Decimal("0") <= self.commission_bps <= Decimal("1000"):
            raise ValueError("commission_bps must be between 0 and 1000")
        if not Decimal("0") <= self.slippage_bps <= Decimal("1000"):
            raise ValueError("slippage_bps must be between 0 and 1000")
        if self.volatility_lookback < 2:
            raise ValueError("volatility_lookback must be at least 2")
        if not 2 <= self.minimum_volatility_observations <= self.volatility_lookback:
            raise ValueError("minimum volatility observations must be in [2, lookback]")
        if self.annualization_sessions <= 0:
            raise ValueError("annualization_sessions must be positive")
        if self.minimum_annualized_volatility <= 0:
            raise ValueError("minimum_annualized_volatility must be positive")
        if self.regime_lookback < 3:
            raise ValueError("regime_lookback must be at least 3")
        if self.memory_horizon_sessions <= 0:
            raise ValueError("memory_horizon_sessions must be positive")


@dataclass(frozen=True, slots=True)
class DecisionRequest:
    context: RunContext
    portfolio: PortfolioSnapshot
    market_risk: MarketRiskSnapshot
    regime: RegimeSnapshot
    memory_context: str = ""


@dataclass(frozen=True, slots=True)
class _PendingMemory:
    decision_index: int
    context: RunContext
    rating: str
    regime: MarketRegime


@dataclass(frozen=True, slots=True)
class TradeRecord:
    decision_date: str
    fill_date: str
    rating: str
    action: str
    requested_quantity: Decimal
    filled_quantity: Decimal
    raw_next_open: Decimal
    fill_price: Decimal
    commission: Decimal
    realized_pnl: Decimal | None
    split_ratio: Decimal
    cash_dividend: Decimal


@dataclass(frozen=True, slots=True)
class EquityPoint:
    session_date: str
    cash: Decimal
    quantity: Decimal
    raw_close: Decimal
    equity: Decimal
    benchmark_equity: Decimal


@dataclass(frozen=True, slots=True)
class BacktestResult:
    ticker: str
    benchmark_ticker: str
    started_on: str
    ended_on: str
    trades: tuple[TradeRecord, ...]
    equity_curve: tuple[EquityPoint, ...]
    metrics: dict[str, float | int | None]
    config: BacktestConfig
    schema: str = BACKTEST_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        def convert(value: Any) -> Any:
            if isinstance(value, Decimal):
                return str(value)
            if isinstance(value, tuple):
                return [convert(item) for item in value]
            if isinstance(value, dict):
                return {key: convert(item) for key, item in value.items()}
            if hasattr(value, "__dataclass_fields__"):
                return convert(asdict(value))
            return value

        return convert(self)

    def write(self, output_dir: str | Path) -> tuple[Path, Path]:
        destination = Path(output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        json_path = destination / "backtest.json"
        markdown_path = destination / "backtest.md"
        payload = self.to_dict()
        _atomic_write(json_path, json.dumps(payload, sort_keys=True, indent=2) + "\n")
        lines = [
            f"# Backtest — {self.ticker}",
            "",
            f"Period: {self.started_on} through {self.ended_on}",
            f"Benchmark: {self.benchmark_ticker}",
            "",
            "## Metrics",
            "",
        ]
        lines.extend(
            f"- {key}: {value if value is not None else 'N/A'}"
            for key, value in sorted(self.metrics.items())
        )
        lines.extend(["", "## Execution", "", f"- Trades: {len(self.trades)}"])
        _atomic_write(markdown_path, "\n".join(lines) + "\n")
        return json_path, markdown_path


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class GraphDecisionProvider:
    """Adapter that asks TradingAgentsGraph for a rating, never for quantity."""

    def __init__(self, graph: Any) -> None:
        self.graph = graph

    def __call__(self, request: DecisionRequest) -> str:
        _state, signal = self.graph.propagate(
            request.context.ticker,
            request.context.trade_date.isoformat(),
            asset_type=request.context.asset_type,
            decision_at=request.context.decision_at,
            run_mode="backtest",
            statistical_context=request.memory_context,
        )
        return signal


class BacktestRunner:
    def __init__(
        self,
        strategy: Callable[[DecisionRequest], str],
        *,
        runtime_config: dict[str, Any] | None = None,
        portfolio_constraints: PortfolioConstraints | None = None,
        config: BacktestConfig | None = None,
        memory_store: RegimeMemoryStore | None = None,
    ) -> None:
        self.strategy = strategy
        self.runtime_config = build_runtime_config(runtime_config)
        self.portfolio_constraints = portfolio_constraints or PortfolioConstraints.from_mapping(
            self.runtime_config.get("portfolio_constraints")
        )
        self.config = config or BacktestConfig()
        self.memory_store = memory_store

    def run(
        self,
        prices: PriceSeries,
        benchmark: PriceSeries,
        *,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
        asset_type: str = "stock",
    ) -> BacktestResult:
        bars, benchmark_bars = self._aligned(prices, benchmark)
        start = date.fromisoformat(str(start_date)) if start_date else bars[0].session_date
        end = date.fromisoformat(str(end_date)) if end_date else bars[-2].session_date
        decision_indices = [
            index for index in range(len(bars) - 1)
            if start <= bars[index].session_date <= end
            and index >= self.config.minimum_volatility_observations
        ]
        if not decision_indices:
            raise ValueError("date range has no decision session with enough prior volatility data")

        first_index = decision_indices[0]
        cash = self.config.initial_cash
        quantity = Decimal("0")
        average_cost = Decimal("0")
        peak_equity = cash
        benchmark_base = benchmark_bars[first_index].raw_close
        benchmark_quantity = self.config.initial_cash / benchmark_base
        benchmark_cash = Decimal("0")
        trades: list[TradeRecord] = []
        pending_memory: list[_PendingMemory] = []
        memory_observations = 0
        curve = [EquityPoint(
            session_date=bars[first_index].session_date.isoformat(),
            cash=cash,
            quantity=quantity,
            raw_close=bars[first_index].raw_close,
            equity=cash,
            benchmark_equity=cash,
        )]

        for index in decision_indices:
            decision_bar = bars[index]
            fill_bar = bars[index + 1]
            marked_equity = cash + quantity * decision_bar.raw_close
            peak_equity = max(peak_equity, marked_equity)
            context = RunContext.create(
                prices.ticker,
                decision_bar.session_date,
                asset_type,
                self.runtime_config,
                run_mode="backtest",
            )
            memory_observations += self._resolve_memory(
                pending_memory,
                through_index=index,
                bars=bars,
                benchmark_bars=benchmark_bars,
            )
            position = (
                Position(prices.ticker, quantity, decision_bar.raw_close)
                if quantity > 0 else None
            )
            portfolio = PortfolioSnapshot(
                as_of=context.decision_at,
                cash=cash,
                equity=marked_equity,
                peak_equity=peak_equity,
                positions=(position,) if position else (),
            )
            volatility = self._volatility(bars, index)
            market = MarketRiskSnapshot(
                ticker=prices.ticker,
                as_of=context.decision_at,
                price=decision_bar.raw_close,
                annualized_volatility=volatility,
            )
            regime = self._regime(bars, index, context)
            memory_context = (
                self.memory_store.context(
                    prices.ticker, regime.regime, as_of=context.decision_at
                )
                if self.memory_store else ""
            )
            signal = self.strategy(DecisionRequest(
                context, portfolio, market, regime, memory_context
            ))
            if signal not in (*RATINGS_5_TIER, RATING_REVIEW):
                raise ValueError(f"strategy returned invalid signal {signal!r}")
            sizing = size_position(
                signal, context, portfolio, market, self.portfolio_constraints
            )
            if signal != RATING_REVIEW:
                pending_memory.append(_PendingMemory(
                    decision_index=index,
                    context=context,
                    rating=signal,
                    regime=regime.regime,
                ))
            requested = sizing.order_quantity
            if fill_bar.split_ratio != 1:
                quantity *= fill_bar.split_ratio
                requested_for_fill = requested * fill_bar.split_ratio
                if average_cost:
                    average_cost /= fill_bar.split_ratio
            else:
                requested_for_fill = requested
            if fill_bar.cash_dividend:
                cash += quantity * fill_bar.cash_dividend
            benchmark_fill_bar = benchmark_bars[index + 1]
            if benchmark_fill_bar.split_ratio != 1:
                benchmark_quantity *= benchmark_fill_bar.split_ratio
            if benchmark_fill_bar.cash_dividend:
                benchmark_cash += benchmark_quantity * benchmark_fill_bar.cash_dividend
            filled, fill_price, commission = self._fill(
                requested_for_fill, fill_bar.raw_open, cash, quantity
            )
            realized_pnl = None
            if filled > 0:
                prior_cost = average_cost * quantity
                cash -= filled * fill_price + commission
                quantity += filled
                average_cost = (prior_cost + filled * fill_price + commission) / quantity
            elif filled < 0:
                sold = abs(filled)
                cash += sold * fill_price - commission
                quantity -= sold
                realized_pnl = (fill_price - average_cost) * sold - commission
                if quantity == 0:
                    average_cost = Decimal("0")

            if filled != 0:
                trades.append(TradeRecord(
                    decision_date=decision_bar.session_date.isoformat(),
                    fill_date=fill_bar.session_date.isoformat(),
                    rating=signal,
                    action="BUY" if filled > 0 else "SELL",
                    requested_quantity=requested,
                    filled_quantity=filled,
                    raw_next_open=fill_bar.raw_open,
                    fill_price=fill_price,
                    commission=commission,
                    realized_pnl=realized_pnl,
                    split_ratio=fill_bar.split_ratio,
                    cash_dividend=fill_bar.cash_dividend,
                ))

            close_equity = cash + quantity * fill_bar.raw_close
            peak_equity = max(peak_equity, close_equity)
            curve.append(EquityPoint(
                session_date=fill_bar.session_date.isoformat(),
                cash=cash,
                quantity=quantity,
                raw_close=fill_bar.raw_close,
                equity=close_equity,
                benchmark_equity=(
                    benchmark_cash
                    + benchmark_quantity * benchmark_fill_bar.raw_close
                ),
            ))

        memory_observations += self._resolve_memory(
            pending_memory,
            through_index=decision_indices[-1] + 1,
            bars=bars,
            benchmark_bars=benchmark_bars,
        )
        metrics = self._metrics(curve, trades)
        metrics["memory_observation_count"] = memory_observations
        return BacktestResult(
            ticker=prices.ticker,
            benchmark_ticker=benchmark.ticker,
            started_on=curve[0].session_date,
            ended_on=curve[-1].session_date,
            trades=tuple(trades),
            equity_curve=tuple(curve),
            metrics=metrics,
            config=self.config,
        )

    @staticmethod
    def _aligned(
        prices: PriceSeries, benchmark: PriceSeries
    ) -> tuple[tuple[PriceBar, ...], tuple[PriceBar, ...]]:
        benchmark_by_date = {bar.session_date: bar for bar in benchmark.bars}
        missing = [bar.session_date for bar in prices.bars if bar.session_date not in benchmark_by_date]
        if missing:
            raise ValueError(f"benchmark is missing {len(missing)} required sessions")
        return prices.bars, tuple(benchmark_by_date[bar.session_date] for bar in prices.bars)

    def _volatility(self, bars: tuple[PriceBar, ...], index: int) -> Decimal:
        start = max(1, index - self.config.volatility_lookback + 1)
        returns = [
            float(bars[position].raw_close / bars[position - 1].raw_close - 1)
            for position in range(start, index + 1)
        ]
        if len(returns) < self.config.minimum_volatility_observations:
            raise ValueError("insufficient point-in-time observations for volatility")
        measured = Decimal(str(statistics.stdev(returns) * math.sqrt(
            self.config.annualization_sessions
        )))
        return max(measured, self.config.minimum_annualized_volatility)

    def _regime(
        self, bars: tuple[PriceBar, ...], index: int, context: RunContext
    ) -> RegimeSnapshot:
        start = max(0, index - self.config.regime_lookback + 1)
        return classify_regime(
            (bar.raw_close for bar in bars[start:index + 1]),
            as_of=context.decision_at,
            annualization_sessions=self.config.annualization_sessions,
        )

    def _resolve_memory(
        self,
        pending: list[_PendingMemory],
        *,
        through_index: int,
        bars: tuple[PriceBar, ...],
        benchmark_bars: tuple[PriceBar, ...],
    ) -> int:
        if self.memory_store is None:
            return 0
        resolved = 0
        remaining = []
        for item in pending:
            resolution_index = item.decision_index + self.config.memory_horizon_sessions
            if resolution_index > through_index or resolution_index >= len(bars):
                remaining.append(item)
                continue
            raw_return = self._holding_return(
                bars, item.decision_index, resolution_index
            )
            benchmark_return = self._holding_return(
                benchmark_bars, item.decision_index, resolution_index
            )
            resolution_context = RunContext.create(
                item.context.ticker,
                bars[resolution_index].session_date,
                item.context.asset_type,
                self.runtime_config,
                run_mode="backtest",
            )
            if self.memory_store.append(RegimeObservation(
                ticker=item.context.ticker,
                rating=item.rating,
                regime=item.regime,
                decision_at=item.context.decision_at,
                resolution_at=resolution_context.decision_at,
                raw_return=raw_return,
                alpha_return=raw_return - benchmark_return,
                holding_sessions=self.config.memory_horizon_sessions,
            )):
                resolved += 1
        pending[:] = remaining
        return resolved

    @staticmethod
    def _holding_return(
        bars: tuple[PriceBar, ...], start_index: int, end_index: int
    ) -> Decimal:
        quantity = Decimal("1")
        cash = Decimal("0")
        for bar in bars[start_index + 1:end_index + 1]:
            quantity *= bar.split_ratio
            cash += quantity * bar.cash_dividend
        ending_value = cash + quantity * bars[end_index].raw_close
        return ending_value / bars[start_index].raw_close - 1

    def _fill(
        self,
        requested: Decimal,
        raw_open: Decimal,
        cash: Decimal,
        current_quantity: Decimal,
    ) -> tuple[Decimal, Decimal, Decimal]:
        if requested == 0:
            return Decimal("0"), raw_open, Decimal("0")
        direction = Decimal("1") if requested > 0 else Decimal("-1")
        fill_price = raw_open * (Decimal("1") + direction * self.config.slippage_bps / _BPS)
        if requested > 0:
            unit_cost = fill_price * (Decimal("1") + self.config.commission_bps / _BPS)
            affordable = (cash / unit_cost / self.portfolio_constraints.quantity_step).to_integral_value(
                rounding=ROUND_DOWN
            ) * self.portfolio_constraints.quantity_step
            filled = min(requested, affordable)
        else:
            filled = -min(abs(requested), current_quantity)
        commission = abs(filled * fill_price) * self.config.commission_bps / _BPS
        return filled, fill_price, commission

    def _metrics(
        self, curve: list[EquityPoint], trades: list[TradeRecord]
    ) -> dict[str, float | int | None]:
        equities = [float(point.equity) for point in curve]
        returns = [equities[index] / equities[index - 1] - 1 for index in range(1, len(equities))]
        total_return = equities[-1] / equities[0] - 1
        benchmark_return = float(curve[-1].benchmark_equity / curve[0].benchmark_equity - 1)
        periods = max(len(returns), 1)
        annualized_return = (1 + total_return) ** (
            self.config.annualization_sessions / periods
        ) - 1 if 1 + total_return > 0 else -1.0
        volatility = (
            statistics.stdev(returns) * math.sqrt(self.config.annualization_sessions)
            if len(returns) >= 2 else 0.0
        )
        sharpe = (
            statistics.mean(returns) / statistics.stdev(returns)
            * math.sqrt(self.config.annualization_sessions)
            if len(returns) >= 2 and statistics.stdev(returns) > 0 else None
        )
        peak = equities[0]
        max_drawdown = 0.0
        for equity in equities:
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, 1 - equity / peak)
        realized = [float(trade.realized_pnl) for trade in trades if trade.realized_pnl is not None]
        turnover = sum(float(abs(trade.filled_quantity * trade.fill_price)) for trade in trades)
        average_equity = statistics.mean(equities)
        return {
            "total_return": total_return,
            "annualized_return": annualized_return,
            "annualized_volatility": volatility,
            "sharpe": sharpe,
            "max_drawdown": max_drawdown,
            "hit_rate": (
                sum(value > 0 for value in realized) / len(realized) if realized else None
            ),
            "turnover": turnover / average_equity if average_equity else None,
            "benchmark_return": benchmark_return,
            "accumulated_alpha": total_return - benchmark_return,
            "trade_count": len(trades),
            "closed_trade_count": len(realized),
        }

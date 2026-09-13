from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256


@dataclass(frozen=True, slots=True)
class Bar:
    timestamp: datetime
    available_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_equity: Decimal = Decimal("100000")
    fee_bps: Decimal = Decimal("5")
    slippage_bps: Decimal = Decimal("2")
    position_pct: Decimal = Decimal("0.1")
    seed: int = 0


@dataclass(frozen=True, slots=True)
class BacktestResult:
    equity_curve: tuple[Decimal, ...]
    trades: int
    total_return: Decimal
    max_drawdown: Decimal
    dataset_hash: str
    manifest: dict[str, str]


def run_backtest(bars: Iterable[Bar], signal: Callable[[tuple[Bar, ...]], int], config: BacktestConfig, *, code_revision: str = "working-tree") -> BacktestResult:
    ordered = tuple(sorted(bars, key=lambda bar: bar.timestamp))
    if any(bar.available_at > bar.timestamp for bar in ordered):
        raise ValueError("available_at não pode ser posterior ao timestamp observado")
    dataset_hash = sha256(json.dumps([asdict(bar) for bar in ordered], default=str, sort_keys=True).encode()).hexdigest()
    equity = config.initial_equity
    curve = [equity]
    trades = 0
    for index in range(1, len(ordered)):
        history = ordered[:index]
        direction = signal(history)
        if direction not in (-1, 0, 1):
            raise ValueError("signal deve retornar -1, 0 ou 1")
        if direction:
            gross = ordered[index].close * config.position_pct * Decimal(direction)
            cost = abs(gross) * (config.fee_bps + config.slippage_bps) / Decimal("10000")
            equity += gross - cost
            trades += 1
        curve.append(equity)
    peak = config.initial_equity
    drawdowns = []
    for value in curve:
        peak = max(peak, value)
        drawdowns.append((peak - value) / peak if peak else Decimal("0"))
    manifest = {"dataset_hash": dataset_hash, "code_revision": code_revision, "seed": str(config.seed), "cost_model": f"fee_bps={config.fee_bps};slippage_bps={config.slippage_bps}"}
    return BacktestResult(tuple(curve), trades, (equity / config.initial_equity) - 1, max(drawdowns, default=Decimal("0")), dataset_hash, manifest)

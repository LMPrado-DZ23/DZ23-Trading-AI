from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .backtest import BacktestConfig, BacktestResult, Bar, run_backtest


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    train_start: int
    train_end: int
    test_start: int
    test_end: int


def windows(size: int, train_size: int, test_size: int, step: int | None = None) -> tuple[WalkForwardWindow, ...]:
    if min(size, train_size, test_size) <= 0 or train_size + test_size > size:
        return ()
    stride = step or test_size
    result = []
    start = 0
    while start + train_size + test_size <= size:
        result.append(WalkForwardWindow(start, start + train_size, start + train_size, start + train_size + test_size))
        start += stride
    return tuple(result)


def run_walk_forward(bars: Sequence[Bar], train_size: int, test_size: int, signal, config: BacktestConfig) -> tuple[BacktestResult, ...]:
    return tuple(run_backtest(bars[w.test_start:w.test_end], signal, config, code_revision=f"walk-forward:{w.train_start}:{w.train_end}") for w in windows(len(bars), train_size, test_size))

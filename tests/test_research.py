from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tradingagents.research import BacktestConfig, Bar, run_backtest, windows


def bars(n=6):
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return tuple(Bar(start + timedelta(days=i), start + timedelta(days=i), Decimal(100+i), Decimal(101+i), Decimal(99+i), Decimal(100+i), Decimal(10)) for i in range(n))


def test_backtest_produces_manifest_and_costs():
    result = run_backtest(bars(), lambda history: 1, BacktestConfig())
    assert result.trades == 5
    assert result.manifest["dataset_hash"] == result.dataset_hash
    assert result.total_return < Decimal("0.01")


def test_backtest_rejects_invalid_availability():
    row = list(bars())
    row[2] = Bar(row[2].timestamp, row[2].timestamp + timedelta(days=1), row[2].open, row[2].high, row[2].low, row[2].close, row[2].volume)
    with pytest.raises(ValueError):
        run_backtest(row, lambda history: 0, BacktestConfig())


def test_walk_forward_windows_are_disjoint():
    result = windows(20, 10, 5)
    assert result[0].train_end == result[0].test_start
    assert result[0].test_end == result[1].test_start

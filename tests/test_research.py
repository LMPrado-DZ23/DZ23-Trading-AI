from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tradingagents.research import BacktestConfig, Bar, run_backtest, run_walk_forward, windows


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


def test_signal_only_sees_strictly_past_bars():
    """Sem esta garantia o backtest veria o futuro: a história entregue ao
    sinal termina ANTES do bar decidido — sem o próprio bar, sem o que vem."""
    vistos = []

    def espiao(history):
        vistos.append(history)
        return 0

    b = bars(6)
    run_backtest(b, espiao, BacktestConfig())
    assert [len(h) for h in vistos] == [1, 2, 3, 4, 5]
    for i, h in enumerate(vistos, start=1):
        assert h == b[:i], "a história não é o passado estrito do bar decidido"
        assert all(bar.timestamp < b[i].timestamp for bar in h), (
            "o sinal recebeu um bar do presente ou do futuro")


def test_walk_forward_marks_each_window_with_its_own_revision():
    """Cada dobra carrega a revisão REAL do código (sha do commit HEAD) e a
    SUA janela em campo próprio — dobra repetida não se confunde com a
    primeira, e nenhum resultado se passa por mais reproduzível do que é."""
    from tradingagents.research import revisao_do_codigo

    b = bars(40)
    cfg = BacktestConfig()
    resultados = run_walk_forward(b, train_size=20, test_size=5,
                                  signal=lambda history: 0, config=cfg)
    janelas = windows(40, 20, 5)
    assert len(resultados) == len(janelas) == 4
    esperada = revisao_do_codigo()
    for r, j in zip(resultados, janelas, strict=True):
        assert r.manifest["code_revision"] == esperada
        assert r.manifest["janela"] == f"{j.train_start}:{j.train_end}"
    assert len({r.manifest["janela"] for r in resultados}) == 4
    assert len({r.dataset_hash for r in resultados}) == 4


def priced_bars(prices, volume=10000):
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    result = []
    for i, (opening, closing) in enumerate(prices):
        stamp = start + timedelta(days=i)
        opening, closing = Decimal(str(opening)), Decimal(str(closing))
        result.append(Bar(stamp, stamp, opening, max(opening, closing),
                          min(opening, closing), closing, Decimal(volume)))
    return tuple(result)


@pytest.mark.parametrize("direction", [-1, 1])
def test_flat_prices_never_create_profit_or_loss_without_costs(direction):
    result = run_backtest(
        priced_bars([(100, 100)] * 3), lambda history: direction,
        BacktestConfig(initial_equity=Decimal("1000"), fee_bps=Decimal("0"),
                       slippage_bps=Decimal("0")),
    )
    assert result.equity_curve == (Decimal("1000"),) * 3
    assert result.total_return == result.max_drawdown == Decimal("0")


@pytest.mark.parametrize("direction,closing,final", [
    (1, 110, "1009.79"), (-1, 110, "989.79"),
    (1, 90, "989.81"), (-1, 90, "1009.81"),
])
def test_round_trip_pnl_and_per_leg_fees(direction, closing, final):
    result = run_backtest(
        priced_bars([(100, 100), (100, closing)]), lambda history: direction,
        BacktestConfig(initial_equity=Decimal("1000"), fee_bps=Decimal("10"),
                       slippage_bps=Decimal("0")),
    )
    assert result.trades == 1
    assert result.equity_curve[-1] == Decimal(final)


def test_entry_uses_next_open_not_last_close_or_future_close():
    result = run_backtest(
        priced_bars([(50, 50), (100, 110)]), lambda history: 1,
        BacktestConfig(initial_equity=Decimal("1000"), fee_bps=Decimal("0"),
                       slippage_bps=Decimal("0")),
    )
    assert result.equity_curve == (Decimal("1000"), Decimal("1010"))


def test_fixed_reference_capital_does_not_compound_wins():
    result = run_backtest(
        priced_bars([(100, 100), (100, 110), (100, 110)]), lambda history: 1,
        BacktestConfig(initial_equity=Decimal("1000"), fee_bps=Decimal("0"),
                       slippage_bps=Decimal("0")),
    )
    assert result.equity_curve == (Decimal("1000"), Decimal("1010"), Decimal("1020"))


def test_bar_volume_budget_covers_both_legs():
    result = run_backtest(
        priced_bars([(100, 100), (100, 110)], volume=2), lambda history: 1,
        BacktestConfig(initial_equity=Decimal("1000"), fee_bps=Decimal("0"),
                       slippage_bps=Decimal("0"), max_participacao=Decimal("0.5")),
    )
    assert result.fills_limitados == 1
    assert result.equity_curve[-1] == Decimal("1005")


def test_slippage_estimate_is_charged_on_both_legs():
    result = run_backtest(
        priced_bars([(100, 100), (100, 110)]), lambda history: 1,
        BacktestConfig(initial_equity=Decimal("1000"), fee_bps=Decimal("0"),
                       slippage_bps=Decimal("10")),
    )
    assert result.equity_curve[-1] == Decimal("1009.79")


@pytest.mark.parametrize("changes", [
    {"initial_equity": Decimal("0")}, {"position_pct": Decimal("-0.1")},
    {"position_pct": Decimal("1.1")}, {"fee_bps": Decimal("-1")},
    {"slippage_bps": Decimal("NaN")}, {"max_participacao": Decimal("1.1")},
])
def test_invalid_economic_config_is_rejected(changes):
    with pytest.raises(ValueError):
        run_backtest(priced_bars([(100, 100)] * 2), lambda history: 1,
                     BacktestConfig(**changes))


def test_duplicate_timestamps_cannot_count_the_same_period_twice():
    rows = priced_bars([(100, 100), (100, 110)])
    with pytest.raises(ValueError):
        run_backtest((rows[0], rows[1], rows[1]), lambda history: 1, BacktestConfig())


@pytest.mark.parametrize("field,value", [
    ("open", "0"), ("close", "-1"), ("high", "NaN"),
    ("low", "Infinity"), ("volume", "NaN"), ("volume", "Infinity"),
    ("high", "99"), ("low", "101"),
])
def test_invalid_market_data_is_rejected_before_calling_signal(field, value):
    from dataclasses import replace

    rows = priced_bars([(100, 100)] * 2)
    rows = (rows[0], replace(rows[1], **{field: Decimal(value)}))
    calls = []

    def signal(history):
        calls.append(history)
        return 1

    with pytest.raises(ValueError):
        run_backtest(rows, signal, BacktestConfig())
    assert calls == []


def test_losses_reduce_next_trade_to_available_capital():
    result = run_backtest(
        priced_bars([(100, 100), (100, 50), (100, 200)], volume=1000000),
        lambda history: 1,
        BacktestConfig(initial_equity=Decimal("100"), position_pct=Decimal("1"),
                       fee_bps=Decimal("0"), slippage_bps=Decimal("0")),
    )
    assert result.equity_curve == (Decimal("100"), Decimal("50"), Decimal("100"))
    assert result.max_drawdown == Decimal("0.5")
    assert result.fills_limitados == 0


def test_entry_budget_includes_entry_costs():
    result = run_backtest(
        priced_bars([(90, 90)] * 2, volume=1000000), lambda history: 1,
        BacktestConfig(initial_equity=Decimal("99"), position_pct=Decimal("1"),
                       fee_bps=Decimal("1000"), slippage_bps=Decimal("0")),
    )
    assert result.equity_curve == (Decimal("99"), Decimal("81"))


def test_insolvency_stops_new_trades_without_erasing_short_debt():
    result = run_backtest(
        priced_bars([(100, 100), (100, 250), (100, 1)], volume=1000000),
        lambda history: -1,
        BacktestConfig(initial_equity=Decimal("100"), position_pct=Decimal("1"),
                       fee_bps=Decimal("0"), slippage_bps=Decimal("0")),
    )
    assert result.equity_curve == (Decimal("100"), Decimal("-50"), Decimal("-50"))
    assert result.trades == 1
    assert result.max_drawdown == Decimal("1.5")
    assert result.fills_rejeitados_por_liquidez == 0


def test_abstention_does_not_incur_costs_or_fills():
    result = run_backtest(priced_bars([(100, 100)] * 3), lambda history: 0,
                          BacktestConfig())
    assert result.equity_curve == (Decimal("100000"),) * 3
    assert result.trades == result.fills_limitados == result.fills_rejeitados_por_liquidez == 0

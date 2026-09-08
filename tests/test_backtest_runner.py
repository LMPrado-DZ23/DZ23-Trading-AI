import json
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pandas as pd
import pytest

from tradingagents.backtest import (
    BacktestConfig,
    BacktestRunner,
    GraphDecisionProvider,
    PriceBar,
    PriceSeries,
    load_yfinance_price_series,
)
from tradingagents.portfolio import PortfolioConstraints


def _series(ticker="AAPL", *, opens=None, closes=None):
    opens = opens or [100, 101, 102, 103, 104, 105, 106, 107]
    closes = closes or [100, 102, 101, 104, 103, 106, 105, 108]
    return PriceSeries(
        ticker,
        tuple(
            PriceBar(date(2024, 1, index + 1), Decimal(str(raw_open)), Decimal(str(close)))
            for index, (raw_open, close) in enumerate(zip(opens, closes, strict=True))
        ),
    )


def _config(**overrides):
    values = {
        "initial_cash": Decimal("10000"),
        "commission_bps": Decimal("0"),
        "slippage_bps": Decimal("0"),
        "volatility_lookback": 3,
        "minimum_volatility_observations": 2,
        "annualization_sessions": 252,
        "minimum_annualized_volatility": Decimal("0.01"),
    }
    values.update(overrides)
    return BacktestConfig(**values)


def _constraints(**overrides):
    values = {
        "target_annualized_volatility": "0.10",
        "max_position_weight": "0.20",
        "max_gross_exposure": "1",
        "max_net_exposure": "1",
        "max_drawdown": "0.50",
        "max_market_data_age_days": "0",
        "quantity_step": "0.000001",
        "min_trade_notional": "0",
    }
    values.update(overrides)
    return PortfolioConstraints.from_mapping(values)


def test_decision_at_close_fills_only_at_next_raw_open():
    requests = []

    def strategy(request):
        requests.append(request)
        return "Buy" if len(requests) == 1 else "Hold"

    result = BacktestRunner(
        strategy, config=_config(), portfolio_constraints=_constraints()
    ).run(_series(), _series("SPY"), end_date="2024-01-03")

    assert requests[0].context.trade_date.isoformat() == "2024-01-03"
    assert requests[0].context.decision_at.hour == 16
    assert requests[0].market_risk.price == Decimal("101")
    assert result.trades[0].decision_date == "2024-01-03"
    assert result.trades[0].fill_date == "2024-01-04"
    assert result.trades[0].raw_next_open == Decimal("103")
    assert result.trades[0].fill_price == Decimal("103")


def test_future_price_changes_cannot_change_earlier_decision_inputs():
    captured = []

    def collect(request):
        captured.append((request.market_risk.price, request.market_risk.annualized_volatility))
        return "Hold"

    base = _series()
    altered = _series(
        opens=[100, 101, 102, 999, 998, 997, 996, 995],
        closes=[100, 102, 101, 900, 800, 700, 600, 500],
    )
    benchmark = _series("SPY")
    runner = BacktestRunner(collect, config=_config(), portfolio_constraints=_constraints())
    runner.run(base, benchmark, end_date="2024-01-03")
    first = captured.pop()
    runner.run(altered, benchmark, end_date="2024-01-03")

    assert captured.pop() == first


def test_slippage_and_commission_are_applied_in_adverse_direction():
    signals = iter(["Buy", "Sell"])
    result = BacktestRunner(
        lambda request: next(signals),
        config=_config(commission_bps="10", slippage_bps="20"),
        portfolio_constraints=_constraints(),
    ).run(_series(), _series("SPY"), end_date="2024-01-04")

    buy, sell = result.trades
    assert buy.fill_price == Decimal("103") * Decimal("1.002")
    assert sell.fill_price == Decimal("104") * Decimal("0.998")
    assert buy.commission > 0
    assert sell.commission > 0


def test_gap_up_buy_is_capped_by_available_cash_without_leverage():
    prices = _series(
        opens=[10, 10, 10, 1000, 1000, 1000, 1000, 1000],
        closes=[10, 11, 10, 1000, 1000, 1000, 1000, 1000],
    )
    result = BacktestRunner(
        lambda request: "Buy",
        config=_config(commission_bps="10"),
        portfolio_constraints=_constraints(max_position_weight="1"),
    ).run(prices, _series("SPY"), end_date="2024-01-03")

    assert result.trades[0].filled_quantity < result.trades[0].requested_quantity
    assert result.equity_curve[-1].cash >= 0


def test_review_produces_no_trade():
    result = BacktestRunner(
        lambda request: "REVIEW",
        config=_config(),
        portfolio_constraints=_constraints(),
    ).run(_series(), _series("SPY"))

    assert result.trades == ()
    assert result.metrics["trade_count"] == 0
    assert result.metrics["hit_rate"] is None


def test_invalid_strategy_signal_fails_closed():
    runner = BacktestRunner(
        lambda request: "STRONG_BUY",
        config=_config(),
        portfolio_constraints=_constraints(),
    )
    with pytest.raises(ValueError, match="invalid signal"):
        runner.run(_series(), _series("SPY"))


def test_metrics_equity_curve_and_reports_are_emitted(tmp_path):
    signals = iter(["Buy", "Hold", "Sell", "Hold", "Hold"])
    result = BacktestRunner(
        lambda request: next(signals),
        config=_config(),
        portfolio_constraints=_constraints(max_position_weight="1"),
    ).run(_series(), _series("SPY"))

    assert len(result.equity_curve) == 6
    assert result.metrics["trade_count"] == 2
    assert result.metrics["closed_trade_count"] == 1
    assert result.metrics["hit_rate"] in {0.0, 1.0}
    assert result.metrics["max_drawdown"] >= 0
    assert result.metrics["turnover"] > 0
    assert result.metrics["accumulated_alpha"] == pytest.approx(
        result.metrics["total_return"] - result.metrics["benchmark_return"]
    )
    json_path, markdown_path = result.write(tmp_path)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["schema"] == "tradingagents/backtest/1"
    assert payload["trades"][0]["raw_next_open"] == "103"
    assert "## Metrics" in markdown_path.read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.tmp"))


def test_benchmark_must_cover_every_asset_session():
    short_benchmark = PriceSeries("SPY", _series("SPY").bars[:-1])
    runner = BacktestRunner(
        lambda request: "Hold",
        config=_config(),
        portfolio_constraints=_constraints(),
    )
    with pytest.raises(ValueError, match="benchmark is missing"):
        runner.run(_series(), short_benchmark)


def test_price_dataframe_uses_raw_open_and_rejects_missing_columns():
    index = pd.to_datetime(["2024-01-01", "2024-01-02"])
    frame = pd.DataFrame(
        {"Open": [10, 20], "Close": [11, 21], "Adj Close": [1, 2]}, index=index
    )
    series = PriceSeries.from_dataframe("AAPL", frame)

    assert series.bars[1].raw_open == Decimal("20")
    with pytest.raises(ValueError, match="raw Open and Close"):
        PriceSeries.from_dataframe("AAPL", frame[["Adj Close"]])


def test_yfinance_loader_explicitly_disables_adjustment():
    client = MagicMock()
    client.history.return_value = pd.DataFrame(
        {
            "Open": [10, 11],
            "Close": [10, 12],
            "Adj Close": [1, 2],
            "Dividends": [0, 0.5],
            "Stock Splits": [0, 2],
        },
        index=pd.to_datetime(["2024-01-01", "2024-01-02"]),
    )

    series = load_yfinance_price_series(
        "AAPL", "2024-01-01", "2024-01-03", history_client=client
    )

    client.history.assert_called_once_with(
        start="2024-01-01", end="2024-01-03", auto_adjust=False, actions=True
    )
    assert series.bars[0].raw_open == Decimal("10")
    assert series.bars[1].cash_dividend == Decimal("0.5")
    assert series.bars[1].split_ratio == Decimal("2")


def test_corporate_actions_adjust_holdings_orders_and_cash():
    bars = list(_series().bars)
    bars[3] = PriceBar(
        bars[3].session_date,
        raw_open="51.5",
        raw_close="52",
        split_ratio="2",
    )
    bars[4] = PriceBar(
        bars[4].session_date,
        raw_open="52",
        raw_close="53",
        cash_dividend="1",
    )
    prices = PriceSeries("AAPL", tuple(bars))
    signals = iter(["Buy", "Hold"])
    result = BacktestRunner(
        lambda request: next(signals),
        config=_config(),
        portfolio_constraints=_constraints(max_position_weight="1"),
    ).run(prices, _series("SPY"), end_date="2024-01-04")

    trade = result.trades[0]
    assert trade.split_ratio == Decimal("2")
    assert trade.filled_quantity == trade.requested_quantity * 2
    day4, day5 = result.equity_curve[-2:]
    assert day5.cash == day4.cash + day4.quantity * Decimal("1")


def test_benchmark_return_accounts_for_splits_and_dividends():
    benchmark_bars = list(_series("SPY").bars)
    benchmark_bars[3] = PriceBar(
        benchmark_bars[3].session_date,
        raw_open="51.5",
        raw_close="52",
        split_ratio="2",
        cash_dividend="1",
    )
    benchmark = PriceSeries("SPY", tuple(benchmark_bars))
    result = BacktestRunner(
        lambda request: "Hold",
        config=_config(),
        portfolio_constraints=_constraints(),
    ).run(_series(), benchmark, end_date="2024-01-03")

    expected = Decimal("10000") / Decimal("101") * 2 * Decimal("52")
    expected += Decimal("10000") / Decimal("101") * 2 * Decimal("1")
    assert result.equity_curve[-1].benchmark_equity == expected


def test_graph_provider_requests_only_rating_in_backtest_mode():
    graph = MagicMock()
    graph.propagate.return_value = ({"portfolio_plan": "ignored"}, "Overweight")
    provider = GraphDecisionProvider(graph)
    runner = BacktestRunner(
        lambda request: "Hold",
        config=_config(),
        portfolio_constraints=_constraints(),
    )
    context = runner.run(_series(), _series("SPY"), end_date="2024-01-03")
    del context
    request_holder = []
    BacktestRunner(
        lambda request: request_holder.append(request) or "Hold",
        config=_config(),
        portfolio_constraints=_constraints(),
    ).run(_series(), _series("SPY"), end_date="2024-01-03")

    assert provider(request_holder[0]) == "Overweight"
    kwargs = graph.propagate.call_args.kwargs
    assert kwargs["run_mode"] == "backtest"
    assert "portfolio_snapshot" not in kwargs
    assert kwargs["statistical_context"] == request_holder[0].memory_context


@pytest.mark.parametrize(
    "kwargs",
    [
        {"initial_cash": "0"},
        {"commission_bps": "-1"},
        {"slippage_bps": "1001"},
        {"volatility_lookback": 1},
        {"volatility_lookback": 3, "minimum_volatility_observations": 4},
    ],
)
def test_invalid_backtest_config_fails_closed(kwargs):
    with pytest.raises(ValueError):
        _config(**kwargs)

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.portfolio import (
    MarketRiskSnapshot,
    PortfolioConstraints,
    PortfolioSnapshot,
    Position,
    SizingRejected,
    size_position,
)
from tradingagents.runtime import RunContext, build_runtime_config, freeze_config

DECISION_AT = datetime(2024, 1, 2, 16, 0, tzinfo=timezone(timedelta(hours=-5)))


def _context(ticker="AAPL"):
    return RunContext.create(
        ticker,
        "2024-01-02",
        "stock",
        build_runtime_config(),
        decision_at=DECISION_AT,
        run_mode="backtest",
    )


def _portfolio(*positions, equity="100000", peak="100000", cash=None, as_of=None):
    if cash is None:
        cash = Decimal(equity) - sum(
            (position.market_value for position in positions), Decimal("0")
        )
    return PortfolioSnapshot(
        as_of=as_of or DECISION_AT,
        cash=Decimal(cash),
        equity=Decimal(equity),
        peak_equity=Decimal(peak),
        positions=tuple(positions),
    )


def _market(ticker="AAPL", price="100", volatility="0.50", as_of=None):
    return MarketRiskSnapshot(
        ticker=ticker,
        as_of=as_of or DECISION_AT,
        price=Decimal(price),
        annualized_volatility=Decimal(volatility),
    )


def test_buy_uses_vol_target_and_position_cap():
    decision = size_position("Buy", _context(), _portfolio(), _market())

    assert decision.target_weight == Decimal("0.20")
    assert decision.target_quantity == Decimal("200.000000")
    assert decision.action == "BUY"


def test_overweight_has_lower_deterministic_conviction():
    constraints = PortfolioConstraints(max_position_weight=Decimal("0.50"))
    decision = size_position(
        "Overweight", _context(), _portfolio(), _market(), constraints
    )

    assert decision.target_weight == Decimal("0.130")
    assert decision.target_quantity == Decimal("130.000000")


def test_hold_preserves_current_position_unless_a_hard_cap_requires_reduction():
    current = Position("AAPL", Decimal("100"), Decimal("100"))
    hold = size_position("Hold", _context(), _portfolio(current), _market())
    capped = size_position(
        "Hold",
        _context(),
        _portfolio(Position("AAPL", Decimal("300"), Decimal("100")), cash="70000"),
        _market(),
    )

    assert hold.order_quantity == 0
    assert capped.target_quantity == Decimal("200.000000")
    assert capped.order_quantity == Decimal("-100.000000")
    assert "max_position_weight" in capped.constraints_applied


def test_underweight_never_increases_an_existing_or_empty_position():
    small = Position("AAPL", Decimal("20"), Decimal("100"))
    existing = size_position("Underweight", _context(), _portfolio(small), _market())
    empty = size_position("Underweight", _context(), _portfolio(), _market())

    assert existing.target_quantity == small.quantity
    assert existing.action == "HOLD"
    assert empty.target_quantity == 0


def test_sell_exits_and_review_never_trades():
    position = Position("AAPL", Decimal("100"), Decimal("100"))
    portfolio = _portfolio(position, cash="90000")
    sell = size_position("Sell", _context(), portfolio, _market())
    review = size_position("REVIEW", _context(), portfolio, _market())

    assert sell.action == "SELL"
    assert sell.target_quantity == 0
    assert review.action == "HOLD"
    assert review.target_quantity == position.quantity
    assert review.blocked_reason == "review_required"


def test_review_never_trades_even_when_caps_or_drawdown_would_reduce_position():
    position = Position("AAPL", Decimal("100"), Decimal("100"))
    portfolio = _portfolio(position, equity="80000", peak="100000", cash="70000")
    constraints = PortfolioConstraints(max_position_weight="0.05")

    decision = size_position("REVIEW", _context(), portfolio, _market(), constraints)

    assert decision.action == "HOLD"
    assert decision.order_quantity == 0
    assert decision.target_quantity == position.quantity
    assert decision.constraints_applied == ()


def test_drawdown_circuit_breaker_liquidates_instead_of_adding_risk():
    position = Position("AAPL", Decimal("100"), Decimal("100"))
    portfolio = _portfolio(position, equity="80000", peak="100000", cash="70000")
    decision = size_position("Buy", _context(), portfolio, _market())

    assert decision.action == "SELL"
    assert decision.target_quantity == 0
    assert decision.blocked_reason == "drawdown_circuit_breaker"
    assert "max_drawdown" in decision.constraints_applied


def test_gross_and_net_exposure_caps_account_for_other_positions():
    other = Position("MSFT", Decimal("850"), Decimal("100"))
    constraints = PortfolioConstraints(
        max_position_weight=Decimal("0.50"),
        max_gross_exposure=Decimal("0.90"),
        max_net_exposure=Decimal("0.90"),
    )
    decision = size_position(
        "Buy", _context(), _portfolio(other, cash="15000"), _market(), constraints
    )

    assert decision.target_weight == Decimal("0.05")
    assert decision.target_quantity == Decimal("50.000000")
    assert "max_gross_exposure" in decision.constraints_applied


def test_quantity_rounds_down_and_small_orders_do_not_cross_minimum():
    constraints = PortfolioConstraints(
        max_position_weight=Decimal("0.50"),
        quantity_step=Decimal("1"),
        min_trade_notional=Decimal("200"),
    )
    rounded = size_position(
        "Overweight", _context(), _portfolio(), _market(price="333"), constraints
    )
    tiny_current = Position("AAPL", rounded.target_quantity - Decimal("0.5"), Decimal("333"))
    no_trade = size_position(
        "Overweight",
        _context(),
        _portfolio(tiny_current),
        _market(price="333"),
        constraints,
    )

    assert rounded.target_quantity == Decimal("39")
    assert no_trade.order_quantity == 0
    assert "min_trade_notional" in no_trade.constraints_applied


def test_minimum_notional_never_suppresses_a_risk_reducing_sale():
    position = Position("AAPL", Decimal("0.5"), Decimal("100"))
    constraints = PortfolioConstraints(min_trade_notional=Decimal("1000"))

    decision = size_position(
        "Sell", _context(), _portfolio(position), _market(), constraints
    )

    assert decision.action == "SELL"
    assert decision.order_quantity == Decimal("-0.500000")


@pytest.mark.parametrize(
    ("portfolio", "market", "message"),
    [
        (
            _portfolio(as_of=DECISION_AT + timedelta(seconds=1)),
            _market(),
            "portfolio snapshot is newer",
        ),
        (
            _portfolio(),
            _market(as_of=DECISION_AT + timedelta(seconds=1)),
            "market-risk snapshot is newer",
        ),
        (
            _portfolio(),
            _market(as_of=DECISION_AT - timedelta(days=6)),
            "market-risk snapshot is stale",
        ),
        (_portfolio(), _market(ticker="MSFT"), "ticker does not match"),
        (
            _portfolio(as_of=DECISION_AT - timedelta(seconds=1)),
            _market(),
            "must share one as_of",
        ),
    ],
)
def test_point_in_time_inputs_fail_closed(portfolio, market, message):
    with pytest.raises(SizingRejected, match=message):
        size_position("Buy", _context(), portfolio, market)


@pytest.mark.parametrize("signal", ["", "Strong Buy", "buy", "UNKNOWN"])
def test_unknown_ratings_fail_closed(signal):
    with pytest.raises(SizingRejected, match="unsupported portfolio rating"):
        size_position(signal, _context(), _portfolio(), _market())


def test_output_is_deterministic_and_json_ready():
    first = size_position("Buy", _context(), _portfolio(), _market()).to_dict()
    second = size_position("Buy", _context(), _portfolio(), _market()).to_dict()

    assert first == second
    assert first["schema"] == "tradingagents/sizing/1"
    assert first["target_quantity"] == "200.000000"


def test_invalid_state_and_policy_values_are_rejected():
    with pytest.raises(ValueError, match="unique tickers"):
        _portfolio(Position("AAPL", 1, 1), Position("AAPL", 2, 1))
    with pytest.raises(ValueError, match="cash plus marked"):
        PortfolioSnapshot(
            as_of=DECISION_AT,
            cash="100",
            equity="200",
            peak_equity="200",
        )
    with pytest.raises(ValueError, match="finite decimal"):
        _market(volatility="NaN")
    with pytest.raises(ValueError, match="net <= gross"):
        PortfolioConstraints(max_net_exposure="0.9", max_gross_exposure="0.8")


def test_target_position_mark_must_match_market_snapshot():
    portfolio = _portfolio(Position("AAPL", Decimal("1"), Decimal("99")))
    with pytest.raises(SizingRejected, match="mark_price does not match"):
        size_position("Hold", _context(), portfolio, _market(price="100"))


def test_graph_attaches_deterministic_plan_before_finalization(tmp_path, monkeypatch):
    graph = object.__new__(TradingAgentsGraph)
    graph.config = freeze_config(build_runtime_config({
        "results_dir": str(tmp_path),
        "data_cache_dir": str(tmp_path / "cache"),
    }))
    graph.memory_log = MagicMock()
    graph.memory_log.get_past_context.return_value = ""
    graph.resolve_instrument_context = MagicMock(return_value="")
    graph.propagator = MagicMock()
    graph.propagator.create_initial_state.return_value = {}
    graph.propagator.get_graph_args.return_value = {}
    graph.callbacks = []
    graph.checkpoint_input = lambda state: state
    graph.graph = MagicMock()
    graph.graph.invoke.return_value = {"final_trade_decision": "Rating: Buy"}
    graph.debug = False
    graph.process_signal = lambda decision: "Buy"
    captured = {}
    monkeypatch.setattr(
        TradingAgentsGraph,
        "finalize_state",
        lambda self, state, context: captured.update(state),
    )
    context = _context()

    final_state, signal = TradingAgentsGraph._run_graph(
        graph,
        "AAPL",
        "2024-01-02",
        run_context=context,
        portfolio_snapshot=_portfolio(),
        market_risk=_market(),
        portfolio_constraints=PortfolioConstraints(),
    )

    assert signal == "Buy"
    assert final_state["portfolio_plan"]["action"] == "BUY"
    assert captured["portfolio_plan"] == final_state["portfolio_plan"]


def test_graph_requires_portfolio_and_market_snapshots_as_a_pair():
    graph = object.__new__(TradingAgentsGraph)
    graph.config = freeze_config(build_runtime_config())

    with pytest.raises(ValueError, match="must be supplied together"):
        TradingAgentsGraph.propagate(
            graph,
            "AAPL",
            "2024-01-02",
            market_risk=_market(),
        )


def test_graph_rejects_future_sizing_data_before_starting_agents():
    graph = object.__new__(TradingAgentsGraph)
    graph.config = freeze_config(build_runtime_config())
    graph._resolve_pending_entries = MagicMock()

    with pytest.raises(SizingRejected, match="market-risk snapshot is newer"):
        TradingAgentsGraph.propagate(
            graph,
            "AAPL",
            "2024-01-02",
            decision_at=DECISION_AT,
            portfolio_snapshot=_portfolio(),
            market_risk=_market(as_of=DECISION_AT + timedelta(seconds=1)),
        )

    graph._resolve_pending_entries.assert_not_called()

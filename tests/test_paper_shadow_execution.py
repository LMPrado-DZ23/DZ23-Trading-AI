import json
from datetime import datetime, timezone

import pytest

from tradingagents.execution import (
    AlpacaPaperBroker,
    ExecutionEngine,
    ExecutionJournal,
    ExecutionRejected,
    LocalPaperBroker,
    ShadowBroker,
    TradingExecutionOrchestrator,
    prepare_execution,
)
from tradingagents.execution.brokers import ALPACA_PAPER_ORDERS_URL
from tradingagents.execution.models import ExecutionIntent


def _state(action="BUY", quantity="2"):
    target_quantity = "0" if action == "HOLD" else "2"
    target_notional = "0" if action == "HOLD" else "200"
    target_weight = "0" if action == "HOLD" else "0.2"
    plan = {
        "schema": "tradingagents/sizing/1",
        "ticker": "AAPL",
        "rating": "Buy",
        "action": action,
        "current_quantity": "0",
        "target_quantity": target_quantity,
        "order_quantity": quantity,
        "decision_price": "100",
        "current_weight": "0",
        "target_weight": target_weight,
        "target_notional": target_notional,
        "drawdown": "0",
        "constraints_applied": [],
        "blocked_reason": None,
        "decision_at": "2026-09-06T16:00:00-04:00",
    }
    return {
        "company_of_interest": "AAPL",
        "trade_date": "2026-09-06",
        "portfolio_plan": plan,
        "transport_security": {
            "schema": "transport_security/1",
            "insecure": False,
            "insecure_targets": [],
            "policy": "backend_policy/rodada5",
            "run": {"ticker": "AAPL", "trade_date": "2026-09-06"},
        },
    }


def _telemetry(mode="paper"):
    return {
        "schema": "tradingagents/telemetry/1",
        "run_id": "a" * 32,
        "run_key": "c" * 64,
        "status": "succeeded",
        "run_context": {
            "ticker": "AAPL",
            "trade_date": "2026-09-06",
            "asset_type": "stock",
            "decision_at": "2026-09-06T16:00:00-04:00",
            "run_mode": mode,
            "market_timezone": "America/New_York",
            "workflow_schema_version": "tradingagents/workflow/3",
            "config_sha256": "b" * 64,
            "historical": True,
        },
    }


NOW = datetime(2026, 9, 6, 21, tzinfo=timezone.utc)


def test_prepare_execution_binds_evidence_and_rejects_live_mode():
    intent = prepare_execution(_state(), _telemetry(), mode="paper", now=NOW)
    assert intent.action == "BUY"
    assert intent.client_order_id.startswith("ta-")
    with pytest.raises(ExecutionRejected, match="real-capital"):
        prepare_execution(_state(), _telemetry("live"), mode="live", now=NOW)


def test_intent_cannot_be_constructed_outside_the_gate():
    with pytest.raises(ValueError, match="prepare_execution"):
        ExecutionIntent(
            mode="paper", ticker="AAPL", asset_type="stock", action="BUY",
            quantity=1, decision_price=100, decision_at="x",
            telemetry_run_id="a" * 32, telemetry_run_key="c" * 64,
            sizing_sha256="b" * 64,
            client_order_id="forged", _capability=object(),
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda state, telemetry: state.pop("transport_security"), "no transport"),
        (lambda state, telemetry: telemetry.update(status="failed"), "succeeded"),
        (lambda state, telemetry: telemetry["run_context"].update(ticker="MSFT"), "ticker"),
        (lambda state, telemetry: state["portfolio_plan"].update(order_quantity="NaN"), "finite"),
        (lambda state, telemetry: state["portfolio_plan"].update(action="SELL"), "disagree"),
    ],
)
def test_gate_fails_closed_on_inconsistent_evidence(mutation, message):
    state, telemetry = _state(), _telemetry()
    mutation(state, telemetry)
    with pytest.raises(ExecutionRejected, match=message):
        prepare_execution(state, telemetry, mode="paper", now=NOW)


def test_gate_rejects_stale_decision_and_blocked_trade():
    with pytest.raises(ExecutionRejected, match="window"):
        prepare_execution(
            _state(), _telemetry(), mode="paper",
            now=datetime(2026, 9, 9, tzinfo=timezone.utc),
        )
    state = _state()
    state["portfolio_plan"]["blocked_reason"] = "risk"
    with pytest.raises(ExecutionRejected, match="blocked"):
        prepare_execution(state, _telemetry(), mode="paper", now=NOW)


def test_shadow_and_local_paper_are_durable_and_idempotent(tmp_path):
    paper = prepare_execution(_state(), _telemetry(), mode="paper", now=NOW)
    engine = ExecutionEngine(LocalPaperBroker(), ExecutionJournal(tmp_path / "paper.sqlite"))
    first = engine.execute(paper)
    second = engine.execute(paper)
    assert first == second
    assert first.status == "simulated_filled"

    shadow = prepare_execution(_state(), _telemetry("shadow"), mode="shadow", now=NOW)
    receipt = ExecutionEngine(
        ShadowBroker(), ExecutionJournal(tmp_path / "shadow.sqlite")
    ).execute(shadow)
    assert receipt.status == "observed"


def test_retry_run_id_does_not_change_order_identity_and_changed_plan_conflicts(tmp_path):
    first_telemetry = _telemetry()
    second_telemetry = _telemetry()
    second_telemetry["run_id"] = "d" * 32
    first = prepare_execution(_state(), first_telemetry, mode="paper", now=NOW)
    replay = prepare_execution(_state(), second_telemetry, mode="paper", now=NOW)
    assert replay.client_order_id == first.client_order_id
    assert replay.request_sha256 == first.request_sha256

    engine = ExecutionEngine(LocalPaperBroker(), ExecutionJournal(tmp_path / "retry.sqlite"))
    engine.execute(first)
    changed_state = _state("BUY", "1")
    changed_state["portfolio_plan"].update(
        target_quantity="1", target_notional="100", target_weight="0.1"
    )
    changed = prepare_execution(changed_state, second_telemetry, mode="paper", now=NOW)
    assert changed.client_order_id == first.client_order_id
    with pytest.raises(RuntimeError, match="collision"):
        engine.execute(changed)


class _Response:
    def __init__(self, status_code, value):
        self.status_code = status_code
        self._value = value
        self.content = json.dumps(value).encode()
        self.text = self.content.decode()
        self.is_redirect = 300 <= status_code < 400

    def json(self):
        return self._value


class _Session:
    trust_env = True

    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return _Response(404, {})

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return _Response(200, {"id": "paper-order-1", "status": "accepted"})


def test_alpaca_adapter_uses_only_pinned_paper_origin_without_redirects_or_proxy():
    session = _Session()
    intent = prepare_execution(_state(), _telemetry(), mode="paper", now=NOW)
    receipt = AlpacaPaperBroker(
        api_key="paper-key", api_secret="paper-secret", _session=session
    ).submit(intent)

    assert receipt.broker == "alpaca-paper"
    assert session.trust_env is False
    assert [call[1] for call in session.calls] == [
        f"{ALPACA_PAPER_ORDERS_URL}:by_client_order_id",
        ALPACA_PAPER_ORDERS_URL,
    ]
    assert all(call[2]["allow_redirects"] is False for call in session.calls)
    assert session.calls[1][2]["json"]["client_order_id"] == intent.client_order_id


def test_alpaca_replay_uses_existing_order_and_hold_never_contacts_network():
    session = _Session()
    session.get = lambda url, **kwargs: _Response(
        200, {"id": "existing", "status": "new"}
    )
    intent = prepare_execution(_state(), _telemetry(), mode="paper", now=NOW)
    receipt = AlpacaPaperBroker(
        api_key="paper-key", api_secret="paper-secret", _session=session
    ).submit(intent)
    assert receipt.broker_order_id == "existing"
    assert not session.calls

    hold_state = _state("HOLD", "0")
    hold = prepare_execution(hold_state, _telemetry(), mode="paper", now=NOW)
    held = AlpacaPaperBroker(
        api_key="paper-key", api_secret="paper-secret", _session=_Session()
    ).submit(hold)
    assert held.status == "held"


def test_orchestrator_connects_graph_gate_and_shadow_engine(tmp_path):
    class Graph:
        last_telemetry = _telemetry("shadow")

        def propagate(self, ticker, trade_date, **kwargs):
            assert kwargs["run_mode"] == "shadow"
            assert kwargs["portfolio_snapshot"] == "portfolio"
            assert kwargs["market_risk"] == "market"
            return _state(), "Buy"

    outcome = TradingExecutionOrchestrator(
        Graph(),
        ExecutionEngine(ShadowBroker(), ExecutionJournal(tmp_path / "bridge.sqlite")),
    ).run(
        "AAPL", "2026-09-06", mode="shadow",
        portfolio_snapshot="portfolio", market_risk="market", now=NOW,
    )

    assert outcome.signal == "Buy"
    assert outcome.receipt.status == "observed"

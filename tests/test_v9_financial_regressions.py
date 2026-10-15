from __future__ import annotations

import dataclasses
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tradingagents.desk.execution import ExecutionCoordinator, ExecutionRefused, OrderState
from tradingagents.desk.ledger import Ledger, LedgerError
from tradingagents.desk.models import (
    Action,
    AgentStage,
    DeskMandate,
    MarketSnapshot as SnapshotDaMesa,
    Mode,
    TradeProposal,
    utcnow,
)
from tradingagents.desk.orchestrator import DeskOrchestrator
from tradingagents.desk.risk import RiskEngine
from tradingagents.desk.scheduler import DeskScheduler
from tradingagents.desk.traders import default_desk
from tradingagents.domain import (
    Environment,
    MarketSnapshot,
    OrderRequest,
    PortfolioSnapshot,
    Side,
    TradeDecision,
)
from tradingagents.execution import PaperAdapter
from tradingagents.risk import RiskEngine as RiskDeMercado

D = Decimal
INSTRUMENT = "BTC/USDT"


class AcknowledgingBroker:
    """Only acknowledges synthetic orders; never executes or contacts a broker."""

    def submit(self, coid, instrument, side, quantity, final_state=None):
        return {
            "id": f"exchange-{coid}", "external_client_id": coid,
            "client_order_id": coid, "symbol": instrument, "side": side,
            "status": "open", "amount": quantity, "filled": D("0"),
            "remaining": quantity, "average": None, "trades": [],
            "fee": {"cost": D("0"), "currency": "USDT"},
        }


@pytest.fixture
def desk():
    mandate = DeskMandate(
        mandate_id="v9-financial", owner="synthetic-owner", account="fake-account",
        mode=Mode.PAPER, instruments=(INSTRUMENT,), capital=D("2000"),
        slippage_buffer_pct=D("0"),
    )
    ledger = Ledger(opening_cash=mandate.capital)
    risk = RiskEngine(mandate, carteira=ledger)
    return ExecutionCoordinator(
        mandate, ledger, risk, worker_id="fake-worker", broker=AcknowledgingBroker(),
    )


def _buy_order(desk, *, notional="200", price="100", agent="trader-A"):
    proposal = TradeProposal(
        agent_id=agent, strategy="fixture", instrument=INSTRUMENT,
        action=Action.BUY, reason="offline V9 regression",
        desired_exposure_pct=D(notional) / D("2000"), confidence=D("0.8"),
        valid_until=utcnow() + timedelta(minutes=5),
    )
    reservation = desk.risk.reserve(proposal, D(price), agent_id=agent)
    intent = desk.risk.intent(
        reservation, Action.BUY, Mode.PAPER, "fake-worker",
        idempotency_key=f"buy-{len(desk.orders)}",
    )
    return desk.submit(intent)


def _sell_order(desk, *, agent="trader-A"):
    result = desk.close_position(
        instrument=INSTRUMENT, quantity=D("2"), price=D("150"),
        cost_basis=D("300"), agent_id=agent,
        client_order_id=f"sell-{len(desk.orders)}",
    )
    return desk.orders[result["order_id"]]


def _fill(desk, rec, fill_id, *, price, quantity="1", fee="0"):
    return desk.aplicar_fill(
        rec, side=rec.lado, quantidade=D(quantity), preco=D(price),
        taxa=D(fee), fee_currency="USDT", fill_id=fill_id,
    )


def _two_price_entry(desk):
    rec = _buy_order(desk, notional="300", price="150")
    assert rec.quantidade == D("2")
    _fill(desk, rec, "buy-100", price="100")
    _fill(desk, rec, "buy-200", price="200")
    assert desk.ledger.position_quantity(INSTRUMENT) == D("2")
    assert desk.ledger.invested_cost() == D("300")
    assert desk.ledger.cash() == D("1700")
    assert desk.risk.reserved_total == desk.ledger.reserved_cash() == D("0")
    assert desk.ledger.is_balanced()


def _position_state(ledger):
    return {
        "quantity": ledger.position_quantity(INSTRUMENT),
        "posted_cost": ledger.balance(f"POSITION:{INSTRUMENT}"),
        "lot_quantity": sum((D(lot["quantidade"])
                             for lot in ledger.position_lots(INSTRUMENT)), D("0")),
        "lot_cost": sum((D(lot["custo_restante"])
                         for lot in ledger.position_lots(INSTRUMENT)), D("0")),
        "pnl": sum(ledger.attribution().values(), D("0")),
        "cash": ledger.cash(),
    }


def test_fin01_partial_sale_uses_the_same_fifo_cost_as_live_lots(desk):
    """Catch average-cost posting paired with FIFO lot reconstruction."""
    _two_price_entry(desk)
    sale = _sell_order(desk)
    _fill(desk, sale, "sell-first", price="150")

    # FIFO: sell the 100 lot for 150, retain the 200 lot, realize +50.
    assert _position_state(desk.ledger) == {
        "quantity": D("1"), "posted_cost": D("200"),
        "lot_quantity": D("1"), "lot_cost": D("200"),
        "pnl": D("50"), "cash": D("1850"),
    }


def test_fin01_two_sales_leave_no_cost_or_phantom_pnl(desk):
    """Catch a negative POSITION balance hidden by open_positions_detail."""
    _two_price_entry(desk)
    sale = _sell_order(desk)
    _fill(desk, sale, "sell-first", price="150")
    _fill(desk, sale, "sell-second", price="150")

    assert sale.state is OrderState.FILLED
    assert sale.filled_qty == D("2")
    assert desk.ledger.is_balanced()  # Double entry alone cannot prove correctness.
    # 300 paid, 300 received: zero remaining cost and zero total P&L.
    assert _position_state(desk.ledger) == {
        "quantity": D("0"), "posted_cost": D("0"),
        "lot_quantity": D("0"), "lot_cost": D("0"),
        "pnl": D("0"), "cash": D("2000"),
    }


def test_fin01_guardian_exit_consumes_the_original_owners_lots(desk):
    """Catch guardian sales that close aggregate inventory but leave owner lots."""
    # Seed two owners using real ledger transactions, as in test_crash_restart.
    for agent, price in (("trader-A", "100"), ("trader-B", "200")):
        desk.ledger.fill_buy(
            INSTRUMENT, D("1"), D(price), D("0"), reserved=D("0"),
            agent_id=agent, ref=f"seed-{agent}",
        )
    assert desk.ledger.position_quantity(INSTRUMENT) == D("2")
    sale = _sell_order(desk, agent="position-guardian")
    _fill(desk, sale, "guardian-sale", price="150", quantity="2")

    assert sale.state is OrderState.FILLED
    assert desk.ledger.is_balanced()
    observed = _position_state(desk.ledger)
    for agent in ("trader-A", "trader-B"):
        observed[f"{agent}_quantity"] = desk.ledger.lot_quantity(INSTRUMENT, agent)
        observed[f"{agent}_cost"] = desk.ledger.lot_cost(INSTRUMENT, agent)
    assert observed == {
        "quantity": D("0"), "posted_cost": D("0"),
        "lot_quantity": D("0"), "lot_cost": D("0"),
        "pnl": D("0"), "cash": D("2000"),
        "trader-A_quantity": D("0"), "trader-A_cost": D("0"),
        "trader-B_quantity": D("0"), "trader-B_cost": D("0"),
    }


@pytest.mark.parametrize(
    "notional,remaining,cash,released,state",
    [
        ("220", "119", "1780", "0", OrderState.PARTIALLY_FILLED),
        ("110", "0", "1899", "9", OrderState.FILLED),
    ], ids=["partial", "terminal"],
)
def test_fin02_failed_ledger_post_can_be_replayed_exactly_once(
    desk, monkeypatch, notional, remaining, cash, released, state,
):
    """Catch premature seen_fills and non-atomic risk consumption on failed post."""
    rec = _buy_order(desk, notional=notional, price="110")
    version = desk.oms_version
    real_fill_buy = desk.ledger.fill_buy
    fail_next = True

    def fail_once(*args, **kwargs):
        nonlocal fail_next
        if fail_next:
            fail_next = False
            raise LedgerError("V9 one-shot failure before ledger posting")
        return real_fill_buy(*args, **kwargs)

    monkeypatch.setattr(desk.ledger, "fill_buy", fail_once)
    with pytest.raises(LedgerError, match="V9 one-shot failure"):
        _fill(desk, rec, "retry-fill", price="100", fee="1")

    _fill(desk, rec, "retry-fill", price="100", fee="1")
    after_retry = deepcopy(desk.snapshot_estado())
    _fill(desk, rec, "retry-fill", price="100", fee="1")
    assert desk.snapshot_estado() == after_retry, "duplicate replay changed durable state"

    reservation = desk.risk.reserva(rec.reserva_id)
    conclusion = desk.risk.incidentes_doc()["conclusoes"].get(rec.reserva_id, {})
    consumed = (reservation.consumed_amount if reservation else
                D(conclusion.get("consumed_amount", "0")))
    risk_quantity = (reservation.filled_quantity if reservation else
                     D(conclusion.get("filled_quantity", "0")))
    release_amount = sum(
        (e.amount for tx in desk.ledger.transactions if tx.kind == "release"
         for e in tx.entries if e.account == "CASH:USDT"), D("0"),
    )
    observed = {
        "quantity": desk.ledger.position_quantity(INSTRUMENT),
        "cost": desk.ledger.invested_cost(), "cash": desk.ledger.cash(),
        "fees": desk.ledger.balance("FEES:USDT"),
        "ledger_reserved": desk.ledger.reserved_cash(rec.reserva_id),
        "risk_reserved": desk.risk.reserved_total,
        "risk_consumed": consumed, "risk_filled": risk_quantity,
        "released": release_amount,
        "buy_posts": sum(tx.kind == "fill_buy" for tx in desk.ledger.transactions),
        "order_filled": rec.filled_qty, "order_average": rec.avg_price,
        "order_fees": rec.fees, "order_state": rec.state,
        "seen": rec.seen_fills, "oms_updates": desk.oms_version - version,
        "incidents": len(desk.risk.incidentes_doc()["incidentes"]),
    }
    # One unit costs 100 + 1 fee. Terminal budget 110 returns exactly 9;
    # partial budget 220 retains 119 for the still-open second unit.
    assert observed == {
        "quantity": D("1"), "cost": D("100"), "cash": D(cash),
        "fees": D("1"), "ledger_reserved": D(remaining),
        "risk_reserved": D(remaining), "risk_consumed": D("101"),
        "risk_filled": D("1"), "released": D(released), "buy_posts": 1,
        "order_filled": D("1"), "order_average": D("100"),
        "order_fees": D("1"), "order_state": state,
        "seen": {"retry-fill"}, "oms_updates": 1, "incidents": 0,
    }
    assert desk.ledger.is_balanced()


def _remote_fill(quantity):
    return {
        "fill_id": "remote-trade", "quantity": D(quantity), "price": D("100"),
        "fee": D("0"), "fee_currency": "USDT", "side": "buy",
    }


def _reconcile(desk, rec, *, status, cumulative, remote_quantity, fills):
    # Strict identity lookup: a query for a different order cannot return these trades.
    remote = {
        "id": rec.external_id, "client_order_id": rec.external_client_id,
        "symbol": INSTRUMENT, "side": "buy", "status": status,
        "amount": D("2"), "filled": D(cumulative) if cumulative is not None else None,
        "remaining": D("2") - D(cumulative) if cumulative is not None else None,
        "average": D("100"),
        "fee": {"cost": D("0"), "currency": "USDT"},
    }
    orders = {rec.external_id: {"resultado": "FOUND", "ordem": remote}}
    trades = {(rec.external_client_id, rec.external_id): fills}

    def query_fills(client_id, external_id):
        response = trades[(client_id, external_id)]
        if isinstance(response, Exception):
            raise response
        return response

    return desk.reconciliar(
        orders.__getitem__, consultar_fills=query_fills,
        consultar_posicoes=lambda: {INSTRUMENT: D(remote_quantity)},
        consultar_ordens_abertas=lambda: [],
        consultar_caixa=lambda: desk.ledger.cash(),
    )


@pytest.mark.parametrize("status", ["canceled", "cancelled", "expired"])
def test_fin03_terminal_partial_imports_fills_before_releasing_remainder(desk, status):
    """Catch terminal handling that refunds the whole reservation and loses a fill."""
    rec = _buy_order(desk)
    assert rec.quantidade == D("2")
    assert desk.ledger.reserved_cash(rec.reserva_id) == D("200")
    reports = [_reconcile(
        desk, rec, status=status, cumulative="1", remote_quantity="1",
        fills=[_remote_fill("1")],
    ) for _ in range(2)]
    conclusion = desk.risk.incidentes_doc()["conclusoes"].get(rec.reserva_id, {})
    observed = {
        "quantity": desk.ledger.position_quantity(INSTRUMENT),
        "cost": desk.ledger.invested_cost(), "cash": desk.ledger.cash(),
        "ledger_reserved": desk.ledger.reserved_cash(rec.reserva_id),
        "risk_reserved": desk.risk.reserved_total,
        "consumed": D(conclusion.get("consumed_amount", "0")),
        "released": D(conclusion.get("sobra_liberada", "0")),
        "filled": rec.filled_qty, "seen": rec.seen_fills, "open": rec.open,
        "posting_order": [tx.kind for tx in desk.ledger.transactions],
        "safe": [report["seguro"] for report in reports],
    }
    assert observed == {
        "quantity": D("1"), "cost": D("100"), "cash": D("1900"),
        "ledger_reserved": D("0"), "risk_reserved": D("0"),
        "consumed": D("100"), "released": D("100"),
        "filled": D("1"), "seen": {"remote-trade"}, "open": False,
        "posting_order": ["opening", "reserve", "fill_buy", "release"],
        "safe": [True, True],
    }
    assert desk.ledger.is_balanced()


@pytest.mark.parametrize("status", ["canceled", "expired"])
def test_fin03_unverified_terminal_partial_keeps_capital_reserved(desk, status):
    """Catch terminal release before a failing trades query has been resolved."""
    rec = _buy_order(desk)
    reports = [_reconcile(
        desk, rec, status=status, cumulative="1", remote_quantity="1",
        fills=RuntimeError("synthetic trades query unavailable"),
    ) for _ in range(2)]
    assert all(report["seguro"] is False for report in reports)
    assert desk.reconciliacao_pendente is True
    assert {
        "ledger_reserved": desk.ledger.reserved_cash(rec.reserva_id),
        "risk_reserved": desk.risk.reserved_total,
        "cash": desk.ledger.cash(),
        "quantity": desk.ledger.position_quantity(INSTRUMENT),
    } == {
        "ledger_reserved": D("200"), "risk_reserved": D("200"),
        "cash": D("1800"), "quantity": D("0"),
    }


def test_fin03_cumulative_trade_mismatch_cannot_turn_safe_on_repeat(desk):
    """Catch unresolved FILLS_DIVERGEM disappearing when the local order fills."""
    rec = _buy_order(desk)
    reports = []
    states = []
    for _ in range(3):
        reports.append(_reconcile(
            desk, rec, status="closed", cumulative="1.5", remote_quantity="2",
            fills=[_remote_fill("2")],
        ))
        states.append(rec.state.value)

    assert reports[0]["fills_status"] == "MISMATCH"
    assert any("FILLS_DIVERGEM" in d for d in reports[0]["divergencias"])
    assert rec.filled_qty == desk.ledger.position_quantity(INSTRUMENT) == D("2")
    assert desk.ledger.invested_cost() == D("200")
    assert desk.ledger.cash() == D("1800")
    assert sum(tx.kind == "fill_buy" for tx in desk.ledger.transactions) == 1
    assert desk.ledger.is_balanced()
    observed = {
        "safe": [report["seguro"] for report in reports],
        "fills_status": [report["fills_status"] for report in reports],
        "gate_closed": desk.reconciliacao_pendente,
        "report_authorizes_execution": desk.relatorio_valido()[0],
    }
    assert observed == {
        "safe": [False, False, False],
        "fills_status": ["MISMATCH", "MISMATCH", "MISMATCH"],
        "gate_closed": True, "report_authorizes_execution": False,
    }, f"unresolved remote cumulative 1.5 vs trades 2; local states={states}; {observed}"


def _restart(desk):
    snapshot = json.loads(json.dumps(desk.snapshot_estado()))
    ledger = Ledger()
    risk = RiskEngine(desk.mandate, carteira=ledger)
    restored = ExecutionCoordinator(
        desk.mandate, ledger, risk, worker_id="fake-worker", broker=AcknowledgingBroker(),
    )
    restored.restaurar_estado(snapshot)
    return restored


def test_fin01_guardian_fifo_is_chronological_across_owners_and_restart(desk):
    for agent, price in (("trader-A", "100"), ("trader-B", "200"), ("trader-A", "300")):
        desk.ledger.fill_buy(INSTRUMENT, D("1"), D(price), D("0"),
                             reserved=D("0"), agent_id=agent)
    sale = _sell_order(desk, agent="position-guardian")
    _fill(desk, sale, "guardian-partial", price="150", quantity="1.5")
    # Oldest A: +50; half of B: -25. Newest A is untouched.
    for ledger in (desk.ledger, _restart(desk).ledger):
        assert ledger.lot_quantity(INSTRUMENT, "trader-A") == D("1")
        assert ledger.lot_cost(INSTRUMENT, "trader-A") == D("300")
        assert ledger.lot_quantity(INSTRUMENT, "trader-B") == D("0.5")
        assert ledger.lot_cost(INSTRUMENT, "trader-B") == D("100")
        assert ledger.attribution() == {"trader-A": D("50"), "trader-B": D("-25")}
        assert ledger.invested_cost() == D("400")
        assert ledger.cash() == D("1625")
        assert ledger.is_balanced()


def test_fin01_an_unprivileged_agent_cannot_consume_another_owners_lots(desk):
    _two_price_entry(desk)
    before = deepcopy(desk.snapshot_estado())
    with pytest.raises(ExecutionRefused, match="outro agente"):
        _sell_order(desk, agent="arbitrary-trader")
    assert desk.snapshot_estado() == before


@pytest.mark.parametrize("stage", ["buy_post", "release", "order_transition"])
def test_fin02_partial_progress_is_rolled_back_before_retry(desk, monkeypatch, stage):
    rec = _buy_order(desk, notional="110", price="110")
    before = deepcopy(desk.snapshot_estado())
    target, method = {
        "buy_post": (desk.ledger, "fill_buy"),
        "release": (desk.ledger, "release"),
        "order_transition": (rec, "carimbar_transicao"),
    }[stage]
    original = getattr(target, method)

    def fail_after_progress(*args, **kwargs):
        original(*args, **kwargs)
        raise LedgerError("failure after economic progress")

    with monkeypatch.context() as patch:
        patch.setattr(target, method, fail_after_progress)
        with pytest.raises(LedgerError, match="after economic progress"):
            _fill(desk, rec, "retry-progress", price="100", fee="1")
    assert desk.snapshot_estado() == before
    restored = _restart(desk)
    rec = restored.orders[rec.order_id]
    assert _fill(restored, rec, "retry-progress", price="100", fee="1") is True
    after = deepcopy(restored.snapshot_estado())
    assert _fill(restored, rec, "retry-progress", price="100", fee="1") is False
    assert restored.snapshot_estado() == after
    assert restored.ledger.cash() == D("1899")
    assert restored.ledger.invested_cost() == D("100")
    assert restored.ledger.balance("FEES:USDT") == D("1")
    assert restored.risk.reserved_total == restored.ledger.reserved_cash() == D("0")
    assert rec.filled_qty == D("1")
    assert len(restored.ledger.transactions) == 4


def test_fin02_fee_and_deficit_incidents_rollback_without_losing_prior_occurrences(desk, monkeypatch):
    rec = _buy_order(desk, notional="100", price="50")
    kwargs = {"side": "buy", "quantidade": D("1"), "preco": D("110"), "taxa": D("0"),
              "fees": [{"cost": D("0.1"), "currency": "BNB"}]}
    desk.aplicar_fill(rec, fill_id="first-deficit", **kwargs)
    before = deepcopy(desk.snapshot_estado())
    original = desk.ledger.fill_buy

    def fail_after_post(*args, **kw):
        original(*args, **kw)
        raise LedgerError("after incidents and post")

    with monkeypatch.context() as patch:
        patch.setattr(desk.ledger, "fill_buy", fail_after_post)
        with pytest.raises(LedgerError, match="after incidents"):
            desk.aplicar_fill(rec, fill_id="retry-deficit", **kwargs)
    assert desk.snapshot_estado() == before
    assert desk.reconciliacao_pendente is True
    desk.aplicar_fill(rec, fill_id="retry-deficit", **kwargs)
    incidents = {i["type"]: i for i in desk.risk.incidentes()}
    assert D(incidents["RESERVATION_DEFICIT"]["amount"]) == D("120")
    assert D(incidents["FEE_VALUATION_UNKNOWN"]["amount"]) == D("0.2")
    assert all(len(i["occurrences"]) == 2 for i in incidents.values())
    assert desk.ledger.cash() == D("1780")
    assert desk.ledger.invested_cost() == D("220")
    assert desk.ledger.balance("FEES:BNB") == D("0.2")
    assert desk.ledger.is_balanced()


def test_fin02_sale_failure_restores_lots_pnl_and_daily_loss(desk, monkeypatch):
    _two_price_entry(desk)
    sale = _sell_order(desk)
    before = deepcopy(desk.snapshot_estado())
    original = sale.carimbar_transicao

    def fail_after_transition(*args):
        original(*args)
        raise LedgerError("after sale")

    with monkeypatch.context() as patch:
        patch.setattr(sale, "carimbar_transicao", fail_after_transition)
        with pytest.raises(LedgerError, match="after sale"):
            _fill(desk, sale, "sale-retry", price="90")
    assert desk.snapshot_estado() == before
    _fill(desk, sale, "sale-retry", price="90")
    assert desk.ledger.invested_cost() == D("200")
    assert desk.ledger.attribution() == {"trader-A": D("-10")}
    assert desk.perda_do_dia == D("10")


@pytest.mark.parametrize("later", ["mismatch", "unavailable", "not_found", "missing_total"])
def test_fin03_unresolved_terminal_evidence_survives_restart_until_actual_agreement(desk, later):
    rec = _buy_order(desk, notional="220", price="110")
    report = _reconcile(desk, rec, status="closed", cumulative="1.5",
                        remote_quantity="2", fills=[_remote_fill("2")])
    assert report["seguro"] is False
    desk = _restart(desk)
    rec = desk.orders[rec.order_id]
    if later == "not_found":
        report = desk.reconciliar(
            lambda key: {"resultado": "CONFIRMED_NOT_FOUND"},
            consultar_fills=lambda *args: [],
            consultar_posicoes=lambda: {INSTRUMENT: D("2")},
            consultar_ordens_abertas=lambda: [],
            consultar_caixa=lambda: desk.ledger.cash(),
        )
    else:
        report = _reconcile(
            desk, rec, status="closed", cumulative=None if later == "missing_total" else "1.5",
            remote_quantity="2", fills=(RuntimeError("unavailable") if later == "unavailable"
                                       else [_remote_fill("2")]),
        )
    assert report["seguro"] is False
    assert desk.reconciliacao_pendente is True
    assert desk.risk.reserved_total == desk.ledger.reserved_cash() == D("20")
    assert desk.ledger.cash() == D("1780")
    desk = _restart(desk)
    rec = desk.orders[rec.order_id]
    report = _reconcile(desk, rec, status="closed", cumulative="2",
                        remote_quantity="2", fills=[_remote_fill("2")])
    assert report["seguro"] is True
    assert desk.risk.reserved_total == desk.ledger.reserved_cash() == D("0")
    assert desk.ledger.cash() == D("1800")
    assert sum(tx.kind == "fill_buy" for tx in desk.ledger.transactions) == 1
    desk = _restart(desk)

    def no_historical_query(*args):
        raise AssertionError("resolved historical order must not be queried")

    report = desk.reconciliar(no_historical_query, consultar_fills=no_historical_query,
                              consultar_posicoes=lambda: {INSTRUMENT: D("2")},
                              consultar_ordens_abertas=lambda: [],
                              consultar_caixa=lambda: desk.ledger.cash())
    assert report["seguro"] is True
    assert report["ordens_conferidas"] == 0


def test_fin03_terminal_release_failure_is_replayable(desk, monkeypatch):
    rec = _buy_order(desk)
    original = desk.ledger.release

    def fail_after_release(*args, **kwargs):
        original(*args, **kwargs)
        raise LedgerError("after terminal release")

    with monkeypatch.context() as patch:
        patch.setattr(desk.ledger, "release", fail_after_release)
        with pytest.raises(LedgerError, match="terminal release"):
            _reconcile(desk, rec, status="expired", cumulative="1",
                       remote_quantity="1", fills=[_remote_fill("1")])
    desk = _restart(desk)
    rec = desk.orders[rec.order_id]
    report = _reconcile(desk, rec, status="expired", cumulative="1",
                        remote_quantity="1", fills=[_remote_fill("1")])
    assert report["seguro"] is True
    assert desk.ledger.cash() == D("1900")
    assert desk.ledger.invested_cost() == D("100")
    assert desk.risk.reserved_total == desk.ledger.reserved_cash() == D("0")
    assert rec.filled_qty == D("1")
    assert len(desk.ledger.transactions) == 4


def test_fin04_reconciliation_applies_every_fee_component(desk):
    """Catch the remote path keeping a single `fee` and dropping `fees[]`.

    Uma execução com o custo dividido em componentes (taxa na moeda de
    cotação mais taxa numa moeda nativa) chegava à reconciliação com apenas
    um `fee`. O componente nativo sumia: o custo ficava menor que o real e
    nenhum incidente FEE_VALUATION_UNKNOWN era criado.
    """
    rec = _buy_order(desk, notional="200", price="100")
    fills = [{
        "fill_id": "multi-fee", "quantity": D("1"), "price": D("100"),
        "fee": D("0"), "fee_currency": "", "side": "buy",
        "fees": [{"cost": D("1"), "currency": "USDT"},
                 {"cost": D("0.1"), "currency": "BNB"}],
    }]
    _reconcile(desk, rec, status="closed", cumulative="1",
               remote_quantity="1", fills=fills)
    incidents = {i["type"]: i for i in desk.risk.incidentes()}
    assert desk.ledger.balance("FEES:USDT") == D("1")
    assert desk.ledger.balance("FEES:BNB") == D("0.1"), (
        "a taxa em moeda nativa não chegou ao livro pela reconciliação")
    assert D(incidents["FEE_VALUATION_UNKNOWN"]["amount"]) == D("0.1")
    assert desk.ledger.invested_cost() == D("100")
    assert desk.ledger.is_balanced()


def test_fin09_daily_brake_counts_the_entry_fee(desk):
    """Catch the realized-loss meter ignoring the fee paid on the ENTRY.

    O freio diário é alimentado só na SAÍDA. Como a taxa de entrada fica
    lançada em FEES (fora do custo da posição), o resultado realizado
    contava apenas a taxa da saída. Comprar e vender 1@100 pagando taxa 1 em
    cada ponta registrava perda 1 no freio, quando a perda real é 2 —
    liberando mais risco diário do que o permitido.
    """
    desk.ledger.fill_buy(INSTRUMENT, D("1"), D("100"), D("1"),
                         reserved=D("0"), agent_id="trader-A",
                         ref="fin09-entry")
    assert desk.ledger.balance("FEES:USDT") == D("1")
    saida = desk.close_position(
        instrument=INSTRUMENT, quantity=D("1"), price=D("100"),
        cost_basis=D("100"), agent_id="trader-A",
        client_order_id="fin09-sell")
    _fill(desk, desk.orders[saida["order_id"]], "fin09-sell",
          price="100", fee="1")
    assert desk.perda_do_dia == D("2"), (
        "a taxa paga na entrada não entrou no freio diário")


def _paper_request(client_id, side, price, quantity="2", instrument="BTCUSDT"):
    return OrderRequest(
        client_id, "decision", "run", "strategy", "paper-account", instrument,
        Environment.PAPER.value, side, "MARKET",
        D(quantity), D(price), D("98"), D("105"),
    )


def test_fin10_long_without_quote_keeps_book_value():
    """Catch a held paper position vanishing from equity when no quote is available."""
    adapter = PaperAdapter()
    adapter.submit(_paper_request("fin10-buy", Side.BUY, "100"), risk_approved=True)
    metrics = adapter.mark_to_market({})
    assert metrics == {
        "equity": D("100000"), "unrealized_pnl": D("0"),
        "gross_exposure": D("200"), "positions": D("1"),
    }


def test_fin10_opening_a_short_from_flat_is_refused():
    """Catch an initial paper short whose notional is added as positive equity."""
    adapter = PaperAdapter()
    before = deepcopy(adapter.reconciliation_snapshot())
    with pytest.raises(ValueError, match="short não habilitado"):
        adapter.submit(_paper_request("fin10-short", Side.SELL, "100"), risk_approved=True)
    assert adapter.get_positions("paper-account") == []
    assert adapter.reconciliation_snapshot() == before
    assert adapter.cash == D("100000")


# --------------------------------------------------------------------------
# V9-FIN-07 — limites de risco PROJETADOS, não do estado atual.
#
# Achado (RISCO/alta): o motor de risco de mercado avaliava os tetos contra o
# estado ATUAL do portfólio e o risco AUTODECLARADO pela decisão —
#   * `portfolio.gross_exposure` (atual) em vez da exposição PROJETADA
#     (atual + notional proposto): uma ordem absurdamente grande passava pelo
#     teto de cobertura porque a cobertura de hoje ainda era pequena;
#   * `decision.risk_pct_equity` (declarado) em vez do risco EFETIVO pela
#     distância ao stop (`quantidade × |entrada − stop|` sobre o patrimônio):
#     a decisão podia declarar risco ínfimo e enviar risco real enorme.
#
# Contraprova do auditor: aumentar a quantidade deve acionar o limite de
# notional E o risco pela distância ao stop. O motor é puro e determinístico —
# nada aqui envia ordem.
# --------------------------------------------------------------------------
def _mercado_snapshot(age=0):
    now = datetime.now(timezone.utc)
    return MarketSnapshot("snap-fin07", "BTCUSDT", "paper", D("99.9"),
                          D("100.1"), D("100"), D("10"), D("0.2"),
                          now - timedelta(seconds=age), now, "test", 1)


def _mercado_portfolio(equity="1000", gross="0"):
    return PortfolioSnapshot("port-fin07", "paper-account", D(equity), D(equity),
                             D("0"), D(equity), D("0"), D("0"), D(gross),
                             D("0"), ())


def _mercado_decision(*, quantity, entry="100", stop="98", target="105",
                      declared_risk="0.001", side=Side.BUY):
    now = datetime.now(timezone.utc)
    return TradeDecision(
        "dec-fin07", "run-fin07", "strat-fin07", "crypto", "BTCUSDT", "paper",
        "paper-account", side, "MARKET", D(entry), D(stop), D(target),
        risk_amount=D("10"), risk_pct_equity=D(declared_risk),
        quantity=D(quantity), time_in_force="GTC",
        signal_expiry=now + timedelta(minutes=5), confidence=D("0.8"),
        regime="TREND", evidence_ids=("ev-fin07",), agent_votes=(),
        created_at=now,
    )


def test_fin07_oversized_notional_is_refused_by_projected_exposure():
    """Notional 2× o patrimônio com stop estreito: o risco por trade cabe, mas
    a cobertura PROJETADA estoura o teto — o motor antigo aprovava."""
    decision = _mercado_decision(quantity="20", stop="99.9", target="101")
    result = RiskDeMercado().evaluate(
        decision, _mercado_portfolio(equity="1000", gross="0"),
        _mercado_snapshot())
    assert result.status.value == "REJECT"
    assert "GROSS_EXPOSURE_LIMIT" in result.reason_codes
    assert result.approved_quantity == D("0") == result.approved_risk_amount


def test_fin07_stop_distance_risk_is_refused_even_when_declared_is_small():
    """Risco declarado ínfimo (0,01%) mas risco EFETIVO 5% pela distância ao
    stop: o teto por trade tem de pegar o risco REAL, não o declarado."""
    decision = _mercado_decision(quantity="50", stop="90", target="115",
                                 declared_risk="0.0001")
    result = RiskDeMercado().evaluate(
        decision, _mercado_portfolio(equity="10000", gross="0"),
        _mercado_snapshot())
    assert result.status.value == "REJECT"
    assert "TRADE_RISK_LIMIT" in result.reason_codes


def test_fin07_a_sane_order_still_opens():
    """O portão não é parede: ordem dentro de todos os tetos continua aprovada
    (um gate que nunca abre é indistinguível de um gate quebrado)."""
    decision = _mercado_decision(quantity="5", stop="98", target="105")
    result = RiskDeMercado().evaluate(
        decision, _mercado_portfolio(equity="10000", gross="0"),
        _mercado_snapshot())
    assert result.status.value == "APPROVE"
    assert result.approved_quantity == D("5")


# ---------------------------------------------------------------------------
# V9-FIN-05 — RISCO/alta: gestão registrada só em FILLED imediato.
# Uma compra ACK → executada DEPOIS (fill reconciliado fora do ciclo) entra
# no ledger, mas o livro de gestão ficava vazio: o Smart Exit nunca a via e
# nenhum stop a avaliava.
# ---------------------------------------------------------------------------


def _painel_de_gestao(tmp_path, *, fechamento="100", preco="100"):
    """Mesa completa offline: corretora que só reconhece (ACK) + livro de
    gestão com os traders habilitados para PAPER."""
    mandate = DeskMandate(
        mandate_id="v9-fin05", owner="synthetic-owner", account="fake-account",
        mode=Mode.PAPER, instruments=(INSTRUMENT,), capital=D("2000"),
        slippage_buffer_pct=D("0"),
    )
    traders = default_desk((INSTRUMENT,))
    for trader in traders:
        for etapa in (AgentStage.VALIDATED, AgentStage.SHADOW,
                      AgentStage.PAPER_ENABLED):
            trader.spec.move_to(etapa)
    ledger = Ledger(opening_cash=mandate.capital)
    risk = RiskEngine(mandate, carteira=ledger)
    coord = ExecutionCoordinator(
        mandate, ledger, risk, worker_id="fake-worker",
        broker=AcknowledgingBroker(),
    )
    orch = DeskOrchestrator(
        mandate, traders, worker_id="fake-worker", risk=risk,
        positions_provider=ledger.open_positions,
    )
    closes = tuple(D("100") for _ in range(39)) + (D(fechamento),)
    sched = DeskScheduler(
        orch, coord,
        snapshot_provider=lambda: [
            SnapshotDaMesa(INSTRUMENT, "1h", utcnow(), closes, source="fixture")],
        price_provider=lambda: {INSTRUMENT: D(preco)},
        state_path=tmp_path / "desk-state.json", interval_seconds=60,
    )
    return sched, orch, coord, ledger


def test_fin05_ack_then_fill_enters_the_management_book(tmp_path):
    """Catch an ACK→fill reconciled order that never reaches the Smart Exit.

    O financeiro fica DONO da posição; o ciclo seguinte precisa levá-la ao
    livro de gestão — sem isso nenhuma saída jamais a considera."""
    sched, orch, coord, ledger = _painel_de_gestao(tmp_path)
    rec = _buy_order(coord, notional="100", price="100", agent="trader-trend")
    assert rec.state is OrderState.ACKNOWLEDGED
    assert orch.book == {}
    _fill(coord, rec, "fin05-late-fill", price="100", quantity="1")
    assert rec.state is OrderState.FILLED
    assert ledger.position_quantity(INSTRUMENT) == D("1")
    assert orch.book == {}   # o defeito: dinheiro na mesa, gestão cega

    out = sched.run_once()

    pos = orch.book[INSTRUMENT]
    assert pos.quantity == D("1")
    assert pos.entry_price == D("100")
    assert pos.agent_id == "trader-trend"
    assert pos.politica is not None
    assert out["encerradas"] == []   # preço na entrada: nada a fechar
    assert ledger.is_balanced()


def test_fin05_ack_then_fill_position_receives_stop_evaluation(tmp_path):
    """Catch a reconciled position that never receives a stop decision.

    Mercado desaba para 60 com entrada em 100: o Smart Exit precisa emitir
    STOP_CONDITION e a ordem de saída precisa sair pelo coordenador."""
    sched, orch, coord, ledger = _painel_de_gestao(
        tmp_path, fechamento="60", preco="60")
    rec = _buy_order(coord, notional="100", price="100", agent="trader-trend")
    _fill(coord, rec, "fin05-crash-fill", price="100", quantity="1")
    assert rec.state is OrderState.FILLED and orch.book == {}
    assert ledger.position_quantity(INSTRUMENT) == D("1")

    out = sched.run_once()

    assert out["encerradas"], out
    saida = out["encerradas"][0]
    assert saida["reason_code"] == "STOP_CONDITION"
    assert saida["estado"] == "acknowledged"   # enviada, aguardando a corretora
    vendas = [r for r in coord.orders.values() if r.lado == "sell"]
    assert len(vendas) == 1
    assert vendas[0].quantidade == D("1")
    assert ledger.is_balanced()


# ---------------------------------------------------------------------------
# V9-FIN-06 — RISCO/alta: o caminho único aceitava decisão ANTIGA (reserva
# vencida, mandato de versão divergente) e não contava saídas em voo — duas
# vendas ACK comprometiam o MESMO lote na corretora.
# ---------------------------------------------------------------------------


def _entrada_para_reserva(desk, reserva, *, client_order_id, **kwargs):
    """Intenção de ENTRADA apontando para uma reserva viva do risco."""
    from tradingagents.desk.intents import ExecutionIntent, Kind, Side as Lado
    campos = {
        "kind": Kind.ENTRY, "side": Lado.BUY, "instrument": INSTRUMENT,
        "quantity": reserva.quantity, "agent_id": reserva.agent_id,
        "account": desk.mandate.account,
        "mandate_version": desk.mandate.version,
        "client_order_id": client_order_id,
        "reservation_id": reserva.reservation_id,
        "reserved_amount": reserva.amount,
    }
    campos.update(kwargs)
    return ExecutionIntent(**campos)


def test_fin06_expired_reservation_cannot_authorize_a_new_order(desk):
    """Reserva vencida recusada no caminho único; reserva viva continua ok.

    A validade existia só em `RiskEngine.intent()`: quem chamava o caminho
    único direto (copiloto, worker, API) enviava ordem coberta por reserva
    que já caducou."""
    vencida = desk.risk.reserve_exato(
        instrument=INSTRUMENT, alvo=D("100"), agent_id="trader-A")
    desk.risk._reservations[vencida.reservation_id] = dataclasses.replace(
        vencida, expires_at=utcnow() - timedelta(minutes=1))

    ei = _entrada_para_reserva(desk, vencida, client_order_id="fin06-vencida")
    with pytest.raises(ExecutionRefused, match="expir"):
        desk.submit_intent(ei)
    assert desk.orders == {}
    assert desk.ledger.reserved_cash() == D("0")

    # CONTROLE: a mesma ordem com reserva viva é aceita — o portão não é
    # parede e a recusa acima não deixou lixo de estado.
    viva = desk.risk.reserve_exato(
        instrument=INSTRUMENT, alvo=D("100"), agent_id="trader-A")
    rec = desk.submit_intent(_entrada_para_reserva(
        desk, viva, client_order_id="fin06-viva"))
    assert rec.state is OrderState.ACKNOWLEDGED
    assert desk.ledger.is_balanced()


def test_fin06_divergent_mandate_version_is_refused(desk):
    """Intenção presa a outra versão do mandato não autoriza ordem.

    A checagem vivia só no adaptador `OrderIntent.assert_sendable`; o
    caminho único aceitava decisão tomada sob política antiga."""
    viva = desk.risk.reserve_exato(
        instrument=INSTRUMENT, alvo=D("100"), agent_id="trader-A")
    ei = _entrada_para_reserva(
        desk, viva, client_order_id="fin06-versao",
        mandate_version=desk.mandate.version + 1)
    with pytest.raises(ExecutionRefused, match="versão"):
        desk.submit_intent(ei)
    assert desk.orders == {}
    assert desk.risk.vinculo(viva.reservation_id) == ""   # nem chegou ao bind

    # CONTROLE: com a versão vigente, a MESMA reserva segue executável.
    rec = desk.submit_intent(_entrada_para_reserva(
        desk, viva, client_order_id="fin06-versao-ok"))
    assert rec.state is OrderState.ACKNOWLEDGED
    assert desk.ledger.is_balanced()


def test_fin06_two_sells_cannot_compromise_the_same_lot(tmp_path):
    """Duas vendas ACK não podem comprometer o MESMO lote na corretora.

    O lote só sai do livro quando o fill chega; no intervalo, cada venda
    via o lote inteiro como livre e a segunda só falharia na corretora,
    depois de já ter comprometido o que a primeira vendia."""
    _sched, _orch, coord, ledger = _painel_de_gestao(tmp_path)
    compra = _buy_order(coord, notional="100", price="100", agent="trader-trend")
    _fill(coord, compra, "fin06-compra", price="100", quantity="1")
    assert ledger.lot_quantity(INSTRUMENT, "trader-trend") == D("1")

    def vender(quantidade, coid):
        return coord.close_position(
            instrument=INSTRUMENT, quantity=D(quantidade), price=D("100"),
            cost_basis=D("100") * D(quantidade), agent_id="trader-trend",
            client_order_id=coid)

    venda1 = vender("0.4", "fin06-venda-1")
    assert venda1["estado"] == "acknowledged"
    venda2 = vender("0.6", "fin06-venda-2")
    assert venda2["estado"] == "acknowledged"   # 0,4 + 0,6 = 1,0: ainda cabe

    with pytest.raises(ExecutionRefused, match="voo"):
        vender("0.1", "fin06-venda-3")
    vendas = [r for r in coord.orders.values() if r.lado == "sell"]
    assert len(vendas) == 2

    # Os fills confirmam que as duas vendas comprometidas fecham o lote
    # exatamente — nenhuma quantidade foi vendida duas vezes.
    _fill(coord, coord.orders[venda1["order_id"]], "fin06-fill-1",
          price="100", quantity="0.4")
    _fill(coord, coord.orders[venda2["order_id"]], "fin06-fill-2",
          price="100", quantity="0.6")
    assert all(r.state is OrderState.FILLED for r in vendas)
    assert ledger.position_quantity(INSTRUMENT) == D("0")
    assert ledger.is_balanced()


# ---------------------------------------------------------------------------
# V9-FIN-08 — RISCO/alta: o snapshot do OMS só era gravado no FIM do ciclo e
# falhas de gravação viravam aviso. Um crash entre o aceite da corretora e a
# gravação apagava a identidade da ordem (ela existia na exchange e não para
# a mesa); com o armazém quebrado a ordem seguia sem nenhuma prova durável.
# ---------------------------------------------------------------------------


class _ArmazemDeTeste:
    """Armazém mínimo com falha ligável (mesma interface salvar/carregar)."""

    def __init__(self):
        self.documento = None
        self.falha = False
        self.gravacoes = 0

    def salvar(self, tenant_id, chave, documento):
        if self.falha:
            raise OSError("disco cheio")
        self.documento = deepcopy(documento)
        self.gravacoes += 1
        return "memoria"

    def carregar(self, tenant_id, chave):
        return deepcopy(self.documento)


def _ligar_wal(desk, armazem):
    """Ponto por onde o serviço liga o WAL: gravar ANTES de cada envio."""
    def gravar():
        armazem.salvar("t", "oms", desk.snapshot_estado())
    desk.persistencia = gravar
    return desk


def test_fin08_write_failure_before_send_blocks_new_sends(desk):
    """Sem gravação durável confirmada a ordem NÃO sai — nem a próxima."""
    armazem = _ArmazemDeTeste()
    armazem.falha = True
    _ligar_wal(desk, armazem)
    envios, original = [], desk.broker

    class _Contando:
        def submit(self, coid, instrument, side, quantity, final_state=None):
            envios.append(coid)
            return original.submit(coid, instrument, side, quantity,
                                   final_state=final_state)

    desk.broker = _Contando()

    with pytest.raises(ExecutionRefused, match="persist"):
        _buy_order(desk)
    assert envios == []                     # a corretora nunca viu a ordem
    recusada = list(desk.orders.values())[-1]
    assert recusada.state is OrderState.REJECTED
    assert "persist" in recusada.note
    assert desk.ledger.reserved_cash() == D("0")    # caixa devolvido
    assert desk.risk.reserved_total == D("0")
    assert desk.ledger.is_balanced()

    # Armazém segue quebrado => o PRÓXIMO envio também é bloqueado.
    with pytest.raises(ExecutionRefused, match="persist"):
        _buy_order(desk)
    assert envios == []
    assert desk.ledger.is_balanced()

    # CONTROLE: gravação restaurada — a mesa volta a enviar. O bloqueio é do
    # armazém, não uma parede permanente.
    armazem.falha = False
    rec = _buy_order(desk)
    assert rec.state is OrderState.ACKNOWLEDGED
    assert len(envios) == 1
    assert armazem.gravacoes == 1           # WAL gravou ANTES do envio


def test_fin08_identity_survives_crash_right_after_broker_accept(desk):
    """Crash entre o aceite da corretora e o fim do ciclo não perde a ordem.

    Sem WAL o único snapshot existente era do ciclo ANTERIOR: a ordem
    recém-aceita sumia do restauro e a mesa acordava sem saber que havia
    ordem viva na corretora."""
    armazem = _ArmazemDeTeste()
    _ligar_wal(desk, armazem)

    rec = _buy_order(desk)
    assert rec.state is OrderState.ACKNOWLEDGED      # a corretora aceitou…
    # …e o processo MORRE aqui, antes de qualquer persistência de fim de
    # ciclo. O armazém só contém o que o WAL gravou ANTES do envio.
    assert armazem.documento is not None

    # Restauro em processo NOVO, a partir do documento durável.
    mandato = desk.mandate
    livro2 = Ledger(opening_cash=mandato.capital)
    risco2 = RiskEngine(mandato, carteira=livro2)
    coord2 = ExecutionCoordinator(mandato, livro2, risco2,
                                  worker_id="fake-worker",
                                  broker=AcknowledgingBroker())
    recuperacao = coord2.restaurar_estado(armazem.carregar("t", "oms"))

    assert recuperacao["restaurado"] is True
    assert recuperacao["exige_reconciliacao"] is True
    restaurada = coord2.orders[rec.order_id]
    assert restaurada.client_order_id == rec.client_order_id
    assert restaurada.instrument == INSTRUMENT
    assert restaurada.quantity == rec.quantity
    assert restaurada.agent_id == "trader-A"
    # Estado CONSERVADOR: gravado antes do envio — a corretora pode ou não
    # ter recebido; quem decide é a reconciliação.
    assert restaurada.state is OrderState.SUBMITTING

    # O portão segura a mesa: nenhuma ordem nova antes de reconciliar.
    # A intenção vai DIRETO ao caminho único — o veto de nova reserva no
    # mesmo instrumento é outra camada (risco), e mascararia aqui o portão
    # do coordenador que este teste quer medir.
    from tradingagents.desk.intents import ExecutionIntent, Kind, Side as Lado
    ei = ExecutionIntent(
        kind=Kind.ENTRY, side=Lado.BUY, instrument=INSTRUMENT,
        quantity=D("1"), agent_id="trader-A",
        account=coord2.mandate.account,
        mandate_version=coord2.mandate.version,
        client_order_id="fin08-gate")
    with pytest.raises(ExecutionRefused, match="reconcilia"):
        coord2.submit_intent(ei)
    assert len(coord2.orders) == 1


# ---------------------------------------------------------------------------
# V9-FIN-12 — FINANCEIRO/P0: o passivo `LIABILITY:FEE_FUNDING` só era creditado
# (valoração) e nunca quitado, e a porta de valoração aceitava a própria moeda
# funcional — valorar uma taxa que JÁ foi despesada no fill contava a despesa
# duas vezes e criava um passivo fantasma. Esperado: reconciliação econômica
# por ocorrência, com a liquidação vindo do aporte do ativo descoberto.
# ---------------------------------------------------------------------------


def _fin12_com_taxa_valorada(quantidade="2", rate="300"):
    """Livro com UMA taxa nativa de BNB já valorada via lastro real."""
    livro = Ledger(opening_cash=D("1000"))
    livro.reserve(D("100"), ref="r", reservation_id="R1")
    livro.fill_buy(INSTRUMENT, D("1"), D("90"), D("0"),
                   reserved=D("100"), ref="o1", agent_id="A",
                   reservation_id="R1",
                   fee_in_other_currency=(D(quantidade), "BNB"))
    ids = [c["fee_component_id"]
           for c in livro.fee_components("BNB", True)]
    livro.fee_valuation(moeda="BNB", quantidade=D(quantidade),
                        rate=D(rate), fonte="binance",
                        em="2026-09-14T23:00:00+00:00",
                        fee_component_ids=ids)
    return livro


def _fin12_aportar(livro, quantidade, *, em):
    return livro.asset_contribution(
        "BNB", D(quantidade), reason="aporte", actor="op",
        rate=D("300"), fonte="binance", em=em)


def _fin12_liquidacoes(livro):
    return [t for t in livro.export_transactions()
            if t["kind"] == "fee_funding_settlement"]


def test_fin12_taxa_na_moeda_funcional_e_recusada_na_valoracao():
    """A taxa cobrada em USDT já foi despesada no fill (FEES:USDT).

    Valorar a MESMA taxa como se fosse moeda estrangeira soma a despesa duas
    vezes e fabrica um passivo sem lastro algum: nenhuma cobrança em USDT
    esperando conversão existe."""
    livro = Ledger(opening_cash=D("1000"))
    livro.reserve(D("100"), ref="r", reservation_id="R1")
    livro.fill_buy(INSTRUMENT, D("1"), D("90"), D("2"),
                   reserved=D("100"), ref="o1", agent_id="A",
                   reservation_id="R1")
    assert livro.balance("FEES:USDT") == D("2")

    with pytest.raises(ValueError, match="moeda funcional|dupla|já"):
        livro.fee_valuation_sem_lastro(
            moeda="USDT", quantidade=D("2"), rate=D("1"),
            fonte="binance", em="2026-09-14T23:00:00+00:00")

    assert livro.balance("FEES:USDT") == D("2"), "a despesa foi contada de novo"
    assert livro.balance("LIABILITY:FEE_FUNDING") == D("0"), (
        "a valoração na moeda funcional criou passivo fantasma")
    assert not [t for t in livro.export_transactions()
                if t["kind"] == "fee_valuation"], (
        "a valoração recusada deixou lançamento no histórico")
    assert livro.is_balanced()


class TestFin12AporteQuitaOPassivo:
    def test_o_aporte_quita_o_passivo_da_taxa_valorada(self):
        livro = _fin12_com_taxa_valorada()
        assert livro.balance("LIABILITY:FEE_FUNDING") == D("-600")
        _fin12_aportar(livro, "2", em="2026-09-14T23:05:00+00:00")
        assert livro.balance("LIABILITY:FEE_FUNDING") == D("0"), (
            "o aporte cobriu o ativo descoberto mas a dívida da taxa ficou "
            "de pé para sempre")
        assert livro.balance("CONTRIBUTED_CAPITAL") == D("-600"), (
            "a quitação não foi declarada como capital aportado (funcional)")
        assert livro.balance("ASSET:BNB") == D("0")
        assert livro.is_balanced()
        assert livro.functional_balanced()

    def test_aportes_parciais_quitam_proporcionalmente(self):
        livro = _fin12_com_taxa_valorada()
        _fin12_aportar(livro, "1", em="2026-09-14T23:05:00+00:00")
        assert livro.balance("LIABILITY:FEE_FUNDING") == D("-300")
        _fin12_aportar(livro, "1", em="2026-09-14T23:06:00+00:00")
        assert livro.balance("LIABILITY:FEE_FUNDING") == D("0")
        assert livro.balance("CONTRIBUTED_CAPITAL") == D("-600")
        assert livro.is_balanced()

    def test_aporte_alem_do_necessario_quita_uma_vez_so(self):
        livro = _fin12_com_taxa_valorada()
        _fin12_aportar(livro, "3", em="2026-09-14T23:05:00+00:00")
        assert livro.balance("LIABILITY:FEE_FUNDING") == D("0")
        assert livro.balance("CONTRIBUTED_CAPITAL") == D("-600"), (
            "a quitação usou a quantidade aportada (3), não o passivo real")
        assert livro.balance("ASSET:BNB") == D("1"), (
            "a sobra do aporte deve permanecer no ativo, visível")
        _fin12_aportar(livro, "1", em="2026-09-14T23:06:00+00:00")
        assert livro.balance("LIABILITY:FEE_FUNDING") == D("0")
        assert livro.balance("CONTRIBUTED_CAPITAL") == D("-600")
        assert len(_fin12_liquidacoes(livro)) == 1, (
            "um aporte posterior re-liquidou um passivo já quitado")
        assert livro.is_balanced()

    def test_aporte_feito_antes_da_valoracao_quita_na_valoracao(self):
        """A ordem dos fatos não pode mudar a economia: quem já cobriu o
        ativo não deve ver a dívida renascer quando a cotação chega."""
        livro = Ledger(opening_cash=D("1000"))
        livro.reserve(D("100"), ref="r", reservation_id="R1")
        livro.fill_buy(INSTRUMENT, D("1"), D("90"), D("0"),
                       reserved=D("100"), ref="o1", agent_id="A",
                       reservation_id="R1",
                       fee_in_other_currency=(D("2"), "BNB"))
        _fin12_aportar(livro, "2", em="2026-09-14T23:01:00+00:00")
        assert livro.balance("LIABILITY:FEE_FUNDING") == D("0")

        ids = [c["fee_component_id"]
               for c in livro.fee_components("BNB", True)]
        livro.fee_valuation(moeda="BNB", quantidade=D("2"), rate=D("300"),
                            fonte="binance",
                            em="2026-09-14T23:02:00+00:00",
                            fee_component_ids=ids)

        assert livro.balance("FEES:USDT") == D("600")
        assert livro.balance("LIABILITY:FEE_FUNDING") == D("0"), (
            "a valoração de uma taxa JÁ coberta reviveu a dívida")
        assert livro.balance("CONTRIBUTED_CAPITAL") == D("-600")
        assert livro.is_balanced()

    def test_cada_ocorrencia_quita_pela_propria_cotacao(self):
        """Duas ocorrências, duas cotações: o passivo é 600 + 350 = 950.

        Liquidar pela cotação do aporte (3 × 320 = 960) trataria o passivo
        como se fosse a quantidade nova — o mesmo erro de somar unidades."""
        livro = Ledger(opening_cash=D("1000"))
        for reservation_id, ref, price, qtd in (("R1", "o1", "90", "2"),
                                                ("R2", "o2", "80", "1")):
            livro.reserve(D("100"), ref=f"r:{ref}",
                          reservation_id=reservation_id)
            livro.fill_buy(INSTRUMENT, D("1"), D(price), D("0"),
                           reserved=D("100"), ref=ref, agent_id="A",
                           reservation_id=reservation_id,
                           fee_in_other_currency=(D(qtd), "BNB"))
        comps = livro.fee_components("BNB", True)
        primeira = [c["fee_component_id"] for c in comps if c["amount"] == "2"]
        segunda = [c["fee_component_id"] for c in comps if c["amount"] == "1"]
        livro.fee_valuation(moeda="BNB", quantidade=D("2"), rate=D("300"),
                            fonte="binance",
                            em="2026-09-14T23:00:00+00:00",
                            fee_component_ids=primeira)
        livro.fee_valuation(moeda="BNB", quantidade=D("1"), rate=D("350"),
                            fonte="binance",
                            em="2026-09-14T23:10:00+00:00",
                            fee_component_ids=segunda)
        assert livro.balance("LIABILITY:FEE_FUNDING") == D("-950")

        _fin12_aportar(livro, "3", em="2026-09-14T23:15:00+00:00")
        assert livro.balance("LIABILITY:FEE_FUNDING") == D("0")
        assert livro.balance("CONTRIBUTED_CAPITAL") == D("-950"), (
            "liquidou pela cotação do aporte (960) em vez do passivo real "
            "de cada ocorrência (950)")
        assert livro.balance("ASSET:BNB") == D("0")
        assert livro.is_balanced()

    def test_o_aporte_cobre_as_ocorrencias_em_ordem_fifo(self):
        livro = Ledger(opening_cash=D("1000"))
        for reservation_id, ref, price, qtd in (("R1", "o1", "90", "2"),
                                                ("R2", "o2", "80", "1")):
            livro.reserve(D("100"), ref=f"r:{ref}",
                          reservation_id=reservation_id)
            livro.fill_buy(INSTRUMENT, D("1"), D(price), D("0"),
                           reserved=D("100"), ref=ref, agent_id="A",
                           reservation_id=reservation_id,
                           fee_in_other_currency=(D(qtd), "BNB"))
        comps = livro.fee_components("BNB", True)
        primeira = [c["fee_component_id"] for c in comps if c["amount"] == "2"]
        segunda = [c["fee_component_id"] for c in comps if c["amount"] == "1"]
        livro.fee_valuation(moeda="BNB", quantidade=D("2"), rate=D("300"),
                            fonte="binance",
                            em="2026-09-14T23:00:00+00:00",
                            fee_component_ids=primeira)
        livro.fee_valuation(moeda="BNB", quantidade=D("1"), rate=D("350"),
                            fonte="binance",
                            em="2026-09-14T23:10:00+00:00",
                            fee_component_ids=segunda)

        _fin12_aportar(livro, "2", em="2026-09-14T23:15:00+00:00")
        assert livro.balance("LIABILITY:FEE_FUNDING") == D("-350"), (
            "2 BNB quitam a PRIMEIRA ocorrência (600); ratear entre as duas "
            "faria a ocorrência nova pagar pela antiga")
        _fin12_aportar(livro, "1", em="2026-09-14T23:20:00+00:00")
        assert livro.balance("LIABILITY:FEE_FUNDING") == D("0")
        assert livro.balance("CONTRIBUTED_CAPITAL") == D("-950")
        assert livro.is_balanced()

    def test_a_quitacao_sobrevive_ao_restauro_sem_requitar(self):
        """O vínculo liquidação→valoração precisa ser durável: sem ele o
        livro restaurado re-liquidaria o mesmo passivo a cada aporte."""
        livro = _fin12_com_taxa_valorada()
        _fin12_aportar(livro, "2", em="2026-09-14T23:05:00+00:00")
        assert livro.balance("LIABILITY:FEE_FUNDING") == D("0")

        restaurado = Ledger.from_transactions(livro.export_transactions(),
                                              currency="USDT")
        assert restaurado.balance("LIABILITY:FEE_FUNDING") == D("0")
        _fin12_aportar(restaurado, "1", em="2026-09-14T23:10:00+00:00")
        assert restaurado.balance("LIABILITY:FEE_FUNDING") == D("0"), (
            "o livro restaurado re-liquidou um passivo já quitado")
        assert restaurado.balance("CONTRIBUTED_CAPITAL") == D("-600")
        assert len(_fin12_liquidacoes(restaurado)) == 1
        assert restaurado.is_balanced()

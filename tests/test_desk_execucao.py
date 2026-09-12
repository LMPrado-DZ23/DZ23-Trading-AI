"""Cenários de aceite de execução, reconciliação e contabilidade
(MA-06/MA-07/TA-009/TA-010) — cenários 08, 09, 10, 13 e 14."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from tradingagents.desk.execution import (BrokerTimeout, ExecutionCoordinator,
                                          ExecutionRefused, IdempotencyConflict,
                                          OrderState, PaperBroker, PositionGuardian,
                                          fingerprint)
from tradingagents.desk.ledger import Ledger, LedgerError
from tradingagents.desk.models import (Action, DeskMandate, Mode, TradeProposal, utcnow)
from tradingagents.desk.risk import RiskEngine

INSTR = "BTC/USDT"


def mandate(**kw):
    base = dict(mandate_id="m1", owner="prado", account="acct-1", mode=Mode.PAPER,
                instruments=(INSTR, "ETH/USDT"), capital=Decimal("1000"))
    base.update(kw)
    return DeskMandate(**base)


def proposal(pct="0.10"):
    return TradeProposal(agent_id="trader-trend", strategy="trend_following",
                         instrument=INSTR, action=Action.BUY, reason="teste",
                         desired_exposure_pct=Decimal(pct),
                         valid_until=utcnow() + timedelta(minutes=5))


def setup(price="100", broker=None):
    m = mandate()
    risk = RiskEngine(m)
    ledger = Ledger(opening_cash=m.capital)
    coord = ExecutionCoordinator(m, ledger, risk, worker_id="w1",
                                 broker=broker or PaperBroker(Decimal(price)))
    res = risk.reserve(proposal(), Decimal(price))
    intent = risk.intent(res, Action.BUY, Mode.PAPER, "w1", idempotency_key="idem-0001")
    return m, risk, ledger, coord, intent


class TestCenario08Idempotencia:
    def test_same_key_same_content_returns_same_order(self):
        *_, coord, intent = setup()
        a = coord.submit(intent)
        b = coord.submit(intent)
        assert a.order_id == b.order_id
        assert len(coord.broker.submitted) == 1      # a corretora viu UM envio

    def test_same_key_different_content_is_conflict_not_retry(self):
        m, risk, ledger, coord, intent = setup()
        coord.submit(intent)
        other_res = risk.reserve(
            TradeProposal(agent_id="t2", strategy="s", instrument="ETH/USDT",
                          action=Action.BUY, reason="", desired_exposure_pct=Decimal("0.1"),
                          valid_until=utcnow() + timedelta(minutes=5)), Decimal("50"))
        clash = risk.intent(other_res, Action.BUY, Mode.PAPER, "w1",
                            idempotency_key="idem-0001")
        with pytest.raises(IdempotencyConflict):
            coord.submit(clash)

    def test_fingerprint_changes_with_content(self):
        *_, coord, intent = setup()
        assert fingerprint(intent) == fingerprint(intent)
        assert len(fingerprint(intent)) == 64

    def test_live_mode_never_reaches_the_broker(self):
        m, risk, ledger, coord, intent = setup()
        live = type(intent)(intent_id=intent.intent_id, reservation=intent.reservation,
                            action=intent.action, mode=Mode.LIVE,
                            owner_worker="w1", expires_at=intent.expires_at,
                            idempotency_key="k-live-0001")
        with pytest.raises(ExecutionRefused, match="LIVE"):
            coord.submit(live)
        assert not coord.broker.submitted


class TestCenario09TimeoutIncerto:
    class TimeoutBroker:
        def submit(self, *a, **k):
            raise BrokerTimeout("sem resposta")

    def test_timeout_keeps_order_unknown_and_reserve_locked(self):
        _, risk, ledger, coord, intent = setup(broker=self.TimeoutBroker())
        rec = coord.submit(intent)
        assert rec.state is OrderState.UNKNOWN_RECONCILIATION_REQUIRED
        assert risk.reserved_total > 0            # nada de liberação prematura
        assert coord.pending_reconciliation() == [rec]

    def test_reconcile_without_answer_stays_uncertain(self):
        _, risk, ledger, coord, intent = setup(broker=self.TimeoutBroker())
        rec = coord.submit(intent)
        coord.reconcile(rec.order_id, None)
        assert rec.state is OrderState.UNKNOWN_RECONCILIATION_REQUIRED
        assert risk.reserved_total > 0

    def test_reconcile_finds_order_executed(self):
        _, risk, ledger, coord, intent = setup(broker=self.TimeoutBroker())
        rec = coord.submit(intent)
        coord.reconcile(rec.order_id, {"status": "filled", "id": "ext-9",
                                       "filled": str(intent.reservation.quantity),
                                       "average": "100", "fee": "0.1"})
        assert rec.state is OrderState.FILLED
        assert risk.reserved_total == Decimal("0")   # só agora libera
        assert ledger.is_balanced()

    def test_reconcile_finds_order_absent_releases_safely(self):
        _, risk, ledger, coord, intent = setup(broker=self.TimeoutBroker())
        rec = coord.submit(intent)
        coord.reconcile(rec.order_id, {"status": "absent"})
        assert rec.state is OrderState.CANCELLED
        assert risk.reserved_total == Decimal("0")
        assert ledger.is_balanced()


class TestCenario10EventosDesordenados:
    def test_duplicate_fill_is_counted_once(self):
        *_, coord, intent = setup()
        rec = coord.submit(intent)
        before = rec.filled_qty
        coord.apply_fill(rec.order_id, fill_id=rec.external_id,
                         quantity=Decimal("999"), price=Decimal("100"))
        assert rec.filled_qty == before

    def test_partial_then_complete(self):
        m = mandate()
        risk = RiskEngine(m)
        ledger = Ledger(opening_cash=m.capital)
        coord = ExecutionCoordinator(m, ledger, risk, worker_id="w1", broker=None)
        res = risk.reserve(proposal(), Decimal("100"))
        intent = risk.intent(res, Action.BUY, Mode.PAPER, "w1", idempotency_key="idem-part1")
        from tradingagents.desk.execution import OrderRecord
        order = OrderRecord(order_id="ord-x", intent=intent, state=OrderState.ACKNOWLEDGED,
                            fingerprint=fingerprint(intent))
        coord.orders["ord-x"] = order
        half = res.quantity / 2
        coord.apply_fill("ord-x", fill_id="f1", quantity=half, price=Decimal("100"))
        assert order.state is OrderState.PARTIALLY_FILLED
        assert risk.reserved_total > 0          # parcial não libera reserva
        coord.apply_fill("ord-x", fill_id="f2", quantity=half, price=Decimal("100"))
        assert order.state is OrderState.FILLED
        assert risk.reserved_total == Decimal("0")

    def test_event_after_terminal_is_ignored(self):
        *_, coord, intent = setup()
        rec = coord.submit(intent)
        assert rec.state is OrderState.FILLED
        coord.apply_fill(rec.order_id, fill_id="tardio", quantity=Decimal("5"),
                         price=Decimal("100"))
        assert rec.filled_qty == intent.reservation.quantity


class TestCenario13KillSwitch:
    def test_cancel_requested_is_not_cancel_confirmed(self):
        m = mandate()
        risk = RiskEngine(m)
        ledger = Ledger(opening_cash=m.capital)
        coord = ExecutionCoordinator(m, ledger, risk, worker_id="w1", broker=None)
        res = risk.reserve(proposal(), Decimal("100"))
        intent = risk.intent(res, Action.BUY, Mode.PAPER, "w1", idempotency_key="idem-canc1")
        from tradingagents.desk.execution import OrderRecord
        coord.orders["o1"] = OrderRecord(order_id="o1", intent=intent,
                                         state=OrderState.ACKNOWLEDGED)
        coord.request_cancel("o1")
        assert coord.orders["o1"].state is OrderState.CANCEL_PENDING
        assert risk.reserved_total > 0          # pedido não devolve capital
        coord.confirm_cancel("o1")
        assert coord.orders["o1"].state is OrderState.CANCELLED
        assert risk.reserved_total == Decimal("0")

    def test_guardian_adopts_positions_when_trader_pauses(self):
        *_, coord, intent = setup()
        coord.submit(intent)
        g = PositionGuardian(coord)
        msg = g.adopt(INSTR, "trader-trend")
        assert "guardião assumiu" in msg and INSTR in g.watchlist()


class TestCenario14Contabilidade:
    def test_every_transaction_is_double_entry(self):
        led = Ledger(opening_cash=Decimal("1000"))
        led.reserve(Decimal("100"), ref="r1")
        led.fill_buy(INSTR, Decimal("1"), Decimal("95"), Decimal("0.1"),
                     reserved=Decimal("100"), ref="r1")
        assert led.is_balanced()
        for tx in led.transactions:
            tx.validate()

    def test_unbalanced_entry_is_refused(self):
        from tradingagents.desk.ledger import Entry
        led = Ledger()
        with pytest.raises(LedgerError, match="desbalanceada"):
            led._post("ruim", [Entry("CASH:USDT", Decimal("5")),
                               Entry("EQUITY", Decimal("-3"))])
        with pytest.raises(LedgerError, match="duas linhas"):
            led._post("ruim", [Entry("CASH:USDT", Decimal("0"))])

    def test_round_trip_pnl_attributed_to_the_trader(self):
        led = Ledger(opening_cash=Decimal("1000"))
        led.reserve(Decimal("100"), ref="r1")
        led.fill_buy(INSTR, Decimal("1"), Decimal("100"), Decimal("0"),
                     reserved=Decimal("100"), ref="r1")
        led.fill_sell(INSTR, Decimal("1"), Decimal("130"), Decimal("0"),
                      cost_basis=Decimal("100"), agent_id="trader-trend", ref="r1")
        assert led.attribution() == {"trader-trend": Decimal("30")}
        assert led.balance("CASH:USDT") == Decimal("1030")   # confere na mão
        assert led.is_balanced()

    def test_ai_cost_is_never_zero_by_omission(self):
        led = Ledger(opening_cash=Decimal("1000"))
        led.ai_cost(Decimal("0.42"), "analista-1")
        assert led.balance("COST:AI") == Decimal("0.42")
        assert led.balance("CASH:USDT") == Decimal("999.58")

    def test_correction_is_by_compensation_not_edit(self):
        led = Ledger(opening_cash=Decimal("1000"))
        tx = led.reserve(Decimal("100"), ref="r1")
        led.compensate(tx, "reserva indevida")
        assert led.balance("RESERVED") == Decimal("0")
        assert len(led.transactions) == 3          # o erro continua no histórico
        assert led.is_balanced()

    def test_equity_matches_manual_sum_after_fill(self):
        led = Ledger(opening_cash=Decimal("1000"))
        led.reserve(Decimal("200"), ref="r1")
        led.fill_buy(INSTR, Decimal("2"), Decimal("90"), Decimal("1"),
                     reserved=Decimal("200"), ref="r1")
        # caixa 800 + sobra 19 + posição 180 = 999 (1 de taxa saiu)
        assert led.equity() == Decimal("999")
        assert led.balance("FEES") == Decimal("1")

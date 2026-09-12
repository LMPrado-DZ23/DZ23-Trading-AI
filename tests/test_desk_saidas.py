"""Ciclo completo: entrada → acompanhamento → saída → resultado realizado.
Fecha a lacuna encontrada pela avaliação (a mesa só comprava)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from tradingagents.desk.execution import ExecutionCoordinator, PaperBroker
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import (Action, AgentStage, DeskMandate, MarketSnapshot,
                                       Mode, utcnow)
from tradingagents.desk.orchestrator import DeskOrchestrator, Position
from tradingagents.desk.risk import RiskEngine
from tradingagents.desk.traders import MeanReversionTrader, TrendTrader, default_desk

UNIV = ("BTC/USDT",)


def snap(closes, instrument="BTC/USDT"):
    return MarketSnapshot(instrument, "1h", utcnow(),
                          tuple(Decimal(str(c)) for c in closes))


def uptrend(n=40):
    return [100 + i * 1.5 for i in range(n)]


def enable(ts):
    for t in ts:
        for st in (AgentStage.VALIDATED, AgentStage.SHADOW, AgentStage.PAPER_ENABLED):
            t.spec.move_to(st)
    return ts


def desk(price="100", capital="1000"):
    m = DeskMandate(mandate_id="m", owner="p", account="a", mode=Mode.PAPER,
                    instruments=UNIV, capital=Decimal(capital))
    risk = RiskEngine(m)
    led = Ledger(opening_cash=m.capital)
    coord = ExecutionCoordinator(m, led, risk, worker_id="w1",
                                 broker=PaperBroker(Decimal(price)))
    orch = DeskOrchestrator(m, enable(default_desk(UNIV)), worker_id="w1", risk=risk,
                            positions_provider=led.open_positions)
    return m, risk, led, coord, orch


class TestGestaoDeSaida:
    def test_stop_triggers_exit(self):
        t = enable([TrendTrader("t", UNIV)])[0]
        p = Position("BTC/USDT", Decimal("1"), Decimal("200"), "t", Decimal("200"))
        call = t.manage(snap(uptrend()), p)
        assert call.action is Action.REDUCE and "stop" in call.reason

    def test_target_triggers_exit(self):
        t = enable([TrendTrader("t", UNIV)])[0]
        p = Position("BTC/USDT", Decimal("1"), Decimal("100"), "t", Decimal("100"))
        assert t.manage(snap(uptrend()), p).action is Action.REDUCE

    def test_thesis_alive_holds(self):
        t = enable([TrendTrader("t", UNIV)])[0]
        p = Position("BTC/USDT", Decimal("1"), Decimal("152"), "t", Decimal("152"))
        call = t.manage(snap(uptrend()), p)
        assert call.action is Action.HOLD and "tese de pé" in call.reason

    def test_thesis_invalidated_by_strategy_rule(self):
        t = enable([MeanReversionTrader("t", UNIV)])[0]
        # preço de volta à média = tese cumprida para reversão
        flat = [100.0] * 19 + [100.0]
        p = Position("BTC/USDT", Decimal("1"), Decimal("99"), "t", Decimal("99"))
        call = t.manage(snap(flat), p)
        assert call.action is Action.REDUCE and "média" in call.reason

    def test_no_data_holds_instead_of_panic_selling(self):
        t = enable([TrendTrader("t", UNIV)])[0]
        p = Position("BTC/USDT", Decimal("1"), Decimal("100"), "t", Decimal("100"))
        call = t.manage(snap([100, 101]), p)
        assert call.action is Action.HOLD and "sem dados" in call.reason


class TestCicloCompleto:
    def test_round_trip_realizes_pnl_for_the_right_trader(self):
        m, risk, led, coord, orch = desk(price="100")
        rep = orch.run_cycle([snap(uptrend())], {"BTC/USDT": Decimal("100")})
        assert rep.intents
        rec = coord.submit(rep.intents[0])
        agent = rec.intent.reservation.agent_id
        orch.register_fill("BTC/USDT", rec.filled_qty, rec.avg_price, agent,
                           rec.filled_qty * rec.avg_price)
        assert led.attribution() == {}                    # nada realizado ainda
        coord.broker.price = Decimal("130")               # mercado subiu
        out = coord.close_position(instrument="BTC/USDT", quantity=rec.filled_qty,
                                   price=Decimal("130"),
                                   cost_basis=rec.filled_qty * rec.avg_price,
                                   agent_id=agent)
        assert out["estado"] == "encerrada"
        assert Decimal(out["resultado"]) > 0              # lucro atribuído
        assert led.attribution()[agent] > 0
        assert led.is_balanced()

    def test_loss_is_recorded_honestly(self):
        m, risk, led, coord, orch = desk(price="100")
        rep = orch.run_cycle([snap(uptrend())], {"BTC/USDT": Decimal("100")})
        rec = coord.submit(rep.intents[0])
        agent = rec.intent.reservation.agent_id
        coord.broker.price = Decimal("80")                # mercado caiu
        coord.close_position(instrument="BTC/USDT", quantity=rec.filled_qty,
                             price=Decimal("80"),
                             cost_basis=rec.filled_qty * rec.avg_price,
                             agent_id=agent)
        assert led.attribution()[agent] < 0               # prejuízo aparece
        assert led.is_balanced()

    def test_closing_frees_the_instrument_for_a_new_entry(self):
        m, risk, led, coord, orch = desk(price="100")
        rep = orch.run_cycle([snap(uptrend())], {"BTC/USDT": Decimal("100")})
        rec = coord.submit(rep.intents[0])
        orch.register_fill("BTC/USDT", rec.filled_qty, rec.avg_price,
                           rec.intent.reservation.agent_id,
                           rec.filled_qty * rec.avg_price)
        blocked = orch.run_cycle([snap(uptrend())], {"BTC/USDT": Decimal("100")})
        assert not blocked.intents
        coord.close_position(instrument="BTC/USDT", quantity=rec.filled_qty,
                             price=Decimal("130"),
                             cost_basis=rec.filled_qty * rec.avg_price,
                             agent_id=rec.intent.reservation.agent_id)
        orch.close_position("BTC/USDT")
        assert "BTC/USDT" not in orch.book

    def test_exit_is_evaluated_before_new_entries(self):
        m, risk, led, coord, orch = desk(price="100")
        orch.book["BTC/USDT"] = Position("BTC/USDT", Decimal("1"), Decimal("200"),
                                         "trader-trend", Decimal("200"))
        rep = orch.run_cycle([snap(uptrend())], {"BTC/USDT": Decimal("160")})
        assert rep.exits and "stop" in rep.exits[0][2]
        assert "saídas" in rep.summary

    def test_orphan_position_goes_to_guardian(self):
        m, risk, led, coord, orch = desk()
        orch.book["BTC/USDT"] = Position("BTC/USDT", Decimal("1"), Decimal("100"),
                                         "trader-que-sumiu", Decimal("100"))
        rep = orch.run_cycle([snap(uptrend())], {"BTC/USDT": Decimal("160")})
        assert any("guardião" in why for _, _, why in rep.exits)

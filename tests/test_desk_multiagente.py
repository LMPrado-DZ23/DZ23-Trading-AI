"""Cenários de aceite da mesa multiagente (MA-10, subconjunto executável).

Fixtures determinísticas — provam coordenação e invariantes, não desempenho
de mercado.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from tradingagents.desk.models import (Action, AgentStage, DeskMandate, MandateError,
                                       MarketSnapshot, Mode, TradeProposal, utcnow)
from tradingagents.desk.risk import PortfolioAllocator, RiskEngine, RiskVeto
from tradingagents.desk.traders import (BreakoutTrader, MeanReversionTrader,
                                        TrendTrader, default_desk)

UNIVERSE = ("BTC/USDT", "ETH/USDT")


def snap(instrument, closes, quality="ok"):
    return MarketSnapshot(instrument=instrument, timeframe="1h", cutoff=utcnow(),
                          closes=tuple(Decimal(str(c)) for c in closes), quality=quality)


def uptrend(n=40, start=100.0, step=1.5):
    return [start + i * step for i in range(n)]


def crash(n=25):
    return [100.0] * (n - 1) + [70.0]


def breakout(n=30):
    return [100.0 + (i % 3) * 0.2 for i in range(n - 1)] + [130.0]


def mandate(**kw):
    base = dict(mandate_id="m1", owner="prado", account="acct-1", mode=Mode.PAPER,
                instruments=UNIVERSE, capital=Decimal("1000"))
    base.update(kw)
    return DeskMandate(**base)


def enable(traders):
    for t in traders:
        t.spec.move_to(AgentStage.VALIDATED)
        t.spec.move_to(AgentStage.SHADOW)
        t.spec.move_to(AgentStage.PAPER_ENABLED)
    return traders


class TestCenario01MultiplosTraders:
    def test_three_distinct_strategies_react_differently(self):
        traders = enable(default_desk(UNIVERSE))
        assert len({t.strategy for t in traders}) == 3
        s = snap("BTC/USDT", uptrend())
        acts = {t.strategy: t.propose(s).action for t in traders}
        assert acts["trend_following"] is Action.BUY       # tendência clara
        assert acts["mean_reversion"] is Action.NO_TRADE   # sem exagero de queda
        # estratégias distintas ⇒ leituras distintas do MESMO snapshot
        assert len(set(acts.values())) > 1

    def test_two_assets_two_simultaneous_operations(self):
        traders = enable(default_desk(UNIVERSE))
        proposals = [t.propose(s) for s in
                     (snap("BTC/USDT", uptrend()), snap("ETH/USDT", crash()))
                     for t in traders]
        actionable = [p for p in proposals if p.is_actionable()]
        assert {p.instrument for p in actionable} == {"BTC/USDT", "ETH/USDT"}
        eng = RiskEngine(mandate())
        decision = PortfolioAllocator().arbitrate(actionable)
        reserved = [eng.reserve(p, Decimal("100")) for p in decision.admitted]
        assert len(reserved) == 2                      # duas operações elegíveis juntas
        assert eng.reserved_total <= mandate().capital


class TestCenario03DisputaDeCapital:
    def test_two_proposals_same_instrument_do_not_double_reserve(self):
        a = TradeProposal(agent_id="t1", strategy="trend_following", instrument="BTC/USDT",
                          action=Action.BUY, reason="x", desired_exposure_pct=Decimal("0.15"),
                          confidence=Decimal("0.6"), valid_until=utcnow() + timedelta(minutes=5))
        b = TradeProposal(agent_id="t2", strategy="breakout", instrument="BTC/USDT",
                          action=Action.BUY, reason="y", desired_exposure_pct=Decimal("0.12"),
                          confidence=Decimal("0.5"), valid_until=utcnow() + timedelta(minutes=5))
        decision = PortfolioAllocator().arbitrate([a, b])
        assert len(decision.admitted) == 1 and decision.admitted[0].agent_id == "t1"
        assert "venceu a disputa" in decision.vetoed[0][1]
        eng = RiskEngine(mandate())
        eng.reserve(decision.admitted[0], Decimal("100"))
        with pytest.raises(RiskVeto, match="já há reserva"):
            eng.reserve(b, Decimal("100"))

    def test_opposite_directions_are_vetoed_not_executed(self):
        buy = TradeProposal(agent_id="t1", strategy="s1", instrument="BTC/USDT",
                            action=Action.BUY, reason="", desired_exposure_pct=Decimal("0.1"),
                            confidence=Decimal("0.9"), valid_until=utcnow() + timedelta(minutes=5))
        sell = TradeProposal(agent_id="t2", strategy="s2", instrument="BTC/USDT",
                             action=Action.SELL, reason="", desired_exposure_pct=Decimal("0.1"),
                             confidence=Decimal("0.8"), valid_until=utcnow() + timedelta(minutes=5))
        d = PortfolioAllocator().arbitrate([buy, sell])
        assert len(d.admitted) == 1
        assert "direção oposta vetada" in d.vetoed[0][1]

    def test_capital_exhaustion_vetoes_instead_of_overdrawing(self):
        eng = RiskEngine(mandate(capital=Decimal("100"), max_position_pct=Decimal("0.6")))
        p1 = TradeProposal(agent_id="t1", strategy="s", instrument="BTC/USDT", action=Action.BUY,
                           reason="", desired_exposure_pct=Decimal("0.6"),
                           valid_until=utcnow() + timedelta(minutes=5))
        p2 = TradeProposal(agent_id="t2", strategy="s", instrument="ETH/USDT", action=Action.BUY,
                           reason="", desired_exposure_pct=Decimal("0.6"),
                           valid_until=utcnow() + timedelta(minutes=5))
        eng.reserve(p1, Decimal("10"))
        with pytest.raises(RiskVeto, match="capital insuficiente"):
            eng.reserve(p2, Decimal("10"))


class TestCenario04Permissoes:
    def test_agent_cannot_use_tool_outside_its_grant(self):
        t = TrendTrader("t", UNIVERSE)
        assert t.spec.may_use("market_data") and not t.spec.may_use("broker_write")

    def test_unvalidated_agent_cannot_propose(self):
        t = TrendTrader("t", UNIVERSE)  # REGISTERED
        p = t.propose(snap("BTC/USDT", uptrend()))
        assert p.action is Action.NO_TRADE and "não propõe" in p.reason

    def test_stage_transitions_are_authorized_only(self):
        t = TrendTrader("t", UNIVERSE)
        with pytest.raises(ValueError):
            t.spec.move_to(AgentStage.PAPER_ENABLED)  # pulo proibido


class TestCenario06AbstencaoNaoViraOrdem:
    def test_insufficient_data_never_produces_order(self):
        t = enable([TrendTrader("t", UNIVERSE)])[0]
        p = t.propose(snap("BTC/USDT", [100, 101, 102]))
        assert p.action is Action.DATA_INSUFFICIENT and not p.is_actionable()
        with pytest.raises(RiskVeto):
            RiskEngine(mandate()).reserve(p, Decimal("100"))

    def test_degraded_quality_blocks_proposal(self):
        t = enable([MeanReversionTrader("t", UNIVERSE)])[0]
        p = t.propose(snap("BTC/USDT", crash(), quality="degraded"))
        assert p.action is Action.DATA_INSUFFICIENT

    def test_no_trade_reason_is_explicit(self):
        t = enable([BreakoutTrader("t", UNIVERSE)])[0]
        p = t.propose(snap("BTC/USDT", [100.0] * 30))
        assert p.action is Action.NO_TRADE and "sem rompimento" in p.reason


class TestCenario07MandatoEIntencao:
    def test_revoked_mandate_blocks_new_sends(self):
        m = mandate()
        eng = RiskEngine(m)
        p = TradeProposal(agent_id="t", strategy="s", instrument="BTC/USDT", action=Action.BUY,
                          reason="", desired_exposure_pct=Decimal("0.1"),
                          valid_until=utcnow() + timedelta(minutes=5))
        res = eng.reserve(p, Decimal("100"))
        intent = eng.intent(res, Action.BUY, Mode.PAPER, "worker-1")
        revoked = mandate(revoked_at=utcnow())
        with pytest.raises(MandateError, match="revogado"):
            intent.assert_sendable(worker_id="worker-1", mandate=revoked)

    def test_expired_intent_and_wrong_worker_are_refused(self):
        m = mandate()
        eng = RiskEngine(m)
        p = TradeProposal(agent_id="t", strategy="s", instrument="BTC/USDT", action=Action.BUY,
                          reason="", desired_exposure_pct=Decimal("0.1"),
                          valid_until=utcnow() + timedelta(minutes=5))
        intent = eng.intent(eng.reserve(p, Decimal("100")), Action.BUY, Mode.PAPER, "worker-1")
        with pytest.raises(MandateError, match="posse"):
            intent.assert_sendable(worker_id="worker-2", mandate=m)
        with pytest.raises(MandateError, match="expirada"):
            intent.assert_sendable(worker_id="worker-1", mandate=m,
                                   now=utcnow() + timedelta(hours=1))

    def test_policy_change_invalidates_reservation(self):
        m = mandate()
        eng = RiskEngine(m)
        p = TradeProposal(agent_id="t", strategy="s", instrument="BTC/USDT", action=Action.BUY,
                          reason="", desired_exposure_pct=Decimal("0.1"),
                          valid_until=utcnow() + timedelta(minutes=5))
        intent = eng.intent(eng.reserve(p, Decimal("100")), Action.BUY, Mode.PAPER, "w1")
        with pytest.raises(MandateError, match="política"):
            intent.assert_sendable(worker_id="w1", mandate=mandate(version=2))

    def test_live_mode_is_refused_by_construction(self):
        with pytest.raises(MandateError, match="LIVE"):
            mandate(mode=Mode.LIVE).assert_active(instrument="BTC/USDT")


class TestCenario09TimeoutNaoLibera:
    def test_release_is_explicit_never_implicit(self):
        eng = RiskEngine(mandate())
        p = TradeProposal(agent_id="t", strategy="s", instrument="BTC/USDT", action=Action.BUY,
                          reason="", desired_exposure_pct=Decimal("0.1"),
                          valid_until=utcnow() + timedelta(minutes=5))
        res = eng.reserve(p, Decimal("100"))
        assert eng.reserved_total > 0
        # nenhuma consulta/timeout libera sozinha; só a liberação explícita
        assert eng.release(res.reservation_id) is True
        assert eng.reserved_total == Decimal("0")
        assert eng.release(res.reservation_id) is False


class TestCenario12PausaNaoAbandonaPosicao:
    def test_paused_trader_stops_proposing_but_reservation_survives(self):
        eng = RiskEngine(mandate())
        t = enable([TrendTrader("t", UNIVERSE)])[0]
        p = t.propose(snap("BTC/USDT", uptrend()))
        res = eng.reserve(p, Decimal("100"))
        t.spec.move_to(AgentStage.PAUSED)
        assert t.propose(snap("BTC/USDT", uptrend())).action is Action.NO_TRADE
        # posição continua sob responsabilidade da mesa (guardião), não some
        assert res.reservation_id in eng._reservations
        assert eng.open_instruments() == {"BTC/USDT"}


class TestCenario02CicloSemComandoHumano:
    """O ciclo roda sozinho a cada evento — nada aqui pede 'faça o próximo trade'."""

    def _desk(self, **kw):
        from tradingagents.desk.orchestrator import DeskOrchestrator
        traders = enable(default_desk(UNIVERSE))
        return DeskOrchestrator(mandate(**kw), traders, worker_id="worker-1")

    def test_cycle_produces_authorized_intents_on_two_assets(self):
        desk = self._desk()
        report = desk.run_cycle(
            [snap("BTC/USDT", uptrend()), snap("ETH/USDT", crash())],
            {"BTC/USDT": Decimal("160"), "ETH/USDT": Decimal("70")})
        assert report.proposals == 6          # 3 traders × 2 ativos
        assert len(report.intents) == 2       # duas operações elegíveis simultâneas
        assert {i.reservation.instrument for i in report.intents} == set(UNIVERSE)
        assert all(i.mode is Mode.PAPER for i in report.intents)

    def test_second_cycle_does_not_duplicate_position(self):
        desk = self._desk()
        prices = {"BTC/USDT": Decimal("160"), "ETH/USDT": Decimal("70")}
        desk.run_cycle([snap("BTC/USDT", uptrend())], prices)
        again = desk.run_cycle([snap("BTC/USDT", uptrend())], prices)
        assert not again.intents
        assert any("posição aberta" in why or "operado por" in why
                   for _, why in again.vetoed)

    def test_revoked_mandate_stops_the_cycle_entirely(self):
        desk = self._desk(revoked_at=utcnow())
        report = desk.run_cycle([snap("BTC/USDT", uptrend())], {"BTC/USDT": Decimal("160")})
        assert not report.intents and "revogado" in report.errors[0]

    def test_broken_trader_does_not_take_the_desk_down(self):
        desk = self._desk()

        class Broken:
            spec = type("S", (), {"agent_id": "broken"})()
            def propose(self, s): raise RuntimeError("boom")

        desk.traders.append(Broken())
        report = desk.run_cycle([snap("BTC/USDT", uptrend())], {"BTC/USDT": Decimal("160")})
        assert report.intents and any("broken" in e for e in report.errors)

    def test_kill_switch_semantics_are_distinct(self):
        desk = self._desk()
        prices = {"BTC/USDT": Decimal("160")}
        desk.run_cycle([snap("BTC/USDT", uptrend())], prices)
        desk.pause_entries()                      # para entradas
        after = desk.run_cycle([snap("BTC/USDT", uptrend())], prices)
        assert after.proposals == 0 and not after.intents
        assert desk.drain() == ["BTC/USDT"]       # saída pedida ≠ saída concluída
        assert desk.risk.open_instruments() == {"BTC/USDT"}

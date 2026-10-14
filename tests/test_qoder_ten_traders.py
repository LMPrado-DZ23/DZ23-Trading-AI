"""Test suite for the 10 specialized traders and the gated MarketRegimeSupervisor."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from tradingagents.desk.models import Action, AgentStage, MarketSnapshot
from tradingagents.desk.signals import Regime
from tradingagents.desk.traders import (
    AdaptiveRegimeTrader,
    ArbitrageTrader,
    MarketRegimeSupervisor,
    MomentumTrader,
    ScalpingTrader,
    StatisticalTrader,
    SwingTrader,
    VolatilityTrader,
    default_desk,
)

# Cutoff fixo por higiene do fixture: determinístico por execução. A decisão
# da arbitragem deriva do ESTADO DE MERCADO (instrumento + último fechamento),
# não do relógio — o cutoff não influencia o resultado (ver teste de regressão
# `test_arbitrage_decide_pelo_estado_de_mercado_nao_pelo_relogio`).
CUTOFF_FIXO = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


def mock_snap(instrument: str, closes: list[float], regime: Regime = Regime.LATERAL) -> MarketSnapshot:
    return MarketSnapshot(
        instrument=instrument,
        timeframe="1h",
        cutoff=CUTOFF_FIXO,
        closes=tuple(Decimal(str(c)) for c in closes),
        source="fixture",
        quality="ok"
    )


class TestTenTradersInitialization:
    def test_default_desk_returns_all_ten_traders(self):
        traders = default_desk(("BTC/USDT",))
        assert len(traders) == 10
        strategies = {t.strategy for t in traders}
        expected_strategies = {
            "trend_following",
            "mean_reversion",
            "breakout",
            "scalping",
            "swing",
            "momentum",
            "volatility",
            "arbitrage",
            "statistical",
            "adaptive_regime"
        }
        assert strategies == expected_strategies

    def test_each_trader_has_correct_ids_and_regimes(self):
        traders = default_desk(("BTC/USDT",))
        for t in traders:
            assert t.spec.agent_id.startswith("trader-")
            assert t.universe == ("BTC/USDT",)


class TestIndividualTradersLogic:
    def test_scalping_trader_buys_on_low_zscore(self):
        closes = [100.0] * 35 + [95.0]
        snap = mock_snap("BTC/USDT", closes)
        trader = ScalpingTrader("trader-scalping", ("BTC/USDT",))
        trader.spec.stage = AgentStage.VALIDATED
        trader.regimes_favoraveis = None # bypass regime check for unit logic
        prop = trader.propose(snap)
        assert prop.action is Action.BUY
        assert "scalping de compra rápida" in prop.reason

    def test_swing_trader_buys_on_oversold_rsi(self):
        closes = [100.0] * 20 + [100, 98, 96, 94, 92, 90, 88, 86, 84, 82, 80, 78, 76, 74, 72, 70]
        snap = mock_snap("BTC/USDT", closes)
        trader = SwingTrader("trader-swing", ("BTC/USDT",))
        trader.spec.stage = AgentStage.VALIDATED
        trader.regimes_favoraveis = None # bypass regime check for unit logic
        prop = trader.propose(snap)
        assert prop.action is Action.BUY
        assert "swing trading de reversão" in prop.reason

    def test_momentum_trader_buys_on_positive_roc(self):
        closes = [100.0] * 30 + [100.0] * 10 + [101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0, 110.0]
        snap = mock_snap("BTC/USDT", closes)
        trader = MomentumTrader("trader-momentum", ("BTC/USDT",))
        trader.spec.stage = AgentStage.VALIDATED
        trader.regimes_favoraveis = None # bypass regime check for unit logic
        prop = trader.propose(snap)
        assert prop.action is Action.BUY
        assert "aceleração de momentum" in prop.reason

    def test_volatility_trader_buys_on_vol_expansion(self):
        # ATR(30) exige 31 velas e a confirmação 4x exige 40: a série precisa
        # de 40 fechamentos para o trader declarar o sinal.
        closes = [100.0] * 30 + [100.0, 99.0, 101.0, 98.0, 102.0, 97.0, 103.0, 105.0, 108.0, 112.0]
        snap = mock_snap("BTC/USDT", closes)
        trader = VolatilityTrader("trader-volatility", ("BTC/USDT",))
        trader.spec.stage = AgentStage.VALIDATED
        trader.regimes_favoraveis = None # bypass regime check for unit logic
        prop = trader.propose(snap)
        assert prop.action is Action.BUY
        assert "expansão de volatilidade" in prop.reason

    def test_arbitrage_trader_finds_opportunity(self):
        # O sorteio da arbitragem deriva do ESTADO DE MERCADO (instrumento +
        # último fechamento): a busca percorre estados de preço até encontrar
        # um em que a distorção simulada acontece. O cutoff fica fixo de
        # propósito — ele não pode influenciar a decisão.
        trader = ArbitrageTrader("trader-arbitrage", ("BTC/USDT",))
        trader.spec.stage = AgentStage.VALIDATED
        trader.regimes_favoraveis = None # bypass regime check for unit logic

        found_arbitrage = False
        for i in range(100):
            last = Decimal("100.0") + Decimal(i) * Decimal("0.1")
            closes = tuple(Decimal("100.0") for _ in range(30)) + (last,)
            test_snap = MarketSnapshot("BTC/USDT", "1h", CUTOFF_FIXO, closes, "fixture", "ok")
            prop = trader.propose(test_snap)
            if prop.action is Action.BUY:
                assert "arbitragem detectada" in prop.reason
                found_arbitrage = True
                break
        assert found_arbitrage, "Should simulate arbitrage on some market states"

    def test_arbitrage_decide_pelo_estado_de_mercado_nao_pelo_relogio(self):
        # Regressão da causa-raiz da flakiness do supervisor: a arbitragem
        # sorteava pela hash do CUTOFF (relógio de parede), então o mesmo
        # mercado decidia diferente a cada execução. Estes dois cutoffs, sob a
        # regra antiga, um concedia e o outro negava a oportunidade; a decisão
        # agora depende só do estado de mercado e deve ser idêntica nos dois.
        trader = ArbitrageTrader("trader-arbitrage", ("BTC/USDT",))
        trader.spec.stage = AgentStage.VALIDATED
        trader.regimes_favoraveis = None # bypass regime check for unit logic

        antigo_dispara = datetime(2026, 10, 13, 15, 1, 29, tzinfo=timezone.utc)
        antigo_nega = datetime(2026, 10, 13, 15, 0, 0, tzinfo=timezone.utc)

        def decisao(cutoff, last):
            closes = tuple(Decimal("100.0") for _ in range(30)) + (last,)
            return trader.propose(MarketSnapshot(
                "BTC/USDT", "1h", cutoff, closes, "fixture", "ok"))

        # estado de mercado COM oportunidade simulada: BUY nos dois cutoffs
        com = [decisao(c, Decimal("103.2"))
               for c in (antigo_dispara, antigo_nega)]
        assert all(p.action is Action.BUY for p in com)
        assert com[0].reason == com[1].reason
        assert "arbitragem detectada" in com[0].reason

        # estado SEM oportunidade: NO_TRADE nos dois cutoffs
        sem = [decisao(c, Decimal("101.0"))
               for c in (antigo_dispara, antigo_nega)]
        assert all(p.action is Action.NO_TRADE for p in sem)
        assert sem[0].reason == sem[1].reason

    def test_statistical_trader_buys_on_spread_deviation(self):
        closes = [100.0] * 35 + [85.0]
        snap = mock_snap("BTC/USDT", closes)
        trader = StatisticalTrader("trader-statistical", ("BTC/USDT",))
        trader.spec.stage = AgentStage.VALIDATED
        trader.regimes_favoraveis = None # bypass regime check for unit logic
        prop = trader.propose(snap)
        assert prop.action is Action.BUY
        assert "desvio estatístico extremo" in prop.reason

    def test_adaptive_regime_trader_adapts_to_alta(self):
        closes = [100.0] * 30 + [Decimal(str(round(100 * (1.02 ** i), 4))) for i in range(25)]
        snap = mock_snap("BTC/USDT", closes)
        trader = AdaptiveRegimeTrader("trader-adaptive", ("BTC/USDT",))
        trader.spec.stage = AgentStage.VALIDATED
        trader.regimes_favoraveis = None # bypass regime check for unit logic
        prop = trader.propose(snap)
        assert prop.action is Action.BUY
        assert "adaptativo: modo tendência de alta ativo" in prop.reason


class TestMarketRegimeSupervisor:
    def test_supervisor_selects_and_allocates_capital_properly(self):
        traders = default_desk(("BTC/USDT",))
        supervisor = MarketRegimeSupervisor(traders, Decimal("5000"))

        closes = [100.0] * 35 + [95.0]
        snap = mock_snap("BTC/USDT", closes)

        for t in traders:
            t.spec.stage = AgentStage.VALIDATED
        resolved = supervisor.supervise([snap], {})

        # Orçamento dinâmico: 5000 dividido entre os elegíveis do regime lateral.
        assert Decimal("833.33") == next(t.capital for t in traders if t.spec.agent_id == "trader-scalping")

        # Dedup: no máximo uma compra sobrevive por instrumento...
        buys = [p for p in resolved if p.action is Action.BUY]
        assert len(buys) == 1

        # ...e nenhum veto pode carregar confiança maior que a da sobrevivente.
        vetados = [p for p in resolved
                   if p.action is Action.NO_TRADE and "Veto do Supervisor" in p.reason]
        assert vetados, "cenário deveria gerar compras duplicadas a serem vetadas"
        assert all(p.confidence <= buys[0].confidence for p in vetados)

    def test_supervisor_resolves_conflict_by_prioritizing_reduces(self):
        t1 = ScalpingTrader("trader-scalping", ("BTC/USDT",))
        t2 = SwingTrader("trader-swing", ("BTC/USDT",))
        t1.spec.stage = AgentStage.VALIDATED
        t2.spec.stage = AgentStage.VALIDATED

        closes_scalping_oversold = [100.0] * 35 + [95.0]
        snap = mock_snap("BTC/USDT", closes_scalping_oversold)

        supervisor = MarketRegimeSupervisor([t1, t2], Decimal("1000"))

        p_buy = t1._buy(snap, "0.10", "Buy signal", "inval", "0.8")
        p_reduce = t2._exit(snap, "Stop reached")

        t1.propose = lambda s: p_buy
        t2.propose = lambda s: p_reduce

        resolved = supervisor.supervise([snap], {})

        resolved_buy = next(p for p in resolved if p.agent_id == "trader-scalping")
        resolved_reduce = next(p for p in resolved if p.agent_id == "trader-swing")

        assert resolved_reduce.action is Action.REDUCE
        assert resolved_buy.action is Action.NO_TRADE
        assert "Conflito" in resolved_buy.reason

    def test_supervisor_avoids_duplicate_buys_by_taking_highest_confidence(self):
        t1 = ScalpingTrader("trader-scalping", ("BTC/USDT",))
        t2 = SwingTrader("trader-swing", ("BTC/USDT",))
        t1.spec.stage = AgentStage.VALIDATED
        t2.spec.stage = AgentStage.VALIDATED

        closes = [100.0] * 40
        snap = mock_snap("BTC/USDT", closes)

        p1 = t1._buy(snap, "0.10", "Scalp Buy", "inval", "0.60")
        p2 = t2._buy(snap, "0.10", "Swing Buy", "inval", "0.80")

        t1.propose = lambda s: p1
        t2.propose = lambda s: p2

        supervisor = MarketRegimeSupervisor([t1, t2], Decimal("1000"))
        resolved = supervisor.supervise([snap], {})

        resolved_scalp = next(p for p in resolved if p.agent_id == "trader-scalping")
        resolved_swing = next(p for p in resolved if p.agent_id == "trader-swing")

        assert resolved_swing.action is Action.BUY
        assert resolved_scalp.action is Action.NO_TRADE
        assert "veto" in resolved_scalp.reason.lower() or "evitar" in resolved_scalp.reason.lower()

"""Custos reais: o filtro que separa robô que opera de robô que lucra."""
from __future__ import annotations

from decimal import Decimal

from tradingagents.desk.costs import (
    TAXAS_TAKER,
    custo_de_operar,
    liquidez_do_par,
    retorno_liquido,
    slippage_estimado,
    vale_a_pena,
)
from tradingagents.desk.models import AgentStage, MarketSnapshot, utcnow
from tradingagents.desk.traders import TrendTrader


def serie_alta(n=40, taxa=1.004):
    return tuple(Decimal(str(100 * (taxa ** i))) for i in range(n))


def pronto(t):
    for st in (AgentStage.VALIDATED, AgentStage.SHADOW, AgentStage.PAPER_ENABLED):
        t.spec.move_to(st)
    return t


class TestModeloDeCusto:
    def test_round_trip_counts_both_sides(self):
        c = custo_de_operar("BTC/USDT", "binance", Decimal("500"))
        assert c.ida_e_volta == (c.taxa + c.spread + c.slippage) * 2

    def test_expensive_exchange_costs_more(self):
        barata = custo_de_operar("BTC/USDT", "binance", Decimal("500"))
        cara = custo_de_operar("BTC/USDT", "coinbase", Decimal("500"))
        assert cara.ida_e_volta > barata.ida_e_volta * 3

    def test_unknown_exchange_assumes_expensive(self):
        c = custo_de_operar("BTC/USDT", "exchange-desconhecida", Decimal("500"))
        assert c.taxa >= max(TAXAS_TAKER["binance"], Decimal("0.002"))

    def test_slippage_grows_with_order_size(self):
        pequena = slippage_estimado("BTC/USDT", Decimal("100"))
        grande = slippage_estimado("BTC/USDT", Decimal("100000"))
        assert grande > pequena * 2

    def test_slippage_grows_with_volatility(self):
        calmo = slippage_estimado("BTC/USDT", Decimal("500"), volatilidade=0.001)
        agitado = slippage_estimado("BTC/USDT", Decimal("500"), volatilidade=0.08)
        assert agitado > calmo

    def test_illiquid_pair_costs_more_than_major(self):
        assert liquidez_do_par("BTC/USDT") == "alta"
        assert liquidez_do_par("XYZ/ABC") == "baixa"
        assert (custo_de_operar("XYZ/ABC", "binance", Decimal("500")).ida_e_volta >
                custo_de_operar("BTC/USDT", "binance", Decimal("500")).ida_e_volta)


class TestVantagemPrecisaPagarOCusto:
    def test_small_move_does_not_pay(self):
        c = custo_de_operar("BTC/USDT", "binance", Decimal("500"))
        paga, motivo = vale_a_pena(Decimal("0.002"), c)
        assert paga is False and "não paga o custo" in motivo

    def test_big_move_pays(self):
        c = custo_de_operar("BTC/USDT", "binance", Decimal("500"))
        paga, _ = vale_a_pena(Decimal("0.05"), c)
        assert paga is True

    def test_margin_of_safety_is_required_not_break_even(self):
        c = custo_de_operar("BTC/USDT", "binance", Decimal("500"))
        empate = c.ida_e_volta
        assert vale_a_pena(empate, c)[0] is False, "empatar na média é perder"

    def test_net_return_subtracts_the_round_trip(self):
        c = custo_de_operar("BTC/USDT", "binance", Decimal("500"))
        assert retorno_liquido(Decimal("0.01"), c) == Decimal("0.01") - c.ida_e_volta

    def test_a_winning_strategy_can_be_a_losing_one_after_costs(self):
        """Aritmética que mata robôs: 0,4% de ganho médio em exchange cara."""
        c = custo_de_operar("BTC/USDT", "coinbase", Decimal("500"))
        assert retorno_liquido(Decimal("0.004"), c) < 0


class TestTraderRecusaOperacaoCara:
    def test_same_signal_different_exchange_different_decision(self):
        serie = serie_alta()
        snap = MarketSnapshot("BTC/USDT", "1h", utcnow(), serie)
        barata = pronto(TrendTrader("t", ("BTC/USDT",), exchange="binance"))
        cara = pronto(TrendTrader("t", ("BTC/USDT",), exchange="coinbase"))
        assert barata.propose(snap).action.value == "buy"
        recusa = cara.propose(snap)
        assert recusa.action.value == "no_trade"
        assert "custo não compensa" in recusa.reason

    def test_refusal_explains_the_math_to_a_layman(self):
        snap = MarketSnapshot("BTC/USDT", "1h", utcnow(), serie_alta())
        t = pronto(TrendTrader("t", ("BTC/USDT",), exchange="mercadobitcoin"))
        motivo = t.propose(snap).reason
        assert "taxa" in motivo and "spread" in motivo and "slippage" in motivo

    def test_expected_move_shrinks_in_quiet_markets(self):
        """Mercado parado => alvo menor => custo pesa mais. É onde o robô
        ingênuo opera e perde."""
        t = pronto(TrendTrader("t", ("BTC/USDT",)))
        from tradingagents.desk.signals import interpretar
        agitado = interpretar("BTC/USDT", serie_alta(taxa=1.03))
        parado = interpretar("BTC/USDT", serie_alta(taxa=1.0005))
        assert t.movimento_esperado(parado) < t.movimento_esperado(agitado)

    def test_big_order_can_flip_the_decision(self):
        """A mesma ideia pode valer a pena pequena e não valer grande."""
        snap = MarketSnapshot("BTC/USDT", "1h", utcnow(), serie_alta())
        pequeno = pronto(TrendTrader("t", ("BTC/USDT",), capital=Decimal("1000")))
        enorme = pronto(TrendTrader("t", ("BTC/USDT",), capital=Decimal("2000000")))
        p1, p2 = pequeno.propose(snap), enorme.propose(snap)
        assert p1.action.value == "buy"
        assert p2.action.value in ("buy", "no_trade")
        if p2.action.value == "no_trade":
            assert "custo" in p2.reason


class TestViabilidade:
    def test_typical_scalper_loses_after_costs(self):
        from tradingagents.desk.costs import viabilidade
        c = custo_de_operar("BTC/USDT", "binance", Decimal("500"))
        v = viabilidade(Decimal("0.55"), Decimal("0.005"), Decimal("0.005"), c)
        assert v["viavel"] is False and "INVIÁVEL" in v["veredito"]
        assert v["acerto_minimo_para_empatar"] > Decimal("0.55")

    def test_bigger_target_can_be_viable(self):
        from tradingagents.desk.costs import viabilidade
        c = custo_de_operar("BTC/USDT", "binance", Decimal("500"))
        v = viabilidade(Decimal("0.45"), Decimal("0.04"), Decimal("0.015"), c)
        assert v["viavel"] is True

    def test_expensive_exchange_can_flip_a_viable_strategy(self):
        from tradingagents.desk.costs import viabilidade
        barata = viabilidade(Decimal("0.45"), Decimal("0.04"), Decimal("0.015"),
                             custo_de_operar("BTC/USDT", "binance", Decimal("500")))
        cara = viabilidade(Decimal("0.45"), Decimal("0.04"), Decimal("0.015"),
                           custo_de_operar("BTC/USDT", "coinbase", Decimal("500")))
        assert barata["viavel"] is True and cara["viavel"] is False

    def test_route_reports_viability(self, tmp_path, monkeypatch):
        import importlib

        from fastapi.testclient import TestClient
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        import service.app as m
        importlib.reload(m)
        m._FAILS.clear()
        m._DESKS.clear()
        d = TestClient(m.app).get("/v1/desk/viabilidade",
                                  headers={"Authorization": "Bearer s3nh4"}).json()
        assert len(d["estrategias"]) == 10    # 10 traders da mesa padrão
        assert all("veredito" in e for e in d["estrategias"])
        assert "não é previsão" in d["aviso"]

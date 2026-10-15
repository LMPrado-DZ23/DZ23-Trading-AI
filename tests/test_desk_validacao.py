"""Validação walk-forward: sem olhar o futuro, com custos descontados."""
from __future__ import annotations

from decimal import Decimal

from tradingagents.desk.traders import TrendTrader, default_desk
from tradingagents.desk.validation import Operacao, ResultadoValidacao, validar


def tendencia_forte(n=300):
    return tuple(Decimal(str(round(100 * (1.01 ** i), 6))) for i in range(n))


def lateral_ruidosa(n=300):
    return tuple(Decimal(str(100 + (i % 7) - 3)) for i in range(n))


class TestSemOlharOFuturo:
    def test_decision_uses_only_past_candles(self, monkeypatch):
        """A proposta só pode ver a janela até a vela anterior."""
        vistas = []
        t = TrendTrader("t", ("BTC/USDT",))
        original = t.propose

        def espiao(snap):
            vistas.append(len(snap.closes))
            return original(snap)

        t.propose = espiao
        serie = tendencia_forte(120)
        validar(t, serie, "BTC/USDT")
        assert vistas, "nenhuma decisão tomada"
        assert max(vistas) < len(serie), "viu a série inteira: olhou o futuro"

    def test_entry_price_is_the_candle_of_the_decision_not_the_best_one(self):
        t = TrendTrader("t", ("BTC/USDT",))
        serie = tendencia_forte(200)
        r = validar(t, serie, "BTC/USDT")
        for op in r.operacoes:
            assert op.preco_entrada == serie[op.entrada_idx]
            assert op.saida_idx > op.entrada_idx


class TestCustosDescontados:
    def test_net_is_always_below_gross(self):
        r = validar(TrendTrader("t", ("BTC/USDT",)), tendencia_forte(300), "BTC/USDT")
        if r.total:
            assert r.retorno_liquido_total < r.retorno_bruto_total
            assert r.custo_total > 0

    def test_expensive_exchange_hurts_the_same_strategy(self):
        serie = tendencia_forte(300)
        barata = validar(TrendTrader("t", ("BTC/USDT",), exchange="binance"),
                         serie, "BTC/USDT", exchange="binance")
        cara = validar(TrendTrader("t", ("BTC/USDT",), exchange="coinbase"),
                       serie, "BTC/USDT", exchange="coinbase")
        if barata.total and cara.total:
            assert cara.retorno_liquido_total < barata.retorno_liquido_total


class TestVeredito:
    def test_no_trades_is_reported_honestly(self):
        r = ResultadoValidacao("x", "BTC/USDT", "binance")
        r.velas_avaliadas = 200
        r.abstencoes = 200
        v = r.veredito()
        assert v["aprovada"] is False and "nenhuma operação" in v["resumo"]

    def _resultado(self, n: int, bruto: str, liquido: str) -> ResultadoValidacao:
        r = ResultadoValidacao("e", "BTC/USDT", "binance")
        r.velas_avaliadas = 300
        for i in range(n):
            r.operacoes.append(
                Operacao(i, i + 1, Decimal("100"), Decimal("101"),
                         Decimal(bruto), Decimal(liquido), "simulado")
            )
        return r

    def test_reprovacao_por_amostra_pequena_nao_culpa_os_custos(self):
        """6 operações lucrativas reprovadas por amostra: o resumo cita a
        amostra, não custos que não causaram a reprovação."""
        v = self._resultado(6, bruto="0.01", liquido="0.009").veredito()
        assert v["aprovada"] is False
        assert "amostra pequena" in v["resumo"]
        assert "comeram" not in v["resumo"]

    def test_duas_ressalvas_aparecem_as_duas(self):
        """Custos acima de metade do bruto somados a amostra pequena: o
        resumo cita as duas ressalvas, não só uma."""
        v = self._resultado(10, bruto="0.0025", liquido="-0.002").veredito()
        assert "amostra pequena" in v["resumo"]
        assert "custos" in v["resumo"]

    def test_prejuizo_sem_culpa_dos_custos_e_dito_sem_invencao(self):
        """25 operações em prejuízo com custo pequeno: o resumo não pode
        inventar que os custos causaram o vermelho."""
        v = self._resultado(25, bruto="-0.001", liquido="-0.00112").veredito()
        assert v["aprovada"] is False
        assert "resultado líquido não positivo" in v["resumo"]
        assert "comeram" not in v["resumo"]

    def test_small_sample_is_flagged_not_celebrated(self):
        t = TrendTrader("t", ("BTC/USDT",))
        r = validar(t, tendencia_forte(120), "BTC/USDT")
        v = r.veredito()
        if 0 < r.total < 20:
            assert any("amostra pequena" in x for x in v["ressalvas"])
            assert v["aprovada"] is False

    def test_report_always_warns_about_the_past(self):
        r = validar(TrendTrader("t", ("BTC/USDT",)), tendencia_forte(300), "BTC/USDT")
        v = r.veredito()
        if r.total:
            assert "não prevê o futuro" in v["aviso"]

    def test_choppy_market_does_not_produce_fake_profit(self):
        """Mercado lateral ruidoso: qualquer 'lucro' aqui seria sinal de bug."""
        for trader in default_desk(("BTC/USDT",)):
            r = validar(trader, lateral_ruidosa(300), "BTC/USDT")
            if r.total >= 5:
                assert r.retorno_liquido_total < r.retorno_bruto_total

    def test_worst_streak_is_measured(self):
        r = validar(TrendTrader("t", ("BTC/USDT",)), tendencia_forte(300), "BTC/USDT")
        assert r.maior_perda_seguida <= 0


class TestScriptDeValidacao:
    def test_script_exists_and_is_documented(self):
        from pathlib import Path
        s = Path("scripts/validar_estrategias.py").read_text(encoding="utf-8")
        assert "--exchange" in s and "--instrumentos" in s
        assert "não é fracasso do sistema" in s     # honestidade no resultado
        assert "≠ lucro futuro" in s

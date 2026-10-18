"""Validação walk-forward: sem olhar o futuro, com custos descontados."""
from __future__ import annotations

from decimal import Decimal

import pytest

from tradingagents.desk.traders import TrendTrader, default_desk
from tradingagents.desk.validation import (
    JanelaValidacao,
    Operacao,
    ResultadoValidacao,
    consolidar_walk_forward,
    validar,
    validar_em_walk_forward,
    veredito_walk_forward,
)


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

    def test_faixa_oos_usa_passado_mas_so_abre_no_teste(self):
        serie = tendencia_forte(300)
        r = validar(TrendTrader("t", ("BTC/USDT",)), serie, "BTC/USDT",
                    inicio_avaliacao=180, fim_avaliacao=260)
        assert r.velas_avaliadas <= 80
        assert all(180 <= op.entrada_idx < op.saida_idx < 260
                   for op in r.operacoes)

    def test_walk_forward_tem_janelas_oos_nao_sobrepostas(self):
        serie = tendencia_forte(500)
        janelas = validar_em_walk_forward(
            lambda: TrendTrader("t", ("BTC/USDT",)), serie, "BTC/USDT",
            train_size=200, test_size=50, embargo=30)
        assert len(janelas) == 5
        for anterior, atual in zip(janelas, janelas[1:], strict=False):
            assert anterior.test_end <= atual.test_start
        for janela in janelas:
            assert all(janela.test_start <= op.entrada_idx < op.saida_idx
                       < janela.test_end for op in janela.resultado.operacoes)
        consolidado = consolidar_walk_forward(janelas)
        assert consolidado.total == sum(
            janela.resultado.total for janela in janelas)

    def test_consolidacao_recusa_janela_oos_repetida_ou_sobreposta(self):
        resultado = ResultadoValidacao("e", "BTC/USDT", "binance")
        primeira = JanelaValidacao(0, 10, 10, 20, resultado)
        repetida = JanelaValidacao(0, 10, 10, 20, resultado)
        sobreposta = JanelaValidacao(5, 15, 15, 25, resultado)
        with pytest.raises(ValueError, match="duplicadas ou sobrepostas"):
            consolidar_walk_forward([primeira, repetida])
        with pytest.raises(ValueError, match="duplicadas ou sobrepostas"):
            consolidar_walk_forward([primeira, sobreposta])

    def test_consolidacao_preserva_janelas_oos_contiguas(self):
        resultado = ResultadoValidacao("e", "BTC/USDT", "binance")
        janelas = [
            JanelaValidacao(0, 10, 10, 20, resultado),
            JanelaValidacao(10, 20, 20, 30, resultado),
        ]
        assert consolidar_walk_forward(janelas).total == 0

    def test_posicao_aberta_e_marcada_a_mercado_no_fim_da_janela(self):
        class NuncaSai:
            @staticmethod
            def avaliar(_variacao, _velas):
                return False, ""

        trader = TrendTrader("t", ("BTC/USDT",))
        trader.exit_policy = NuncaSai()
        resultado = validar(
            trader, tendencia_forte(220), "BTC/USDT",
            inicio_avaliacao=180, fim_avaliacao=220)
        assert resultado.operacoes
        assert resultado.operacoes[-1].motivo_saida == "fim_da_janela_oos"
        assert resultado.operacoes[-1].saida_idx == 219


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
        assert v["aprovada"] is False

    def test_resultado_inconclusivo_por_custos_nao_pode_ser_aprovado(self):
        v = self._resultado(25, bruto="0.0025", liquido="0.001").veredito()
        assert v["retorno_liquido"] == "2.50%"
        assert v["conclusivo"] is False
        assert v["aprovada"] is False

    def test_lucro_agregado_instavel_entre_janelas_e_reprovado(self):
        janelas = []
        for indice in range(5):
            if indice < 2:
                resultado = self._resultado(5, bruto="0.05", liquido="0.049")
            else:
                resultado = self._resultado(5, bruto="-0.001", liquido="-0.0011")
            janelas.append(JanelaValidacao(
                indice * 100, indice * 100 + 50,
                indice * 100 + 50, indice * 100 + 100, resultado))
        v = veredito_walk_forward(janelas)
        assert not v["retorno_liquido"].startswith("-")
        assert v["aprovada"] is False
        assert v["conclusivo"] is False
        assert v["janelas_positivas"] == 2
        assert any("instável" in item for item in v["ressalvas"])

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
        assert "historical_snapshot" in s
        assert "validar_em_walk_forward" in s
        assert "--saida-json" in s
        assert "não é fracasso do sistema" in s     # honestidade no resultado
        assert "não garante lucro futuro" in s

    def test_script_imprime_no_terminal_padrao_do_windows(self):
        """O console cp1252 não pode abortar a rodada no primeiro aviso."""
        from pathlib import Path
        s = Path("scripts/validar_estrategias.py").read_text(encoding="utf-8")
        s.encode("cp1252")

    def test_evidencia_publica_nao_contem_credenciais(self):
        import json
        from pathlib import Path

        caminho = Path(
            "docs/mission/evidence/real-readiness/"
            "binance-btc-eth-1h-oos.json")
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        assert dados["schema"] == "dz23-validacao-oos-v1"
        assert all(item["qualidade"] == "ok" for item in dados["instrumentos"])
        texto = caminho.read_text(encoding="utf-8").lower()
        assert "api_key" not in texto
        assert "api_secret" not in texto
        assert "password" not in texto

"""V9-QUANT-05 — o experimento precisa ser comparável e reprodutível.

Achado do baseline: `desk/evolucao.py` comparava champion e challenger em
horizontes diferentes, aplicava o custo de UM instrumento sobre as operações
de TODOS, não cobrava custo do benchmark e não deixava rastro do dado, do
código nem das janelas usados — o mesmo "aprovado" podia sair de réguas
diferentes, irreproduzíveis.

Contrato testado:
- o executável vê apenas o passado (`closes[:i+1]`) — nunca a série inteira;
- champion e challenger são medidos no MESMO horizonte (o maior dos dois);
- a métrica de custos soma o custo REAL por instrumento e o benchmark também
  paga o custo da rodada;
- o veredito registra o que decide a reprodutibilidade: hash das séries,
  revisão do código, grade de janelas efetivamente usada e custos aplicados.
"""
from __future__ import annotations

import re

import pytest

from tradingagents.desk import evolucao as evolucao_mod
from tradingagents.desk.evolucao import EvolucaoAutonoma, Experimento
from tradingagents.desk.laboratorio import Hipotese

SINAL = 250


def _serie(n: int = 300) -> list[float]:
    """De lado na primeira parte; sobe firme na região de teste."""
    lado = [100.0, 99.7, 100.0, 100.3, 99.8]
    valores: list[float] = []
    for i in range(n):
        if i < 180:
            valores.append(lado[i % 5])
        else:
            valores.append(round(valores[-1] * 1.004, 6))
    return valores


def _h(regra: str, *, horizonte: int = 2, marca: int = 1) -> Hipotese:
    return Hipotese(id=f"H-{marca}", titulo="espião de prefixo",
                    familia="espionagem", tipo_de_regra=regra,
                    parametros=(("marca", marca),),
                    instrumentos=("BTC/USDT",),
                    janela_treino_barras=100, janela_teste_barras=120,
                    horizonte_barras=horizonte)


def _exp(champion: Hipotese, challenger: Hipotese) -> Experimento:
    return Experimento(id="X-005", trader="trader-espiao",
                       champion=champion, challenger=challenger,
                       familia="espionagem")


class _CustoFalso:
    def __init__(self, taxa: float):
        self.ida_e_volta = taxa


def _custos_falsos(taxas: dict[str, float]):
    def fabrica(instrumento, exchange, valor_ordem, volatilidade=None):
        return _CustoFalso(taxas[instrumento])
    return fabrica


def _espiao_que_ve_tudo(sinais: set[int], vistos: list[tuple[int, int]]):
    """Executor de teste: registra (len(closes), i) de cada chamada."""
    def executor(closes, i, params):
        vistos.append((len(closes), i))
        return 1 if i in sinais else 0
    return executor


def _registrar(monkeypatch, espiao) -> None:
    monkeypatch.setitem(evolucao_mod.EXECUTORES, "espiao_v9", espiao)


class TestExecucaoCausal:
    def test_executor_ve_apenas_o_prefixo_causal(self, monkeypatch):
        vistos: list[tuple[int, int]] = []
        _registrar(monkeypatch, _espiao_que_ve_tudo({SINAL}, vistos))
        evo = EvolucaoAutonoma()
        exp = _exp(_h("espiao_v9", marca=1), _h("espiao_v9", marca=2))
        evo.registrar_experimento(exp)
        v = evo.executar_experimento(exp, {"BTC/USDT": _serie()})
        assert vistos
        assert all(comprimento == i + 1 for comprimento, i in vistos)
        assert vistos[0] == (181, 180)  # primeira entrada: janela começa em 100
        assert v.champion.operacoes == 1
        assert v.challenger.operacoes == 1


class TestHorizonteComum:
    def _esperado(self, closes: list[float], h: int, taxa: float) -> float:
        return round((closes[290 + h] - closes[290]) / closes[290] - taxa, 6)

    def _roda(self, monkeypatch, h_c: int, h_l: int):
        _registrar(monkeypatch, _espiao_que_ve_tudo({290}, []))
        monkeypatch.setattr(evolucao_mod, "custo_de_operar",
                            _custos_falsos({"BTC/USDT": 0.001}))
        evo = EvolucaoAutonoma()
        exp = _exp(_h("espiao_v9", horizonte=h_c, marca=1),
                   _h("espiao_v9", horizonte=h_l, marca=2))
        evo.registrar_experimento(exp)
        return evo.executar_experimento(exp, {"BTC/USDT": _serie()})

    def test_lados_sao_medidos_no_maior_horizonte(self, monkeypatch):
        closes = _serie()
        v = self._roda(monkeypatch, 2, 5)
        assert v.horizonte_efetivo == 5
        esperado = self._esperado(closes, 5, 0.001)
        assert v.champion.expectativa_liquida == pytest.approx(
            esperado, abs=1e-6)
        assert (v.champion.expectativa_liquida
                == v.challenger.expectativa_liquida)
        assert v.champion.operacoes == v.challenger.operacoes == 1

    def test_ordem_dos_horizontes_nao_muda_a_regua(self, monkeypatch):
        closes = _serie()
        v = self._roda(monkeypatch, 5, 2)
        assert v.horizonte_efetivo == 5
        esperado = self._esperado(closes, 5, 0.001)
        assert v.challenger.expectativa_liquida == pytest.approx(
            esperado, abs=1e-6)
        assert (v.champion.expectativa_liquida
                == v.challenger.expectativa_liquida)


class TestCustosComparaveis:
    def _dois_instrumentos(self, monkeypatch):
        _registrar(monkeypatch, _espiao_que_ve_tudo({290}, []))
        monkeypatch.setattr(
            evolucao_mod, "custo_de_operar",
            _custos_falsos({"AAA/USDT": 0.001, "BBB/USDT": 0.002}))
        evo = EvolucaoAutonoma()
        exp = _exp(_h("espiao_v9", horizonte=2, marca=1),
                   _h("espiao_v9", horizonte=5, marca=2))
        evo.registrar_experimento(exp)
        serie = _serie()
        return evo.executar_experimento(
            exp, {"AAA/USDT": serie, "BBB/USDT": list(serie)})

    def test_custos_somam_o_custo_real_de_cada_instrumento(self, monkeypatch):
        v = self._dois_instrumentos(monkeypatch)
        assert v.champion.operacoes == 2
        assert v.challenger.operacoes == 2
        assert v.challenger.custos == pytest.approx(0.001 + 0.002)
        assert v.champion.custos == pytest.approx(0.001 + 0.002)

    def test_benchmark_tambem_paga_o_custo_da_janela(self, monkeypatch):
        v = self._dois_instrumentos(monkeypatch)
        por_instrumento = v.janelas // 2
        assert v.benchmark.operacoes == v.janelas
        assert v.benchmark.custos == pytest.approx(
            0.001 * por_instrumento + 0.002 * por_instrumento)
        assert v.benchmark.custos > 0


class TestMetadadosCongelados:
    def _roda(self, monkeypatch, series, *, janelas=4):
        _registrar(monkeypatch, _espiao_que_ve_tudo({250}, []))
        taxas = dict.fromkeys(series, 0.001)
        monkeypatch.setattr(
            evolucao_mod, "custo_de_operar", _custos_falsos(taxas))
        evo = EvolucaoAutonoma()
        exp = _exp(_h("espiao_v9", horizonte=2, marca=1),
                   _h("espiao_v9", horizonte=2, marca=2))
        evo.registrar_experimento(exp)
        return evo.executar_experimento(exp, series, janelas=janelas)

    def test_veredito_registra_dado_codigo_janelas_e_custos(self, monkeypatch):
        serie = _serie()
        v = self._roda(monkeypatch, {"BTC/USDT": serie},
                       janelas=1)
        assert re.fullmatch(r"[0-9a-f]{64}", v.hash_das_series)
        assert re.fullmatch(r"[0-9a-f]{64}", v.code_revision)
        assert v.custos_aplicados == "BTC/USDT:0.00100000"
        assert v.janelas == 1
        # grade congelada: entrada i vê até i+2; fim = 300 - 2 = 298
        assert v.grade_de_janelas == (("BTC/USDT", 180, 298),)

    def test_grade_reflete_as_janelas_pedidas(self, monkeypatch):
        v = self._roda(monkeypatch, {"BTC/USDT": _serie()}, janelas=2)
        assert v.janelas == 2
        assert v.grade_de_janelas == (("BTC/USDT", 180, 239),
                                      ("BTC/USDT", 239, 298))

    def test_hash_das_series_ignora_ordem_e_muda_com_o_dado(
            self, monkeypatch):
        serie = _serie()
        ab = {"AAA/USDT": serie, "BBB/USDT": list(serie)}
        ba = {"BBB/USDT": list(serie), "AAA/USDT": list(serie)}
        v_ab = self._roda(monkeypatch, ab)
        assert v_ab.custos_aplicados == ("AAA/USDT:0.00100000;"
                                         "BBB/USDT:0.00100000")
        assert v_ab.janelas == 8
        assert len(v_ab.grade_de_janelas) == 8
        assert v_ab.code_revision  # código identificado, não vazio
        v_ba = self._roda(monkeypatch, ba)
        assert v_ba.hash_das_series == v_ab.hash_das_series
        assert v_ba.code_revision == v_ab.code_revision
        modificada = list(serie)
        modificada[-2] = modificada[-2] * 1.05
        v_mod = self._roda(monkeypatch,
                           {"AAA/USDT": serie, "BBB/USDT": modificada})
        assert v_mod.hash_das_series != v_ab.hash_das_series

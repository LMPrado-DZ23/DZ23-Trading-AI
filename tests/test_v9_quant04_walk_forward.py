"""V9-QUANT-04 — embargo no walk-forward, lookahead fail-closed e executor
que só vê o prefixo.

Três defeitos apontados pela auditoria V9 (RISCO/alta) nos módulos de
pesquisa da Escola:

1. `windows`/`run_walk_forward` não ofereciam embargo entre treino e teste:
   sem essa separação, o fim da janela de treino cola no início do teste e a
   medição carrega o resíduo do treino. O caminho honesto exige a opção e o
   registro do que ela consome da amostra.
2. `detectar_lookahead` devolvia `ok=True` quando NENHUM corte era
   aplicável: "sem evidência" virava "sem vazamento" — aprovação por
   ausência de prova. O veredito honesto é fail-closed.
3. `Laboratorio.executar` entregava a série COMPLETA ao executor, embora o
   contrato do módulo prometesse `serie[:i+1]`. Promessa que só a convenção
   sustenta não vale; precisa valer por construção.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tradingagents.desk import laboratorio as lab_mod
from tradingagents.desk.laboratorio import Hipotese, Laboratorio
from tradingagents.research import (
    BacktestConfig,
    Bar,
    run_backtest,
    run_walk_forward,
    windows,
)
from tradingagents.research.lookahead import detectar_lookahead

BASE = datetime(2025, 1, 1, tzinfo=timezone.utc)


def bars(n=40):
    return tuple(
        Bar(BASE + timedelta(days=i), BASE + timedelta(days=i),
            Decimal(100 + i), Decimal(101 + i), Decimal(99 + i),
            Decimal(100 + i), Decimal(10))
        for i in range(n))


def _saw(n: int) -> list[float]:
    """Serie periódica [100..98..100] — preços válidos para o laboratório."""
    padrao = [100.0, 99.5, 99.0, 98.5, 98.0, 98.5, 99.0, 99.5]
    return [padrao[i % 8] for i in range(n)]


class TestEmbargoNasJanelas:
    def test_embargo_separa_fim_do_treino_do_inicio_do_teste(self):
        janelas = windows(40, 20, 5, embargo=3)
        assert janelas, "o embargo não pode esvaziar amostra que cabe"
        for j in janelas:
            assert j.test_start == j.train_end + 3

    def test_embargo_zero_preserva_o_comportamento_legado(self):
        assert windows(40, 20, 5) == windows(40, 20, 5, embargo=0)

    def test_embargo_negativo_e_recusado(self):
        with pytest.raises(ValueError):
            windows(40, 20, 5, embargo=-1)

    def test_embargo_consome_amostra(self):
        sem = windows(40, 20, 5)
        com = windows(40, 20, 5, embargo=13)
        assert len(sem) == 4
        assert len(com) == 1

    def test_amostra_sem_espaco_para_o_embargo_produz_nada(self):
        assert windows(30, 20, 5, embargo=6) == ()


class TestWalkForwardComEmbargo:
    def test_sinal_do_walk_forward_so_ve_passado_estrito(self):
        vistos = []

        def espiao(history):
            vistos.append(len(history))
            return 0

        run_walk_forward(bars(40), 20, 5, espiao, BacktestConfig())
        assert vistos == [1, 2, 3, 4] * 4

    def test_embargo_atravessa_da_janela_ate_os_dados(self):
        b = bars(40)
        cfg = BacktestConfig()
        resultados = run_walk_forward(b, 20, 5, lambda history: 0, cfg,
                                      embargo=3)
        janelas = windows(40, 20, 5, embargo=3)
        assert len(janelas) == 3
        assert len(resultados) == len(janelas)
        for r, j in zip(resultados, janelas, strict=True):
            assert j.test_start == j.train_end + 3
            esperado = run_backtest(b[j.test_start:j.test_end],
                                    lambda history: 0, cfg)
            assert r.dataset_hash == esperado.dataset_hash


class TestLookaheadFailClosed:
    def test_sem_corte_aplicavel_nao_aprova(self):
        r = detectar_lookahead(bars(2), lambda b: lambda h: 0)
        assert r["cortes"] == []
        assert r["ok"] is False
        assert "sem corte aplicável" in r["aviso"]

    def test_corte_vazio_explicito_tambem_nao_aprova(self):
        r = detectar_lookahead(bars(10), lambda b: lambda h: 0, cortes=())
        assert r["ok"] is False

    def test_com_corte_real_o_veredito_continua_normal(self):
        def causal(b):
            def sinal(h):
                return 1 if len(h) >= 2 and h[-1].close > h[-2].close else 0
            return sinal

        r = detectar_lookahead(bars(20), causal, cortes=(10,))
        assert r["ok"] is True
        assert r["vazamentos"] == []


class TestExecutorSoVePrefixo:
    def test_executor_do_laboratorio_recebe_apenas_o_prefixo(self, monkeypatch):
        comprimentos: list[tuple[int, int]] = []

        def espiao(serie, i, params):
            comprimentos.append((len(serie), i))
            return 0.0

        monkeypatch.setitem(lab_mod.EXECUTORES, "espiao_v9", espiao)
        lab = Laboratorio()
        h = Hipotese(
            id="H-quant04", titulo="spy de prefixo", familia="spy",
            tipo_de_regra="espiao_v9",
            parametros=(("janela", 3),),
            instrumentos=("BTC/USDT",),
            janela_treino_barras=10, janela_teste_barras=5,
            horizonte_barras=2)
        lab.registrar(h)
        lab.executar(h, {"BTC/USDT": _saw(30)})

        assert comprimentos == [(26, 25), (27, 26), (28, 27)]
        for tamanho, i in comprimentos:
            assert tamanho == i + 1, (
                "o executor viu além do prefixo `serie[:i+1]` prometido "
                "pelo contrato do módulo")

"""Métricas §8 da formação + bloqueio por instabilidade temporal (FQ-1/B).

A Escola Central não pode formar estratégia cuja prova final passou no IC
por concentração num único período: além do IC contra o alvo, as duas
metades temporais da prova precisam ser positivas. As demais métricas §8
(expectativa líquida, fator de lucro, drawdown, retorno ajustado ao risco,
risco de cauda, regime) tornam o julgamento auditável — medição honesta,
nunca veredito decorativo.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tradingagents.desk.escola import (
    AMOSTRA_PROVA,
    EscolaDeTraders,
    EstadoDaEscola,
    PerfilDeMercado,
    metricas_de_formacao,
)
from tradingagents.desk.learning import Desfecho

BASE = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
SPOT = PerfilDeMercado("spot")
CANDIDATA = EstadoDaEscola.CANDIDATA
FORMADA = EstadoDaEscola.FORMADA
PROVA_FINAL = EstadoDaEscola.PROVA_FINAL


def _desfechos(estrategia, retornos, *, inicio=BASE, regime="tendencia"):
    return [
        Desfecho(estrategia=estrategia, regime=regime,
                 instrumento="BTC/USDT", retorno=Decimal(str(r)),
                 fechado_em=inicio + timedelta(minutes=i))
        for i, r in enumerate(retornos)
    ]


def _alternando(a, b, n):
    return [a if i % 2 == 0 else b for i in range(n)]


def _positivos(n):
    return _alternando(0.010, 0.012, n)


def _instavel(n=AMOSTRA_PROVA):
    """Rende no primeiro período e perde devagar no segundo: o IC da prova
    inteira ainda fecha acima do alvo, mas as metades não são ambas
    positivas — o caso que a formação precisa recusar."""
    metade = n // 2
    return [0.012] * metade + _alternando(-0.004, -0.005, n - metade)


class TestMetricasDeFormacao:
    def test_mede_expectativa_fator_e_drawdown(self):
        d = _desfechos("e1", [0.02, -0.01, 0.02, -0.01])
        m = metricas_de_formacao(d, agora=BASE + timedelta(minutes=10))
        assert m["amostras"] == 4
        assert m["expectativa_liquida"] == pytest.approx(0.005, rel=1e-3)
        assert m["fator_de_lucro"] == pytest.approx(2.0, rel=1e-3)
        assert m["max_drawdown"] == pytest.approx(0.01 / 1.02, rel=1e-3)
        assert m["estabilidade"] == "estavel"
        assert m["unidade"] == "retorno_liquido"

    def test_metades_mistas_sao_instaveis(self):
        d = _desfechos("e1", _instavel(100))
        m = metricas_de_formacao(d, agora=BASE + timedelta(minutes=200))
        assert m["estabilidade"] == "instavel"
        assert m["metades"][0] > 0 > m["metades"][1]

    def test_sem_perdas_fator_indefinido_e_nao_infinito(self):
        m = metricas_de_formacao(_desfechos("e1", [0.01, 0.02]),
                                 agora=BASE + timedelta(minutes=5))
        assert m["fator_de_lucro"] is None

    def test_amostra_vazia_nao_inventa(self):
        m = metricas_de_formacao([], agora=BASE)
        assert m["amostras"] == 0
        assert m["fator_de_lucro"] is None
        assert m["estabilidade"] == "indeterminada"

    def test_um_desfecho_indetermina_estabilidade(self):
        m = metricas_de_formacao(_desfechos("e1", [0.01]),
                                 agora=BASE + timedelta(minutes=1))
        assert m["estabilidade"] == "indeterminada"

    def test_por_regime_separa_medias(self):
        d = (_desfechos("e1", [0.02, 0.02], regime="tendencia")
             + _desfechos("e1", [-0.01, -0.01], regime="lateral",
                          inicio=BASE + timedelta(minutes=10)))
        m = metricas_de_formacao(d, agora=BASE + timedelta(minutes=20))
        assert m["por_regime"]["tendencia"]["media"] == pytest.approx(
            0.02, rel=1e-3)
        assert m["por_regime"]["lateral"]["media"] == pytest.approx(
            -0.01, rel=1e-3)


class TestBloqueioPorInstabilidade:
    def _caminho_ate_prova(self, esc, prova_retornos):
        d1 = _desfechos("trader-x", _positivos(220), inicio=BASE)
        t1 = BASE + timedelta(days=30)
        av = esc.avaliar(estrategia="trader-x", desfechos=d1, perfil=SPOT,
                         agora=t1)
        assert av.estado is CANDIDATA
        prova = _desfechos("trader-x", prova_retornos,
                           inicio=t1 + timedelta(hours=1))
        t2 = t1 + timedelta(days=90)
        return d1, prova, t2

    def test_ic_aprovado_com_metades_mistas_nao_forma(self):
        esc = EscolaDeTraders()
        d1, prova, t2 = self._caminho_ate_prova(esc, _instavel())
        av = esc.avaliar(estrategia="trader-x", desfechos=d1 + prova,
                         perfil=SPOT, agora=t2)
        assert av.estado is PROVA_FINAL
        assert av.aprovada is True          # o IC passou...
        assert av.estabilidade == "instavel"  # ...mas a estabilidade segurou
        assert av.base_das_metricas == "prova_final", (
            "na prova, o número reportado é o MESMO que decide a formação")
        assert "BLOQUEADA" in av.motivo
        assert av.max_drawdown > 0
        ok, _ = esc.elegivel_para_live("trader-x")
        assert ok is False

    def test_bloqueio_nao_e_terminal_evidencia_boa_forma(self):
        esc = EscolaDeTraders()
        d1, prova, t2 = self._caminho_ate_prova(esc, _instavel())
        av = esc.avaliar(estrategia="trader-x", desfechos=d1 + prova,
                         perfil=SPOT, agora=t2)
        assert av.estado is PROVA_FINAL
        # mais evidência boa: as duas metades ficam positivas e a formação
        # acontece — bloqueio é gate, não sentença
        extra = _desfechos("trader-x", [0.012] * 150,
                           inicio=t2 + timedelta(hours=1))
        t3 = t2 + timedelta(days=30)
        av = esc.avaliar(estrategia="trader-x", desfechos=d1 + prova + extra,
                         perfil=SPOT, agora=t3)
        assert av.estado is FORMADA
        assert av.estabilidade == "estavel"

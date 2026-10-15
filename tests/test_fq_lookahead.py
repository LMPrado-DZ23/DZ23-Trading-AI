"""Detecção de lookahead (FQ-1/E). A decisão de uma barra anterior a um
corte não pode MUDAR quando existem dados posteriores ao corte — se muda,
o pipeline leu o futuro (normalização pelo máximo da amostra, z-score
global, cache por tamanho) e o backtest vira falsa aprovação.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tradingagents.desk import signals
from tradingagents.research import Bar
from tradingagents.research.lookahead import detectar_lookahead

BASE = datetime(2025, 1, 1, tzinfo=timezone.utc)


def serie(n: int, preco=lambda i: 100 + i) -> tuple[Bar, ...]:
    def b(i):
        d = Decimal(str(preco(i)))
        t = BASE + timedelta(days=i)
        return Bar(t, t, d, d + 1, d - 1, d, Decimal("10"))
    return tuple(b(i) for i in range(n))


def fabrica_causal(bars):
    def sinal(history):
        if len(history) < 2:
            return 0
        return 1 if history[-1].close > history[-2].close else 0
    return sinal


def fabrica_vazada(bars):
    """Normaliza pelo MÁXIMO DO DATASET INTEIRO: a decisão de barras antigas
    muda quando existem barras futuras — vazamento clássico."""
    maximo = max(bar.close for bar in bars)

    def sinal(history):
        return 1 if history[-1].close >= maximo - Decimal("1") else 0
    return sinal


def fabrica_com_indicador(bars):
    def sinal(history):
        valores = [float(bar.close) for bar in history]
        m = signals.sma(valores, 10)
        return 1 if m is not None and valores[-1] > m else 0
    return sinal


def test_pipeline_causal_nao_acusa_vazamento():
    r = detectar_lookahead(serie(40), fabrica_causal, cortes=(15, 30))
    assert r["ok"] is True
    assert r["vazamentos"] == []
    assert r["cortes"] == [15, 30]


def test_indicador_da_casa_e_causal():
    r = detectar_lookahead(serie(50), fabrica_com_indicador, cortes=(25,))
    assert r["ok"] is True


def test_pipeline_vazado_e_detectado_com_evidencia():
    r = detectar_lookahead(serie(30), fabrica_vazada, cortes=(15,))
    assert r["ok"] is False
    assert r["vazamentos"], "o vazamento precisa de evidência localizada"
    v = r["vazamentos"][0]
    assert v["corte"] == 15
    assert v["direcao_completa"] != v["direcao_truncada"]


def test_corte_fora_da_amostra_e_rejeitado():
    with pytest.raises(ValueError):
        detectar_lookahead(serie(10), fabrica_causal, cortes=(0,))
    with pytest.raises(ValueError):
        detectar_lookahead(serie(10), fabrica_causal, cortes=(10,))


def test_amostra_curta_nao_produz_corte():
    r = detectar_lookahead(serie(2), fabrica_causal)
    assert r["cortes"] == []
    assert "sem corte aplicável" in r["aviso"]
    # V9-QUANT-04: sem corte NÃO há evidência — o veredito é fail-closed;
    # "não produzido" jamais pode ser lido como "sem vazamento".
    assert r["ok"] is False

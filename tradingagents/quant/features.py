"""Features determinísticas para a camada quantitativa (itens 2 e 5).

Cálculo financeiro aqui é Python puro e reprodutível: nenhum LLM participa de
número que vira decisão (item 3). Janela insuficiente vira `None` — nunca
estimativa otimista.
"""
from __future__ import annotations

from statistics import fmean, pstdev

# Ordem canônica do vetor para modelos: mudar isto muda o contrato dos
# modelos treinados — features novas entram no fim, removidas só com retreino.
NOMES_DE_FEATURES: tuple[str, ...] = (
    "retorno_ultimo",
    "retorno_medio_5",
    "vol_10",
    "rsi_norm",
    "momentum_10",
    "zscore_20",
    "sma_ratio",
    "atr_pct_14",
)

JANELA_MINIMA_PADRAO = 31


def retornos(closes) -> list[float]:
    v = [float(c) for c in closes]
    return [(b - a) / a for a, b in zip(v[:-1], v[1:], strict=True) if a]


def vetor_de_features(closes, janela_minima: int = JANELA_MINIMA_PADRAO) -> dict[str, float] | None:
    """Features nomeadas — dicionário explicável, não vetor anônimo.

    `None` quando a janela não permite TODAS as features: um modelo alimentado
    com feature inventada aprenderia ruído.
    """
    from tradingagents.desk.signals import atr_pct, momentum, rsi, sma, zscore

    if len(closes) < janela_minima:
        return None

    r = retornos(closes)
    if len(r) < 10:
        return None

    rsi_v = rsi(closes, 14)
    momentum_v = momentum(closes, 10)
    z = zscore(closes, 20)
    sma_fast, sma_slow = sma(closes, 10), sma(closes, 30)
    atr = atr_pct(closes, 14)

    if None in (rsi_v, momentum_v, z, sma_fast, sma_slow, atr):
        return None
    if sma_slow == 0:
        return None

    return {
        "retorno_ultimo": r[-1],
        "retorno_medio_5": fmean(r[-5:]),
        "vol_10": pstdev(r[-10:]),
        "rsi_norm": rsi_v / 100.0,
        "momentum_10": momentum_v,
        "zscore_20": z,
        "sma_ratio": (sma_fast / sma_slow) - 1.0,
        "atr_pct_14": atr,
    }


def para_vetor(features: dict[str, float]) -> list[float]:
    """Converte o dicionário na ordem canônica que os modelos esperam."""
    return [float(features[n]) for n in NOMES_DE_FEATURES]

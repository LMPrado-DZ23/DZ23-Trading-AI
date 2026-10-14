"""Causalidade dos indicadores (FQ-1/E) — indicadores recursivos não podem
depender de dado fora da própria janela nem recalcular série já publicada:

- funções de janela (sma, rsi simples, zscore, momentum, atr_pct, pivôs):
  mudar um valor BEM ANTES da janela não pode alterar o resultado;
- séries recursivas (Wilder em sala_trader): recalcular num prefixo tem que
  reproduzir EXATAMENTE os valores já publicados até o corte;
- aquecimento: janela insuficiente vira `None` — nunca estimativa otimista.
"""
from tradingagents.desk import signals
from tradingagents.desk.sala_trader import media_movel, rsi


def closes(n: int = 60) -> list[float]:
    return [100 + (i % 7) * 1.5 + i * 0.3 for i in range(n)]


def test_indicadores_de_janela_ignoram_dado_fora_da_janela():
    v = closes(80)
    casos = [
        ("sma", signals.sma, 20),
        ("rsi", signals.rsi, 14),
        ("zscore", signals.zscore, 20),
        ("momentum", signals.momentum, 10),
        ("atr_pct", signals.atr_pct, 14),
        ("suporte_resistencia", signals.suporte_resistencia, 30),
    ]
    for nome, fn, n in casos:
        base = fn(v, n)
        mutado = list(v)
        mutado[0] = v[0] * 0.5  # muito antes da janela
        assert fn(mutado, n) == base, f"{nome} leu dado fora da própria janela"


def test_serie_rsi_wilder_e_prefixo_estavel():
    v = closes(60)
    completa = rsi(v, 14)
    for corte in (20, 35, 60):
        prefixa = rsi(v[:corte], 14)
        for i in range(corte):
            a, b = completa[i], prefixa[i]
            assert (a is None) == (b is None), f"presença mudou no índice {i}"
            if a is not None:
                assert a == b, f"valor no índice {i} mudou no prefixo {corte}"


def test_media_movel_e_prefixo_estavel():
    v = closes(40)
    completa = media_movel(v, 9)
    for corte in (12, 25, 40):
        prefixa = media_movel(v[:corte], 9)
        for i in range(corte):
            assert completa[i] == prefixa[i], (
                f"média mudou no índice {i} no prefixo {corte}")


def test_aquecimento_nao_vira_estimativa():
    v = closes(60)
    for k in range(1, 20):
        assert signals.sma(v[:k], 20) is None
        assert signals.ema(v[:k], 20) is None
    assert signals.sma(v[:20], 20) is not None
    assert signals.ema(v[:20], 20) is not None

    for k in range(1, 15):
        assert signals.rsi(v[:k], 14) is None
    assert signals.rsi(v[:15], 14) is not None

    mm = media_movel(v, 9)
    assert all(x is None for x in mm[:8])
    assert mm[8] is not None

    serie_rsi = rsi(v, 14)
    assert all(x is None for x in serie_rsi[:14])
    assert serie_rsi[14] is not None

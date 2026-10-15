"""Detector de lookahead (FQ-1/E) — vazamento de futuro no pipeline.

A garantia estrutural do backtest (o sinal só recebe o passado estrito) não
impede o outro vazamento: a decisão de uma barra MUDAR quando o dataset
contém barras posteriores — normalização pelo máximo/z-score da amostra
inteira, indicador recalculado com o fim da série, cache acumulado por
tamanho. O protocolo honesto de pesquisa exige decisões causais: truncar o
dataset num corte não pode alterar nenhuma decisão anterior ao corte.

Este detector compara as decisões do pipeline completo com as do mesmo
pipeline rodado sobre o prefixo e aponta cada divergência com corte, índice
e as duas direções — evidência localizada, auditável, sem heurística.

Veredito **fail-closed**: sem corte aplicável não existe evidência — o
resultado é `ok=False` com aviso explícito de que nada foi medido, nunca
aprovação por ausência de teste.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from .backtest import Bar


def _direcoes(bars: tuple[Bar, ...], fabrica: Callable) -> list[int]:
    sinal = fabrica(bars)
    return [sinal(bars[:i]) for i in range(1, len(bars))]


def detectar_lookahead(bars: Iterable[Bar], fabrica: Callable, *,
                       cortes: Sequence[int] | None = None) -> dict:
    """Confronta decisões do pipeline completo × truncado em cada corte.

    `fabrica` recebe o dataset disponível e devolve o sinal — é assim que o
    detector simula "o que o pipeline decidiria se o mundo acabasse hoje".
    Corte válido: 2 ≤ corte ≤ n-1 (com corte ≤ 1 não existe decisão anterior
    para auditar; corte = n é o próprio dataset completo).
    """
    ordered = tuple(sorted(bars, key=lambda bar: bar.timestamp))
    n = len(ordered)
    if cortes is None:
        cortes = (n // 2,) if n >= 3 else ()
    cortes = tuple(sorted({int(c) for c in cortes}))
    for c in cortes:
        if not 2 <= c <= n - 1:
            raise ValueError(
                f"corte {c} fora do intervalo válido [2, {n - 1}]")

    completo = _direcoes(ordered, fabrica)
    vazamentos = []
    for corte in cortes:
        truncado = _direcoes(ordered[:corte], fabrica)
        for i, direcao in enumerate(truncado):
            if direcao != completo[i]:
                vazamentos.append({
                    "corte": corte, "indice": i + 1,
                    "direcao_completa": completo[i],
                    "direcao_truncada": direcao})
    if not cortes:
        aviso = ("sem corte aplicável (amostra curta): nenhuma evidência "
                 "de vazamento foi produzida")
    elif vazamentos:
        aviso = ("decisão de barra anterior ao corte mudou quando dados "
                 "futuros existem — o pipeline não é causal")
    else:
        aviso = "decisões estáveis sob truncamento: sem vazamento detectado"
    return {"total_barras": n, "cortes": list(cortes),
            "vazamentos": vazamentos, "ok": bool(cortes) and not vazamentos,
            "aviso": aviso}

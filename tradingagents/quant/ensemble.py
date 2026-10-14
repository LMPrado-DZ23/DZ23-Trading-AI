"""Ensemble quantitativo (item 5).

Combina vozes independentes (traders determinísticos e, quando existir,
modelo experimental) num consenso líquido. Regras explícitas e auditáveis:

- cada voz vale o peso da sua confiança — calibrada quando há histórico;
- empate técnico não vira ordem: abaixo do limiar o consenso ABSTÉM;
- divergência alta aparece no resultado, não fica escondida na média.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Voz:
    nome: str
    direcao: int          # +1 comprar · 0 abster · -1 vender
    confianca: float      # 0 a 1
    origem: str = "deterministico"


@dataclass(frozen=True)
class Consenso:
    direcao: str          # comprar | vender | abster
    confianca: float
    liquido: float        # soma ponderada normalizada, -1 a +1
    vozes: tuple[Voz, ...]
    nota: str


def consenso(vozes: Sequence[Voz], *, calibrador=None, limiar: float = 0.25) -> Consenso:
    """Consenso ponderado por confiança; `calibrador` mapeia confiança bruta."""
    if not vozes:
        return Consenso("abster", 0.0, 0.0, (), "sem vozes")

    pesos: list[tuple[Voz, float]] = []
    for v in vozes:
        if v.direcao not in (-1, 0, 1):
            raise ValueError(f"direção inválida em '{v.nome}': {v.direcao}")
        bruta = float(v.confianca)
        calibrada = calibrador.calibrar(bruta) if calibrador is not None else bruta
        pesos.append((v, max(0.0, min(1.0, float(calibrada)))))

    soma = sum(peso for _, peso in pesos)
    if soma <= 0:
        return Consenso("abster", 0.0, 0.0, tuple(vozes), "vozes sem peso")

    liquido = sum(peso * v.direcao for v, peso in pesos) / soma
    if liquido >= limiar:
        direcao = "comprar"
    elif liquido <= -limiar:
        direcao = "vender"
    else:
        direcao = "abster"
    nota = (f"{len(vozes)} vozes · líquido {liquido:+.2f} · "
            f"limiar {limiar:.2f}")
    return Consenso(direcao, abs(liquido), liquido, tuple(vozes), nota)

"""Monitoramento de drift (item 5).

Mercado que muda de comportamento invalida modelo treinado — e o modelo não
avisa sozinho. PSI (Population Stability Index) compara a distribuição atual
de uma feature com a referência do treino:

    PSI < 0,10  → estável
    0,10–0,25   → atenção (vale reavaliar)
    >= 0,25     → deriva: confiança do modelo deve CAIR até reavaliação
"""
from __future__ import annotations

import math
from collections.abc import Sequence


def psi(referencia: Sequence[float], atual: Sequence[float], *, bins: int = 10) -> float:
    """PSI entre duas distribuições; cortes pelas quantis da referência."""
    r = [float(x) for x in referencia]
    a = [float(x) for x in atual]
    if len(r) < bins or len(a) < bins:
        raise ValueError("amostra insuficiente para PSI")
    if bins < 2:
        raise ValueError("bins precisa ser >= 2")

    ordenada = sorted(r)
    cortes = [ordenada[int(len(ordenada) * i / bins)] for i in range(1, bins)]

    def histograma(valores: list[float]) -> list[float]:
        h = [0] * bins
        for v in valores:
            i = 0
            while i < len(cortes) and v > cortes[i]:
                i += 1
            h[i] += 1
        return [x / len(valores) for x in h]

    hr, ha = histograma(r), histograma(a)
    valor = 0.0
    for pr, pa in zip(hr, ha, strict=True):
        # Piso pequeno: bin vazio na referência não pode zerar o índice nem
        # estourar log(0) — o sinal de drift mora justamente nesse bin.
        pr_ = max(pr, 1e-6)
        pa_ = max(pa, 1e-6)
        valor += (pa_ - pr_) * math.log(pa_ / pr_)
    return valor


class MonitorDeDrift:
    """Guarda a referência (dados de treino) e gradua a resposta."""

    def __init__(self, referencia: Sequence[float], *,
                 limiar_atencao: float = 0.10, limiar_deriva: float = 0.25):
        self.referencia = tuple(float(x) for x in referencia)
        self.limiar_atencao = limiar_atencao
        self.limiar_deriva = limiar_deriva

    def avaliar(self, atual: Sequence[float]) -> dict:
        try:
            valor = psi(self.referencia, atual)
        except ValueError as exc:
            return {"psi": None, "nivel": "indisponivel", "deriva": False,
                    "motivo": str(exc)}
        if valor >= self.limiar_deriva:
            nivel = "deriva"
        elif valor >= self.limiar_atencao:
            nivel = "atencao"
        else:
            nivel = "estavel"
        return {"psi": valor, "nivel": nivel, "deriva": nivel == "deriva",
                "motivo": f"PSI {valor:.3f} ({nivel})"}

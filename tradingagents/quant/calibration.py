"""Calibração de confiança (item 5).

Confiança de modelo NÃO é probabilidade até ser medida: um modelo que diz
"90%" e acerta 60% está mentindo com número. Aqui a confiança bruta vira
frequência observada por faixa, e o ECE mostra o tamanho da mentira.
"""
from __future__ import annotations


class CalibradorDeConfianca:
    """Mapa confiança bruta → frequência observada, em faixas fixas.

    Faixa sem amostra devolve a própria confiança (identidade): calibrar com
    zero dado seria pior que não calibrar.
    """

    def __init__(self, faixas: int = 10):
        if faixas < 2:
            raise ValueError("faixas precisa ser >= 2")
        self.faixas = faixas
        self._total = [0] * faixas
        self._acertos = [0] * faixas

    def _faixa(self, confianca: float) -> int:
        return min(self.faixas - 1, max(0, int(float(confianca) * self.faixas)))

    def registrar(self, confianca: float, acertou: bool) -> None:
        i = self._faixa(confianca)
        self._total[i] += 1
        if acertou:
            self._acertos[i] += 1

    def calibrar(self, confianca: float) -> float:
        i = self._faixa(confianca)
        total = self._total[i]
        if not total:
            return float(confianca)
        return self._acertos[i] / total

    @property
    def amostras(self) -> int:
        return sum(self._total)

    def ece(self) -> float | None:
        """Expected Calibration Error; `None` sem amostras (não é zero)."""
        if not self.amostras:
            return None
        erro = 0.0
        for i in range(self.faixas):
            if not self._total[i]:
                continue
            media_da_faixa = (i + 0.5) / self.faixas
            frequencia = self._acertos[i] / self._total[i]
            erro += (self._total[i] / self.amostras) * abs(frequencia - media_da_faixa)
        return erro

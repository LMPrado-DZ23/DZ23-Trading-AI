"""Leitura de mercado dos traders (MA-04 avançado).

Aqui mora a interpretação: transformar preço bruto em sinais nomeados, com
regime, força e confiança. Tudo determinístico e auditável — um número que
o trader usa precisa poder ser recalculado por qualquer um.

Invariantes:
- NENHUM cálculo olha além do `cutoff` do snapshot. As séries já chegam
  cortadas; aqui só se usa o que existe.
- Janela insuficiente não vira estimativa otimista: vira `None` e o trader
  se abstém.
- Confiança é uma nota do PESO DA EVIDÊNCIA, não probabilidade de lucro.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from statistics import fmean, pstdev


class Regime(str, Enum):
    ALTA = "tendencia_alta"
    BAIXA = "tendencia_baixa"
    LATERAL = "lateral"
    VOLATIL = "volatil"
    INDEFINIDO = "indefinido"


def _f(valores) -> list[float]:
    return [float(v) for v in valores]


def sma(valores, n: int) -> float | None:
    v = _f(valores)
    return fmean(v[-n:]) if len(v) >= n else None


def ema(valores, n: int) -> float | None:
    v = _f(valores)
    if len(v) < n:
        return None
    k = 2 / (n + 1)
    atual = fmean(v[:n])
    for preco in v[n:]:
        atual = preco * k + atual * (1 - k)
    return atual


def rsi(valores, n: int = 14) -> float | None:
    v = _f(valores)
    if len(v) < n + 1:
        return None
    ganhos, perdas = [], []
    for antes, depois in zip(v[-n - 1:-1], v[-n:]):
        d = depois - antes
        ganhos.append(max(d, 0.0))
        perdas.append(max(-d, 0.0))
    mg, mp = fmean(ganhos), fmean(perdas)
    if mp == 0:
        return 100.0 if mg > 0 else 50.0
    rs = mg / mp
    return 100 - (100 / (1 + rs))


def atr_pct(valores, n: int = 14) -> float | None:
    """Volatilidade relativa a partir de fechamentos (proxy de ATR)."""
    v = _f(valores)
    if len(v) < n + 1:
        return None
    variacoes = [abs(b - a) / a for a, b in zip(v[-n - 1:-1], v[-n:]) if a]
    return fmean(variacoes) if variacoes else None


def momentum(valores, n: int = 10) -> float | None:
    v = _f(valores)
    if len(v) < n + 1 or v[-n - 1] == 0:
        return None
    return (v[-1] - v[-n - 1]) / v[-n - 1]


def zscore(valores, n: int = 20) -> float | None:
    v = _f(valores)
    if len(v) < n:
        return None
    janela = v[-n:]
    desvio = pstdev(janela)
    if desvio == 0:
        return None
    return (v[-1] - fmean(janela)) / desvio


def suporte_resistencia(valores, n: int = 30) -> tuple[float, float] | None:
    v = _f(valores)
    if len(v) < n:
        return None
    janela = v[-n:]
    return min(janela), max(janela)


@dataclass(frozen=True)
class Leitura:
    """O que o trader 'vê'. Serve de evidência auditável da decisão."""
    instrumento: str
    regime: Regime
    tendencia: float | None          # inclinação curta vs longa
    momentum: float | None
    rsi: float | None
    zscore: float | None
    volatilidade: float | None
    suporte: float | None
    resistencia: float | None
    forca: float                      # 0..1, peso da evidência
    motivos: tuple[str, ...]

    def resumo(self) -> str:
        return " · ".join(self.motivos) or "sem sinais relevantes"

    def suficiente(self) -> bool:
        return self.regime is not Regime.INDEFINIDO


def interpretar(instrumento: str, closes) -> Leitura:
    """Interpreta a série já cortada no cutoff. Sem dados, diz que não sabe."""
    if len(closes) < 30:
        return Leitura(instrumento, Regime.INDEFINIDO, None, None, None, None,
                       None, None, None, 0.0,
                       (f"série curta ({len(closes)} velas; preciso de 30)",))
    curta, longa = sma(closes, 10), sma(closes, 30)
    mom, r, z = momentum(closes, 10), rsi(closes, 14), zscore(closes, 20)
    vol = atr_pct(closes, 14)
    sr = suporte_resistencia(closes, 30)
    inclinacao = (curta - longa) / longa if (curta and longa) else None

    motivos: list[str] = []
    regime = Regime.LATERAL
    if vol is not None and vol > 0.04:
        regime = Regime.VOLATIL
        motivos.append(f"volatilidade alta ({vol:.1%} por vela)")
    elif inclinacao is not None and inclinacao > 0.01:
        regime = Regime.ALTA
        motivos.append(f"média curta {inclinacao:+.1%} acima da longa")
    elif inclinacao is not None and inclinacao < -0.01:
        regime = Regime.BAIXA
        motivos.append(f"média curta {inclinacao:+.1%} abaixo da longa")
    else:
        motivos.append("médias coladas: mercado sem direção")

    if mom is not None:
        motivos.append(f"momentum {mom:+.1%} em 10 velas")
    if r is not None:
        if r >= 70:
            motivos.append(f"RSI {r:.0f} (esticado para cima)")
        elif r <= 30:
            motivos.append(f"RSI {r:.0f} (esticado para baixo)")
    if z is not None and abs(z) >= 2:
        motivos.append(f"preço a {z:+.1f} desvios da média")

    # Força = quantas evidências independentes apontam na mesma direção.
    evidencias = 0
    if inclinacao is not None and abs(inclinacao) > 0.01:
        evidencias += 1
    if mom is not None and abs(mom) > 0.02:
        evidencias += 1
    if r is not None and (r >= 70 or r <= 30):
        evidencias += 1
    if z is not None and abs(z) >= 2:
        evidencias += 1
    forca = min(1.0, evidencias / 4)

    return Leitura(instrumento, regime, inclinacao, mom, r, z, vol,
                   sr[0] if sr else None, sr[1] if sr else None,
                   round(forca, 2), tuple(motivos))


__all__ = ["Regime", "Leitura", "interpretar", "sma", "ema", "rsi", "atr_pct",
           "momentum", "zscore", "suporte_resistencia"]


# ------------------------------------------------- confirmação multi-prazo
def agregar(closes, fator: int):
    """Reamostra a série para um prazo maior (ex.: 1h -> 4h com fator 4).

    Usa o ÚLTIMO fechamento de cada bloco — é o que uma vela de prazo maior
    de fato fecha. Blocos incompletos no fim são descartados: usar vela em
    formação de 4h é olhar o futuro pela janela.
    """
    if fator <= 1:
        return tuple(closes)
    completos = len(closes) // fator
    if completos == 0:
        return ()
    return tuple(closes[(i + 1) * fator - 1] for i in range(completos))


def confirmacao_superior(closes, fator: int = 4) -> tuple[bool | None, str]:
    """O prazo maior concorda com a alta? Sem dados suficientes, devolve
    None — e quem chama decide (aqui: não opera sem confirmação)."""
    serie = agregar(closes, fator)
    # 10 velas agregadas = ~1,7 dia em 4h: mínimo para a média significar algo.
    if len(serie) < 10:
        return None, (f"sem histórico para o prazo {fator}x "
                      f"({len(serie)} velas agregadas; preciso de 10)")
    curta, longa = sma(serie, 3), sma(serie, 10)
    if curta is None or longa is None:
        return None, "prazo maior sem médias calculáveis"
    if curta > longa:
        return True, f"prazo {fator}x confirma alta (média 5 acima da 20)"
    return False, f"prazo {fator}x NÃO confirma: média 5 abaixo da 20"

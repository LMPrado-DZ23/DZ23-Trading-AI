"""Tamanho por risco e correlação entre posições (melhoria ③).

Dois erros silenciosos que o percentual fixo comete:

1. **Tamanho igual para riscos diferentes.** 15% do capital num ativo que
   oscila 1% ao dia e noutro que oscila 8% são apostas completamente
   distintas. O tamanho tem de sair do RISCO, não do capital.
2. **Correlação ignorada.** BTC e ETH sobem e caem juntos quase sempre.
   Comprar os dois "diversificando" é dobrar a mesma aposta — e é assim que
   se perde o dobro num dia ruim.

Aqui os dois viram cálculo explícito.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from statistics import pstdev

# Correlação histórica típica entre grandes criptos (conservadora: alta).
# Não substitui cálculo com dados reais — é o piso de prudência enquanto
# não houver histórico suficiente no próprio sistema.
CORRELACAO_PRESUMIDA: dict[frozenset, float] = {
    frozenset({"BTC", "ETH"}): 0.85,
    frozenset({"BTC", "SOL"}): 0.80,
    frozenset({"ETH", "SOL"}): 0.82,
    frozenset({"BTC", "BNB"}): 0.78,
}
CORRELACAO_MESMA_BASE = 0.95     # BTC/USDT e BTC/BRL são o mesmo risco
CORRELACAO_CRIPTO_PADRAO = 0.70  # duas criptos quaisquer andam juntas


def moeda_base(instrumento: str) -> str:
    return instrumento.upper().split("/")[0].strip()


def correlacao(a: str, b: str) -> float:
    """Correlação presumida entre dois instrumentos."""
    base_a, base_b = moeda_base(a), moeda_base(b)
    if base_a == base_b:
        return CORRELACAO_MESMA_BASE
    conhecida = CORRELACAO_PRESUMIDA.get(frozenset({base_a, base_b}))
    return conhecida if conhecida is not None else CORRELACAO_CRIPTO_PADRAO


def retornos(closes) -> list[float]:
    v = [float(c) for c in closes]
    # strict=True: v[:-1] e v[1:] têm sempre o mesmo tamanho para qualquer
    # série; a checagem é de graça e protege contra alteração futura.
    return [(b - a) / a for a, b in zip(v[:-1], v[1:], strict=True) if a]


def volatilidade_por_vela(closes) -> float | None:
    r = retornos(closes)
    if len(r) < 10:
        return None
    return pstdev(r) or None


@dataclass(frozen=True)
class TamanhoCalculado:
    fracao_do_capital: Decimal
    risco_por_operacao: Decimal
    stop_sugerido: Decimal
    motivo: str


def tamanho_por_risco(capital: Decimal, closes, *,
                      risco_maximo: Decimal = Decimal("0.01"),
                      multiplo_stop: Decimal = Decimal("2"),
                      teto_exposicao: Decimal = Decimal("0.25")
                      ) -> TamanhoCalculado | None:
    """Quanto comprar para que UMA operação arrisque no máximo `risco_maximo`.

    A ideia: o stop fica a `multiplo_stop` desvios típicos do preço. Se o
    stop é largo (ativo volátil), a posição encolhe; se é apertado, cresce —
    até o teto de exposição, que continua sendo do mandato.
    """
    vol = volatilidade_por_vela(closes)
    if vol is None or vol <= 0:
        return None
    stop = Decimal(str(round(vol, 8))) * multiplo_stop
    if stop <= 0:
        return None
    fracao = (risco_maximo / stop).quantize(Decimal("0.0001"), ROUND_DOWN)
    limitado = min(fracao, teto_exposicao)
    motivo = (f"volatilidade {vol:.2%}/vela ⇒ stop {stop:.2%}; "
              f"arriscando {risco_maximo:.1%} do capital, cabe "
              f"{fracao:.1%} de exposição")
    if limitado < fracao:
        motivo += f" (limitado ao teto de {teto_exposicao:.0%})"
    return TamanhoCalculado(limitado, risco_maximo, stop, motivo)


def exposicao_correlacionada(instrumento: str,
                             posicoes: dict[str, Decimal]) -> Decimal:
    """Exposição efetiva a este risco, somando o que já existe ponderado
    pela correlação. É o número que revela a aposta dobrada."""
    total = Decimal("0")
    for aberto, tamanho in posicoes.items():
        total += Decimal(str(correlacao(instrumento, aberto))) * tamanho
    return total


def limite_de_correlacao(instrumento: str, tamanho_novo: Decimal,
                         posicoes: dict[str, Decimal],
                         teto_correlacionado: Decimal = Decimal("0.35")
                         ) -> tuple[bool, str]:
    """Permite a nova posição? Considera o que ela realmente adiciona de
    risco, não o rótulo do ativo."""
    ja = exposicao_correlacionada(instrumento, posicoes)
    total = ja + tamanho_novo
    if total > teto_correlacionado:
        vizinhos = ", ".join(
            f"{a} (corr {correlacao(instrumento, a):.0%})" for a in posicoes) or "—"
        return False, (f"exposição correlacionada chegaria a {total:.0%} "
                       f"(teto {teto_correlacionado:.0%}); já aberto: {vizinhos}. "
                       "Comprar isto seria dobrar a mesma aposta.")
    return True, (f"exposição correlacionada {total:.0%} dentro do teto "
                  f"{teto_correlacionado:.0%}")


__all__ = ["tamanho_por_risco", "TamanhoCalculado", "limite_de_correlacao",
           "exposicao_correlacionada", "correlacao", "volatilidade_por_vela"]

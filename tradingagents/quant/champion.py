"""Champion/challenger (item 5).

Compara estratégia campeã com desafiante usando o resultado LÍQUIDO
(depois de custos — item 8). Promoção tem teto explícito:

- destino máximo é SHADOW/PAPER;
- LIVE permanece fora do alcance de QUALQUER modelo (item 9);
- sem amostra mínima ou sem margem, o desafiante fica onde está.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Desempenho:
    estrategia: str
    amostras: int
    retorno_liquido_medio: Decimal
    taxa_acerto: Decimal


@dataclass(frozen=True)
class DecisaoDePromocao:
    promover: bool
    destino: str | None       # None quando não promove; nunca "LIVE"
    motivo: str


DESTINO_MAXIMO = "PAPER"


def comparar(campeao: Desempenho, desafiante: Desempenho, *,
             minimo_amostras: int = 30,
             margem: Decimal = Decimal("0.0005")) -> DecisaoDePromocao:
    """Decide se o desafiante merece ensaio; a decisão fica registrada."""
    if desafiante.amostras < minimo_amostras:
        return DecisaoDePromocao(
            False, None,
            f"desafiante sem amostra mínima ({desafiante.amostras}/{minimo_amostras})")
    if desafiante.retorno_liquido_medio <= campeao.retorno_liquido_medio + margem:
        return DecisaoDePromocao(
            False, None,
            f"desafiante não supera o campeão com margem de {margem} "
            f"({desafiante.retorno_liquido_medio:.4%} vs "
            f"{campeao.retorno_liquido_medio:.4%})")
    if desafiante.taxa_acerto < campeao.taxa_acerto:
        return DecisaoDePromocao(
            False, None,
            f"desafiante supera em retorno mas piora a taxa de acerto "
            f"({desafiante.taxa_acerto:.0%} vs {campeao.taxa_acerto:.0%})")
    return DecisaoDePromocao(
        True, DESTINO_MAXIMO,
        "ensaio limitado a SHADOW/PAPER — LIVE permanece fora do alcance de "
        "qualquer modelo (item 9)")

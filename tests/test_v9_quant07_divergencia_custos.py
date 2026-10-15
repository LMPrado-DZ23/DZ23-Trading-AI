"""V9-QUANT-07 — divergência de modelo de taxas DZ23 vs convenção por ponta.

Achado: `desk/costs.py` subtrai as duas taxas do notional inicial
(`ida_e_volta = (taxa+spread+slippage)*2`), enquanto a convenção contábil
por ponta (usada por ferramentas como o Freqtrade) cobra a taxa de saída
sobre o notional de saída. A diferença é exatamente `taxa * retorno_bruto`.

Estes testes fixam os números medidos no probe offline
(`tools/v9_quant07_divergencia_custos.py`, evidências
`docs/mission/evidence/v9-baseline/v9-quant07-probe-*.json`) para que
qualquer mudança futura de fórmula seja deliberada, não acidental.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tradingagents.desk.costs import ModeloDeCusto, retorno_liquido

TAXA = Decimal("0.001")            # 0,1% por ponta (binance taker, medida do probe)
ENTRADA = Decimal("100")
QUANTIDADE = Decimal("1")


def _custo(taxa: Decimal = TAXA) -> ModeloDeCusto:
    """Custo sem spread/slippage — isola o efeito das taxas, como no probe."""
    return ModeloDeCusto(exchange="binance", taxa=taxa,
                         spread=Decimal("0"), slippage=Decimal("0"))


def _dz23_liquido(saida: Decimal) -> Decimal:
    bruto = (saida - ENTRADA) / ENTRADA
    return retorno_liquido(bruto, _custo()) * ENTRADA * QUANTIDADE


def _por_ponta_liquido(saida: Decimal) -> Decimal:
    """Referência contábil por ponta: taxa de saída incide sobre o valor vendido."""
    desembolso = QUANTIDADE * ENTRADA * (Decimal("1") + TAXA)
    recebido = QUANTIDADE * saida * (Decimal("1") - TAXA)
    return recebido - desembolso


def test_quant07_dz23_ganho_valor_exato_do_probe() -> None:
    """Compra 1@100, vende 1@110: DZ23 dá 9,800 (0,098 de fração)."""
    assert retorno_liquido(Decimal("0.10"), _custo()) == Decimal("0.098")
    assert _dz23_liquido(Decimal("110")) == Decimal("9.800")


def test_quant07_por_ponta_ganho_valor_exato_do_probe() -> None:
    """Mesma operação na convenção por ponta: 9,79 (corrobora Freqtrade 2026.9)."""
    assert _por_ponta_liquido(Decimal("110")) == Decimal("9.79")


def test_quant07_divergencia_e_exatamente_taxa_vezes_bruto() -> None:
    """A divergência DZ23 − por-ponta é `taxa * retorno_bruto`, não ruído.

    Ganho: DZ23 fica 0,0001 de fração (0,01 unidade) acima da convenção.
    """
    bruto = Decimal("0.10")
    divergencia = _dz23_liquido(Decimal("110")) - _por_ponta_liquido(Decimal("110"))
    assert divergencia == TAXA * bruto * ENTRADA
    assert divergencia == Decimal("0.01")


def test_quant07_divergencia_em_perda_conservadora() -> None:
    """Saída a 90: DZ23 marca −10,200 contra −10,19 da convenção por ponta.

    Em perda o modelo DZ23 superestima o prejuízo — direção conservadora
    para capital, que é o lado certo de errar.
    """
    assert _dz23_liquido(Decimal("90")) == Decimal("-10.200")
    assert _por_ponta_liquido(Decimal("90")) == Decimal("-10.19")
    assert _dz23_liquido(Decimal("90")) < _por_ponta_liquido(Decimal("90"))


@pytest.mark.parametrize("saida", [Decimal("110"), Decimal("90")])
def test_quant07_divergencia_sempre_taxa_vezes_bruto(saida: Decimal) -> None:
    bruto = (saida - ENTRADA) / ENTRADA
    divergencia = _dz23_liquido(saida) - _por_ponta_liquido(saida)
    assert divergencia == TAXA * bruto * ENTRADA


def test_quant07_formula_ida_e_volta_nao_mudou_sem_decisao() -> None:
    """Pin da fórmula: 2*(taxa+spread+slippage), sem base de saída.

    Mudar isto altera a semântica dos `Desfecho` já registrados na Escola.
    Se um dia for mudar, que seja com decisão explícita (ver §5.1 do
    V9-BASELINE), não por acidente.
    """
    c = ModeloDeCusto(exchange="binance", taxa=Decimal("0.001"),
                      spread=Decimal("0.0002"), slippage=Decimal("0.0003"))
    assert c.ida_e_volta == Decimal("0.003")
    assert c.ida_e_volta == (c.taxa + c.spread + c.slippage) * 2
    assert retorno_liquido(Decimal("0.10"), c) == Decimal("0.097")

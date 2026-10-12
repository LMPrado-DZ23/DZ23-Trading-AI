"""Blocker 4 — o incidente CONGELA o que valora na proposta.

`valorar_taxa` propõe uma cotação para a quantidade conhecida NAQUELE
momento. Uma taxa que chega depois é outra ocorrência: não pode inflar a
valoração já proposta nem entrar no batch no instante de aplicar.
"""
from __future__ import annotations

from decimal import Decimal

from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import DeskMandate, Mode
from tradingagents.desk.risk import RiskEngine

_EM = "2026-01-01T00:00:00+00:00"


class Ator:
    actor_id = "risk-manager-1"
    capabilities = frozenset({"RESOLVE_RISK_INCIDENT", "VALUE_FEE_INCIDENT"})


def _sistema(capital="10000"):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=("BTC/USDT",),
                    capital=Decimal(capital), slippage_buffer_pct=Decimal("0"))
    livro = Ledger(opening_cash=m.capital)
    return m, RiskEngine(m, carteira=livro), livro


def _cobrar(livro, qtd, moeda="BNB", ref="a", fill_id=""):
    livro.reserve(Decimal("100"), ref=ref, reservation_id=f"R{ref}")
    livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("90"), Decimal("0"),
                   reserved=Decimal("100"), ref=ref, agent_id="A",
                   reservation_id=f"R{ref}",
                   native_fees=[(Decimal(qtd), moeda)], fill_id=fill_id or ref)


def test_b4_fee_nova_apos_proposta_nao_infla_a_valoracao():
    """Mutação F: taxa nova (mesma moeda) depois da PROPOSTA não pode inflar
    a valoração de 2 para 3 BNB no instante de aplicar."""
    m, risco, livro = _sistema()
    _cobrar(livro, "2", ref="a", fill_id="F1")
    inc = risco.registrar_taxa_nao_valorada(moeda="BNB", quantidade=Decimal("2"),
                                            fill_id="F1")
    risco.valorar_taxa(inc, rate=Decimal("300"), fonte="binance", em=_EM)
    # nova taxa em BNB DEPOIS da proposta — hoje funde no incidente por moeda
    _cobrar(livro, "1", ref="b", fill_id="F2")
    risco.registrar_taxa_nao_valorada(moeda="BNB", quantidade=Decimal("1"),
                                      fill_id="F2")
    risco.aplicar_valoracao(inc, actor=Ator(), evidence="e")
    tx = livro.find_fee_valuation(inc)
    assert Decimal(tx["native_amount"]) == Decimal("2"), (
        "a proposta era para 2 BNB; a valoração inflou com a taxa nova")
    # a taxa nova continua NÃO valorada: será outra ocorrência
    ainda = [c["fill_id"]
             for c in livro.fee_components("BNB", apenas_nao_valorados=True)]
    assert "F2" in ainda, "a taxa nova foi varrida no batch da proposta antiga"


def test_b4_componente_congelado_valorado_fora_derruba_lastro():
    """Mutação E: o batch da proposta é dono dos SEUS componentes. Se um sai
    (valorado por outro processo), aplicar recusa — não adota outro no lugar."""
    import pytest
    m, risco, livro = _sistema()
    _cobrar(livro, "2", ref="a", fill_id="F1")
    ids_f1 = [c["fee_component_id"] for c in livro.fee_components("BNB")]
    inc = risco.registrar_taxa_nao_valorada(moeda="BNB", quantidade=Decimal("2"),
                                            fill_id="F1")
    risco.valorar_taxa(inc, rate=Decimal("300"), fonte="x", em=_EM)  # congela F1
    # F1 é valorado por FORA (outro fluxo) antes de aplicar
    livro.fee_valuation(moeda="BNB", quantidade=Decimal("2"), rate=Decimal("300"),
                        fonte="y", em=_EM, fee_component_ids=ids_f1)
    # chega F2, também 2 BNB, NÃO coberto pela proposta
    _cobrar(livro, "2", ref="b", fill_id="F2")
    with pytest.raises(ValueError, match="lastro|parcial"):
        risco.aplicar_valoracao(inc, actor=Ator(), evidence="e")

"""F4 — liquidação do passivo funcional LIABILITY:FEE_FUNDING.

Valorar cria o passivo (a mesa deve o valor funcional da taxa paga em moeda
não funcional). Financiar o ativo descoberto NÃO zerava esse passivo — ele
ficava para sempre. F4 liquida o funding POR COMPONENTE, exatamente uma vez,
usando o valor funcional que a valoração daquele componente produziu.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tradingagents.desk.ledger import Ledger

_EM = "2026-09-14T23:00:00+00:00"


def _cobrar(livro, qtd, moeda="BNB", ref="a", fill_id=""):
    antes = {c["fee_component_id"] for c in livro.fee_components(moeda)}
    livro.reserve(Decimal("100"), ref=ref, reservation_id=f"R{ref}")
    livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("90"), Decimal("0"),
                   reserved=Decimal("100"), ref=ref, agent_id="A",
                   reservation_id=f"R{ref}",
                   native_fees=[(Decimal(qtd), moeda)], fill_id=fill_id or ref)
    return next(c["fee_component_id"] for c in livro.fee_components(moeda)
                if c["fee_component_id"] not in antes)


def _valorar(livro, cid, qtd, rate, moeda="BNB"):
    livro.fee_valuation(moeda=moeda, quantidade=Decimal(qtd),
                        rate=Decimal(rate), fonte="binance", em=_EM,
                        fee_component_ids=[cid])


def _aportar(livro, qtd, rate, moeda="BNB"):
    livro.asset_contribution(moeda, Decimal(qtd), reason="aporte", actor="op",
                             rate=Decimal(rate), fonte="binance", em=_EM)


def test_f4a_liquidar_zera_o_passivo():
    """Após valorar (passivo -600) e aportar o ativo, liquidar zera o funding."""
    livro = Ledger(opening_cash=Decimal("10000"))
    cid = _cobrar(livro, "2")
    _valorar(livro, cid, "2", "300")
    assert livro.balance("LIABILITY:FEE_FUNDING") == Decimal("-600")
    _aportar(livro, "2", "300")
    livro.liquidar_fee_funding(cid, actor="op")
    assert livro.balance("LIABILITY:FEE_FUNDING") == Decimal("0")
    assert livro.is_balanced()


def test_f4b_liquidacao_por_componente():
    """Dois componentes valorados a cotações diferentes liquidam cada um o SEU."""
    livro = Ledger(opening_cash=Decimal("10000"))
    a = _cobrar(livro, "1", ref="a", fill_id="F1")
    b = _cobrar(livro, "1", ref="b", fill_id="F2")
    _valorar(livro, a, "1", "300")   # 300
    _valorar(livro, b, "1", "600")   # 600
    assert livro.balance("LIABILITY:FEE_FUNDING") == Decimal("-900")
    _aportar(livro, "2", "450")      # cobre os 2 BNB descobertos
    livro.liquidar_fee_funding(a, actor="op")
    assert livro.balance("LIABILITY:FEE_FUNDING") == Decimal("-600")  # só o A
    livro.liquidar_fee_funding(b, actor="op")
    assert livro.balance("LIABILITY:FEE_FUNDING") == Decimal("0")


def test_f4c_exatamente_uma_vez():
    """Liquidar o mesmo componente duas vezes não move dinheiro de novo."""
    livro = Ledger(opening_cash=Decimal("10000"))
    cid = _cobrar(livro, "2")
    _valorar(livro, cid, "2", "300")
    _aportar(livro, "2", "300")
    livro.liquidar_fee_funding(cid, actor="op")
    saldo = livro.balance("LIABILITY:FEE_FUNDING")
    cc = livro.balance("CONTRIBUTED_CAPITAL")
    livro.liquidar_fee_funding(cid, actor="op")   # retry
    assert livro.balance("LIABILITY:FEE_FUNDING") == saldo
    assert livro.balance("CONTRIBUTED_CAPITAL") == cc


def test_f4d_componente_sem_valoracao_nao_liquida():
    livro = Ledger(opening_cash=Decimal("10000"))
    _cobrar(livro, "2")            # cobrado mas NÃO valorado
    with pytest.raises(ValueError, match="valora|liquid"):
        livro.liquidar_fee_funding("fc_inexistente00", actor="op")


def test_f4e_ativo_descoberto_bloqueia_a_liquidacao():
    """Sem financiar o ativo, liquidar o funding fingiria um lastro que não há."""
    livro = Ledger(opening_cash=Decimal("10000"))
    cid = _cobrar(livro, "2")
    _valorar(livro, cid, "2", "300")   # ASSET:BNB = -2 (descoberto)
    with pytest.raises(ValueError, match="descoberto|ASSET|financ"):
        livro.liquidar_fee_funding(cid, actor="op")


def test_f4f_restart_preserva_a_liquidacao():
    livro = Ledger(opening_cash=Decimal("10000"))
    cid = _cobrar(livro, "2")
    _valorar(livro, cid, "2", "300")
    _aportar(livro, "2", "300")
    livro.liquidar_fee_funding(cid, actor="op")
    rest = Ledger.from_transactions(livro.export_transactions(), currency="USDT")
    assert rest.balance("LIABILITY:FEE_FUNDING") == Decimal("0")
    # e o retry após restart continua idempotente
    rest.liquidar_fee_funding(cid, actor="op")
    assert rest.balance("LIABILITY:FEE_FUNDING") == Decimal("0")


def test_f4g_lado_nativo_intacto_e_funcional_fecha():
    livro = Ledger(opening_cash=Decimal("10000"))
    cid = _cobrar(livro, "2")
    _valorar(livro, cid, "2", "300")
    _aportar(livro, "2", "300")
    livro.liquidar_fee_funding(cid, actor="op")
    assert livro.balance("FEES:BNB") == Decimal("2")   # a taxa cobrada permanece
    assert livro.balance("FEES:USDT") == Decimal("600")  # a despesa permanece
    assert livro.functional_balanced()
    assert livro.is_balanced()

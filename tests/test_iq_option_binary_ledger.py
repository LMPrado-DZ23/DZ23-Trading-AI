"""Subledger de aposta/liquidação para opções binárias.

O livro spot não pode representar uma opção binária como compra e venda do
ativo subjacente. Estes testes fixam a economia correta e o recovery antes de
qualquer adaptador poder alcançar ``place_trade``.
"""
from decimal import Decimal

import pytest

from tradingagents.desk.ledger import Ledger, LedgerError

D = Decimal


def _reserved(amount="100"):
    ledger = Ledger(D("1000"), "USD")
    ledger.reserve(D(amount), ref="risk-r1", reservation_id="r1")
    return ledger


def test_abertura_move_aposta_da_reserva_para_exposicao_binaria():
    ledger = _reserved()

    tx = ledger.binary_open(
        position_id="pos-1", stake=D("100"), reservation_id="r1",
        instrument="EUR/USD", agent_id="trader-trend")

    assert tx.kind == "binary_open"
    assert ledger.cash_available() == D("900")
    assert ledger.reserved_cash("r1") == D("0")
    assert ledger.binary_at_risk("pos-1") == D("100")
    assert ledger.invested_cost() == D("100")
    assert ledger.portfolio_equity() == D("1000")
    assert ledger.equity() == D("1000")
    assert ledger.is_balanced()


def test_abertura_e_exatamente_uma_vez_e_conflito_nao_reescreve_historia():
    ledger = _reserved()
    first = ledger.binary_open(
        position_id="pos-2", stake=D("100"), reservation_id="r1",
        instrument="EUR/USD", agent_id="trader-trend")
    before = len(ledger.transactions)

    same = ledger.binary_open(
        position_id="pos-2", stake=D("100"), reservation_id="r1",
        instrument="EUR/USD", agent_id="trader-trend")
    assert same.tx_id == first.tx_id
    assert len(ledger.transactions) == before

    with pytest.raises(LedgerError, match="conflito"):
        ledger.binary_open(
            position_id="pos-2", stake=D("99"), reservation_id="r1",
            instrument="EUR/USD", agent_id="trader-trend")


@pytest.mark.parametrize(
    ("result", "returned", "cash", "pnl"),
    [
        ("win", "185", "1085", "85"),
        ("loss", "0", "900", "-100"),
        ("equal", "100", "1000", "0"),
        ("canceled", "60", "960", "-40"),
    ],
)
def test_liquidacao_contabiliza_retorno_total_sem_inventar_compra_spot(
        result, returned, cash, pnl):
    ledger = _reserved()
    ledger.binary_open(
        position_id="pos-3", stake=D("100"), reservation_id="r1",
        instrument="EUR/USD", agent_id="trader-trend")

    tx = ledger.binary_settle(
        position_id="pos-3", result=result,
        returned_amount=D(returned))

    assert tx.kind == "binary_settlement"
    assert ledger.cash_available() == D(cash)
    assert ledger.binary_at_risk("pos-3") == D("0")
    assert ledger.attribution().get("trader-trend", D("0")) == D(pnl)
    assert ledger.invested_cost() == D("0")
    assert ledger.portfolio_equity() == D(cash)
    assert ledger.open_positions() == set()
    assert ledger.is_balanced()


def test_liquidacao_repetida_apos_restart_nao_movimenta_dinheiro_duas_vezes():
    ledger = _reserved()
    ledger.binary_open(
        position_id="pos-4", stake=D("100"), reservation_id="r1",
        instrument="EUR/USD", agent_id="trader-trend")
    settled = ledger.binary_settle(
        position_id="pos-4", result="win", returned_amount=D("185"))
    restored = Ledger.from_transactions(
        ledger.export_transactions(), currency="USD")
    before = len(restored.transactions)

    same = restored.binary_settle(
        position_id="pos-4", result="win", returned_amount=D("185"))

    assert same.tx_id == settled.tx_id
    assert len(restored.transactions) == before
    assert restored.cash_available() == D("1085")
    assert restored.attribution()["trader-trend"] == D("85")
    assert restored.is_balanced()


def test_liquidacao_conflitante_ou_sem_abertura_falha_fechado():
    ledger = _reserved()
    ledger.binary_open(
        position_id="pos-5", stake=D("100"), reservation_id="r1",
        instrument="EUR/USD", agent_id="trader-trend")
    ledger.binary_settle(
        position_id="pos-5", result="loss", returned_amount=D("0"))

    with pytest.raises(LedgerError, match="conflito"):
        ledger.binary_settle(
            position_id="pos-5", result="win", returned_amount=D("185"))
    with pytest.raises(LedgerError, match="abertura"):
        ledger.binary_settle(
            position_id="desconhecida", result="loss", returned_amount=D("0"))


@pytest.mark.parametrize(
    ("result", "returned"),
    [
        ("win", "100"),
        ("loss", "1"),
        ("equal", "99"),
        ("canceled", "101"),
        ("unknown", "0"),
        ("win", "NaN"),
    ],
)
def test_liquidacao_recusa_semantica_financeira_impossivel(result, returned):
    ledger = _reserved()
    ledger.binary_open(
        position_id="pos-6", stake=D("100"), reservation_id="r1",
        instrument="EUR/USD", agent_id="trader-trend")

    with pytest.raises((LedgerError, ValueError), match="liquidação|resultado"):
        ledger.binary_settle(
            position_id="pos-6", result=result,
            returned_amount=D(returned))
    assert ledger.binary_at_risk("pos-6") == D("100")
    assert ledger.cash_available() == D("900")
    assert ledger.is_balanced()

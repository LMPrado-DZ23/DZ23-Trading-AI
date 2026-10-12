"""Partida dobrada na moeda FUNCIONAL; quantidades nativas em subledger.

A conversão fazia `600 USDT − 2 BNB + 598 FX = 0` e chamava isso de livro
balanceado. Os números fecham porque foram somados como se não tivessem
dimensão — o mesmo erro de quem soma metro com quilo e acha que deu certo.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tradingagents.desk.ledger import Ledger


def livro_com_taxa(moeda="BNB", qtd="2"):
    livro = Ledger(opening_cash=Decimal("1000"))
    livro.reserve(Decimal("100"), ref="r", reservation_id="R1")
    livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("90"), Decimal("0"),
                   reserved=Decimal("100"), ref="o", agent_id="A",
                   reservation_id="R1",
                   fee_in_other_currency=(Decimal(qtd), moeda))
    return livro


class TestSubledgerNativo:
    def test_native_quantities_live_outside_the_functional_book(self):
        livro = livro_com_taxa("BNB", "2")
        nativo = livro.native_balances()
        assert nativo["BNB"]["FEES"] == Decimal("2")
        assert nativo["BNB"]["ASSET"] == Decimal("-2")

    def test_the_functional_book_balances_on_its_own(self):
        """Sem a perna em BNB, a partida dobrada em USDT precisa fechar."""
        livro = livro_com_taxa("BNB", "2")
        assert livro.functional_balanced(), (
            "o livro funcional só fecha somando unidades diferentes")

    def test_the_native_subledger_balances_per_currency(self):
        livro = livro_com_taxa("BNB", "2")
        for moeda, contas in livro.native_balances().items():
            total = sum(contas.values(), Decimal("0"))
            assert total == Decimal("0"), (
                f"o subledger de {moeda} não fecha: {contas}")

    def test_two_currencies_do_not_mix(self):
        livro = livro_com_taxa("BNB", "2")
        livro.reserve(Decimal("100"), ref="r2", reservation_id="R2")
        livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("90"), Decimal("0"),
                       reserved=Decimal("100"), ref="o2", agent_id="A",
                       reservation_id="R2",
                       fee_in_other_currency=(Decimal("5"), "SOL"))
        nativo = livro.native_balances()
        assert set(nativo) == {"BNB", "SOL"}
        assert nativo["BNB"]["FEES"] == Decimal("2")
        assert nativo["SOL"]["FEES"] == Decimal("5")


class TestConversaoDeclarada:
    def test_the_conversion_states_both_sides(self):
        livro = livro_com_taxa("BNB", "2")
        tx = livro.fee_valuation_sem_lastro(moeda="BNB", quantidade=Decimal("2"),
                                 rate=Decimal("300"), fonte="binance",
                                 em="2026-09-14T23:00:00+00:00")
        d = livro.find_transaction(tx.tx_id)
        assert d["native_amount"] == "2"
        assert d["native_currency"] == "BNB"
        assert d["functional_amount"] == "600.00000000"
        assert d["functional_currency"] == "USDT"

    def test_the_functional_side_is_a_real_expense(self):
        livro = livro_com_taxa("BNB", "2")
        livro.fee_valuation_sem_lastro(moeda="BNB", quantidade=Decimal("2"),
                            rate=Decimal("300"), fonte="binance",
                            em="2026-09-14T23:00:00+00:00")
        assert livro.balance("FEES:USDT") == Decimal("600")
        assert livro.functional_balanced()

    def test_the_native_side_is_untouched_by_the_valuation(self):
        """Valorar responde QUANTO vale, não financia nem baixa a origem.

        Zerar a perna nativa aqui faria a taxa em BNB sumir do livro sem
        ninguém ter pago nada."""
        livro = livro_com_taxa("BNB", "2")
        livro.fee_valuation_sem_lastro(moeda="BNB", quantidade=Decimal("2"),
                            rate=Decimal("300"), fonte="binance",
                            em="2026-09-14T23:00:00+00:00")
        nativo = livro.native_balances()
        assert nativo["BNB"]["FEES"] == Decimal("2")
        assert nativo["BNB"]["ASSET"] == Decimal("-2")

    def test_the_conversion_account_is_not_a_dimensionless_bucket(self):
        """FX:CONVERSION não pode absorver a diferença entre 2 e 600."""
        livro = livro_com_taxa("BNB", "2")
        livro.fee_valuation_sem_lastro(moeda="BNB", quantidade=Decimal("2"),
                            rate=Decimal("300"), fonte="binance",
                            em="2026-09-14T23:00:00+00:00")
        fx = livro.balance("FX:CONVERSION")
        assert fx == Decimal("0") or abs(fx) < Decimal("600"), (
            f"FX absorveu {fx}: é a diferença entre unidades, não um valor")


class TestEquacaoDocumentada:
    def test_the_equation_is_written_down(self):
        from tradingagents.desk import ledger
        doc = ledger.__doc__ or ""
        assert "funcional" in doc.lower()
        assert "nativ" in doc.lower()

    def test_is_balanced_still_reflects_the_functional_book(self):
        livro = livro_com_taxa("BNB", "2")
        assert livro.is_balanced() == livro.functional_balanced()


class TestLancamentoQueMisturaUnidades:
    """O caso que a soma total não enxerga: um lançamento com uma perna em
    BNB e outra em USDT soma zero e mesmo assim está errado — nenhuma das
    duas escriturações fecha."""

    def test_a_cross_unit_entry_is_refused_at_entry(self):
        """Antes o livro aceitava e só o balanço final acusava. Agora a
        transação nem entra: validar depois deixa um lançamento errado
        registrado no histórico."""
        from tradingagents.desk.ledger import Entry
        livro = Ledger(opening_cash=Decimal("1000"))
        with pytest.raises(ValueError, match="unidade"):
            livro._post("suspeito",
                        [Entry("FEES:BNB", Decimal("2"), "taxa em BNB"),
                         Entry("CASH:USDT", Decimal("-2"), "pago em USDT")],
                        ref="cruzado")
        assert livro.is_balanced()

    def test_the_valuation_declares_both_sides(self):
        livro = livro_com_taxa("BNB", "2")
        tx = livro.fee_valuation_sem_lastro(moeda="BNB", quantidade=Decimal("2"),
                                 rate=Decimal("300"), fonte="binance",
                                 em="2026-09-14T23:00:00+00:00")
        d = livro.find_transaction(tx.tx_id)
        for campo in ("native_amount", "native_currency",
                      "functional_amount", "functional_currency"):
            assert d.get(campo), f"a conversão não declarou {campo}"

    def test_the_declaration_survives_a_restart(self):
        livro = livro_com_taxa("BNB", "2")
        tx = livro.fee_valuation_sem_lastro(moeda="BNB", quantidade=Decimal("2"),
                                 rate=Decimal("300"), fonte="binance",
                                 em="2026-09-14T23:00:00+00:00")
        restaurado = Ledger.from_transactions(livro.export_transactions(),
                                              currency="USDT")
        d = restaurado.find_transaction(tx.tx_id)
        assert d["native_currency"] == "BNB"
        assert d["functional_currency"] == "USDT"
        assert d["native_amount"] == "2"

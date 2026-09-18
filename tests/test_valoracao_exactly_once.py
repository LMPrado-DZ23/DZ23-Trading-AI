"""Valoração: só o que tem lastro, e uma vez só.

`aplicar_valoracao` resolvia o incidente inteiro mesmo quando o livro tinha
menos taxa registrada do que o incidente afirmava — a conversão baixava o
que existia e declarava tudo resolvido. E não havia serialização: duas
chamadas simultâneas podiam lançar duas vezes.
"""
from __future__ import annotations

import contextlib
from decimal import Decimal

import pytest

from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import DeskMandate, Mode
from tradingagents.desk.risk import RiskEngine


class Ator:
    actor_id = "risk-manager-1"
    capabilities = frozenset({"RESOLVE_RISK_INCIDENT", "VALUE_FEE_INCIDENT"})


def sistema(capital="10000"):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=("BTC/USDT",),
                    capital=Decimal(capital), slippage_buffer_pct=Decimal("0"))
    livro = Ledger(opening_cash=m.capital)
    return m, RiskEngine(m, carteira=livro), livro


def taxa_no_livro(livro, moeda, qtd, ref="a"):
    livro.reserve(Decimal("100"), ref=ref, reservation_id=f"R{ref}")
    livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("90"), Decimal("0"),
                   reserved=Decimal("100"), ref=ref, agent_id="A",
                   reservation_id=f"R{ref}",
                   fee_in_other_currency=(Decimal(qtd), moeda))


def incidente(risco, moeda="BNB", qtd="2"):
    risco.registrar_taxa_nao_valorada(moeda=moeda, quantidade=Decimal(qtd),
                                      order_id="o1", instrumento="BTC/USDT")
    return next(i for i in risco.incidentes_abertos()
                if i["type"] == "FEE_VALUATION_UNKNOWN")


def propor(risco, inc, rate="300"):
    risco.valorar_taxa(inc["incident_id"], rate=Decimal(rate),
                       fonte="binance ticker", em="2026-09-14T23:00:00+00:00")


class TestLastroNativo:
    def test_valuing_without_any_native_fee_is_refused(self):
        """Incidente diz 2 BNB, livro não tem nenhum: não há o que converter."""
        m, risco, livro = sistema()
        inc = incidente(risco, "BNB", "2")
        propor(risco, inc)
        with pytest.raises(ValueError, match="lastro|registrad|FEES:BNB"):
            risco.aplicar_valoracao(inc["incident_id"], actor=Ator(),
                                    evidence="sem lastro")
        assert risco.incidentes_abertos()
        assert livro.balance("FEES:USDT") == Decimal("0")

    def test_a_partial_backing_does_not_resolve_the_whole(self):
        """Livro tem 0,5 BNB e o incidente afirma 2: converter tudo
        inventaria 1,5 BNB que nunca foram registrados."""
        m, risco, livro = sistema()
        taxa_no_livro(livro, "BNB", "0.5")
        inc = incidente(risco, "BNB", "2")
        propor(risco, inc)
        with pytest.raises(ValueError, match="lastro|parcial|0.5"):
            risco.aplicar_valoracao(inc["incident_id"], actor=Ator(),
                                    evidence="parcial")
        assert risco.incidentes_abertos()

    def test_a_full_backing_resolves(self):
        m, risco, livro = sistema()
        taxa_no_livro(livro, "BNB", "2")
        inc = incidente(risco, "BNB", "2")
        propor(risco, inc)
        risco.aplicar_valoracao(inc["incident_id"], actor=Ator(),
                                evidence="lastro completo")
        # a perna nativa PERMANECE: a taxa em BNB foi cobrada de verdade e
        # valorar não a desfaz — o que entra é a despesa em cotação contra
        # o passivo de financiamento
        assert livro.balance("FEES:BNB") == Decimal("2")
        assert livro.balance("FEES:USDT") == Decimal("600")
        assert livro.balance("LIABILITY:FEE_FUNDING") == Decimal("-600")
        assert not [i for i in risco.incidentes_abertos()
                    if i["type"] == "FEE_VALUATION_UNKNOWN"]


class TestExactlyOnce:
    def test_four_threads_produce_one_entry(self):
        import threading
        m, risco, livro = sistema()
        taxa_no_livro(livro, "BNB", "2")
        inc = incidente(risco, "BNB", "2")
        propor(risco, inc)
        partida = threading.Barrier(4)
        erros = []

        def aplicar():
            partida.wait()
            try:
                risco.aplicar_valoracao(inc["incident_id"], actor=Ator(),
                                        evidence="concorrente")
            except Exception as exc:
                erros.append(exc)

        fios = [threading.Thread(target=aplicar) for _ in range(4)]
        for f in fios:
            f.start()
        for f in fios:
            f.join()

        lancamentos = [t for t in livro.export_transactions()
                       if t["kind"] == "fee_valuation"]
        assert len(lancamentos) == 1, (
            f"{len(lancamentos)} lançamentos de valoração para uma taxa só")
        assert livro.balance("FEES:USDT") == Decimal("600")

    def test_a_valuation_id_is_durable(self):
        m, risco, livro = sistema()
        taxa_no_livro(livro, "BNB", "2")
        inc = incidente(risco, "BNB", "2")
        propor(risco, inc)
        registro = risco.aplicar_valoracao(inc["incident_id"], actor=Ator(),
                                           evidence="ok",
                                           valuation_id="val-1")
        assert registro["valuation"]["valuation_id"] == "val-1"
        doc = risco.incidentes_doc()
        m2, risco2, livro2 = sistema()
        risco2.restaurar_incidentes(doc)
        alvo = next(i for i in risco2.incidentes()
                    if i["incident_id"] == inc["incident_id"])
        assert alvo["valuation"]["valuation_id"] == "val-1"
        assert alvo["valuation"]["status"] == "APLICADA"

    def test_a_retry_with_the_same_id_moves_nothing(self):
        m, risco, livro = sistema()
        taxa_no_livro(livro, "BNB", "2")
        inc = incidente(risco, "BNB", "2")
        propor(risco, inc)
        risco.aplicar_valoracao(inc["incident_id"], actor=Ator(),
                                evidence="ok", valuation_id="val-1")
        saldo = livro.balance("FEES:USDT")
        risco.aplicar_valoracao(inc["incident_id"], actor=Ator(),
                                evidence="retry", valuation_id="val-1")
        assert livro.balance("FEES:USDT") == saldo

    def test_a_crash_after_the_ledger_is_adopted(self):
        """Lançamento gravado, incidente ainda aberto: o retry ADOTA."""
        m, risco, livro = sistema()
        taxa_no_livro(livro, "BNB", "2")
        inc = incidente(risco, "BNB", "2")
        propor(risco, inc)
        tx = livro.fee_valuation(moeda="BNB", quantidade=Decimal("2"),
                                 rate=Decimal("300"), fonte="binance ticker",
                                 em="2026-09-14T23:00:00+00:00",
                                 incident_id=inc["incident_id"])
        saldo = livro.balance("FEES:USDT")

        registro = risco.aplicar_valoracao(inc["incident_id"], actor=Ator(),
                                           evidence="retomada")
        assert livro.balance("FEES:USDT") == saldo, "lançou de novo"
        assert registro["status"] == "RESOLVED"
        assert registro["valuation"]["ledger_tx_id"] == tx.tx_id


class TestSerializacaoForcada:
    """Quatro threads rápidas podem passar por sorte de escalonamento. Aqui
    o lançamento é deliberadamente lento, para que a janela entre 'não
    existe' e 'existe' fique aberta de verdade."""

    def test_a_slow_ledger_write_still_produces_one_entry(self):
        import threading
        import time
        m, risco, livro = sistema()
        taxa_no_livro(livro, "BNB", "2")
        inc = incidente(risco, "BNB", "2")
        propor(risco, inc)

        original = livro.fee_valuation

        def lento(*args, **kwargs):
            time.sleep(0.05)          # mantém a janela aberta
            return original(*args, **kwargs)

        livro.fee_valuation = lento
        partida = threading.Barrier(4)

        def aplicar():
            partida.wait()
            # As perdedoras da corrida levantam; o que importa é o efeito
            # no livro, verificado no fim.
            with contextlib.suppress(Exception):
                risco.aplicar_valoracao(inc["incident_id"], actor=Ator(),
                                        evidence="concorrente")

        fios = [threading.Thread(target=aplicar) for _ in range(4)]
        for f in fios:
            f.start()
        for f in fios:
            f.join()

        lancamentos = [t for t in livro.export_transactions()
                       if t["kind"] == "fee_valuation"]
        assert len(lancamentos) == 1, (
            f"{len(lancamentos)} lançamentos: a janela não estava serializada")
        assert livro.balance("FEES:USDT") == Decimal("600")

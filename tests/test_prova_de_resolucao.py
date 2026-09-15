"""Resolver incidente exige PROVA econômica, não descrição.

`resolve_risk_incident` checava invariantes gerais de saldo — que já estão
verdes logo depois de um déficit, porque o dinheiro saiu do caixa e a
aritmética fecha. Resultado: bastava escrever "aporte realizado" para
destravar a mesa, sem nenhum movimento contábil correspondente.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from tradingagents.desk.execution import ExecutionCoordinator
from tradingagents.desk.intents import ExecutionIntent, Kind, Side
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import Action, DeskMandate, Mode, TradeProposal, utcnow
from tradingagents.desk.risk import RiskEngine


class Corretora:
    mode = "testnet"

    def submit(self, coid, instrument, side, quantity, final_state=None):
        return {"id": f"ex-{coid}", "status": "open", "filled": 0}


class Ator:
    actor_id = "risk-manager-1"
    capabilities = frozenset({"RESOLVE_RISK_INCIDENT"})


def sistema(capital="1000"):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=("BTC/USDT", "ETH/USDT"),
                    capital=Decimal(capital), slippage_buffer_pct=Decimal("0"))
    livro = Ledger(opening_cash=m.capital)
    risco = RiskEngine(m, carteira=livro)
    coord = ExecutionCoordinator(m, livro, risco, worker_id="w",
                                 broker=Corretora())
    return m, risco, livro, coord


def criar_deficit(coord, risco, excesso="10"):
    p = TradeProposal(agent_id="A", strategy="s", instrument="BTC/USDT",
                      action=Action.BUY, reason="t",
                      desired_exposure_pct=Decimal("0.10"),
                      confidence=Decimal("0.8"),
                      valid_until=utcnow() + timedelta(minutes=5))
    res = risco.reserve(p, Decimal("100"), agent_id="A")
    ei = ExecutionIntent(kind=Kind.ENTRY, side=Side.BUY, instrument="BTC/USDT",
                         quantity=res.quantity, agent_id="A",
                         account="conta-1", client_order_id="c1",
                         reservation_id=res.reservation_id,
                         reserved_amount=res.amount)
    rec = coord.submit_intent(ei)
    preco = (res.hard_reserved_amount + Decimal(excesso)) / res.quantity
    coord.aplicar_fill(rec, side="buy", quantidade=res.quantity, preco=preco,
                       taxa=Decimal("0"), fill_id="f1")
    return next(i for i in risco.incidentes_abertos()
                if i["type"] == "RESERVATION_DEFICIT")


class TestTextoNaoResolve:
    def test_a_description_alone_is_refused(self):
        m, risco, livro, coord = sistema()
        inc = criar_deficit(coord, risco, "10")
        with pytest.raises(ValueError, match="prova|compensa|ajuste"):
            risco.resolve_risk_incident(
                inc["incident_id"], actor=Ator(),
                evidence="aporte realizado", reason="corrigido")
        assert risco.incidentes_abertos(), "o incidente fechou só com texto"
        assert risco.deficit_total() == Decimal("10")

    def test_the_general_invariants_are_green_and_still_not_enough(self):
        """É por isso que o genérico não servia: a aritmética já fecha."""
        m, risco, livro, coord = sistema()
        criar_deficit(coord, risco, "10")
        assert livro.is_balanced()
        ok, _ = risco._invariantes_de_saldo()
        assert ok is True, "o cenário precisa ter saldos coerentes"


class TestCompensacaoEconomica:
    def test_an_adjustment_that_covers_it_allows_resolution(self):
        m, risco, livro, coord = sistema()
        inc = criar_deficit(coord, risco, "10")
        caixa_antes = livro.cash_available()
        tx = livro.capital_adjustment(
            Decimal("10"), reason="aporte para cobrir déficit",
            actor="risk-manager-1", external_reference="TED 2026-09-14")
        assert livro.cash_available() == caixa_antes + Decimal("10")

        risco.resolve_risk_incident(
            inc["incident_id"], actor=Ator(), evidence="TED 2026-09-14",
            reason="aporte", resolution_refs={"ledger_tx_ids": [tx.tx_id]})

        assert not risco.incidentes_abertos()
        assert risco.deficit_total() == Decimal("0")
        assert risco.historical_deficit_total() == Decimal("10")

    def test_an_insufficient_adjustment_is_refused(self):
        m, risco, livro, coord = sistema()
        inc = criar_deficit(coord, risco, "10")
        tx = livro.capital_adjustment(Decimal("5"), reason="metade",
                                      actor="op", external_reference="x")
        with pytest.raises(ValueError, match="cobre|insuficiente"):
            risco.resolve_risk_incident(
                inc["incident_id"], actor=Ator(), evidence="parcial",
                reason="", resolution_refs={"ledger_tx_ids": [tx.tx_id]})
        assert risco.deficit_total() == Decimal("10")

    def test_an_unrelated_transaction_is_refused(self):
        m, risco, livro, coord = sistema()
        inc = criar_deficit(coord, risco, "10")
        # um lançamento de compra qualquer não é compensação de capital
        outra = livro.export_transactions()[-1]["tx_id"]
        with pytest.raises(ValueError, match="ajuste|não é"):
            risco.resolve_risk_incident(
                inc["incident_id"], actor=Ator(), evidence="qualquer",
                reason="", resolution_refs={"ledger_tx_ids": [outra]})

    def test_an_unknown_transaction_is_refused(self):
        m, risco, livro, coord = sistema()
        inc = criar_deficit(coord, risco, "10")
        with pytest.raises(ValueError, match="não encontrad|inexistente"):
            risco.resolve_risk_incident(
                inc["incident_id"], actor=Ator(), evidence="x", reason="",
                resolution_refs={"ledger_tx_ids": ["tx-que-nao-existe"]})

    def test_the_same_proof_cannot_resolve_two_incidents(self):
        """Uma compensação de 10 não paga duas dívidas de 10."""
        m, risco, livro, coord = sistema()
        inc1 = criar_deficit(coord, risco, "10")
        risco.abrir_incidente("RESERVATION_DEFICIT", amount=Decimal("10"),
                              reservation_id="R-outra")
        inc2 = next(i for i in risco.incidentes_abertos()
                    if i["reservation_id"] == "R-outra")
        tx = livro.capital_adjustment(Decimal("10"), reason="aporte",
                                      actor="op", external_reference="TED")
        risco.resolve_risk_incident(
            inc1["incident_id"], actor=Ator(), evidence="TED", reason="",
            resolution_refs={"ledger_tx_ids": [tx.tx_id]})
        with pytest.raises(ValueError, match="já foi usad|reutiliz"):
            risco.resolve_risk_incident(
                inc2["incident_id"], actor=Ator(), evidence="TED", reason="",
                resolution_refs={"ledger_tx_ids": [tx.tx_id]})


class TestAporteNaoEhLucro:
    def test_the_adjustment_does_not_become_trader_pnl(self):
        m, risco, livro, coord = sistema()
        livro.capital_adjustment(Decimal("10"), reason="aporte", actor="op",
                                 external_reference="TED")
        assert livro.attribution() == {} or all(
            v == Decimal("0") for v in livro.attribution().values()), (
            "o aporte virou resultado de trader")
        assert livro.is_balanced()

    def test_it_lands_on_contributed_capital(self):
        m, risco, livro, coord = sistema()
        livro.capital_adjustment(Decimal("10"), reason="aporte", actor="op",
                                 external_reference="TED")
        saldos = livro.balances()
        assert "CONTRIBUTED_CAPITAL" in saldos
        assert saldos["CONTRIBUTED_CAPITAL"] == Decimal("-10")

    def test_opening_cash_is_never_rewritten(self):
        m, risco, livro, coord = sistema()
        abertura = [t for t in livro.export_transactions()
                    if t["kind"] == "opening"]
        livro.capital_adjustment(Decimal("10"), reason="a", actor="o",
                                 external_reference="r")
        assert [t for t in livro.export_transactions()
                if t["kind"] == "opening"] == abertura, (
            "o lançamento de abertura foi reescrito")


class TestValidadorPorTipo:
    def test_a_coverage_mismatch_uses_its_own_criterion(self):
        """Compensação de capital não conserta divergência de cobertura."""
        m, risco, livro, coord = sistema()
        risco.abrir_incidente("COVERAGE_LEDGER_MISMATCH", amount=Decimal("5"),
                              reservation_id="R1")
        inc = risco.incidentes_abertos()[0]
        tx = livro.capital_adjustment(Decimal("50"), reason="aporte",
                                      actor="op", external_reference="x")
        # o critério aqui é a reconciliação dos saldos, não o aporte
        risco.resolve_risk_incident(
            inc["incident_id"], actor=Ator(), evidence="saldos reconciliados",
            reason="", resolution_refs={"ledger_tx_ids": [tx.tx_id]})
        assert not risco.incidentes_abertos()

    def test_the_dispatch_exists(self):
        import inspect
        fonte = inspect.getsource(RiskEngine)
        assert "validate_incident_resolution" in fonte


class TestRegistroDaResolucao:
    def test_the_proof_is_recorded(self):
        m, risco, livro, coord = sistema()
        inc = criar_deficit(coord, risco, "10")
        tx = livro.capital_adjustment(Decimal("10"), reason="aporte",
                                      actor="op", external_reference="TED-77")
        risco.resolve_risk_incident(
            inc["incident_id"], actor=Ator(), evidence="TED-77", reason="ok",
            resolution_refs={"ledger_tx_ids": [tx.tx_id]})
        registro = next(i for i in risco.incidentes()
                        if i["incident_id"] == inc["incident_id"])
        assert registro["resolution_refs"]["ledger_tx_ids"] == [tx.tx_id]
        assert registro["validated_by_policy"]
        assert registro["policy_version"]


class TestDeficitsSaiDoRuntime:
    def test_the_new_snapshot_has_no_legacy_deficits(self):
        m, risco, livro, coord = sistema()
        criar_deficit(coord, risco, "10")
        doc = risco.incidentes_doc()
        assert "deficits" not in doc or not doc["deficits"], (
            "o snapshot novo ainda grava o estado paralelo")
        assert doc["incidentes"]

    def test_the_reader_still_migrates_the_old_field(self):
        m, risco, livro, coord = sistema()
        risco.restaurar_incidentes({"deficits": {"R-velha": "7"},
                                    "conclusoes": {}, "incidentes": {}})
        assert risco.deficit_total() == Decimal("7")
        # e o snapshot seguinte já sai limpo
        doc = risco.incidentes_doc()
        assert not doc.get("deficits")

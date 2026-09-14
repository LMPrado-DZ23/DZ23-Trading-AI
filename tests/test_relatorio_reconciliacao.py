"""Relatório de reconciliação: durável, identificado e com validade.

Um SAFE em memória morre no primeiro deploy — e, pior, um SAFE sem
identidade poderia ser confundido com o de outro inquilino ou outra conta.
"""
from __future__ import annotations

from decimal import Decimal

from tradingagents.desk.execution import ExecutionCoordinator
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import DeskMandate, Mode
from tradingagents.desk.risk import RiskEngine


def sistema(conta="conta-1", tenant="tenant-A"):
    m = DeskMandate(mandate_id="m", owner=tenant, account=conta,
                    mode=Mode.PAPER, instruments=("BTC/USDT",),
                    capital=Decimal("10000"))
    risco = RiskEngine(m)
    livro = Ledger(opening_cash=m.capital)
    coord = ExecutionCoordinator(m, livro, risco, worker_id="w")
    coord.tenant_id = tenant
    return m, risco, livro, coord


def reconciliar_limpo(coord):
    return coord.reconciliar(
        lambda coid: {"resultado": "CONFIRMED_NOT_FOUND", "ordem": None},
        consultar_posicoes=lambda: {},
        consultar_fills=lambda coid, exid="": [])


class TestIdentidadeDoRelatorio:
    def test_report_carries_who_and_what_it_refers_to(self):
        m, risco, livro, coord = sistema()
        rel = reconciliar_limpo(coord)
        for campo in ("report_id", "tenant_id", "account", "mandate_id",
                      "worker_id", "oms_version", "em"):
            assert campo in rel, f"relatório sem {campo}"
        assert rel["account"] == "conta-1"
        assert rel["tenant_id"] == "tenant-A"
        assert rel["mandate_id"] == "m"

    def test_each_report_has_its_own_id(self):
        m, risco, livro, coord = sistema()
        a = reconciliar_limpo(coord)
        b = reconciliar_limpo(coord)
        assert a["report_id"] != b["report_id"]


class TestValidadeDoRelatorio:
    def test_a_safe_report_authorises_only_its_own_version(self):
        m, risco, livro, coord = sistema()
        rel = reconciliar_limpo(coord)
        assert rel["seguro"] is True
        ok, motivo = coord.relatorio_valido()
        assert ok is True, motivo

        coord.oms_version += 1          # estado financeiro mudou
        ok, motivo = coord.relatorio_valido()
        assert ok is False and "versão" in motivo.lower()

    def test_no_report_means_not_valid(self):
        m, risco, livro, coord = sistema()
        ok, motivo = coord.relatorio_valido()
        assert ok is False and "nenhuma" in motivo.lower()

    def test_an_unsafe_report_never_authorises(self):
        m, risco, livro, coord = sistema()
        rel = coord.reconciliar(lambda coid: {"resultado": "UNKNOWN",
                                              "ordem": None, "motivo": "x"})
        assert rel["seguro"] is False
        ok, _ = coord.relatorio_valido()
        assert ok is False

    def test_report_from_another_account_is_refused(self):
        m, risco, livro, coord = sistema(conta="conta-1")
        rel = reconciliar_limpo(coord)
        alheio = {**rel, "account": "conta-2"}
        coord.ultimo_relatorio = alheio
        ok, motivo = coord.relatorio_valido()
        assert ok is False and "conta" in motivo.lower()

    def test_report_from_another_tenant_is_refused(self):
        m, risco, livro, coord = sistema(tenant="tenant-A")
        rel = reconciliar_limpo(coord)
        coord.ultimo_relatorio = {**rel, "tenant_id": "tenant-B"}
        ok, motivo = coord.relatorio_valido()
        assert ok is False and ("inquilino" in motivo.lower()
                                or "tenant" in motivo.lower())


class TestDurabilidade:
    def test_report_survives_a_restart(self):
        m, risco, livro, coord = sistema()
        rel = reconciliar_limpo(coord)
        doc = coord.snapshot_estado()

        m2, risco2, livro2, coord2 = sistema()
        coord2.restaurar_estado(doc)
        assert coord2.ultimo_relatorio is not None, (
            "o relatório morreu no reinício: o SAFE era só de memória")
        assert coord2.ultimo_relatorio["report_id"] == rel["report_id"]
        ok, motivo = coord2.relatorio_valido()
        assert ok is True, motivo

    def test_a_financial_change_after_restart_invalidates_it(self):
        m, risco, livro, coord = sistema()
        reconciliar_limpo(coord)
        doc = coord.snapshot_estado()
        m2, risco2, livro2, coord2 = sistema()
        coord2.restaurar_estado(doc)
        coord2.oms_version += 1
        ok, _ = coord2.relatorio_valido()
        assert ok is False

    def test_report_is_in_the_snapshot_document(self):
        m, risco, livro, coord = sistema()
        reconciliar_limpo(coord)
        doc = coord.snapshot_estado()
        assert doc.get("ultimo_relatorio"), "relatório fora do documento"
        assert doc["ultimo_relatorio"]["report_id"]

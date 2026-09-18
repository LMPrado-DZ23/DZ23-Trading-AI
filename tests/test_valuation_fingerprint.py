"""Recovery compara o CONTEÚDO da valoração, não só a existência.

O retry pós-crash adotava qualquer lançamento de valoração do incidente e
gravava os metadados novos por cima. Se o processo anterior converteu 2 BNB
a 300 e o retry tenta a 400, adotar o antigo e registrar 400 produz um
registro que diz uma coisa e um lançamento que diz outra.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import DeskMandate, Mode
from tradingagents.desk.risk import RecoveryConflict, RiskEngine


class Ator:
    actor_id = "risk-manager-1"
    capabilities = frozenset({"RESOLVE_RISK_INCIDENT", "VALUE_FEE_INCIDENT"})


def sistema(capital="10000"):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=("BTC/USDT",),
                    capital=Decimal(capital), slippage_buffer_pct=Decimal("0"))
    livro = Ledger(opening_cash=m.capital)
    return m, RiskEngine(m, carteira=livro), livro


def cobrar(livro, moeda="BNB", qtd="2", ref="a"):
    livro.reserve(Decimal("100"), ref=ref, reservation_id=f"R{ref}")
    livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("90"), Decimal("0"),
                   reserved=Decimal("100"), ref=ref, agent_id="A",
                   reservation_id=f"R{ref}", fill_id=f"F{ref}",
                   native_fees=[(Decimal(qtd), moeda)])


def incidente(risco, qtd="2"):
    risco.registrar_taxa_nao_valorada(moeda="BNB", quantidade=Decimal(qtd),
                                      order_id="o1", instrumento="BTC/USDT")
    return next(i for i in risco.incidentes_abertos()
                if i["type"] == "FEE_VALUATION_UNKNOWN")


class TestFingerprintNoLivro:
    def test_the_valuation_records_its_identity(self):
        m, risco, livro = sistema()
        cobrar(livro)
        ids = [c["fee_component_id"] for c in livro.fee_components("BNB")]
        tx = livro.fee_valuation(moeda="BNB", quantidade=Decimal("2"),
                                 rate=Decimal("300"), fonte="binance",
                                 em="2026-09-14T23:00:00+00:00",
                                 fee_component_ids=ids,
                                 incident_id="inc_1", valuation_id="val-A")
        d = livro.find_transaction(tx.tx_id)
        assert d["valuation_id"] == "val-A"
        assert d["valuation_fingerprint"], "sem impressão, nada a comparar"

    def test_the_fingerprint_covers_what_matters(self):
        """Mudar cotação, fonte, instante, valor ou componentes muda a
        impressão; um detalhe irrelevante não."""
        m, risco, livro = sistema()
        cobrar(livro)
        ids = [c["fee_component_id"] for c in livro.fee_components("BNB")]
        base = {"moeda": "BNB", "quantidade": Decimal("2"),
                "rate": Decimal("300"), "fonte": "binance",
                "em": "2026-09-14T23:00:00+00:00", "fee_component_ids": ids,
                "incident_id": "inc_1", "valuation_id": "val-A"}
        impressao = livro.fingerprint_de_valoracao(**base)
        for campo, valor in (("rate", Decimal("400")), ("fonte", "outra"),
                             ("em", "2026-09-15T10:00:00+00:00"),
                             ("quantidade", Decimal("3"))):
            assert livro.fingerprint_de_valoracao(**{**base, campo: valor}) \
                != impressao, f"{campo} não entra na impressão"

    def test_it_survives_a_restart(self):
        m, risco, livro = sistema()
        cobrar(livro)
        ids = [c["fee_component_id"] for c in livro.fee_components("BNB")]
        tx = livro.fee_valuation(moeda="BNB", quantidade=Decimal("2"),
                                 rate=Decimal("300"), fonte="binance",
                                 em="2026-09-14T23:00:00+00:00",
                                 fee_component_ids=ids, incident_id="inc_1",
                                 valuation_id="val-A")
        restaurado = Ledger.from_transactions(livro.export_transactions(),
                                              currency="USDT")
        d = restaurado.find_transaction(tx.tx_id)
        assert d["valuation_id"] == "val-A"
        assert d["valuation_fingerprint"]


class TestConflitoDeRecovery:
    def _preparar(self):
        m, risco, livro = sistema()
        cobrar(livro)
        inc = incidente(risco)
        return m, risco, livro, inc

    def test_a_retry_with_a_different_rate_is_a_conflict(self):
        """O processo anterior converteu a 300; este quer 400."""
        m, risco, livro, inc = self._preparar()
        ids = [c["fee_component_id"] for c in livro.fee_components("BNB")]
        livro.fee_valuation(moeda="BNB", quantidade=Decimal("2"),
                            rate=Decimal("300"), fonte="binance",
                            em="2026-09-14T23:00:00+00:00",
                            fee_component_ids=ids,
                            incident_id=inc["incident_id"],
                            valuation_id="val-A")
        saldo = livro.balance("FEES:USDT")

        risco.valorar_taxa(inc["incident_id"], rate=Decimal("400"),
                           fonte="binance", em="2026-09-15T10:00:00+00:00")
        with pytest.raises(RecoveryConflict, match="300|cota|impress"):
            risco.aplicar_valoracao(inc["incident_id"], actor=Ator(),
                                    evidence="retry com outra cotação",
                                    valuation_id="val-B")
        assert livro.balance("FEES:USDT") == saldo, "lançou por cima"
        registro = next(i for i in risco.incidentes()
                        if i["incident_id"] == inc["incident_id"])
        assert registro["status"] != "RESOLVED", (
            "resolveu com metadado que não corresponde ao lançamento")

    def test_a_retry_with_the_same_content_is_adopted(self):
        m, risco, livro, inc = self._preparar()
        ids = [c["fee_component_id"] for c in livro.fee_components("BNB")]
        livro.fee_valuation(moeda="BNB", quantidade=Decimal("2"),
                            rate=Decimal("300"), fonte="binance",
                            em="2026-09-14T23:00:00+00:00",
                            fee_component_ids=ids,
                            incident_id=inc["incident_id"],
                            valuation_id="val-A")
        saldo = livro.balance("FEES:USDT")

        risco.valorar_taxa(inc["incident_id"], rate=Decimal("300"),
                           fonte="binance", em="2026-09-14T23:00:00+00:00")
        registro = risco.aplicar_valoracao(inc["incident_id"], actor=Ator(),
                                           evidence="retomada",
                                           valuation_id="val-A")
        assert livro.balance("FEES:USDT") == saldo, "lançou de novo"
        assert registro["status"] == "RESOLVED"

    def test_a_different_source_is_also_a_conflict(self):
        m, risco, livro, inc = self._preparar()
        ids = [c["fee_component_id"] for c in livro.fee_components("BNB")]
        livro.fee_valuation(moeda="BNB", quantidade=Decimal("2"),
                            rate=Decimal("300"), fonte="binance",
                            em="2026-09-14T23:00:00+00:00",
                            fee_component_ids=ids,
                            incident_id=inc["incident_id"],
                            valuation_id="val-A")
        risco.valorar_taxa(inc["incident_id"], rate=Decimal("300"),
                           fonte="fonte-diferente",
                           em="2026-09-14T23:00:00+00:00")
        with pytest.raises(RecoveryConflict):
            risco.aplicar_valoracao(inc["incident_id"], actor=Ator(),
                                    evidence="x", valuation_id="val-B")

    def test_the_conflict_names_what_differs(self):
        m, risco, livro, inc = self._preparar()
        ids = [c["fee_component_id"] for c in livro.fee_components("BNB")]
        livro.fee_valuation(moeda="BNB", quantidade=Decimal("2"),
                            rate=Decimal("300"), fonte="binance",
                            em="2026-09-14T23:00:00+00:00",
                            fee_component_ids=ids,
                            incident_id=inc["incident_id"],
                            valuation_id="val-A")
        risco.valorar_taxa(inc["incident_id"], rate=Decimal("400"),
                           fonte="binance", em="2026-09-14T23:00:00+00:00")
        with pytest.raises(RecoveryConflict) as erro:
            risco.aplicar_valoracao(inc["incident_id"], actor=Ator(),
                                    evidence="x", valuation_id="val-B")
        texto = str(erro.value)
        assert "val-A" in texto, "não diz qual valoração já existe"
        assert "300" in texto or "400" in texto, "não diz o que diverge"


class TestIdNaoEhConteudo:
    """Dois identificadores diferentes para o MESMO conteúdo não são
    divergência: o segundo processo só não sabia o id do primeiro. Incluir o
    id na impressão transformaria toda retomada em conflito."""

    def test_the_same_content_under_another_id_is_adopted(self):
        m, risco, livro = sistema()
        cobrar(livro)
        inc = incidente(risco)
        ids = [c["fee_component_id"] for c in livro.fee_components("BNB")]
        livro.fee_valuation(moeda="BNB", quantidade=Decimal("2"),
                            rate=Decimal("300"), fonte="binance",
                            em="2026-09-14T23:00:00+00:00",
                            fee_component_ids=ids,
                            incident_id=inc["incident_id"],
                            valuation_id="val-do-processo-anterior")
        saldo = livro.balance("FEES:USDT")

        risco.valorar_taxa(inc["incident_id"], rate=Decimal("300"),
                           fonte="binance", em="2026-09-14T23:00:00+00:00")
        registro = risco.aplicar_valoracao(
            inc["incident_id"], actor=Ator(), evidence="retomada sem saber o id",
            valuation_id="val-novo")
        assert livro.balance("FEES:USDT") == saldo, "lançou de novo"
        assert registro["status"] == "RESOLVED", (
            "tratou id diferente como conteúdo diferente")
        # B9/B1: identidade canônica ÚNICA. O livro guarda a do processo
        # anterior; incidente e refs têm de convergir para ELA — não para o
        # id novo que este processo trouxe sem conhecer o do primeiro. Duas
        # identidades para a mesma operação é o defeito.
        canonico = "val-do-processo-anterior"
        ledger_tx = livro.find_fee_valuation(inc["incident_id"])
        assert ledger_tx["valuation_id"] == canonico, "livro perdeu o id"
        assert registro["valuation"]["valuation_id"] == canonico, (
            "incidente gravou o id novo por cima do canônico")
        assert registro["resolution_refs"]["valuation_id"] == canonico, (
            "resolution_refs aponta para id que não é o do lançamento")
        assert registro["resolution_refs"].get("requested_valuation_id") \
            == "val-novo", "o id trazido devia ficar como alias de auditoria"

    def test_the_fingerprint_ignores_the_id(self):
        m, risco, livro = sistema()
        base = {"moeda": "BNB", "quantidade": Decimal("2"),
                "rate": Decimal("300"), "fonte": "binance",
                "em": "2026-09-14T23:00:00+00:00",
                "fee_component_ids": ["fc_1"], "incident_id": "inc_1"}
        assert livro.fingerprint_de_valoracao(**base, valuation_id="A") == \
            livro.fingerprint_de_valoracao(**base, valuation_id="B")

"""Reconciliação exchange-first: os testes que o auditor exigiu.

Cada um nasceu de um caminho REAL em que o sistema declarava SAFE sem ter
conferido a verdade financeira na corretora.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tradingagents.desk.execution import ESTADOS_NAO_TERMINAIS, ExecutionCoordinator, OrderState
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import Action, DeskMandate, Mode, TradeProposal, utcnow
from tradingagents.desk.risk import RiskEngine


def sistema(capital="1000"):
    m = DeskMandate(mandate_id="m", owner="p", account="a", mode=Mode.PAPER,
                    instruments=("BTC/USDT",), capital=Decimal(capital))
    risco = RiskEngine(m)
    livro = Ledger(opening_cash=m.capital)
    return m, risco, livro, ExecutionCoordinator(m, livro, risco, worker_id="w")


def reserva(risco, preco="100"):
    from datetime import timedelta
    p = TradeProposal(agent_id="trader-trend", strategy="trend_following",
                      instrument="BTC/USDT", action=Action.BUY, reason="t",
                      desired_exposure_pct=Decimal("0.15"),
                      confidence=Decimal("0.8"),
                      valid_until=utcnow() + timedelta(minutes=5))
    return risco.reserve(p, Decimal(preco), agent_id="trader-trend")


def doc_com_ordem(estado: OrderState, res, *, posicoes=None, livro=None):
    """Documento persistido como o snapshot produz."""
    return {
        "schema": "dz23.oms/2", "worker_id": "w", "dia": "", "perda_do_dia": "0",
        "ordens": [{"order_id": "ord-1", "estado": estado.value,
                    "fingerprint": "fp-1", "filled": "0", "preco_medio": "0",
                    "fees": "0", "fills_vistos": [], "external_id": "",
                    "nota": "", "instrumento": "BTC/USDT", "quantidade": "1.5",
                    "lado": "buy", "client_order_id": "coid-1",
                    "agente": "trader-trend",
                    "reserva_id": res.reservation_id, "procedencia": None}],
        "posicoes": posicoes or {}, "caixa": "850", "moeda": "USDT",
        "lancamentos": livro or [],
        "reservas": [{"reservation_id": res.reservation_id, "account": "a",
                      "agent_id": "trader-trend", "instrument": "BTC/USDT",
                      "amount": str(res.amount), "quantity": str(res.quantity),
                      "mandate_version": 1, "expires_at": None}],
    }


class TestTodasAsOrdensNaoTerminaisSaoConferidas:
    """ACHADO CRÍTICO 1: só ordens UNKNOWN eram consultadas. Uma
    ACKNOWLEDGED restaurada podia ter sido preenchida enquanto o processo
    estava morto — e o portão liberava assim mesmo."""

    @pytest.mark.parametrize("estado", [
        OrderState.SUBMITTING, OrderState.ACKNOWLEDGED,
        OrderState.PARTIALLY_FILLED, OrderState.CANCEL_PENDING,
        OrderState.UNKNOWN_RECONCILIATION_REQUIRED])
    def test_every_non_terminal_state_is_queried_on_the_exchange(self, estado):
        m, risco, livro, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc_com_ordem(estado, res))
        consultas = []

        def consultar(coid):
            consultas.append(coid)
            return {"status": "canceled"}

        coord.reconciliar(consultar, consultar_posicoes=lambda: {})
        assert consultas == ["coid-1"], (
            f"ordem em {estado.value} NÃO foi conferida na corretora")

    def test_the_set_of_non_terminal_states_is_explicit(self):
        assert OrderState.ACKNOWLEDGED in ESTADOS_NAO_TERMINAIS
        assert OrderState.FILLED not in ESTADOS_NAO_TERMINAIS
        assert OrderState.REJECTED not in ESTADOS_NAO_TERMINAIS

    def test_open_order_on_the_exchange_keeps_the_gate_closed(self):
        m, risco, _, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc_com_ordem(OrderState.ACKNOWLEDGED, res))
        rel = coord.reconciliar(lambda coid: {"status": "open"},
                                consultar_posicoes=lambda: {})
        assert rel["seguro"] is False
        assert coord.reconciliacao_pendente is True


class TestPosicoesSaoComparadasComACorretora:
    """ACHADO CRÍTICO 2: posições restauradas eram limpas sem comparação."""

    def test_remote_orphan_position_is_detected(self):
        """Local não sabe da posição; a corretora tem 0.5 BTC."""
        m, risco, _, coord = sistema()
        rel = coord.reconciliar(lambda coid: None,
                                consultar_posicoes=lambda: {"BTC/USDT": "0.5"})
        assert rel["seguro"] is False
        assert any("POSICAO_ORFA_REMOTA" in d for d in rel["divergencias"])

    def test_local_position_missing_on_the_exchange_is_not_erased(self):
        m, risco, livro, coord = sistema()
        livro.reserve(Decimal("150"), ref="r")
        livro.fill_buy("BTC/USDT", Decimal("1.5"), Decimal("100"), Decimal("0"),
                       reserved=Decimal("150"), ref="o", agent_id="t")
        rel = coord.reconciliar(lambda coid: None, consultar_posicoes=lambda: {})
        assert rel["seguro"] is False
        assert any("POSICAO_AUSENTE_REMOTA" in d for d in rel["divergencias"])
        assert livro.open_positions_detail(), "posição apagada silenciosamente"

    def test_quantity_mismatch_is_detected(self):
        m, risco, livro, coord = sistema()
        livro.reserve(Decimal("150"), ref="r")
        livro.fill_buy("BTC/USDT", Decimal("0.5"), Decimal("100"), Decimal("0"),
                       reserved=Decimal("150"), ref="o", agent_id="t")
        rel = coord.reconciliar(lambda coid: None,
                                consultar_posicoes=lambda: {"BTC/USDT": "0.7"})
        assert rel["seguro"] is False
        assert any("QUANTIDADE_DIVERGENTE" in d for d in rel["divergencias"])

    def test_without_position_query_the_status_is_unknown_not_safe(self):
        m, risco, livro, coord = sistema()
        livro.reserve(Decimal("150"), ref="r")
        livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("100"), Decimal("0"),
                       reserved=Decimal("150"), ref="o", agent_id="t")
        rel = coord.reconciliar(lambda coid: None)      # sem consulta de posição
        assert rel["posicoes_status"] == "UNKNOWN" and rel["seguro"] is False


class TestFillRemotoEntraNoLivro:
    """ACHADO CRÍTICO 3: FILLED na corretora só mudava campos do registro —
    o livro ficava sem a posição e a reserva presa."""

    def test_remote_fill_creates_the_position_and_consumes_the_reservation(self):
        m, risco, livro, coord = sistema()
        res = reserva(risco)
        assert risco.available == Decimal("850.00")
        coord.restaurar_estado(doc_com_ordem(OrderState.ACKNOWLEDGED, res))

        rel = coord.reconciliar(
            lambda coid: {"id": "ex-9", "status": "filled",
                          "filled": "1.5", "average": "100", "fee": "0.15"},
            consultar_posicoes=lambda: {"BTC/USDT": "1.5"})

        assert rel["seguro"] is True, rel["divergencias"]
        rec = coord.orders["ord-1"]
        assert rec.state is OrderState.FILLED
        # a posição EXISTE no livro
        posicao = coord.ledger.open_positions_detail()["BTC/USDT"]
        assert Decimal(posicao["quantidade"]) == Decimal("1.5")
        # a reserva foi consumida, não ficou presa
        assert risco.reserved_total == Decimal("0")
        assert coord.ledger.is_balanced()

    def test_the_same_remote_fill_is_not_applied_twice(self):
        m, risco, livro, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc_com_ordem(OrderState.ACKNOWLEDGED, res))
        remota = {"id": "ex-9", "status": "filled", "filled": "1.5",
                  "average": "100", "fee": "0.15"}
        coord.reconciliar(lambda coid: remota,
                          consultar_posicoes=lambda: {"BTC/USDT": "1.5"})
        qtd = coord.ledger.position_quantity("BTC/USDT")
        coord.reconciliar(lambda coid: remota,
                          consultar_posicoes=lambda: {"BTC/USDT": "1.5"})
        assert coord.ledger.position_quantity("BTC/USDT") == qtd, (
            "reconciliar duas vezes dobrou a posição")

    def test_filled_without_numbers_does_not_invent_them(self):
        """Corretora diz 'fechada' mas não dá quantidade/preço."""
        m, risco, _, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc_com_ordem(OrderState.ACKNOWLEDGED, res))
        rel = coord.reconciliar(lambda coid: {"status": "closed"},
                                consultar_posicoes=lambda: {})
        assert rel["seguro"] is False
        assert any("RECONCILIACAO_INCOMPLETA" in d for d in rel["divergencias"])


class TestTerminalRemotoLiberaCapital:
    """ACHADO ALTO: cancelada/rejeitada/expirada não devolvia a reserva."""

    @pytest.mark.parametrize("estado", ["canceled", "cancelled", "rejected",
                                        "expired"])
    def test_every_terminal_status_releases_the_reservation(self, estado):
        m, risco, _, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc_com_ordem(OrderState.ACKNOWLEDGED, res))
        assert risco.reserved_total == Decimal("150.00")
        coord.reconciliar(lambda coid: {"status": estado},
                          consultar_posicoes=lambda: {})
        assert risco.reserved_total == Decimal("0"), (
            f"status {estado} deixou capital preso")
        assert risco.available == Decimal("1000")


class TestLivroBalanceadoNaoEProvaDeIgualdade:
    def test_balanced_book_alone_does_not_make_it_safe(self):
        """O livro pode fechar perfeitamente e estar economicamente errado."""
        m, risco, livro, coord = sistema()
        assert livro.is_balanced()
        rel = coord.reconciliar(lambda coid: None,
                                consultar_posicoes=lambda: {"BTC/USDT": "0.5"})
        assert rel["livro_status"] == "BALANCED"
        assert rel["seguro"] is False, (
            "livro balanceado não pode bastar para declarar SAFE")

    def test_report_separates_each_dimension(self):
        m, risco, _, coord = sistema()
        rel = coord.reconciliar(lambda coid: None, consultar_posicoes=lambda: {})
        for campo in ("ordens_status", "posicoes_status", "livro_status",
                      "oms_version", "em"):
            assert campo in rel, f"relatório sem {campo}"


class TestFrescorDoRelatorio:
    """Um SAFE antigo não pode autorizar para sempre."""

    def test_oms_version_changes_after_a_financial_mutation(self):
        m, risco, _, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc_com_ordem(OrderState.ACKNOWLEDGED, res))
        rel = coord.reconciliar(lambda coid: {"status": "canceled"},
                                consultar_posicoes=lambda: {})
        versao_no_relatorio = rel["oms_version"]
        # nova mutação financeira
        res2 = reserva(risco)
        coord.restaurar_estado(doc_com_ordem(OrderState.ACKNOWLEDGED, res2))
        coord.reconciliar(lambda coid: {"status": "canceled"},
                          consultar_posicoes=lambda: {})
        assert coord.oms_version > versao_no_relatorio, (
            "a versão do OMS não avançou: um relatório antigo continuaria "
            "valendo depois de o estado mudar")

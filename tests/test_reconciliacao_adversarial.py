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


def _confirma_ausencia(client_order_id):
    """Dublê de corretora que CONFIRMA não conhecer a ordem — diferente de
    'não achei na janela', que é UNKNOWN e mantém a reserva."""
    return {"resultado": "CONFIRMED_NOT_FOUND", "ordem": None}


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
        rel = coord.reconciliar(_confirma_ausencia,
                                consultar_posicoes=lambda: {"BTC/USDT": "0.5"})
        assert rel["seguro"] is False
        assert any("POSICAO_ORFA_REMOTA" in d for d in rel["divergencias"])

    def test_local_position_missing_on_the_exchange_is_not_erased(self):
        m, risco, livro, coord = sistema()
        livro.reserve(Decimal("150"), ref="r")
        livro.fill_buy("BTC/USDT", Decimal("1.5"), Decimal("100"), Decimal("0"),
                       reserved=Decimal("150"), ref="o", agent_id="t")
        rel = coord.reconciliar(_confirma_ausencia, consultar_posicoes=lambda: {})
        assert rel["seguro"] is False
        assert any("POSICAO_AUSENTE_REMOTA" in d for d in rel["divergencias"])
        assert livro.open_positions_detail(), "posição apagada silenciosamente"

    def test_quantity_mismatch_is_detected(self):
        m, risco, livro, coord = sistema()
        livro.reserve(Decimal("150"), ref="r")
        livro.fill_buy("BTC/USDT", Decimal("0.5"), Decimal("100"), Decimal("0"),
                       reserved=Decimal("150"), ref="o", agent_id="t")
        rel = coord.reconciliar(_confirma_ausencia,
                                consultar_posicoes=lambda: {"BTC/USDT": "0.7"})
        assert rel["seguro"] is False
        assert any("QUANTIDADE_DIVERGENTE" in d for d in rel["divergencias"])

    def test_without_position_query_the_status_is_unknown_not_safe(self):
        m, risco, livro, coord = sistema()
        livro.reserve(Decimal("150"), ref="r")
        livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("100"), Decimal("0"),
                       reserved=Decimal("150"), ref="o", agent_id="t")
        rel = coord.reconciliar(_confirma_ausencia)      # sem consulta de posição
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
        rel = coord.reconciliar(_confirma_ausencia,
                                consultar_posicoes=lambda: {"BTC/USDT": "0.5"})
        assert rel["livro_status"] == "BALANCED"
        assert rel["seguro"] is False, (
            "livro balanceado não pode bastar para declarar SAFE")

    def test_report_separates_each_dimension(self):
        m, risco, _, coord = sistema()
        rel = coord.reconciliar(_confirma_ausencia, consultar_posicoes=lambda: {})
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


class TestSemConsultaRemotaNuncaEMatched:
    """ACHADO: local vazio + sem consulta virava MATCHED. Ausência de posição
    LOCAL não prova ausência na CORRETORA — é o caso da posição órfã."""

    def test_no_provider_and_no_local_state_is_unknown_not_matched(self):
        m, risco, _, coord = sistema()
        rel = coord.reconciliar(_confirma_ausencia)      # sem consulta de posições
        assert rel["posicoes_status"] == "UNKNOWN"
        assert rel["seguro"] is False
        assert coord.reconciliacao_pendente is True

    def test_provider_error_is_unknown_too(self):
        m, risco, _, coord = sistema()

        def explode():
            raise RuntimeError("exchange fora do ar")

        rel = coord.reconciliar(_confirma_ausencia, consultar_posicoes=explode)
        assert rel["posicoes_status"] == "UNKNOWN" and rel["seguro"] is False


class TestVendaReconciliadaFechaPosicao:
    """ACHADO CRÍTICO: a reconciliação chamava fill_buy incondicionalmente —
    uma VENDA reconciliada criava posição em vez de fechá-la."""

    def _com_posicao(self):
        m, risco, livro, coord = sistema()
        livro.reserve(Decimal("100"), ref="r0")
        livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("100"), Decimal("0"),
                       reserved=Decimal("100"), ref="o0", agent_id="trader-trend")
        return m, risco, livro, coord

    def _ordem_de_saida(self, coord):
        from tradingagents.desk.execution import OrderRecord
        rec = OrderRecord.__new__(OrderRecord)
        rec.order_id, rec.intent = "exit-1", None
        rec.state = OrderState.UNKNOWN_RECONCILIATION_REQUIRED
        rec.filled_qty = Decimal("0")
        rec.avg_price = Decimal("0")
        rec.fees = Decimal("0")
        rec.external_id = ""
        rec.fingerprint = "fp-exit"
        rec.note = ""
        rec.seen_fills = set()
        rec.instrument = "BTC/USDT"
        rec.side = "sell"
        rec.quantity = Decimal("1")
        rec.provenance = None
        rec._client_order_id = "coid-exit"
        rec.agent_id = "trader-trend"
        rec.reservation_id = ""
        coord.orders["exit-1"] = rec
        return rec

    def test_reconciled_sell_closes_the_position_and_realizes_pnl(self):
        m, risco, livro, coord = self._com_posicao()
        self._ordem_de_saida(coord)
        assert livro.position_quantity("BTC/USDT") == Decimal("1")

        rel = coord.reconciliar(
            lambda coid: {"id": "ex-sell", "status": "filled", "filled": "1",
                          "average": "110", "fee": "0.5"},
            consultar_posicoes=lambda: {})

        assert livro.position_quantity("BTC/USDT") == Decimal("0"), (
            "a venda reconciliada não fechou a posição")
        # resultado realizado = 110 - 100 - 0.5 = 9.5 de LUCRO
        atribuicao = livro.attribution().get("trader-trend", Decimal("0"))
        assert atribuicao > 0, "o lucro da saída não foi realizado no livro"
        assert rel["seguro"] is True, rel["divergencias"]

    def test_reconciled_sell_at_a_loss_feeds_the_daily_brake(self):
        m, risco, livro, coord = self._com_posicao()
        self._ordem_de_saida(coord)
        coord.reconciliar(
            lambda coid: {"id": "ex", "status": "filled", "filled": "1",
                          "average": "80", "fee": "0"},
            consultar_posicoes=lambda: {})
        assert coord.perda_do_dia == Decimal("20"), (
            "prejuízo descoberto na reconciliação não alimentou o freio diário")

    def test_a_sell_never_creates_a_position(self):
        m, risco, livro, coord = self._com_posicao()
        self._ordem_de_saida(coord)
        coord.reconciliar(
            lambda coid: {"id": "ex", "status": "filled", "filled": "1",
                          "average": "110", "fee": "0"},
            consultar_posicoes=lambda: {})
        assert "BTC/USDT" not in livro.open_positions_detail()


class TestFillIdNaoPodeSerOrderId:
    """ACHADO: deduplicar por id da ORDEM esconderia execuções parciais —
    uma ordem tem vários fills."""

    def test_two_partial_fills_of_the_same_order_are_both_counted(self):
        m, risco, livro, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc_com_ordem(OrderState.ACKNOWLEDGED, res))
        rec = coord.orders["ord-1"]

        aplicou1 = coord.aplicar_fill(rec, side="buy", quantidade=Decimal("0.3"),
                                      preco=Decimal("100"), taxa=Decimal("0"),
                                      fill_id="trade-1")
        aplicou2 = coord.aplicar_fill(rec, side="buy", quantidade=Decimal("0.2"),
                                      preco=Decimal("102"), taxa=Decimal("0"),
                                      fill_id="trade-2")
        assert aplicou1 and aplicou2, "o segundo fill foi engolido"
        assert rec.filled_qty == Decimal("0.5")
        # preço médio ponderado: (0.3*100 + 0.2*102)/0.5 = 100.8
        assert abs(rec.avg_price - Decimal("100.8")) < Decimal("0.0001")
        assert rec.state is OrderState.PARTIALLY_FILLED

    def test_the_same_fill_id_is_ignored(self):
        m, risco, livro, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc_com_ordem(OrderState.ACKNOWLEDGED, res))
        rec = coord.orders["ord-1"]
        coord.aplicar_fill(rec, side="buy", quantidade=Decimal("0.3"),
                           preco=Decimal("100"), taxa=Decimal("0"),
                           fill_id="trade-1")
        repetido = coord.aplicar_fill(rec, side="buy", quantidade=Decimal("0.3"),
                                      preco=Decimal("100"), taxa=Decimal("0"),
                                      fill_id="trade-1")
        assert repetido is False and rec.filled_qty == Decimal("0.3")

    def test_a_fill_without_identifier_is_refused(self):
        m, risco, _, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc_com_ordem(OrderState.ACKNOWLEDGED, res))
        with pytest.raises(ValueError, match="identificador"):
            coord.aplicar_fill(coord.orders["ord-1"], side="buy",
                               quantidade=Decimal("1"), preco=Decimal("100"),
                               taxa=Decimal("0"), fill_id="")


class TestReservaProporcionalEmParcial:
    """ACHADO: o primeiro fill parcial liberava a reserva INTEIRA, deixando
    sem cobertura a parte que ainda podia executar."""

    def test_partial_buy_keeps_the_remaining_reservation(self):
        m, risco, livro, coord = sistema()
        res = reserva(risco)                       # 150 sobre 1.5 de alvo
        coord.restaurar_estado(doc_com_ordem(OrderState.ACKNOWLEDGED, res))
        rec = coord.orders["ord-1"]
        coord.aplicar_fill(rec, side="buy", quantidade=Decimal("0.45"),
                           preco=Decimal("100"), taxa=Decimal("0"),
                           fill_id="t-1")          # 30% de 1.5
        assert rec.state is OrderState.PARTIALLY_FILLED
        assert risco.reserved_total > 0, (
            "o primeiro parcial liberou a reserva toda: os 70% restantes "
            "ficariam sem cobertura")

    def test_terminal_fill_releases_the_reservation(self):
        m, risco, livro, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc_com_ordem(OrderState.ACKNOWLEDGED, res))
        rec = coord.orders["ord-1"]
        coord.aplicar_fill(rec, side="buy", quantidade=Decimal("1.5"),
                           preco=Decimal("100"), taxa=Decimal("0"), fill_id="t-1")
        assert rec.state is OrderState.FILLED
        assert risco.reserved_total == Decimal("0")


class TestVersaoDoOmsSobreviveAoRestart:
    """Sem persistir a versão, o frescor do relatório é teatro: tudo volta a
    zero e um relatório antigo parece atual."""

    def test_version_is_restored(self):
        m, risco, _, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc_com_ordem(OrderState.ACKNOWLEDGED, res))
        coord.reconciliar(_confirma_ausencia, consultar_posicoes=lambda: {})
        versao = coord.oms_version
        assert versao > 0
        doc = coord.snapshot_estado()

        m2, risco2, _, coord2 = sistema()
        assert coord2.oms_version == 0
        coord2.restaurar_estado(doc)
        assert coord2.oms_version == versao, "a versão do OMS voltou a zero"

    def test_a_new_mutation_advances_the_restored_version(self):
        m, risco, _, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc_com_ordem(OrderState.ACKNOWLEDGED, res))
        coord.reconciliar(_confirma_ausencia, consultar_posicoes=lambda: {})
        doc = coord.snapshot_estado()
        m2, risco2, _, coord2 = sistema()
        coord2.restaurar_estado(doc)
        antes = coord2.oms_version
        res2 = reserva(risco2)
        coord2.restaurar_estado(doc_com_ordem(OrderState.ACKNOWLEDGED, res2))
        coord2.reconciliar(_confirma_ausencia, consultar_posicoes=lambda: {})
        assert coord2.oms_version > antes


class TestOrdemForaDaJanelaMantemReserva:
    """UNKNOWN não pode devolver capital."""

    def test_unknown_lookup_keeps_the_reservation(self):
        m, risco, _, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc_com_ordem(OrderState.ACKNOWLEDGED, res))
        assert risco.reserved_total == Decimal("150.00")
        rel = coord.reconciliar(
            lambda coid: {"resultado": "UNKNOWN", "ordem": None,
                          "motivo": "fora da janela consultada"},
            consultar_posicoes=lambda: {})
        assert rel["seguro"] is False
        assert risco.reserved_total == Decimal("150.00"), (
            "capital devolvido sobre ordem de destino desconhecido")


class TestIdempotenciaSobreviveAoRestart:
    """ACHADO: o restore reconstruía o índice de idempotência pela
    FINGERPRINT, não pela chave usada no fluxo normal. O mesmo pedido, após
    um reinício, criaria uma SEGUNDA ordem na corretora."""

    def test_the_index_is_rebuilt_by_the_client_order_id(self):
        m, risco, _, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc_com_ordem(OrderState.ACKNOWLEDGED, res))
        assert coord._by_key.get("coid-1") == "ord-1", (
            "idempotência restaurada com a chave errada: o retry não "
            "encontraria a ordem existente")

    def test_fingerprint_alone_is_not_the_retry_key(self):
        m, risco, _, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc_com_ordem(OrderState.ACKNOWLEDGED, res))
        # a fingerprint pode existir no índice, mas a chave de retry é o coid
        assert "coid-1" in coord._by_key


class TestUmaUnicaFuncaoDeFill:
    """ACHADO: existiam duas implementações econômicas. A antiga só lançava
    no livro ao atingir o total, ignorando parciais."""

    def test_apply_fill_is_a_strict_alias(self):
        import inspect

        from tradingagents.desk.execution import ExecutionCoordinator
        fonte = inspect.getsource(ExecutionCoordinator.apply_fill)
        assert "self.aplicar_fill(" in fonte
        assert "fill_buy" not in fonte and "fill_sell" not in fonte, (
            "a função ainda tem lógica contábil própria")

    def test_partial_fill_is_economically_real_immediately(self):
        """0.3 executado JÁ mudou a realidade: o livro precisa saber."""
        m, risco, livro, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc_com_ordem(OrderState.ACKNOWLEDGED, res))
        coord.apply_fill("ord-1", fill_id="t-1", quantity=Decimal("0.3"),
                         price=Decimal("100"), fee=Decimal("0"))
        assert livro.position_quantity("BTC/USDT") == Decimal("0.3"), (
            "parcial não apareceu no livro: a posição real ficou invisível")

    def test_both_entry_points_produce_the_same_state(self):
        """Equivalência: runtime e reconciliação, mesmos fills, mesmo fim."""
        def rodar(usar_alias: bool):
            m, risco, livro, coord = sistema()
            res = reserva(risco)
            coord.restaurar_estado(doc_com_ordem(OrderState.ACKNOWLEDGED, res))
            rec = coord.orders["ord-1"]
            fills = [("f1", "0.3", "100"), ("f2", "0.2", "102"),
                     ("f3", "1.0", "101")]
            for fid, qtd, preco in fills:
                if usar_alias:
                    coord.apply_fill("ord-1", fill_id=fid, quantity=Decimal(qtd),
                                     price=Decimal(preco), fee=Decimal("0"))
                else:
                    coord.aplicar_fill(rec, side="buy", quantidade=Decimal(qtd),
                                       preco=Decimal(preco), taxa=Decimal("0"),
                                       fill_id=fid)
            return (rec.filled_qty, rec.avg_price, rec.state,
                    livro.position_quantity("BTC/USDT"), livro.cash())

        assert rodar(True) == rodar(False), (
            "os dois caminhos divergem: há duas verdades econômicas")

    def test_cumulative_quantities_are_not_double_counted(self):
        m, risco, livro, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc_com_ordem(OrderState.ACKNOWLEDGED, res))
        rec = coord.orders["ord-1"]
        for fid, qtd in (("f1", "0.3"), ("f2", "0.2"), ("f3", "1.0")):
            coord.aplicar_fill(rec, side="buy", quantidade=Decimal(qtd),
                               preco=Decimal("100"), taxa=Decimal("0"),
                               fill_id=fid)
        assert rec.filled_qty == Decimal("1.5"), (
            f"soma incorreta dos deltas: {rec.filled_qty}")
        assert livro.position_quantity("BTC/USDT") == Decimal("1.5")


class TestInvalidOrderNaoProvaAusencia:
    """ACHADO: InvalidOrder cobre identificador malformado e consulta não
    suportada. Tratar como ausência devolveria capital de ordem viva."""

    def test_invalid_order_is_unknown(self):
        from tradingagents.desk.brokers import _e_ordem_inexistente

        class InvalidOrder(Exception):
            pass

        class OrderNotFound(Exception):
            pass

        assert _e_ordem_inexistente(InvalidOrder()) is False
        assert _e_ordem_inexistente(OrderNotFound()) is True

    def test_invalid_order_keeps_the_reservation(self):
        m, risco, _, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc_com_ordem(OrderState.ACKNOWLEDGED, res))
        coord.reconciliar(
            lambda coid: {"resultado": "UNKNOWN", "ordem": None,
                          "motivo": "InvalidOrder na consulta"},
            consultar_posicoes=lambda: {})
        assert risco.reserved_total == Decimal("150.00")


class TestGuardiaoSobreviveAoRestart:
    """ACHADO: o guardião lia r.intent.reservation e quebrava justamente
    depois de um reinício, que é quando ele mais importa."""

    def test_watchlist_works_with_restored_orders(self):
        from tradingagents.desk.execution import PositionGuardian
        m, risco, _, coord = sistema()
        res = reserva(risco)
        coord.restaurar_estado(doc_com_ordem(OrderState.ACKNOWLEDGED, res))
        lista = PositionGuardian(coord).watchlist()
        assert "BTC/USDT" in lista

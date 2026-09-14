"""Crash → restart → reconcile, com estado financeiro NÃO VAZIO.

A auditoria mostrou por que isto é indispensável: os testes anteriores
verificavam se o documento tinha as CHAVES certas, e passavam com
`posicoes: {}`, `reservas: []`, `caixa: "0"`. Verde e inútil.

Aqui cada asserção compara VALORES, e o processo é destruído de verdade
(objetos novos, nada compartilhado).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tradingagents.desk.execution import ExecutionCoordinator, OrderState
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import Action, DeskMandate, Mode, TradeProposal, utcnow
from tradingagents.desk.risk import RiskEngine


def mandato():
    return DeskMandate(mandate_id="m", owner="p", account="a", mode=Mode.PAPER,
                       instruments=("BTC/USDT",), capital=Decimal("1000"))


class CorretoraQueAceita:
    mode = "testnet"

    def __init__(self):
        self.enviadas = []

    def submit(self, coid, instrument, side, quantity, final_state=None):
        """Corretora que devolve a EXECUÇÃO individualizada, com id próprio.
        Resposta só com acumulado não produz efeito econômico — de propósito:
        o id da ordem não é identidade de execução."""
        self.enviadas.append(coid)
        return {"id": f"ex-{coid}", "status": "filled", "filled": quantity,
                "average": Decimal("100"), "fee": Decimal("0.1"),
                "trades": [{"id": f"trade-{coid}", "amount": quantity,
                            "price": Decimal("100"), "fee": {"cost": Decimal("0.1")}}]}


def sistema(broker=None):
    m = mandato()
    risco = RiskEngine(m)
    livro = Ledger(opening_cash=m.capital)
    coord = ExecutionCoordinator(m, livro, risco, worker_id="w",
                                 broker=broker or CorretoraQueAceita())
    return m, risco, livro, coord


def proposta(exposicao="0.15"):
    from datetime import timedelta
    return TradeProposal(agent_id="trader-trend", strategy="trend_following",
                         instrument="BTC/USDT", action=Action.BUY, reason="t",
                         desired_exposure_pct=Decimal(exposicao),
                         confidence=Decimal("0.8"),
                         valid_until=utcnow() + timedelta(minutes=5))


def intencao(risco, provenance=None):
    res = risco.reserve(proposta(), Decimal("100"), agent_id="trader-trend")
    return risco.intent(res, Action.BUY, Mode.PAPER, "w",
                        provenance=provenance or {"x": 1})


class TestEstadoFinanceiroNaoVaiVazio:
    """O bug que o auditor achou: o snapshot chamava métodos inexistentes e
    persistia posições e reservas VAZIAS, sem ninguém notar."""

    def test_snapshot_carries_real_cash_not_zero(self):
        _, risco, livro, coord = sistema()
        doc = coord.snapshot_estado()
        assert Decimal(doc["caixa"]) == Decimal("1000"), (
            "caixa persistido como zero: a chave da conta estava errada")

    def test_snapshot_carries_the_actual_reservation(self):
        _, risco, _, coord = sistema()
        intencao(risco)
        doc = coord.snapshot_estado()
        assert doc["reservas"], "reservas persistidas vazias"
        assert Decimal(doc["reservas"][0]["amount"]) == Decimal("150.00")
        assert doc["reservas"][0]["instrument"] == "BTC/USDT"

    def test_snapshot_carries_the_open_position_with_cost_and_owner(self):
        _, risco, livro, coord = sistema()
        coord.submit(intencao(risco))
        doc = coord.snapshot_estado()
        assert doc["posicoes"], "posições persistidas vazias"
        pos = doc["posicoes"]["BTC/USDT"]
        assert Decimal(pos["quantidade"]) > 0
        assert Decimal(pos["custo_total"]) > 0
        assert pos["agente"] == "trader-trend", "sem dono não há atribuição"

    def test_snapshot_carries_the_ledger_transactions(self):
        _, risco, _, coord = sistema()
        coord.submit(intencao(risco))
        doc = coord.snapshot_estado()
        assert len(doc["lancamentos"]) >= 2, (
            "persistir só saldos destruiria a auditabilidade")


class TestCrashDepoisDeEnviarComRespostaAmbigua:
    """Cenário exigido pela auditoria, com valores conferidos."""

    def test_full_cycle_crash_restart_reconcile(self):
        # --- processo A ---
        m, risco, livro, coord = sistema()
        intent = intencao(risco)
        assert risco.available == Decimal("850.00")

        from tradingagents.desk.execution import BrokerTimeout

        class Ambigua:
            mode = "testnet"

            def submit(self, *a, **k):
                raise BrokerTimeout("resposta perdida")

        coord.broker = Ambigua()
        rec = coord.submit(intent)
        assert rec.state is OrderState.UNKNOWN_RECONCILIATION_REQUIRED
        doc = coord.snapshot_estado()

        # --- processo A morre; processo B nasce do zero ---
        del m, risco, livro, coord, intent, rec
        m2, risco2, livro2, coord2 = sistema()
        assert risco2.available == Decimal("1000")     # instância virgem
        resultado = coord2.restaurar_estado(doc)

        # 1. a ordem existe de novo
        assert resultado["ordens"] >= 1 and coord2.orders
        # 2. UMA reserva de 150 sobre capital de 1000 => 850 disponíveis.
        #    A equação é verificável: available == capital - reservas ativas.
        assert risco2.reserved_total == Decimal("150.00")
        assert len(risco2.active_reservations()) == 1
        assert risco2.available == m2.capital - risco2.reserved_total
        assert risco2.available == Decimal("850.00"), (
            "capital reservado voltou a parecer disponível após o reinício")
        # 3. o portão está ligado
        assert resultado["exige_reconciliacao"] is True
        # 4. e uma ordem nova é RECUSADA antes da reconciliação. A intenção
        #    é construída ANTES do restore para que a recusa venha do portão,
        #    não de um veto de reserva duplicada.
        # Reenvio de uma ordem RESTAURADA passa pelo caminho único: a
        # recusa tem de vir do portão, não de validação de tipo.
        from tradingagents.desk.execution import ExecutionRefused
        from tradingagents.desk.intents import ExecutionIntent, Kind, Side
        nova = ExecutionIntent(kind=Kind.ENTRY, side=Side.BUY,
                               instrument="BTC/USDT", quantity=Decimal("1"),
                               agent_id="trader-trend", account="a")
        with pytest.raises(ExecutionRefused, match="reconcilia"):
            coord2.submit_intent(nova)

        # --- reconciliação: a corretora diz que a ordem existe e preencheu ---
        def consultar(client_order_id):
            return {"id": "ex-1", "status": "closed", "filled": Decimal("1.5"),
                    "average": Decimal("100")}

        # A posição também é conferida: a corretora informa 1.5 BTC, que é
        # exatamente o que o fill reconciliado cria no livro.
        relatorio = coord2.reconciliar(
            consultar, consultar_posicoes=lambda: {"BTC/USDT": Decimal("1.5")})
        assert relatorio["ordens_conferidas"] == 1
        assert relatorio["seguro"] is True, relatorio["divergencias"]
        # 5. e SÓ agora o portão libera
        assert coord2.reconciliacao_pendente is False

    def test_order_the_exchange_never_saw_frees_the_capital(self):
        m, risco, _, coord = sistema()
        from tradingagents.desk.execution import BrokerTimeout

        class Ambigua:
            mode = "testnet"

            def submit(self, *a, **k):
                raise BrokerTimeout("resposta perdida")

        coord.broker = Ambigua()
        coord.submit(intencao(risco))
        doc = coord.snapshot_estado()

        m2, risco2, _, coord2 = sistema()
        coord2.restaurar_estado(doc)
        assert risco2.available == Decimal("850.00")

        relatorio = coord2.reconciliar(
            lambda coid: {"resultado": "CONFIRMED_NOT_FOUND", "ordem": None},
            consultar_posicoes=lambda: {})
        assert relatorio["seguro"] is True, relatorio["divergencias"]
        assert risco2.available == Decimal("1000"), (
            "ordem inexistente na corretora deve devolver o capital")

    def test_without_broker_access_the_gate_stays_closed(self):
        """'Não perguntei' nunca pode virar 'está tudo certo'."""
        m, risco, _, coord = sistema()
        coord.exigir_reconciliacao("teste")
        relatorio = coord.reconciliar(None)
        assert relatorio["seguro"] is False
        assert coord.reconciliacao_pendente is True


class TestCrashDepoisDoFill:
    def test_position_and_cash_survive_a_restart(self):
        m, risco, livro, coord = sistema()
        coord.submit(intencao(risco))
        caixa_antes = livro.cash()
        posicoes_antes = livro.open_positions_detail()
        assert posicoes_antes, "o cenário precisa ter posição de verdade"
        doc = coord.snapshot_estado()

        m2, risco2, livro2, coord2 = sistema()
        coord2.restaurar_estado(doc)

        assert coord2.ledger.cash() == caixa_antes, "o caixa mudou no restore"
        assert coord2.ledger.open_positions_detail() == posicoes_antes, (
            "a posição desapareceu ou mudou ao reiniciar")
        assert coord2.ledger.is_balanced(), "o livro não fecha depois do restore"

    def test_daily_loss_survives_the_restart(self):
        m, risco, livro, coord = sistema()
        coord.submit(intencao(risco))
        coord.close_position(instrument="BTC/USDT", quantity=Decimal("1.5"),
                             price=Decimal("80"), cost_basis=Decimal("150"),
                             agent_id="trader-trend")
        perda = coord.perda_do_dia
        assert perda > 0, "o cenário precisa ter prejuízo real"
        doc = coord.snapshot_estado()

        m2, risco2, _, coord2 = sistema()
        coord2.restaurar_estado(doc)
        assert coord2.perda_do_dia == perda, (
            "reiniciar zerava a perda do dia e destravava o freio")


class TestEquacaoDeCapital:
    """O auditor pegou uma inconsistência no meu relato (1000-150 descrito
    como 700). A equação precisa ser verificável e única."""

    def test_single_reservation_leaves_exactly_capital_minus_reservation(self):
        m, risco, livro, coord = sistema()
        intencao(risco)
        assert len(risco.active_reservations()) == 1
        assert risco.reserved_total == Decimal("150.00")
        assert risco.available == m.capital - risco.reserved_total == Decimal("850.00")

    def test_the_equation_holds_after_restart(self):
        m, risco, _, coord = sistema()
        intencao(risco)
        doc = coord.snapshot_estado()
        m2, risco2, _, coord2 = sistema()
        coord2.restaurar_estado(doc)
        assert len(risco2.active_reservations()) == 1, "reserva duplicada no restore"
        assert risco2.available == m2.capital - risco2.reserved_total
        assert risco2.available == Decimal("850.00")

    def test_restore_is_idempotent(self):
        """restore(doc) três vezes não pode triplicar nada."""
        m, risco, _, coord = sistema()
        coord.submit(intencao(risco))
        doc = coord.snapshot_estado()
        m2, risco2, _, coord2 = sistema()
        for _ in range(3):
            coord2.restaurar_estado(doc)
        assert len(coord2.orders) == len(doc["ordens"]), "ordens multiplicadas"
        assert len(risco2.active_reservations()) == len(doc["reservas"]), (
            "reservas multiplicadas")
        assert len(coord2.ledger.export_transactions()) == len(doc["lancamentos"]), (
            "lançamentos contábeis multiplicados: o livro passaria a mentir")
        assert coord2.ledger.cash() == Decimal(doc["caixa"])
        assert coord2.ledger.is_balanced()

    def test_snapshot_restore_snapshot_is_stable(self):
        m, risco, _, coord = sistema()
        coord.submit(intencao(risco))
        antes = coord.snapshot_estado()
        m2, risco2, _, coord2 = sistema()
        coord2.restaurar_estado(antes)
        depois = coord2.snapshot_estado()
        for campo in ("caixa", "moeda", "posicoes", "perda_do_dia"):
            assert antes[campo] == depois[campo], f"{campo} mudou no ciclo"
        assert len(antes["lancamentos"]) == len(depois["lancamentos"])
        assert [t["tx_id"] for t in antes["lancamentos"]] == \
               [t["tx_id"] for t in depois["lancamentos"]], (
                   "restore recriou lançamentos com IDs novos: a auditoria "
                   "deixaria de bater com o histórico original")


class TestQuantidadeNaoEntraNaContabilidade:
    """Quantidade é unidade do ativo; amount é valor monetário. Misturar as
    duas quebraria as partidas dobradas silenciosamente."""

    def test_quantity_does_not_participate_in_the_double_entry_sum(self):
        livro = Ledger(opening_cash=Decimal("1000"))
        livro.reserve(Decimal("150"), ref="r1")
        tx = livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("100"),
                            Decimal("1"), reserved=Decimal("150"), ref="o1",
                            agent_id="t")
        assert sum(e.amount for e in tx.entries) == 0, "partidas não fecham"
        assert tx.quantity == Decimal("1")
        assert tx.instrument == "BTC/USDT"
        assert livro.is_balanced()

    def test_partial_sell_keeps_the_book_balanced_and_the_position_right(self):
        livro = Ledger(opening_cash=Decimal("1000"))
        livro.reserve(Decimal("150"), ref="r1")
        livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("100"), Decimal("1"),
                       reserved=Decimal("150"), ref="o1", agent_id="t")
        livro.fill_sell("BTC/USDT", Decimal("0.4"), Decimal("110"),
                        Decimal("0.5"), cost_basis=Decimal("40"), agent_id="t",
                        ref="s1")
        assert livro.position_quantity("BTC/USDT") == Decimal("0.6")
        assert livro.is_balanced()
        detalhe = livro.open_positions_detail()["BTC/USDT"]
        assert Decimal(detalhe["quantidade"]) == Decimal("0.6")
        assert Decimal(detalhe["custo_total"]) == Decimal("60.00000000")


class TestSaidaPassaPeloOms:
    """A saída era a segunda porta de dinheiro: ia direto ao broker, sem
    registro durável. Timeout devolvia um dicionário que morria com o
    processo."""

    def _com_posicao(self, dono="trader-trend"):
        m, risco, livro, coord = sistema()
        livro.reserve(Decimal("100"), ref="r0")
        livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("100"), Decimal("0"),
                       reserved=Decimal("100"), ref="o0", agent_id=dono)
        return m, risco, livro, coord

    def test_exit_creates_a_durable_order_record(self):
        m, risco, livro, coord = self._com_posicao()
        out = coord.close_position(instrument="BTC/USDT", quantity=Decimal("1"),
                                   price=Decimal("110"), cost_basis=Decimal("100"),
                                   agent_id="trader-trend")
        rec = coord.orders[out["order_id"]]
        assert rec.lado == "sell" and rec.client_order_id == out["client_order_id"]
        assert out["order_id"] in coord.snapshot_estado()["ordens"][-1]["order_id"]

    def test_exit_timeout_persists_the_order_and_closes_the_gate(self):
        m, risco, livro, coord = self._com_posicao()
        from tradingagents.desk.execution import BrokerTimeout

        class Timeout:
            mode = "testnet"

            def submit(self, *a, **k):
                raise BrokerTimeout("resposta perdida na saída")

        coord.broker = Timeout()
        out = coord.close_position(instrument="BTC/USDT", quantity=Decimal("1"),
                                   price=Decimal("110"), cost_basis=Decimal("100"),
                                   agent_id="trader-trend")
        assert out["estado"] == "incerto"
        rec = coord.orders[out["order_id"]]
        assert rec.state is OrderState.UNKNOWN_RECONCILIATION_REQUIRED
        assert coord.reconciliacao_pendente is True
        # e o registro sobrevive ao snapshot
        doc = coord.snapshot_estado()
        assert any(o["lado"] == "sell" for o in doc["ordens"])

    def test_exit_is_idempotent(self):
        """Pedir a mesma saída de novo não manda uma segunda venda."""
        m, risco, livro, coord = self._com_posicao(dono="t")
        enviadas = []

        class Conta(CorretoraQueAceita):
            def submit(self, coid, instrument, side, quantity, final_state=None):
                enviadas.append(coid)
                return super().submit(coid, instrument, side, quantity, final_state)

        coord.broker = Conta()
        a = coord.close_position(instrument="BTC/USDT", quantity=Decimal("1"),
                                 price=Decimal("110"), cost_basis=Decimal("100"),
                                 agent_id="t", client_order_id="exit-fixo")
        b = coord.close_position(instrument="BTC/USDT", quantity=Decimal("1"),
                                 price=Decimal("110"), cost_basis=Decimal("100"),
                                 agent_id="t", client_order_id="exit-fixo")
        assert len(enviadas) == 1, "a mesma saída foi enviada duas vezes"
        assert b.get("reaproveitada") is True
        assert a["order_id"] == b["order_id"]

    def test_exit_after_crash_is_adopted_not_resent(self):
        """Timeout na saída, processo morre, reconciliação encontra a venda
        executada: a posição zera e NENHUMA segunda venda é enviada."""
        m, risco, livro, coord = self._com_posicao()
        from tradingagents.desk.execution import BrokerTimeout

        class Timeout:
            mode = "testnet"

            def submit(self, *a, **k):
                raise BrokerTimeout("perdida")

        coord.broker = Timeout()
        coord.close_position(instrument="BTC/USDT", quantity=Decimal("1"),
                             price=Decimal("110"), cost_basis=Decimal("100"),
                             agent_id="trader-trend", client_order_id="exit-1")
        doc = coord.snapshot_estado()

        # processo novo
        m2, risco2, livro2, coord2 = sistema()
        livro2.reserve(Decimal("100"), ref="r0")
        livro2.fill_buy("BTC/USDT", Decimal("1"), Decimal("100"), Decimal("0"),
                        reserved=Decimal("100"), ref="o0", agent_id="trader-trend")
        enviadas = []
        coord2.broker = type("Nada", (), {
            "mode": "testnet",
            "submit": lambda self, *a, **k: enviadas.append(a)})()
        coord2.restaurar_estado(doc)

        rel = coord2.reconciliar(
            lambda coid: {"resultado": "FOUND", "ordem": {
                "id": "ex-sell", "status": "filled", "filled": "1",
                "average": "110", "fee": "0"}},
            consultar_posicoes=lambda: {})
        # o restore substitui o livro pelo reconstruído dos lançamentos
        assert coord2.ledger.position_quantity("BTC/USDT") == Decimal("0"), (
            "a venda encontrada não fechou a posição")
        assert not enviadas, "uma SEGUNDA venda foi enviada"
        assert rel["seguro"] is True, rel["divergencias"]


class TestSegurancaDaSaida:
    """A saída ficou durável, mas ainda não tinha as travas da entrada."""

    def _com_posicao(self, qtd="1"):
        m, risco, livro, coord = sistema()
        livro.reserve(Decimal("100"), ref="r0")
        # o dono da posição é quem fecha nestes cenários; a posse tem teste
        # dedicado em TestPosseDaPosicao
        livro.fill_buy("BTC/USDT", Decimal(qtd), Decimal("100"), Decimal("0"),
                       reserved=Decimal("100"), ref="o0", agent_id="t")
        enviadas = []

        class Conta(CorretoraQueAceita):
            def submit(self, coid, instrument, side, quantity, final_state=None):
                enviadas.append(coid)
                return super().submit(coid, instrument, side, quantity, final_state)

        coord.broker = Conta()
        return m, risco, livro, coord, enviadas

    def test_selling_more_than_owned_is_refused_before_the_network(self):
        """Em spot isso viraria venda a descoberto: exposição na direção
        oposta, sem ninguém autorizar."""
        from tradingagents.desk.execution import ExecutionRefused
        m, risco, livro, coord, enviadas = self._com_posicao("1")
        with pytest.raises(ExecutionRefused, match="maior que o lote"):
            coord.close_position(instrument="BTC/USDT", quantity=Decimal("2"),
                                 price=Decimal("110"), cost_basis=Decimal("100"),
                                 agent_id="t")
        assert not enviadas, "a ordem chegou à corretora mesmo assim"

    def test_zero_or_negative_quantity_is_refused(self):
        from tradingagents.desk.execution import ExecutionRefused
        m, risco, livro, coord, enviadas = self._com_posicao()
        for q in (Decimal("0"), Decimal("-1")):
            with pytest.raises(ExecutionRefused):
                coord.close_position(instrument="BTC/USDT", quantity=q,
                                     price=Decimal("110"),
                                     cost_basis=Decimal("100"), agent_id="t")
        assert not enviadas

    def test_exit_is_blocked_while_reconciliation_is_pending(self):
        """Posição real desconhecida: vender no escuro pode virar posição
        vendida sem querer."""
        from tradingagents.desk.execution import ExecutionRefused
        m, risco, livro, coord, enviadas = self._com_posicao()
        coord.exigir_reconciliacao("ordem incerta")
        with pytest.raises(ExecutionRefused, match="reconciliação pendente"):
            coord.close_position(instrument="BTC/USDT", quantity=Decimal("1"),
                                 price=Decimal("110"), cost_basis=Decimal("100"),
                                 agent_id="t")
        assert not enviadas

    def test_same_coid_with_different_content_is_a_conflict(self):
        from tradingagents.desk.execution import IdempotencyConflict
        m, risco, livro, coord, enviadas = self._com_posicao()
        coord.close_position(instrument="BTC/USDT", quantity=Decimal("0.5"),
                             price=Decimal("110"), cost_basis=Decimal("50"),
                             agent_id="t", client_order_id="X")
        with pytest.raises(IdempotencyConflict, match="conteúdo diferente"):
            coord.close_position(instrument="BTC/USDT", quantity=Decimal("0.4"),
                                 price=Decimal("110"), cost_basis=Decimal("40"),
                                 agent_id="t", client_order_id="X")
        assert len(enviadas) == 1

    def test_identical_retry_is_still_idempotent(self):
        m, risco, livro, coord, enviadas = self._com_posicao()
        a = coord.close_position(instrument="BTC/USDT", quantity=Decimal("0.5"),
                                 price=Decimal("110"), cost_basis=Decimal("50"),
                                 agent_id="t", client_order_id="Y")
        b = coord.close_position(instrument="BTC/USDT", quantity=Decimal("0.5"),
                                 price=Decimal("110"), cost_basis=Decimal("50"),
                                 agent_id="t", client_order_id="Y")
        assert len(enviadas) == 1 and b["reaproveitada"] is True
        assert a["order_id"] == b["order_id"]


class TestCustoDeReferenciaSobreviveAoRestart:
    """Sem persistir o custo, um prejuízo de 10 viraria 'lucro' do valor
    inteiro da venda depois de um reinício."""

    def test_loss_is_still_a_loss_after_restart(self):
        m, risco, livro, coord = sistema()
        from tradingagents.desk.execution import BrokerTimeout

        class Timeout:
            mode = "testnet"

            def submit(self, *a, **k):
                raise BrokerTimeout("perdida")

        # posição REAL no livro: vender sem ela é venda a descoberto
        livro.reserve(Decimal("100"), ref="r0")
        livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("100"), Decimal("0"),
                       reserved=Decimal("100"), ref="o0", agent_id="trader-trend")
        coord.broker = Timeout()
        coord.close_position(instrument="BTC/USDT", quantity=Decimal("1"),
                             price=Decimal("90"), cost_basis=Decimal("100"),
                             agent_id="trader-trend", client_order_id="exit-9")
        doc = coord.snapshot_estado()
        assert doc["ordens"][-1]["custo_referencia"] == "100", (
            "custo de referência não foi persistido")

        m2, risco2, livro2, coord2 = sistema()
        coord2.restaurar_estado(doc)
        coord2.reconciliar(
            lambda coid: {"resultado": "FOUND", "ordem": {
                "id": "ex", "status": "filled", "filled": "1",
                "average": "90", "fee": "0"}},
            consultar_posicoes=lambda: {})
        # vendeu a 90 o que custou 100 => prejuízo de 10, nunca lucro de 90
        assert coord2.perda_do_dia == Decimal("10"), (
            f"resultado errado após reinício: perda={coord2.perda_do_dia}")


class TestPosicaoZeroNaoPermiteVenda:
    """Bug introduzido por MIM na rodada anterior: a checagem era
    `conhecida > 0 and quantity > conhecida`, então com posição ZERO
    qualquer venda passava — venda a descoberto sem ninguém autorizar."""

    def _sistema_sem_posicao(self):
        m, risco, livro, coord = sistema()
        enviadas = []

        class Conta(CorretoraQueAceita):
            def submit(self, coid, instrument, side, quantity, final_state=None):
                enviadas.append(coid)
                return super().submit(coid, instrument, side, quantity, final_state)

        coord.broker = Conta()
        return coord, livro, enviadas

    def test_selling_with_zero_position_is_refused(self):
        from tradingagents.desk.execution import ExecutionRefused
        coord, livro, enviadas = self._sistema_sem_posicao()
        assert livro.position_quantity("BTC/USDT") == Decimal("0")
        with pytest.raises(ExecutionRefused):
            coord.close_position(instrument="BTC/USDT", quantity=Decimal("1"),
                                 price=Decimal("110"), cost_basis=Decimal("100"),
                                 agent_id="t")
        assert not enviadas, "venda a descoberto chegou à corretora"

    def test_cost_basis_does_not_authorise_a_sale(self):
        """cost_basis é informação CONTÁBIL: não prova que existe ativo na
        corretora."""
        from tradingagents.desk.execution import ExecutionRefused
        coord, livro, enviadas = self._sistema_sem_posicao()
        with pytest.raises(ExecutionRefused):
            coord.close_position(instrument="BTC/USDT", quantity=Decimal("0.1"),
                                 price=Decimal("110"),
                                 cost_basis=Decimal("999999"), agent_id="t")
        assert not enviadas


class TestSaidaRespeitaOMandato:
    """A saída não pode ter menos verificação que a entrada."""

    def _coord(self, instrumentos=("BTC/USDT",), modo=None, live_ok=False):
        from tradingagents.desk.models import DeskMandate, Mode
        m = DeskMandate(mandate_id="m", owner="p", account="a",
                        mode=modo or Mode.PAPER, instruments=instrumentos,
                        capital=Decimal("1000"), live_authorized=live_ok)
        risco = RiskEngine(m)
        livro = Ledger(opening_cash=m.capital)
        livro.reserve(Decimal("100"), ref="r0")
        livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("100"), Decimal("0"),
                       reserved=Decimal("100"), ref="o0", agent_id="t")
        enviadas = []

        class Conta(CorretoraQueAceita):
            def submit(self, coid, instrument, side, quantity, final_state=None):
                enviadas.append(coid)
                return super().submit(coid, instrument, side, quantity, final_state)

        coord = ExecutionCoordinator(m, livro, risco, worker_id="w",
                                     broker=Conta())
        return coord, enviadas

    def test_instrument_outside_the_mandate_is_refused(self):
        from tradingagents.desk.execution import ExecutionRefused
        coord, enviadas = self._coord(instrumentos=("ETH/USDT",))
        with pytest.raises(ExecutionRefused, match="mandato"):
            coord.close_position(instrument="BTC/USDT", quantity=Decimal("1"),
                                 price=Decimal("110"), cost_basis=Decimal("100"),
                                 agent_id="t")
        assert not enviadas

    def test_revoked_mandate_blocks_the_exit(self):
        from tradingagents.desk.execution import ExecutionRefused
        coord, enviadas = self._coord()
        from tradingagents.desk.models import utcnow as _agora
        object.__setattr__(coord.mandate, "revoked_at", _agora())
        with pytest.raises(ExecutionRefused, match="mandato inativo"):
            coord.close_position(instrument="BTC/USDT", quantity=Decimal("1"),
                                 price=Decimal("110"), cost_basis=Decimal("100"),
                                 agent_id="t")
        assert not enviadas

    def test_live_without_authorisation_blocks_the_exit(self):
        from tradingagents.desk.execution import ExecutionRefused
        from tradingagents.desk.models import Mode
        coord, enviadas = self._coord(modo=Mode.LIVE, live_ok=False)
        with pytest.raises(ExecutionRefused, match="LIVE sem autorização"):
            coord.close_position(instrument="BTC/USDT", quantity=Decimal("1"),
                                 price=Decimal("110"), cost_basis=Decimal("100"),
                                 agent_id="t")
        assert not enviadas


class TestEntradaAmbiguaTrancaNovoRisco:
    """UNKNOWN → BLOCK NEW RISK. O timeout da ENTRADA marcava a ordem como
    incerta mas NÃO ligava o portão: a mesa seguia abrindo posição sem saber
    se a anterior existe na corretora."""

    def _sistema(self):
        """Dois instrumentos: assim a segunda tentativa é barrada pelo
        PORTÃO, e não pelo veto de reserva duplicada no mesmo ativo."""
        from tradingagents.desk.execution import BrokerTimeout
        from tradingagents.desk.models import DeskMandate, Mode
        m = DeskMandate(mandate_id="m", owner="p", account="a", mode=Mode.PAPER,
                        instruments=("BTC/USDT", "ETH/USDT"),
                        capital=Decimal("10000"))
        risco = RiskEngine(m)
        livro = Ledger(opening_cash=m.capital)
        coord = ExecutionCoordinator(m, livro, risco, worker_id="w")
        chamadas = []

        class Timeout:
            mode = "testnet"

            def submit(self, coid, instrument, side, quantity, final_state=None):
                chamadas.append(coid)
                raise BrokerTimeout("resposta perdida")

        coord.broker = Timeout()
        return m, risco, coord, chamadas

    def test_ambiguous_entry_blocks_the_next_entry(self):
        from tradingagents.desk.execution import ExecutionRefused, OrderState
        m, risco, coord, chamadas = self._sistema()
        # a segunda intenção é criada ANTES, para que a recusa venha do
        # PORTÃO e não do veto de reserva duplicada no mesmo instrumento
        segunda = self._intencao(risco, "ETH/USDT")
        rec = coord.submit(self._intencao(risco, "BTC/USDT"))
        assert rec.state is OrderState.UNKNOWN_RECONCILIATION_REQUIRED
        assert coord.reconciliacao_pendente is True, (
            "ordem incerta não ligou o portão: a mesa seguiria operando")
        with pytest.raises(ExecutionRefused, match="reconcilia"):
            coord.submit(segunda)
        assert len(chamadas) == 1, "uma segunda ordem foi enviada às cegas"

    @staticmethod
    def _intencao(risco, instrumento):
        from datetime import timedelta

        from tradingagents.desk.models import Action, Mode, TradeProposal
        p = TradeProposal(agent_id="trader-trend", strategy="s",
                          instrument=instrumento, action=Action.BUY, reason="t",
                          desired_exposure_pct=Decimal("0.05"),
                          confidence=Decimal("0.8"),
                          valid_until=utcnow() + timedelta(minutes=5))
        res = risco.reserve(p, Decimal("100"), agent_id="trader-trend")
        return risco.intent(res, Action.BUY, Mode.PAPER, "w",
                            provenance={"x": 1})

    def test_ambiguous_entry_advances_the_oms_version(self):
        m, risco, coord, _ = self._sistema()
        antes = coord.oms_version
        coord.submit(self._intencao(risco, "BTC/USDT"))
        assert coord.oms_version > antes, (
            "versão não avançou: um relatório SAFE anterior continuaria "
            "valendo depois de uma ordem virar incerta")

    def test_a_previous_safe_report_stops_authorising(self):
        m, risco, coord, _ = self._sistema()
        coord.ultimo_relatorio = {"seguro": True, "oms_version": coord.oms_version}
        coord.submit(self._intencao(risco, "BTC/USDT"))
        assert coord.ultimo_relatorio["oms_version"] != coord.oms_version


class TestCancelamentoAposRestart:
    """confirm_cancel lia rec.intent.reservation e estourava com ordem
    restaurada — falhava exatamente depois de um reinício."""

    def test_confirm_cancel_works_on_a_restored_order(self):
        from tradingagents.desk.execution import OrderState
        m, risco, livro, coord = sistema()
        res = intencao(risco).reservation
        doc = {"schema": "dz23.oms/2", "worker_id": "w", "dia": "",
               "perda_do_dia": "0", "oms_version": 5,
               "ordens": [{"order_id": "ord-c", "estado": "cancel_pending",
                           "fingerprint": "fp", "filled": "0",
                           "preco_medio": "0", "fees": "0", "fills_vistos": [],
                           "external_id": "", "nota": "", "instrumento": "BTC/USDT",
                           "quantidade": "1.5", "lado": "buy",
                           "client_order_id": "coid-c", "agente": "trader-trend",
                           "reserva_id": res.reservation_id,
                           "procedencia": None, "custo_referencia": ""}],
               "posicoes": {}, "caixa": "850", "moeda": "USDT",
               "lancamentos": [],
               "reservas": [{"reservation_id": res.reservation_id, "account": "a",
                             "agent_id": "trader-trend", "instrument": "BTC/USDT",
                             "amount": str(res.amount), "quantity": str(res.quantity),
                             "mandate_version": 1, "expires_at": None}]}
        m2, risco2, livro2, coord2 = sistema()
        coord2.restaurar_estado(doc)
        assert risco2.reserved_total > 0
        rec = coord2.confirm_cancel("ord-c")      # não pode estourar
        assert rec.state is OrderState.CANCELLED
        assert risco2.reserved_total == Decimal("0"), (
            "cancelamento confirmado não devolveu a reserva")


class TestPosseDaPosicao:
    """Outro agente fechando em silêncio destrói a atribuição de resultado e
    deixa o dono achando que ainda está exposto."""

    def _com_posicao_de(self, dono="trader-trend"):
        m, risco, livro, coord = sistema()
        livro.reserve(Decimal("100"), ref="r0")
        livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("100"), Decimal("0"),
                       reserved=Decimal("100"), ref="o0", agent_id=dono)
        enviadas = []

        class Conta(CorretoraQueAceita):
            def submit(self, coid, instrument, side, quantity, final_state=None):
                enviadas.append(coid)
                return super().submit(coid, instrument, side, quantity, final_state)

        coord.broker = Conta()
        return coord, enviadas

    def test_another_agent_cannot_close_someone_elses_position(self):
        from tradingagents.desk.execution import ExecutionRefused
        coord, enviadas = self._com_posicao_de("trader-trend")
        with pytest.raises(ExecutionRefused, match="pertence a"):
            coord.close_position(instrument="BTC/USDT", quantity=Decimal("1"),
                                 price=Decimal("110"), cost_basis=Decimal("100"),
                                 agent_id="trader-breakout")
        assert not enviadas

    def test_the_owner_can_close(self):
        coord, enviadas = self._com_posicao_de("trader-trend")
        out = coord.close_position(instrument="BTC/USDT", quantity=Decimal("1"),
                                   price=Decimal("110"), cost_basis=Decimal("100"),
                                   agent_id="trader-trend")
        assert out["estado"] in ("filled", "acknowledged") and enviadas

    def test_the_guardian_may_close_on_behalf(self):
        coord, enviadas = self._com_posicao_de("trader-trend")
        coord.close_position(instrument="BTC/USDT", quantity=Decimal("1"),
                             price=Decimal("110"), cost_basis=Decimal("100"),
                             agent_id="position-guardian")
        assert enviadas, "o guardião precisa conseguir encerrar posição órfã"


class TestLotesPorAgente:
    """BLOCKER: position_owner devolvia o agente da COMPRA MAIS RECENTE. Com
    dois agentes no mesmo instrumento, o último a comprar podia fechar o lote
    do outro — destruindo a atribuição e deixando o primeiro achando que
    ainda está exposto."""

    def _dois_agentes(self):
        m, risco, livro, coord = sistema("10000")
        livro.reserve(Decimal("100"), ref="rA")
        livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("100"), Decimal("0"),
                       reserved=Decimal("100"), ref="oA", agent_id="agente-A")
        livro.reserve(Decimal("110"), ref="rB")
        livro.fill_buy("BTC/USDT", Decimal("1"), Decimal("110"), Decimal("0"),
                       reserved=Decimal("110"), ref="oB", agent_id="agente-B")
        enviadas = []

        class Conta(CorretoraQueAceita):
            """Executa ao preço configurado — o preço vem da CORRETORA."""
            preco = Decimal("120")

            def submit(self, coid, instrument, side, quantity, final_state=None):
                enviadas.append((coid, quantity))
                return {"id": f"ex-{coid}", "status": "filled",
                        "filled": quantity, "average": self.preco,
                        "fee": Decimal("0"),
                        "trades": [{"id": f"t-{coid}", "amount": quantity,
                                    "price": self.preco,
                                    "fee": {"cost": Decimal("0")}}]}

        coord.broker = Conta()
        return coord, livro, enviadas

    def test_each_agent_has_its_own_lot(self):
        coord, livro, _ = self._dois_agentes()
        lotes = livro.position_lots("BTC/USDT")
        por_agente = {lote["agente"]: Decimal(lote["quantidade"]) for lote in lotes}
        assert por_agente == {"agente-A": Decimal("1"), "agente-B": Decimal("1")}
        assert livro.position_quantity("BTC/USDT") == Decimal("2")

    def test_an_agent_cannot_sell_more_than_its_own_lot(self):
        from tradingagents.desk.execution import ExecutionRefused
        coord, livro, enviadas = self._dois_agentes()
        with pytest.raises(ExecutionRefused):
            coord.close_position(instrument="BTC/USDT", quantity=Decimal("2"),
                                 price=Decimal("120"), cost_basis=Decimal("210"),
                                 agent_id="agente-B")
        assert not enviadas, "B vendeu o lote de A"

    def test_selling_own_lot_leaves_the_other_intact(self):
        coord, livro, enviadas = self._dois_agentes()
        coord.close_position(instrument="BTC/USDT", quantity=Decimal("1"),
                             price=Decimal("120"), cost_basis=Decimal("110"),
                             agent_id="agente-B")
        restantes = {lote["agente"]: Decimal(lote["quantidade"])
                     for lote in coord.ledger.position_lots("BTC/USDT")}
        assert restantes.get("agente-A") == Decimal("1"), "o lote de A sumiu"
        assert restantes.get("agente-B", Decimal("0")) == Decimal("0")

    def test_pnl_goes_to_the_selling_agent(self):
        coord, livro, _ = self._dois_agentes()
        coord.close_position(instrument="BTC/USDT", quantity=Decimal("1"),
                             price=Decimal("120"), cost_basis=Decimal("110"),
                             agent_id="agente-B")
        atrib = coord.ledger.attribution()
        # B comprou a 110 e vendeu a 120: resultado EXATO de +10.
        # Se o custo viesse da posição agregada (105 por unidade), daria +15 —
        # é assim que o custo de quem comprou barato contamina quem comprou
        # caro, e o valor exato é o que pega isso.
        assert atrib.get("agente-B") == Decimal("10.00000000"), atrib
        assert "agente-A" not in atrib or atrib["agente-A"] == Decimal("0")

    def test_partial_close_reduces_only_that_lot(self):
        coord, livro, _ = self._dois_agentes()
        coord.close_position(instrument="BTC/USDT", quantity=Decimal("0.4"),
                             price=Decimal("120"), cost_basis=Decimal("44"),
                             agent_id="agente-B")
        lotes = {lote["agente"]: Decimal(lote["quantidade"])
                 for lote in coord.ledger.position_lots("BTC/USDT")}
        assert lotes["agente-B"] == Decimal("0.6")
        assert lotes["agente-A"] == Decimal("1")

    def test_lots_sum_to_the_aggregate_position(self):
        """Invariante: soma dos lotes == posição agregada do livro."""
        coord, livro, _ = self._dois_agentes()
        soma = sum(Decimal(lote["quantidade"])
                   for lote in livro.position_lots("BTC/USDT"))
        assert soma == livro.position_quantity("BTC/USDT")

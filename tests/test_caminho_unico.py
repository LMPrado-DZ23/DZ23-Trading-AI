"""Caminho único de execução: entrada e saída passam pelo MESMO código.

Quase todos os defeitos das auditorias anteriores nasceram da divergência
entre os dois caminhos. Estes testes existem para que a divergência não
possa voltar sem alguém perceber.
"""
from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from tradingagents.desk.execution import (
    ExecutionCoordinator,
    ExecutionRefused,
    IdempotencyConflict,
    OrderState,
)
from tradingagents.desk.intents import ExecutionIntent, IntentInvalid, Kind, Side, exit_intent
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import DeskMandate, Mode
from tradingagents.desk.risk import RiskEngine


class Corretora:
    mode = "testnet"

    def __init__(self, preco=Decimal("100"), erro=None):
        self.preco = preco
        self.erro = erro
        self.enviadas = []

    def submit(self, coid, instrument, side, quantity, final_state=None):
        self.enviadas.append((coid, side, quantity))
        if self.erro:
            raise self.erro
        return {"id": f"ex-{coid}", "status": "filled", "filled": quantity,
                "average": self.preco, "fee": Decimal("0"),
                "trades": [{"id": f"t-{coid}", "amount": quantity,
                            "price": self.preco, "fee": {"cost": Decimal("0")}}]}


def sistema(broker=None, com_posicao=None, dono="agente-A"):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=("BTC/USDT",),
                    capital=Decimal("10000"))
    risco = RiskEngine(m)
    livro = Ledger(opening_cash=m.capital)
    if com_posicao:
        livro.reserve(Decimal("100"), ref="r0")
        livro.fill_buy("BTC/USDT", Decimal(com_posicao), Decimal("100"),
                       Decimal("0"), reserved=Decimal("100"), ref="o0",
                       agent_id=dono)
    coord = ExecutionCoordinator(m, livro, risco, worker_id="w",
                                 broker=broker or Corretora())
    return m, risco, livro, coord


def entrada(agent="agente-A", qtd="1", coid=""):
    return ExecutionIntent(kind=Kind.ENTRY, side=Side.BUY,
                           instrument="BTC/USDT", quantity=Decimal(qtd),
                           agent_id=agent, account="conta-1",
                           client_order_id=coid)


def saida(agent="agente-A", qtd="1", coid="", custo="100"):
    return exit_intent(instrument="BTC/USDT", quantity=Decimal(qtd),
                       agent_id=agent, account="conta-1",
                       cost_basis=Decimal(custo), client_order_id=coid)


class TestContratoDaIntencao:
    def test_quantity_must_be_positive(self):
        for q in (Decimal("0"), Decimal("-1")):
            with pytest.raises(IntentInvalid, match="positiva"):
                entrada(qtd=q)

    def test_instrument_and_agent_are_required(self):
        with pytest.raises(IntentInvalid, match="instrumento"):
            ExecutionIntent(kind=Kind.ENTRY, side=Side.BUY, instrument="",
                            quantity=Decimal("1"), agent_id="a", account="c")
        with pytest.raises(IntentInvalid, match="agente"):
            ExecutionIntent(kind=Kind.ENTRY, side=Side.BUY,
                            instrument="BTC/USDT", quantity=Decimal("1"),
                            agent_id="", account="c")

    def test_kind_and_side_must_agree(self):
        with pytest.raises(IntentInvalid, match="entrada"):
            ExecutionIntent(kind=Kind.ENTRY, side=Side.SELL,
                            instrument="BTC/USDT", quantity=Decimal("1"),
                            agent_id="a", account="c")
        with pytest.raises(IntentInvalid, match="saída"):
            ExecutionIntent(kind=Kind.EXIT, side=Side.BUY,
                            instrument="BTC/USDT", quantity=Decimal("1"),
                            agent_id="a", account="c")

    def test_the_intent_is_immutable(self):
        """Uma intenção não muda de ideia: o que foi autorizado é o que é
        enviado. Asserção específica, não exceção cega."""
        import dataclasses
        ei = entrada()
        with pytest.raises(dataclasses.FrozenInstanceError):
            ei.quantity = Decimal("99")


class TestFingerprintCanonica:
    """Uma fórmula só. Fórmulas distintas por caminho deixavam a entrada sem
    detecção de conflito enquanto a saída tinha."""

    def test_same_request_same_fingerprint(self):
        assert entrada().fingerprint() == entrada().fingerprint()
        assert saida().fingerprint() == saida().fingerprint()

    def test_different_quantity_changes_it(self):
        assert entrada(qtd="1").fingerprint() != entrada(qtd="2").fingerprint()

    def test_different_agent_changes_it(self):
        assert entrada(agent="A").fingerprint() != entrada(agent="B").fingerprint()

    def test_entry_and_exit_never_collide(self):
        assert entrada().fingerprint() != saida().fingerprint()

    def test_both_sides_use_the_same_formula(self):
        fonte = inspect.getsource(ExecutionIntent.fingerprint)
        assert "self.kind.value" in fonte and "self.side.value" in fonte


class TestEquivalenciaEntreOsLados:
    """As mesmas regras, na mesma ordem, para comprar e vender."""

    @pytest.mark.parametrize("construir,posicao", [
        (entrada, None), (saida, "1")])
    def test_reconciliation_gate_blocks_both_sides(self, construir, posicao):
        m, risco, livro, coord = sistema(com_posicao=posicao)
        coord.exigir_reconciliacao("estado incerto")
        with pytest.raises(ExecutionRefused, match="reconciliação pendente"):
            coord.submit_intent(construir())
        assert not coord.broker.enviadas

    @pytest.mark.parametrize("construir,posicao", [
        (entrada, None), (saida, "1")])
    def test_inactive_mandate_blocks_both_sides(self, construir, posicao):
        from tradingagents.desk.models import utcnow
        m, risco, livro, coord = sistema(com_posicao=posicao)
        object.__setattr__(coord.mandate, "revoked_at", utcnow())
        with pytest.raises(ExecutionRefused, match="mandato"):
            coord.submit_intent(construir())
        assert not coord.broker.enviadas

    @pytest.mark.parametrize("construir,posicao", [
        (entrada, None), (saida, "1")])
    def test_timeout_is_ambiguous_on_both_sides(self, construir, posicao):
        from tradingagents.desk.execution import BrokerTimeout
        broker = Corretora(erro=BrokerTimeout("perdida"))
        m, risco, livro, coord = sistema(broker, com_posicao=posicao)
        rec = coord.submit_intent(construir())
        assert rec.state is OrderState.UNKNOWN_RECONCILIATION_REQUIRED
        assert coord.reconciliacao_pendente is True, (
            "timeout precisa ligar o portão nos DOIS lados")

    @pytest.mark.parametrize("construir,posicao", [
        (entrada, None), (saida, "1")])
    def test_idempotent_retry_on_both_sides(self, construir, posicao):
        m, risco, livro, coord = sistema(com_posicao=posicao)
        a = coord.submit_intent(construir(coid="fixo"))
        b = coord.submit_intent(construir(coid="fixo"))
        assert a.order_id == b.order_id
        assert len(coord.broker.enviadas) == 1

    @pytest.mark.parametrize("construir,posicao", [
        (entrada, None), (saida, "1")])
    def test_conflicting_coid_is_refused_on_both_sides(self, construir, posicao):
        m, risco, livro, coord = sistema(com_posicao="2")
        coord.submit_intent(construir(coid="X", qtd="1"))
        with pytest.raises(IdempotencyConflict):
            coord.submit_intent(construir(coid="X", qtd="0.5"))

    @pytest.mark.parametrize("construir,posicao", [
        (entrada, None), (saida, "1")])
    def test_ack_is_not_execution_on_both_sides(self, construir, posicao):
        """Resposta sem trades identificados não produz efeito econômico."""
        class SemTrades(Corretora):
            def submit(self, coid, instrument, side, quantity, final_state=None):
                self.enviadas.append((coid, side, quantity))
                return {"id": "O1", "status": "closed", "filled": quantity,
                        "average": Decimal("100")}

        m, risco, livro, coord = sistema(SemTrades(), com_posicao=posicao)
        rec = coord.submit_intent(construir())
        assert rec.state is OrderState.ACKNOWLEDGED
        assert rec.filled_qty == Decimal("0") and not rec.seen_fills

    @pytest.mark.parametrize("construir,posicao", [
        (entrada, None), (saida, "1")])
    def test_every_order_has_a_durable_record(self, construir, posicao):
        m, risco, livro, coord = sistema(com_posicao=posicao)
        rec = coord.submit_intent(construir())
        doc = coord.snapshot_estado()
        ids = [o["order_id"] for o in doc["ordens"]]
        assert rec.order_id in ids
        gravada = next(o for o in doc["ordens"] if o["order_id"] == rec.order_id)
        assert gravada["client_order_id"] and gravada["fingerprint"]
        assert gravada["lado"] in ("buy", "sell")


class TestAdaptadoresSemLogicaPropria:
    """submit e close_position não podem voltar a ter caminho próprio."""

    def test_submit_delegates_to_the_single_path(self):
        fonte = inspect.getsource(ExecutionCoordinator.submit)
        assert "self.submit_intent(" in fonte
        for proibido in ("self.broker.submit(", "OrderRecord.__new__",
                         "marcar_incerta", "_by_key["):
            assert proibido not in fonte, f"submit voltou a ter lógica: {proibido}"

    def test_close_position_delegates_to_the_single_path(self):
        fonte = inspect.getsource(ExecutionCoordinator.close_position)
        assert "self.submit_intent(" in fonte
        for proibido in ("self.broker.submit(", "OrderRecord.__new__",
                         "marcar_incerta", "_by_key["):
            assert proibido not in fonte, f"close voltou a ter lógica: {proibido}"

    def test_only_one_place_creates_order_records(self):
        fonte = inspect.getsource(ExecutionCoordinator)
        assert fonte.count("OrderRecord.__new__") == 1, (
            "mais de um lugar cria OrderRecord: a divergência voltou")

    def test_only_one_place_calls_the_broker_for_new_orders(self):
        fonte = inspect.getsource(ExecutionCoordinator.submit_intent)
        assert "self.broker.submit(" in fonte
        outros = inspect.getsource(ExecutionCoordinator).count("self.broker.submit(")
        assert outros == 1, "há mais de uma porta de envio para a corretora"


class TestRegrasEspecificasDeCadaLado:
    """O que difere entre comprar e vender está DECLARADO, não implícito."""

    def test_sell_requires_an_open_lot(self):
        m, risco, livro, coord = sistema()
        with pytest.raises(ExecutionRefused, match="nenhuma posição"):
            coord.submit_intent(saida())

    def test_sell_cannot_exceed_the_agents_lot(self):
        m, risco, livro, coord = sistema(com_posicao="1", dono="agente-A")
        with pytest.raises(ExecutionRefused, match="maior que o lote"):
            coord.submit_intent(saida(qtd="2"))

    def test_buy_does_not_require_a_lot(self):
        m, risco, livro, coord = sistema()
        rec = coord.submit_intent(entrada())
        assert rec.state in (OrderState.ACKNOWLEDGED, OrderState.FILLED)

    def test_side_specific_rules_live_in_one_method(self):
        fonte = inspect.getsource(ExecutionCoordinator._autorizar_saida)
        assert "lot_quantity" in fonte
        caminho = inspect.getsource(ExecutionCoordinator.submit_intent)
        assert "_autorizar_saida" in caminho

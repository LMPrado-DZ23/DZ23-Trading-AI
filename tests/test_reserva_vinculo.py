"""Uma reserva financia UMA ordem.

`_autorizar_entrada` comparava com o valor ORIGINAL da reserva. Depois de um
consumo parcial, uma segunda ordem apresentando o mesmo `reservation_id` e o
mesmo valor original passava na autorização — o mesmo capital cobrindo duas
ordens ao mesmo tempo. E `filled_quantity` nunca avançava, então a cobertura
por quantidade também nunca encolhia.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from tradingagents.desk.execution import ExecutionCoordinator, ExecutionRefused
from tradingagents.desk.intents import ExecutionIntent, Kind, Side
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import Action, DeskMandate, Mode, TradeProposal, utcnow
from tradingagents.desk.risk import RiskEngine, RiskVeto


class Corretora:
    mode = "testnet"

    def __init__(self):
        self.enviadas = []

    def submit(self, coid, instrument, side, quantity, final_state=None):
        self.enviadas.append((coid, quantity))
        return {"id": f"ex-{coid}", "status": "open", "filled": 0}


def sistema(capital="1000"):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=("BTC/USDT",),
                    capital=Decimal(capital))
    livro = Ledger(opening_cash=m.capital)
    risco = RiskEngine(m, carteira=livro)
    coord = ExecutionCoordinator(m, livro, risco, worker_id="w",
                                 broker=Corretora())
    return m, risco, livro, coord


def reservar(risco, exposicao="0.25"):
    p = TradeProposal(agent_id="A", strategy="s", instrument="BTC/USDT",
                      action=Action.BUY, reason="t",
                      desired_exposure_pct=Decimal(exposicao),
                      confidence=Decimal("0.8"),
                      valid_until=utcnow() + timedelta(minutes=5))
    return risco.reserve(p, Decimal("100"), agent_id="A")


def intencao(res, coid, quantidade=None):
    return ExecutionIntent(
        kind=Kind.ENTRY, side=Side.BUY, instrument="BTC/USDT",
        quantity=quantidade if quantidade is not None else res.quantity,
        agent_id="A", account="conta-1", client_order_id=coid,
        reservation_id=res.reservation_id, reserved_amount=res.amount)


class TestUmaReservaUmaOrdem:
    def test_a_second_order_cannot_reuse_the_same_reservation(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        coord.submit_intent(intencao(res, "coid-A"))
        assert len(coord.broker.enviadas) == 1

        with pytest.raises((ExecutionRefused, RiskVeto)):
            coord.submit_intent(intencao(res, "coid-B"))
        assert len(coord.broker.enviadas) == 1, (
            "a mesma reserva financiou uma SEGUNDA ordem")

    def test_reuse_is_refused_even_after_a_partial_consumption(self):
        """O caso que passava: com a reserva já parcialmente consumida, a
        segunda ordem apresentava o valor ORIGINAL e era aceita."""
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        rec = coord.submit_intent(intencao(res, "coid-A"))
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity / 2,
                           preco=Decimal("100"), taxa=Decimal("0"),
                           fill_id="f1")
        with pytest.raises((ExecutionRefused, RiskVeto)):
            coord.submit_intent(intencao(res, "coid-B"))
        assert len(coord.broker.enviadas) == 1

    def test_the_legitimate_retry_still_works(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        a = coord.submit_intent(intencao(res, "coid-A"))
        coord.aplicar_fill(a, side="buy", quantidade=res.quantity / 2,
                           preco=Decimal("100"), taxa=Decimal("0"),
                           fill_id="f1")
        b = coord.submit_intent(intencao(res, "coid-A"))   # mesmo pedido
        assert a.order_id == b.order_id
        assert len(coord.broker.enviadas) == 1

    def test_binding_is_visible_in_the_reservation(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        coord.submit_intent(intencao(res, "coid-A"))
        viva = risco.reserva(res.reservation_id)
        assert viva.bound_client_order_id == "coid-A"


class TestVinculoAtomico:
    def test_only_one_binding_wins_under_concurrency(self):
        import threading
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        vitorias, derrotas = [], []

        def tentar(coid):
            try:
                risco.bind(res.reservation_id, coid)
                vitorias.append(coid)
            except RiskVeto:
                derrotas.append(coid)

        fios = [threading.Thread(target=tentar, args=(c,))
                for c in ("A", "B", "C", "D")]
        for f in fios:
            f.start()
        for f in fios:
            f.join()
        assert len(vitorias) == 1, f"mais de um vínculo venceu: {vitorias}"
        assert len(derrotas) == 3

    def test_binding_the_same_order_twice_is_idempotent(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        risco.bind(res.reservation_id, "coid-A")
        risco.bind(res.reservation_id, "coid-A")      # não pode estourar
        assert risco.reserva(res.reservation_id).bound_client_order_id == "coid-A"

    def test_binding_a_different_order_is_refused(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        risco.bind(res.reservation_id, "coid-A")
        with pytest.raises(RiskVeto, match="vinculada"):
            risco.bind(res.reservation_id, "coid-B")


class TestQuantidadeConsumidaAvanca:
    def test_filled_quantity_moves_with_each_consumption(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        base_q = res.original_quantity
        risco.consume(res.reservation_id, res.original_amount * Decimal("0.2"),
                      quantidade=base_q * Decimal("0.2"))
        viva = risco.reserva(res.reservation_id)
        assert viva.filled_quantity == base_q * Decimal("0.2")
        assert viva.remaining_quantity == base_q * Decimal("0.8")

        risco.consume(res.reservation_id, res.original_amount * Decimal("0.3"),
                      quantidade=base_q * Decimal("0.3"))
        viva = risco.reserva(res.reservation_id)
        assert viva.filled_quantity == base_q * Decimal("0.5")
        assert viva.remaining_quantity == base_q * Decimal("0.5")

    def test_quantity_cannot_be_overfilled(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        with pytest.raises(RiskVeto, match="quantidade"):
            risco.consume(res.reservation_id, res.original_amount / 2,
                          quantidade=res.original_quantity * 2)

    def test_a_real_fill_advances_the_quantity(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        rec = coord.submit_intent(intencao(res, "coid-A"))
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity / 4,
                           preco=Decimal("100"), taxa=Decimal("0"),
                           fill_id="f1")
        viva = risco.reserva(res.reservation_id)
        assert viva.filled_quantity == res.quantity / 4, (
            "o fill não avançou a quantidade consumida da reserva")


class TestVinculoSobreviveAoRestart:
    def test_binding_is_persisted(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        coord.submit_intent(intencao(res, "coid-A"))
        docs = risco.active_reservations()

        m2, risco2, livro2, coord2 = sistema()
        risco2.restore_reservations(docs)
        viva = risco2.reserva(res.reservation_id)
        assert viva.bound_client_order_id == "coid-A", "o vínculo se perdeu"
        with pytest.raises(RiskVeto):
            risco2.bind(res.reservation_id, "coid-B")


class TestDefesaEmProfundidade:
    """O vínculo é checado em DUAS camadas: no `bind()` (que decide sob lock)
    e na autorização (que recusa antes de qualquer efeito). A segunda é
    redundante de propósito — se alguém chamar a autorização por outro
    caminho, ela ainda precisa recusar sozinha."""

    def test_the_authorisation_layer_refuses_on_its_own(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        risco.bind(res.reservation_id, "coid-A")
        # chamada DIRETA à autorização, sem passar pelo bind do envio
        with pytest.raises(ExecutionRefused, match="já financia"):
            coord._autorizar_entrada(intencao(res, "coid-B"))

    def test_the_authorisation_layer_allows_the_bound_order(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        risco.bind(res.reservation_id, "coid-A")
        coord._autorizar_entrada(intencao(res, "coid-A"))   # não estoura

    def test_a_fully_consumed_reservation_covers_nothing(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        rec = coord.submit_intent(intencao(res, "coid-A"))
        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                           preco=Decimal("100"), taxa=Decimal("0"),
                           fill_id="f-total")
        assert risco.reserva(res.reservation_id) is None
        with pytest.raises(ExecutionRefused, match="não está viva"):
            coord._autorizar_entrada(intencao(res, "coid-C"))

"""Cada reserva tem sua própria conta de capital travado.

O livro tinha UMA conta `RESERVED` global. O risco sabia quanto R1 tinha,
mas o livro só sabia o total — então uma ordem podia consumir ou liberar
dinheiro de outra, e a verificação por soma não via nada de errado.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from tradingagents.desk.execution import ExecutionCoordinator, OrderState
from tradingagents.desk.intents import ExecutionIntent, Kind, Side
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import Action, DeskMandate, Mode, TradeProposal, utcnow
from tradingagents.desk.risk import RiskEngine


class Corretora:
    mode = "testnet"

    def __init__(self, erro=None):
        self.erro = erro
        self.enviadas = []

    def submit(self, coid, instrument, side, quantity, final_state=None):
        self.enviadas.append(coid)
        if self.erro:
            raise self.erro
        return {"id": f"ex-{coid}", "status": "open", "filled": 0}


def sistema(capital="1000", buffer="0", broker=None):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER,
                    instruments=("BTC/USDT", "ETH/USDT", "SOL/USDT"),
                    capital=Decimal(capital),
                    slippage_buffer_pct=Decimal(buffer))
    livro = Ledger(opening_cash=m.capital)
    risco = RiskEngine(m, carteira=livro)
    coord = ExecutionCoordinator(m, livro, risco, worker_id="w",
                                 broker=broker or Corretora())
    return m, risco, livro, coord


def reservar(risco, instrumento="BTC/USDT", pct="0.10"):
    p = TradeProposal(agent_id="A", strategy="s", instrument=instrumento,
                      action=Action.BUY, reason="t",
                      desired_exposure_pct=Decimal(pct),
                      confidence=Decimal("0.8"),
                      valid_until=utcnow() + timedelta(minutes=5))
    return risco.reserve(p, Decimal("100"), agent_id="A")


def enviar(coord, res, coid):
    ei = ExecutionIntent(kind=Kind.ENTRY, side=Side.BUY,
                         instrument=res.instrument, quantity=res.quantity,
                         agent_id="A", account="conta-1", client_order_id=coid,
                         reservation_id=res.reservation_id,
                         reserved_amount=res.amount)
    return coord.submit_intent(ei)


class TestContaPorReserva:
    def test_the_ledger_knows_each_reservation_separately(self):
        livro = Ledger(opening_cash=Decimal("1000"))
        livro.reserve(Decimal("100"), ref="o1", reservation_id="R1")
        livro.reserve(Decimal("150"), ref="o2", reservation_id="R2")
        assert livro.reserved_cash("R1") == Decimal("100")
        assert livro.reserved_cash("R2") == Decimal("150")
        assert livro.reserved_cash() == Decimal("250"), "o total ainda serve"

    def test_releasing_one_does_not_touch_the_other(self):
        livro = Ledger(opening_cash=Decimal("1000"))
        livro.reserve(Decimal("100"), ref="o1", reservation_id="R1")
        livro.reserve(Decimal("150"), ref="o2", reservation_id="R2")
        livro.release(Decimal("100"), ref="o1", reservation_id="R1")
        assert livro.reserved_cash("R1") == Decimal("0")
        assert livro.reserved_cash("R2") == Decimal("150"), (
            "a liberação de R1 mexeu no dinheiro de R2")

    def test_releasing_more_than_that_reservation_is_refused(self):
        livro = Ledger(opening_cash=Decimal("1000"))
        livro.reserve(Decimal("100"), ref="o1", reservation_id="R1")
        livro.reserve(Decimal("150"), ref="o2", reservation_id="R2")
        with pytest.raises(ValueError, match="R1|reservado"):
            livro.release(Decimal("120"), ref="o1", reservation_id="R1")
        assert livro.reserved_cash("R2") == Decimal("150")


class TestSemConsumoCruzado:
    def test_an_expensive_fill_cannot_eat_another_reservation(self):
        m, risco, livro, coord = sistema()
        r1 = reservar(risco, "BTC/USDT")
        r2 = reservar(risco, "ETH/USDT")
        rec1 = enviar(coord, r1, "c1")
        enviar(coord, r2, "c2")
        assert livro.reserved_cash(r2.reservation_id) == r2.hard_reserved_amount

        # R1 executa 50% mais caro que o reservado
        coord.aplicar_fill(rec1, side="buy", quantidade=r1.quantity,
                           preco=Decimal("150"), taxa=Decimal("0"),
                           fill_id="f1")
        assert livro.reserved_cash(r2.reservation_id) == r2.hard_reserved_amount, (
            "a execução cara de R1 comeu a reserva de R2")
        assert risco.deficit_total() > 0, "o excesso precisa virar déficit"

    def test_cancelling_one_leaves_the_other_untouched(self):
        m, risco, livro, coord = sistema()
        r1 = reservar(risco, "BTC/USDT")
        r2 = reservar(risco, "ETH/USDT")
        rec1 = enviar(coord, r1, "c1")
        enviar(coord, r2, "c2")
        caixa_antes = livro.cash_available()

        rec1.state = OrderState.CANCEL_PENDING
        coord.confirm_cancel(rec1.order_id)
        assert livro.reserved_cash(r2.reservation_id) == r2.hard_reserved_amount
        assert livro.cash_available() == caixa_antes + r1.hard_reserved_amount, (
            "a devolução de R1 não bateu com a cobertura de R1")

    def test_a_rejection_only_frees_its_own_reservation(self):
        m, risco, livro, coord = sistema()
        r2 = reservar(risco, "ETH/USDT")
        enviar(coord, r2, "c2")
        coord.broker = Corretora(erro=RuntimeError("rejected"))
        r1 = reservar(risco, "BTC/USDT")
        enviar(coord, r1, "c1")
        assert livro.reserved_cash(r2.reservation_id) == r2.hard_reserved_amount
        assert livro.reserved_cash(r1.reservation_id) == Decimal("0")


class TestSomaEnganosa:
    """Duas reservas trocadas somam igual e ainda assim estão erradas."""

    def test_matching_totals_do_not_prove_consistency(self):
        m, risco, livro, coord = sistema()
        r1 = reservar(risco, "BTC/USDT")
        r2 = reservar(risco, "ETH/USDT")
        enviar(coord, r1, "c1")
        enviar(coord, r2, "c2")
        # move dinheiro de R1 para R2 no livro: o TOTAL continua igual
        livro.release(Decimal("10"), ref="desvio", reservation_id=r1.reservation_id)
        livro.reserve(Decimal("10"), ref="desvio", reservation_id=r2.reservation_id)
        assert livro.reserved_cash() == risco.reserved_total, (
            "o cenário precisa ter totais iguais")

        ok, motivo = risco.capital_state_consistent()
        assert ok is False, "a verificação passou só porque os totais batem"
        assert "R" in motivo or "reserva" in motivo.lower()

    def test_the_mismatch_names_the_reservation(self):
        m, risco, livro, coord = sistema()
        r1 = reservar(risco, "BTC/USDT")
        enviar(coord, r1, "c1")
        livro.release(Decimal("10"), ref="desvio", reservation_id=r1.reservation_id)
        ok, motivo = risco.capital_state_consistent()
        assert ok is False
        assert r1.reservation_id in motivo, f"motivo não identifica a reserva: {motivo}"


class TestIdentidadeSobreviveAoRestart:
    def test_each_reservation_keeps_its_own_balance(self):
        m, risco, livro, coord = sistema()
        r1 = reservar(risco, "BTC/USDT")
        r2 = reservar(risco, "ETH/USDT")
        enviar(coord, r1, "c1")
        enviar(coord, r2, "c2")
        doc = coord.snapshot_estado()

        m2, risco2, livro2, coord2 = sistema()
        coord2.restaurar_estado(doc)
        assert coord2.ledger.reserved_cash(r1.reservation_id) == r1.hard_reserved_amount
        assert coord2.ledger.reserved_cash(r2.reservation_id) == r2.hard_reserved_amount
        ok, motivo = risco2.capital_state_consistent()
        assert ok is True, motivo


class TestConcorrenciaEntreReservas:
    def test_simultaneous_fills_do_not_cross(self):
        import threading
        m, risco, livro, coord = sistema(capital="10000")
        pares = []
        for i, inst in enumerate(("BTC/USDT", "ETH/USDT", "SOL/USDT")):
            r = reservar(risco, inst)
            pares.append((r, enviar(coord, r, f"c{i}")))

        def executar(par):
            r, rec = par
            coord.aplicar_fill(rec, side="buy", quantidade=r.quantity / 2,
                               preco=Decimal("100"), taxa=Decimal("0"),
                               fill_id=f"f-{r.reservation_id}")

        fios = [threading.Thread(target=executar, args=(p,)) for p in pares]
        for f in fios:
            f.start()
        for f in fios:
            f.join()
        for r, _ in pares:
            assert livro.reserved_cash(r.reservation_id) == \
                risco.reserva(r.reservation_id).remaining_amount, (
                f"reserva {r.reservation_id} divergiu sob concorrência")


class TestMigracaoDeSnapshotAntigo:
    """Snapshots antigos têm a conta global `RESERVED` sem dono. Dividir esse
    saldo entre reservas sem prova seria inventar contabilidade."""

    def _doc_antigo(self, reservas, reservado="250"):
        return {
            "schema": "dz23.oms/2", "worker_id": "w", "dia": "",
            "perda_do_dia": "0", "oms_version": 1, "ordens": [],
            "posicoes": {}, "caixa": "750", "moeda": "USDT",
            "lancamentos": [
                {"tx_id": "t1", "kind": "opening", "ref": "abertura",
                 "at": utcnow().isoformat(), "instrument": "", "quantity": "0",
                 "agent_id": "", "entries": [
                     {"account": "CASH:USDT", "amount": "1000", "memo": ""},
                     {"account": "EQUITY", "amount": "-1000", "memo": ""}]},
                {"tx_id": "t2", "kind": "reserve", "ref": "o1",
                 "at": utcnow().isoformat(), "instrument": "", "quantity": "0",
                 "agent_id": "", "entries": [
                     {"account": "RESERVED", "amount": reservado, "memo": ""},
                     {"account": "CASH:USDT", "amount": f"-{reservado}",
                      "memo": ""}]},
            ],
            "reservas": reservas,
        }

    def _reserva_doc(self, rid, valor="250"):
        return {"reservation_id": rid, "account": "conta-1", "agent_id": "A",
                "instrument": "BTC/USDT", "amount": valor, "quantity": "2.5",
                "consumed_amount": "0", "filled_quantity": "0",
                "buffer_amount": "0", "bound_client_order_id": "",
                "remaining_amount": valor, "remaining_quantity": "2.5",
                "mandate_version": 1, "expires_at": None}

    def test_a_single_matching_reservation_is_adopted(self):
        """Uma reserva viva e o valor bate: há evidência suficiente."""
        m, risco, livro, coord = sistema()
        doc = self._doc_antigo([self._reserva_doc("R1", "250")], "250")
        coord.restaurar_estado(doc)
        assert coord.ledger.reserved_cash("R1") == Decimal("250"), (
            "a migração não atribuiu o saldo à única reserva possível")
        ok, motivo = risco.capital_state_consistent()
        assert ok is True, motivo

    def test_two_reservations_are_not_split_by_guesswork(self):
        m, risco, livro, coord = sistema()
        doc = self._doc_antigo(
            [self._reserva_doc("R1", "125"), self._reserva_doc("R2", "125")],
            "250")
        info = coord.restaurar_estado(doc)
        assert coord.ledger.reserved_cash("R1") == Decimal("0"), (
            "o saldo antigo foi dividido sem prova de quem era o dono")
        assert coord.reconciliacao_pendente is True
        assert any("UNKNOWN_RESERVATION_OWNERSHIP" in p
                   for p in info.get("pendencias", [])), info

    def test_a_value_that_does_not_match_is_not_adopted(self):
        m, risco, livro, coord = sistema()
        doc = self._doc_antigo([self._reserva_doc("R1", "200")], "250")
        coord.restaurar_estado(doc)
        assert coord.ledger.reserved_cash("R1") == Decimal("0")
        assert coord.reconciliacao_pendente is True


class TestContaDaReservaNuncaFicaNegativa:
    """Consumir mais do que a própria reserva tem deixa a conta DELA
    negativa — e uma conta de capital negativa é capital emprestado de
    outra, mesmo que o saldo da outra pareça intacto."""

    def test_an_expensive_fill_does_not_overdraw_its_own_account(self):
        m, risco, livro, coord = sistema()
        r1 = reservar(risco, "BTC/USDT")
        r2 = reservar(risco, "ETH/USDT")
        rec1 = enviar(coord, r1, "c1")
        enviar(coord, r2, "c2")

        coord.aplicar_fill(rec1, side="buy", quantidade=r1.quantity,
                           preco=Decimal("150"), taxa=Decimal("0"),
                           fill_id="f1")
        saldo_r1 = livro.reserved_cash(r1.reservation_id)
        assert saldo_r1 >= 0, (
            f"a conta de R1 ficou em {saldo_r1}: consumiu cobertura que não "
            "era dela")
        assert livro.reserved_cash(r2.reservation_id) == r2.hard_reserved_amount

    def test_no_reservation_account_is_ever_negative(self):
        """Várias reservas vivas, uma delas executando caro: nenhuma conta
        de capital pode ficar negativa.

        (O primeiro déficit fecha o portão — por isso os envios acontecem
        todos antes das execuções.)"""
        m, risco, livro, coord = sistema(capital="10000")
        pares = []
        for i, inst in enumerate(("BTC/USDT", "ETH/USDT", "SOL/USDT")):
            r = reservar(risco, inst)
            pares.append((r, enviar(coord, r, f"c{i}")))
        for i, (r, rec) in enumerate(pares):
            coord.aplicar_fill(rec, side="buy", quantidade=r.quantity,
                               preco=Decimal("130"), taxa=Decimal("2"),
                               fill_id=f"f{i}")
        for conta, saldo in livro.balances().items():
            if conta.startswith("RESERVED"):
                assert saldo >= 0, f"{conta} ficou negativa ({saldo})"
        assert risco.deficit_total() > 0
        assert coord.reconciliacao_pendente is True

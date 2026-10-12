"""Autorização de ENTRADA no caminho único — simétrica à da saída.

A refatoração deixou `_autorizar_saida` no caminho único, mas a entrada
seguiu sem simétrico: quem chamasse `submit_intent` com uma intenção de
compra pulava toda a verificação de cobertura de capital, porque ela vivia
fora, no RiskEngine. Vender exigia lote; comprar não exigia nada.
"""
from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from tradingagents.desk.execution import ExecutionCoordinator, ExecutionRefused, OrderState
from tradingagents.desk.intents import ExecutionIntent, Kind, Side
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import Action, DeskMandate, Mode, TradeProposal, utcnow
from tradingagents.desk.risk import RiskEngine


class Corretora:
    mode = "testnet"

    def __init__(self):
        self.enviadas = []

    def submit(self, coid, instrument, side, quantity, final_state=None):
        self.enviadas.append((coid, side, quantity))
        return {"id": f"ex-{coid}", "status": "filled", "filled": quantity,
                "average": Decimal("100"), "fee": Decimal("0"),
                "trades": [{"id": f"t-{coid}", "amount": quantity,
                            "price": Decimal("100"), "fee": {"cost": Decimal("0")}}]}


def sistema():
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=("BTC/USDT", "ETH/USDT"),
                    capital=Decimal("10000"))
    risco = RiskEngine(m)
    livro = Ledger(opening_cash=m.capital)
    broker = Corretora()
    coord = ExecutionCoordinator(m, livro, risco, worker_id="w", broker=broker)
    return m, risco, livro, coord, broker


def reservar(risco, instrumento="BTC/USDT", agente="agente-A", preco="100",
             exposicao="0.10"):
    from datetime import timedelta
    p = TradeProposal(agent_id=agente, strategy="s", instrument=instrumento,
                      action=Action.BUY, reason="t",
                      desired_exposure_pct=Decimal(exposicao),
                      confidence=Decimal("0.8"),
                      valid_until=utcnow() + timedelta(minutes=5))
    return risco.reserve(p, Decimal(preco), agent_id=agente)


def entrada(res=None, **over):
    dados = {"kind": Kind.ENTRY, "side": Side.BUY, "instrument": "BTC/USDT",
             "quantity": res.quantity if res else Decimal("1"),
             "agent_id": "agente-A", "account": "conta-1",
             "reservation_id": res.reservation_id if res else "",
             "reserved_amount": res.amount if res else Decimal("0")}
    dados.update(over)
    return ExecutionIntent(**dados)


class TestEntradaExigeReservaViva:
    def test_entry_without_reservation_is_refused(self):
        """Comprar sem reserva é comprar sem cobertura de capital."""
        m, risco, livro, coord, broker = sistema()
        with pytest.raises(ExecutionRefused, match="reserva"):
            coord.submit_intent(entrada())
        assert not broker.enviadas, "ordem sem reserva chegou à corretora"

    def test_entry_with_unknown_reservation_is_refused(self):
        m, risco, livro, coord, broker = sistema()
        with pytest.raises(ExecutionRefused, match="reserva"):
            coord.submit_intent(entrada(reservation_id="res-inexistente",
                                        reserved_amount=Decimal("100")))
        assert not broker.enviadas

    def test_released_reservation_cannot_be_reused(self):
        """Reserva já consumida não cobre uma segunda ordem."""
        m, risco, livro, coord, broker = sistema()
        res = reservar(risco)
        risco.release(res.reservation_id)
        with pytest.raises(ExecutionRefused, match="reserva"):
            coord.submit_intent(entrada(res))
        assert not broker.enviadas

    def test_valid_reservation_goes_through(self):
        m, risco, livro, coord, broker = sistema()
        res = reservar(risco)
        rec = coord.submit_intent(entrada(res))
        assert rec.state in (OrderState.ACKNOWLEDGED, OrderState.FILLED)
        assert broker.enviadas


class TestReservaPrecisaCobrirAOrdem:
    def test_quantity_above_the_reserved_one_is_refused(self):
        m, risco, livro, coord, broker = sistema()
        res = reservar(risco)
        maior = res.quantity * 2
        with pytest.raises(ExecutionRefused, match="cobre|quantidade"):
            coord.submit_intent(entrada(res, quantity=maior))
        assert not broker.enviadas

    def test_amount_must_match_the_reservation(self):
        """Valor declarado diferente do reservado é incoerência: alguém está
        contando capital que o risco não separou."""
        m, risco, livro, coord, broker = sistema()
        res = reservar(risco)
        with pytest.raises(ExecutionRefused, match="valor"):
            coord.submit_intent(entrada(res, reserved_amount=res.amount * 3))
        assert not broker.enviadas


class TestReservaPertenceAoPedido:
    def test_reservation_of_another_instrument_is_refused(self):
        m, risco, livro, coord, broker = sistema()
        res = reservar(risco, instrumento="ETH/USDT")
        with pytest.raises(ExecutionRefused, match="instrumento"):
            coord.submit_intent(entrada(res, instrument="BTC/USDT"))
        assert not broker.enviadas

    def test_reservation_of_another_agent_is_refused(self):
        m, risco, livro, coord, broker = sistema()
        res = reservar(risco, agente="agente-A")
        with pytest.raises(ExecutionRefused, match="agente"):
            coord.submit_intent(entrada(res, agent_id="agente-B"))
        assert not broker.enviadas

    def test_reservation_of_another_account_is_refused(self):
        m, risco, livro, coord, broker = sistema()
        res = reservar(risco)
        with pytest.raises(ExecutionRefused, match="conta"):
            coord.submit_intent(entrada(res, account="outra-conta"))
        assert not broker.enviadas


class TestSimetriaEstrutural:
    def test_entry_authorisation_exists_and_is_called(self):
        fonte = inspect.getsource(ExecutionCoordinator.submit_intent)
        assert "_autorizar_entrada" in fonte, (
            "o caminho único chama _autorizar_saida mas não tem simétrico "
            "para a entrada")
        assert "_autorizar_saida" in fonte

    def test_both_sides_are_authorised_in_the_same_step(self):
        fonte = inspect.getsource(ExecutionCoordinator.submit_intent)
        i_entrada = fonte.index("_autorizar_entrada")
        i_saida = fonte.index("_autorizar_saida")
        i_envio = fonte.index("self.broker.submit(")
        assert i_entrada < i_envio and i_saida < i_envio, (
            "autorização precisa acontecer ANTES do envio nos dois lados")

    def test_entry_rules_live_in_one_method(self):
        fonte = inspect.getsource(ExecutionCoordinator._autorizar_entrada)
        for termo in ("reserva", "instrument", "agent"):
            assert termo in fonte.lower()

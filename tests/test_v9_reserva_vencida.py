"""Reserva vencida e SEM vínculo não pode travar a mesa para sempre.

`expires_at` só era aplicado no MOMENTO DO USO (intent / _autorizar_entrada):
uma reserva recusada ANTES do bind — versão divergente, reconciliação
pendente, incidente aberto — ficava viva indefinidamente. `available` seguia
reduzido, `open_instruments()` bloqueava o instrumento para sempre, e o
snapshot a reidratava a cada reinício.

O contrato FIN-06 exige que a recusa NÃO libere a reserva (o retry válido da
MESMA decisão continua possível). O vencimento é o limite natural: passado o
prazo sem vínculo, o capital volta — sem tocar em reserva VINCULADA, que
cobre ordem em voo e pertence ao ciclo da ordem (fill/rejeição/
reconciliação).
"""
from __future__ import annotations

import dataclasses
from datetime import timedelta
from decimal import Decimal

import pytest

from tradingagents.desk.execution import ExecutionCoordinator, ExecutionRefused, OrderState
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import Action, DeskMandate, Mode, TradeProposal, utcnow
from tradingagents.desk.risk import RiskEngine, RiskVeto


class Corretora:
    mode = "testnet"

    def submit(self, coid, instrument, side, quantity, final_state=None):
        return {"id": f"ex-{coid}", "status": "open", "filled": 0}


def sistema(capital="10000"):
    m = DeskMandate(mandate_id="m", owner="p", account="conta-1",
                    mode=Mode.PAPER, instruments=("BTC/USDT", "ETH/USDT"),
                    capital=Decimal(capital), slippage_buffer_pct=Decimal("0"))
    risco = RiskEngine(m)
    livro = Ledger(opening_cash=m.capital)
    coord = ExecutionCoordinator(m, livro, risco, worker_id="w",
                                 broker=Corretora())
    return m, risco, livro, coord


def reservar(risco, instrumento="BTC/USDT", agente="A", preco="100",
             exposicao="0.15"):
    p = TradeProposal(agent_id=agente, strategy="s", instrument=instrumento,
                      action=Action.BUY, reason="t",
                      desired_exposure_pct=Decimal(exposicao),
                      confidence=Decimal("0.8"),
                      valid_until=utcnow() + timedelta(minutes=5))
    return risco.reserve(p, Decimal(preco), agent_id=agente)


def ordem_de(coord, risco, res):
    from tradingagents.desk.intents import ExecutionIntent, Kind, Side
    ei = ExecutionIntent(kind=Kind.ENTRY, side=Side.BUY,
                         instrument=res.instrument, quantity=res.quantity,
                         agent_id=res.agent_id, account="conta-1",
                         reservation_id=res.reservation_id,
                         reserved_amount=res.amount)
    return coord.submit_intent(ei)


def vencer(risco, reservation_id):
    """Vence a reserva VIVA no engine — mesma convenção dos testes FIN-06.

    Substitui o objeto ARMAZENADO (que pode já ter vínculo), não a cópia
    original: vencer não pode apagar um bind existente.
    """
    atual = risco._reservations[reservation_id]
    risco._reservations[reservation_id] = dataclasses.replace(
        atual, expires_at=utcnow() - timedelta(minutes=1))


class TestSweepDeReservaVencida:
    def test_vencida_sem_vinculo_devolve_capital_e_libera_o_instrumento(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        assert risco.available == m.capital - res.amount
        with pytest.raises(RiskVeto, match="já há reserva ativa"):
            reservar(risco)   # CONTROLE: reserva VIVA bloqueia o instrumento

        vencer(risco, res.reservation_id)
        nova = reservar(risco)   # vencida não pode mais bloquear
        assert nova.reservation_id != res.reservation_id
        assert risco.reserva(res.reservation_id) is None
        assert risco.reserved_total == nova.amount
        assert risco.available == m.capital - nova.amount
        assert livro.is_balanced()

    def test_sweep_registra_conclusao_para_auditoria(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        vencer(risco, res.reservation_id)

        assert risco.expirar_reservas_vencidas() == 1
        concl = risco.conclusao(res.reservation_id)
        assert concl["estado"] == "RELEASED"
        assert "vencid" in concl["motivo"]
        assert risco.reserved_total == Decimal("0")
        assert risco.expirar_reservas_vencidas() == 0   # idempotente

    def test_reserva_viva_sem_vinculo_nao_e_varrida(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        assert risco.expirar_reservas_vencidas() == 0
        assert risco.reserva(res.reservation_id) is not None
        assert risco.reserved_total == res.amount

    def test_reserva_vinculada_vencida_nao_e_varrida_e_o_fill_resolve(self):
        """Vínculo = ordem possivelmente em voo; quem encerra é o fill."""
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        rec = ordem_de(coord, risco, res)
        assert rec.state is OrderState.ACKNOWLEDGED
        assert risco.vinculo(res.reservation_id) != ""
        vencer(risco, res.reservation_id)

        assert risco.expirar_reservas_vencidas() == 0
        assert risco.reserva(res.reservation_id) is not None

        coord.aplicar_fill(rec, side="buy", quantidade=res.quantity,
                           preco=Decimal("100"), taxa=Decimal("0"),
                           fill_id="venc-f1")
        assert rec.state is OrderState.FILLED
        assert risco.reserva(res.reservation_id) is None
        assert risco.reserved_total == Decimal("0")
        assert livro.is_balanced()


class TestRecusaDeixaReservaVivaMasVencimentoCura:
    def test_recusa_por_reconciliacao_pendente_e_curada_pelo_vencimento(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        coord.exigir_reconciliacao("posições divergentes na corretora")
        with pytest.raises(ExecutionRefused, match="reconcilia"):
            ordem_de(coord, risco, res)

        # FIN-06: a recusa NÃO libera — a decisão válida pode tentar de novo.
        assert risco.reserva(res.reservation_id) is not None
        assert risco.vinculo(res.reservation_id) == ""

        vencer(risco, res.reservation_id)
        nova = reservar(risco)
        assert nova.reservation_id != res.reservation_id
        assert risco.available == m.capital - nova.amount
        assert risco.conclusao(res.reservation_id)["estado"] == "RELEASED"


class TestReinicioComReservaVencida:
    def test_snapshot_com_vencida_nao_ressuscita_o_bloqueio(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        vencer(risco, res.reservation_id)
        doc = coord.snapshot_estado()

        m2, risco2, livro2, coord2 = sistema()
        coord2.restaurar_estado(doc)
        assert risco2.reserva(res.reservation_id) is None
        assert risco2.reserved_total == Decimal("0")
        nova = reservar(risco2)
        assert risco2.available == m2.capital - nova.amount

    def test_snapshot_com_viva_ainda_bloqueia(self):
        m, risco, livro, coord = sistema()
        res = reservar(risco)
        doc = coord.snapshot_estado()

        m2, risco2, livro2, coord2 = sistema()
        coord2.restaurar_estado(doc)
        assert risco2.reserva(res.reservation_id) is not None
        with pytest.raises(RiskVeto, match="já há reserva ativa"):
            reservar(risco2)
        assert risco2.reserved_total == res.amount

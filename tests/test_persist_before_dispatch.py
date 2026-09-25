"""§4: a intenção/estado é persistida DURAVELMENTE ANTES do despacho externo.
Falha dessa persistência IMPEDE o envio; um crash entre o envio e o save do fim
do ciclo nunca deixa uma ordem viva sem rastro (restore exige reconciliação)."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from tradingagents.desk.execution import (
    BrokerTimeout,
    ExecutionCoordinator,
    ExecutionRefused,
)
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import Action, DeskMandate, Mode, TradeProposal, utcnow
from tradingagents.desk.risk import RiskEngine

INSTR = "BTC/USDT"


def _mandate():
    return DeskMandate(mandate_id="m1", owner="prado", account="acct-1",
                       mode=Mode.PAPER, instruments=(INSTR, "ETH/USDT"),
                       capital=Decimal("1000"))


def _proposal():
    return TradeProposal(agent_id="trader-trend", strategy="trend_following",
                         instrument=INSTR, action=Action.BUY, reason="teste",
                         desired_exposure_pct=Decimal("0.10"),
                         valid_until=utcnow() + timedelta(minutes=5))


def _setup(broker):
    m = _mandate()
    risk = RiskEngine(m)
    ledger = Ledger(opening_cash=m.capital)
    coord = ExecutionCoordinator(m, ledger, risk, worker_id="w1", broker=broker)
    res = risk.reserve(_proposal(), Decimal("100"))
    intent = risk.intent(res, Action.BUY, Mode.PAPER, "w1", idempotency_key="idem-0001")
    return coord, intent, ledger


class _TimeoutBroker:
    """Envio AMBÍGUO: a corretora pode ter recebido (janela de crash)."""
    mode = "paper"

    def __init__(self):
        self.called = 0

    def submit(self, *a, **k):
        self.called += 1
        raise BrokerTimeout("ambíguo: pode ter chegado")


class _OkBroker:
    mode = "paper"

    def __init__(self):
        self.called = 0

    def submit(self, *a, **k):
        self.called += 1
        return {"id": "x", "status": "closed", "filled": 0}


def test_persiste_antes_do_envio_e_restore_exige_reconciliacao():
    snaps: list[dict] = []
    broker = _TimeoutBroker()
    coord, intent, _ = _setup(broker)
    coord.persist_hook = lambda: snaps.append(coord.snapshot_estado())

    coord.submit(intent)                        # persiste ANTES -> envia -> timeout

    assert broker.called == 1                   # o envio (ambíguo) aconteceu
    assert snaps, "deveria persistir ANTES do despacho"
    doc = snaps[-1]                             # snapshot capturado PRÉ-envio

    # restaura num coordenador NOVO a partir do snapshot pré-envio
    m2 = _mandate()
    coord2 = ExecutionCoordinator(m2, Ledger(opening_cash=m2.capital),
                                  RiskEngine(m2), worker_id="w1", broker=_OkBroker())
    info = coord2.restaurar_estado(doc)
    assert info["exige_reconciliacao"] is True  # NÃO diz "zero ordens / False"
    assert any(r.nao_terminal for r in coord2.orders.values())


def test_falha_de_persistencia_bloqueia_o_envio():
    broker = _OkBroker()
    coord, intent, ledger = _setup(broker)
    caixa_antes = ledger.cash()

    def _boom():
        raise RuntimeError("armazém fora do ar")

    coord.persist_hook = _boom
    with pytest.raises(ExecutionRefused):
        coord.submit(intent)

    assert broker.called == 0                   # NADA foi enviado
    # a reserva foi desfeita (não vazou capital): o caixa disponível volta
    assert ledger.cash() == caixa_antes

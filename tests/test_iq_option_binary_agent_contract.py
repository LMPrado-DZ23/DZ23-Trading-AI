"""Contrato agente -> executor binario, sem adaptar BUY/SELL por suposicao."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from tradingagents.desk.binary_agent import (
    BinaryAgentDecision,
    BinaryAgentDecisionError,
    BinaryAgentExecutionGateway,
)
from tradingagents.desk.piloto import (
    AutorizacaoDePiloto,
    EstadoDoPiloto,
)
from tradingagents.desk.salas import ContextoDeCertificacao

CONTEXT = ContextoDeCertificacao(
    corretora="iq_option",
    conta="treinamento-iq",
    mercado="opcoes_binarias",
    modo="paper",
    ativo="EUR/USD",
    horizonte="5m",
    payout="0.85",
)
NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)


def _payload(**overrides):
    payload = {
        "client_order_id": "iq-agent-1",
        "reservation_id": "risk-1",
        "agent_id": "trader-trend",
        "strategy": "trend_following",
        "strategy_version": "3",
        "instrument": "EUR/USD",
        "binary_direction": "call",
        "amount": "25.50",
        "expiration": 1_800_000_300,
        "min_profit_percent": 80,
        "certification_context": CONTEXT.como_dict(),
    }
    payload.update(overrides)
    return payload


def _authorization(**overrides):
    values = {
        "authorization_id": "AUT-1",
        "tenant_id": "tenant-1",
        "agent_id": "trader-trend",
        "estrategia": "trend_following",
        "versao": "3",
        "corretora": "iq_option",
        "conta": "real-principal",
        "conta_real": True,
        "capital_maximo": Decimal("100"),
        "perda_diaria_maxima": Decimal("50"),
        "perda_total_maxima": Decimal("75"),
        "prazo_dias": 30,
        "condicoes_de_suspensao": (),
        "solicitado_em": "2026-10-17T10:00:00+00:00",
        "autorizado_em": "2026-10-17T10:01:00+00:00",
        "valido_ate": "2026-11-16T10:01:00+00:00",
        "estado": EstadoDoPiloto.PILOTO_AUTORIZADO,
        "broker_identity_fingerprint": "account-fingerprint",
        "contexto": CONTEXT.como_dict(),
    }
    values.update(overrides)
    return AutorizacaoDePiloto(**values)


class FakeCoordinator:
    def __init__(self):
        self.calls = []

    def submit(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs


def test_decisao_binaria_explicita_chega_ao_coordenador_sem_inferencias():
    coordinator = FakeCoordinator()
    gateway = BinaryAgentExecutionGateway(coordinator, now=lambda: NOW)
    decision = BinaryAgentDecision.from_payload(_payload())

    result = gateway.submit(
        decision,
        authorization=_authorization(),
        balance_id=912345,
        account_identity_fingerprint="account-fingerprint",
    )

    assert result["direction"] == "call"
    assert result["expiration"] == 1_800_000_300
    assert result["min_profit_percent"] == 80
    assert result["amount"] == Decimal("25.50")
    assert len(coordinator.calls) == 1


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"binary_direction": None, "action": "BUY"}, "direcao binaria"),
        ({"binary_direction": "BUY"}, "direcao binaria"),
        ({"expiration": "1800000300"}, "vencimento"),
        ({"expiration": True}, "vencimento"),
        ({"min_profit_percent": "80"}, "piso de payout"),
        ({"amount": "NaN"}, "valor"),
        ({"certification_context": {}}, "contexto"),
    ],
)
def test_payload_ambiguo_ou_malformado_falha_antes_do_executor(changes, match):
    with pytest.raises(BinaryAgentDecisionError, match=match):
        BinaryAgentDecision.from_payload(_payload(**changes))


@pytest.mark.parametrize(
    "context_changes,match",
    [
        ({"corretora": "binance"}, "IQ Option"),
        ({"mercado": "spot"}, "opcoes binarias"),
        ({"ativo": "GBP/USD"}, "instrumento"),
        ({"payout": "0.79"}, "payout certificado"),
    ],
)
def test_contexto_incompativel_falha_fechado(context_changes, match):
    context = {**CONTEXT.como_dict(), **context_changes}
    with pytest.raises(BinaryAgentDecisionError, match=match):
        BinaryAgentDecision.from_payload(
            _payload(certification_context=context))


@pytest.mark.parametrize(
    "authorization,identity,match",
    [
        (_authorization(estado=EstadoDoPiloto.AGUARDANDO_AUTORIZACAO),
         "account-fingerprint", "nao esta autorizado"),
        (_authorization(conta_real=False), "account-fingerprint", "conta REAL"),
        (_authorization(agent_id="outro"), "account-fingerprint", "agente"),
        (_authorization(estrategia="outra"), "account-fingerprint", "estrategia"),
        (_authorization(versao="2"), "account-fingerprint", "versao"),
        (_authorization(corretora="binance"), "account-fingerprint", "corretora"),
        (_authorization(contexto={}), "account-fingerprint", "certificacao"),
        (_authorization(valido_ate="2026-10-17T11:59:59+00:00"),
         "account-fingerprint", "prazo"),
        (_authorization(valido_ate="invalido"),
         "account-fingerprint", "prazo"),
        (_authorization(), "outra-conta", "identidade"),
        (_authorization(broker_identity_fingerprint=""), "", "identidade"),
    ],
)
def test_autorizacao_ou_identidade_divergente_nao_chama_coordenador(
        authorization, identity, match):
    coordinator = FakeCoordinator()
    gateway = BinaryAgentExecutionGateway(coordinator, now=lambda: NOW)
    decision = BinaryAgentDecision.from_payload(_payload())

    with pytest.raises(BinaryAgentDecisionError, match=match):
        gateway.submit(
            decision,
            authorization=authorization,
            balance_id=912345,
            account_identity_fingerprint=identity,
        )

    assert coordinator.calls == []


def test_balance_id_invalido_e_limite_do_piloto_bloqueiam_antes_do_executor():
    coordinator = FakeCoordinator()
    gateway = BinaryAgentExecutionGateway(coordinator, now=lambda: NOW)
    decision = BinaryAgentDecision.from_payload(_payload())

    with pytest.raises(BinaryAgentDecisionError, match="balance_id"):
        gateway.submit(
            decision,
            authorization=_authorization(),
            balance_id=True,
            account_identity_fingerprint="account-fingerprint",
        )
    with pytest.raises(BinaryAgentDecisionError, match="capital maximo"):
        gateway.submit(
            replace(decision, amount=Decimal("100.01")),
            authorization=_authorization(),
            balance_id=912345,
            account_identity_fingerprint="account-fingerprint",
        )

    assert coordinator.calls == []


def test_contexto_da_autorizacao_persiste_e_volta_tipado():
    authorization = _authorization()
    document = authorization.como_dict()

    assert document["contexto"] == CONTEXT.como_dict()

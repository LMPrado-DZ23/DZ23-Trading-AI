"""Montagem persistente do runtime binario IQ Option, ainda sob selo LIVE."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from service.iq_option_binary_runtime import (
    IqOptionBinaryRuntimeError,
    build_iq_option_binary_runtime,
)
from service.iq_option_execution import IqOptionResolvedLiveAccount
from tradingagents.brokers.ccxt_bridge import LIVE_CONFIRM_PHRASE
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.piloto import AutorizacaoDePiloto, EstadoDoPiloto
from tradingagents.desk.salas import ContextoDeCertificacao

NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
CONTEXT = ContextoDeCertificacao(
    "iq_option", "opcoes_binarias", "EUR/USD", "5m",
    "training", "paper", "0.85")


def _run(coroutine):
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result()


class Store:
    def __init__(self):
        self.documents = {}

    def carregar(self, tenant_id, key):
        return deepcopy(self.documents.get((tenant_id, key)))

    def salvar(self, tenant_id, key, document):
        self.documents[(tenant_id, key)] = deepcopy(document)


class FakeTools:
    def __init__(self):
        self.calls = []

    def __call__(self, name, arguments):
        self.calls.append((name, deepcopy(arguments)))
        if name == "list_assets":
            return {"assets": [{
                "asset_id": 76,
                "symbol": "EUR/USD",
                "is_open": True,
                "min_amount": "1",
                "max_amount": "100",
                "profit_percent": 85,
                "expirations": [1_800_000_300],
            }]}
        if name == "place_trade":
            return {"position_id": "position-1"}
        if name == "list_positions":
            return {"positions": []}
        if name == "get_trade_history":
            return {"positions": []}
        raise AssertionError(name)


def _authorization(**overrides):
    values = {
        "authorization_id": "AUT-IQ-1",
        "tenant_id": "tenant-1",
        "agent_id": "agent-1",
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


def _payload():
    return {
        "client_order_id": "iq-agent-1",
        "reservation_id": "risk-1",
        "agent_id": "agent-1",
        "strategy": "trend_following",
        "strategy_version": "3",
        "instrument": "EUR/USD",
        "binary_direction": "call",
        "amount": "25",
        "expiration": 1_800_000_300,
        "min_profit_percent": 80,
        "certification_context": CONTEXT.como_dict(),
    }


def _env():
    return {
        "TRADINGAGENTS_EXCHANGE": "iq_option",
        "LIVE_EXECUTION_EXCHANGE": "iq_option",
        "IQ_OPTION_MCP_BEARER_TOKEN": "read-secret",
        "IQ_OPTION_MCP_LIVE_BEARER_TOKEN": "live-secret",
        "LIVE_EXECUTION_ACCOUNT_ID": "912345",
    }


async def _resolver(_env):
    return IqOptionResolvedLiveAccount(
        balance_id=912345,
        currency="USD",
        broker_identity_fingerprint="account-fingerprint",
        credential_fingerprint="credential-fingerprint",
        trade_tools_verified=(
            "place_trade", "rollover_position", "sell_position"),
    )


def test_selo_falso_bloqueia_antes_de_resolver_conta_ou_criar_transporte():
    calls = []

    async def resolver(_env):
        calls.append("resolver")
        raise AssertionError("nao deveria resolver")

    with pytest.raises(IqOptionBinaryRuntimeError, match="selo de codigo"):
        _run(build_iq_option_binary_runtime(
            env=_env(), tenant_id="tenant-1", authorization=_authorization(),
            ledger=Ledger(Decimal("1000"), "USD"), store=Store(),
            live_code_authorized=False,
            confirmation_phrase=LIVE_CONFIRM_PHRASE,
            account_resolver=resolver,
            transport_factory=lambda **_: calls.append("transport"),
            now=lambda: NOW,
        ))

    assert calls == []


def test_frase_invalida_bloqueia_antes_da_leitura_da_conta():
    calls = []

    async def resolver(_env):
        calls.append("resolver")
        raise AssertionError("nao deveria resolver")

    with pytest.raises(IqOptionBinaryRuntimeError, match="frase"):
        _run(build_iq_option_binary_runtime(
            env=_env(), tenant_id="tenant-1", authorization=_authorization(),
            ledger=Ledger(Decimal("1000"), "USD"), store=Store(),
            live_code_authorized=True, confirmation_phrase="sim",
            account_resolver=resolver,
            transport_factory=lambda **_: calls.append("transport"),
            now=lambda: NOW,
        ))

    assert calls == []


def test_runtime_composto_persiste_e_executa_so_pelo_gateway_certificado():
    store = Store()
    tools = FakeTools()
    ledger = Ledger(Decimal("1000"), "USD")
    ledger.reserve(Decimal("25"), ref="risk", reservation_id="risk-1")

    runtime = _run(build_iq_option_binary_runtime(
        env=_env(), tenant_id="tenant-1", authorization=_authorization(),
        ledger=ledger, store=store, live_code_authorized=True,
        confirmation_phrase=LIVE_CONFIRM_PHRASE,
        account_resolver=_resolver,
        transport_factory=lambda **_: tools,
        now=lambda: NOW,
        max_order_value="100",
        max_daily_loss="50",
    ))
    result = runtime.submit_payload(
        _payload(), current_authorization=_authorization())

    assert result.position_id == "position-1"
    assert [name for name, _ in tools.calls] == ["list_assets", "place_trade"]
    assert store.documents[(
        "tenant-1", "iq_option_binary_runtime::AUT-IQ-1")]["schema"] == (
            "dz23.iq-option-binary-coordinator/1")
    assert "912345" not in repr(runtime)

    tools_after_restart = FakeTools()
    restored = _run(build_iq_option_binary_runtime(
        env=_env(), tenant_id="tenant-1", authorization=_authorization(),
        ledger=None, store=store, live_code_authorized=True,
        confirmation_phrase=LIVE_CONFIRM_PHRASE,
        account_resolver=_resolver,
        transport_factory=lambda **_: tools_after_restart,
        now=lambda: NOW,
        max_order_value="100",
        max_daily_loss="50",
    ))
    replay = restored.submit_payload(
        _payload(), current_authorization=_authorization())

    assert replay.position_id == "position-1"
    assert tools_after_restart.calls == []
    assert restored.coordinator.ledger.binary_at_risk("position-1") == (
        Decimal("25"))


def test_submissao_exige_fotografia_atual_da_autorizacao():
    tools = FakeTools()
    runtime = _run(build_iq_option_binary_runtime(
        env=_env(), tenant_id="tenant-1", authorization=_authorization(),
        ledger=Ledger(Decimal("1000"), "USD"), store=Store(),
        live_code_authorized=True,
        confirmation_phrase=LIVE_CONFIRM_PHRASE,
        account_resolver=_resolver,
        transport_factory=lambda **_: tools,
        now=lambda: NOW,
        max_order_value="100",
        max_daily_loss="50",
    ))

    with pytest.raises(IqOptionBinaryRuntimeError, match="revalidada"):
        runtime.submit_payload(_payload())

    assert tools.calls == []


@pytest.mark.parametrize("current", [
    _authorization(estado=EstadoDoPiloto.REVOGADO),
    _authorization(authorization_id="AUT-IQ-2"),
])
def test_submissao_recusa_revogacao_ou_mandato_substituido(current):
    tools = FakeTools()
    ledger = Ledger(Decimal("1000"), "USD")
    ledger.reserve(Decimal("25"), ref="risk", reservation_id="risk-1")
    runtime = _run(build_iq_option_binary_runtime(
        env=_env(), tenant_id="tenant-1", authorization=_authorization(),
        ledger=ledger, store=Store(), live_code_authorized=True,
        confirmation_phrase=LIVE_CONFIRM_PHRASE,
        account_resolver=_resolver,
        transport_factory=lambda **_: tools,
        now=lambda: NOW,
        max_order_value="100",
        max_daily_loss="50",
    ))

    with pytest.raises(IqOptionBinaryRuntimeError, match="revalidada"):
        runtime.submit_payload(
            _payload(), current_authorization=current)

    assert tools.calls == []


def test_identidade_divergente_falha_antes_de_criar_transporte():
    calls = []

    async def divergent(_env):
        return IqOptionResolvedLiveAccount(
            balance_id=912345,
            currency="USD",
            broker_identity_fingerprint="other-account",
            credential_fingerprint="credential-fingerprint",
            trade_tools_verified=(
                "place_trade", "rollover_position", "sell_position"),
        )

    with pytest.raises(IqOptionBinaryRuntimeError, match="identidade"):
        _run(build_iq_option_binary_runtime(
            env=_env(), tenant_id="tenant-1", authorization=_authorization(),
            ledger=Ledger(Decimal("1000"), "USD"), store=Store(),
            live_code_authorized=True,
            confirmation_phrase=LIVE_CONFIRM_PHRASE,
            account_resolver=divergent,
            transport_factory=lambda **_: calls.append("transport"),
            now=lambda: NOW,
        ))

    assert calls == []


def test_runtime_novo_abre_subledger_na_moeda_resolvida_sem_presumir_usd():
    tools = FakeTools()

    async def resolver_brl(_env):
        return IqOptionResolvedLiveAccount(
            balance_id=912345,
            currency="BRL",
            broker_identity_fingerprint="account-fingerprint",
            credential_fingerprint="credential-fingerprint",
            trade_tools_verified=(
                "place_trade", "rollover_position", "sell_position"),
        )

    runtime = _run(build_iq_option_binary_runtime(
        env=_env(), tenant_id="tenant-1", authorization=_authorization(),
        ledger=None, opening_cash="1000", store=Store(),
        live_code_authorized=True,
        confirmation_phrase=LIVE_CONFIRM_PHRASE,
        account_resolver=resolver_brl,
        transport_factory=lambda **_: tools,
        now=lambda: NOW,
        max_order_value="100",
        max_daily_loss="50",
    ))

    assert runtime.coordinator.ledger.currency == "BRL"
    assert runtime.coordinator.ledger.cash_available() == Decimal("1000")
    assert tools.calls == []


def test_ledger_injetado_em_outra_moeda_falha_antes_do_transporte():
    calls = []

    with pytest.raises(IqOptionBinaryRuntimeError, match="moeda"):
        _run(build_iq_option_binary_runtime(
            env=_env(), tenant_id="tenant-1", authorization=_authorization(),
            ledger=Ledger(Decimal("1000"), "BRL"), store=Store(),
            live_code_authorized=True,
            confirmation_phrase=LIVE_CONFIRM_PHRASE,
            account_resolver=_resolver,
            transport_factory=lambda **_: calls.append("transport"),
            now=lambda: NOW,
            max_order_value="100",
            max_daily_loss="50",
        ))

    assert calls == []

"""Homologação IQ Option em treinamento, isolada do selo LIVE."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from service.iq_option_training import IqOptionResolvedTrainingAccount
from service.iq_option_training_homologation import (
    IQ_OPTION_TRAINING_PROOF_KEY,
    IQ_OPTION_TRAINING_STAKE,
    IqOptionTrainingHomologationError,
    build_iq_option_training_homologation_runtime,
)
from tradingagents.brokers.ccxt_bridge import LIVE_CONFIRM_PHRASE
from tradingagents.brokers.iq_option_binary import TRAINING_CONFIRM_PHRASE

NOW = datetime(2026, 10, 17, 15, tzinfo=timezone.utc)


def _run(coroutine):
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result()


class Store:
    def __init__(self, events=None):
        self.documents = {}
        self.events = events if events is not None else []

    def carregar(self, tenant_id, key):
        return deepcopy(self.documents.get((tenant_id, key)))

    def salvar(self, tenant_id, key, document):
        self.documents[(tenant_id, key)] = deepcopy(document)
        if key.startswith("iq_option_training_homologation::wal::"):
            coordinator = document["coordinator"]
            intent = coordinator["intents"]
            submissions = coordinator["executor"]["submissions"]
            self.events.append((
                "persist",
                intent[-1]["state"] if intent else "empty",
                submissions[-1]["state"] if submissions else "empty",
            ))


class FakeTools:
    def __init__(self, events=None):
        self.events = events if events is not None else []
        self.calls = []
        self.positions = {"positions": []}
        self.history = {"positions": []}

    def __call__(self, name, arguments):
        self.calls.append((name, deepcopy(arguments)))
        self.events.append(("call", name))
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
            return {"position_id": "training-position-1"}
        if name == "list_positions":
            return deepcopy(self.positions)
        if name == "get_trade_history":
            return deepcopy(self.history)
        raise AssertionError(name)


def _env():
    return {
        "TRADINGAGENTS_EXCHANGE": "iq_option",
        "TRADINGAGENTS_BROKER_MODE": "testnet",
        "LIVE_EXECUTION_EXCHANGE": "iq_option",
        "IQ_OPTION_MCP_BEARER_TOKEN": "read-secret",
        "IQ_OPTION_MCP_LIVE_BEARER_TOKEN": "training-secret",
    }


async def _resolver(_env):
    return IqOptionResolvedTrainingAccount(
        balance_id=812345,
        currency="USD",
        balance_filter="PRACTICE",
        credential_fingerprint="a" * 64,
        trade_tools_verified=(
            "place_trade", "rollover_position", "sell_position"),
    )


def _build(*, tools=None, store=None, phrase=TRAINING_CONFIRM_PHRASE,
           resolver=_resolver):
    return _run(build_iq_option_training_homologation_runtime(
        env=_env(),
        tenant_id="tenant-1",
        store=store or Store(),
        confirmation_phrase=phrase,
        account_resolver=resolver,
        transport_factory=lambda **_: tools or FakeTools(),
        now=lambda: NOW,
    ))


def _submit(runtime, **overrides):
    values = {
        "client_order_id": "training-check-1",
        "instrument": "EUR/USD",
        "direction": "call",
        "amount": IQ_OPTION_TRAINING_STAKE,
        "expiration": 1_800_000_300,
        "min_profit_percent": 80,
    }
    values.update(overrides)
    return runtime.submit(**values)


@pytest.mark.parametrize("phrase", ["sim", LIVE_CONFIRM_PHRASE, ""])
def test_exige_frase_exclusiva_de_treinamento_antes_da_rede(phrase):
    calls = []

    async def resolver(_env):
        calls.append("resolver")
        raise AssertionError("não deveria resolver")

    with pytest.raises(IqOptionTrainingHomologationError, match="frase"):
        _build(phrase=phrase, resolver=resolver)

    assert calls == []


def test_wal_antecede_envio_e_restart_nao_duplica_ordem():
    events = []
    store = Store(events)
    tools = FakeTools(events)
    runtime = _build(tools=tools, store=store)

    intent = _submit(runtime)

    assert intent.position_id == "training-position-1"
    place_index = events.index(("call", "place_trade"))
    assert ("persist", "created", "prepared") in events[:place_index]
    assert [name for name, _ in tools.calls].count("place_trade") == 1

    after_restart = FakeTools()
    restored = _build(tools=after_restart, store=store)
    replay = _submit(restored)

    assert replay.position_id == "training-position-1"
    assert after_restart.calls == []


@pytest.mark.parametrize("amount", ["1.01", "2", "NaN", "Infinity", "0"])
def test_aposta_fora_do_valor_rigido_falha_sem_rede(amount):
    tools = FakeTools()
    runtime = _build(tools=tools)

    with pytest.raises(IqOptionTrainingHomologationError, match="valor"):
        _submit(runtime, amount=amount)

    assert tools.calls == []


def test_uma_unica_ordem_de_homologacao_por_credencial():
    tools = FakeTools()
    runtime = _build(tools=tools)
    _submit(runtime)
    calls_before = len(tools.calls)

    with pytest.raises(IqOptionTrainingHomologationError, match="única"):
        _submit(runtime, client_order_id="training-check-2")

    assert len(tools.calls) == calls_before


def test_rotacao_abre_novo_wal_sem_apagar_o_anterior():
    store = Store()
    first = _build(tools=FakeTools(), store=store)
    _submit(first)

    async def rotated_resolver(_env):
        return IqOptionResolvedTrainingAccount(
            balance_id=912345,
            currency="USD",
            balance_filter="PRACTICE",
            credential_fingerprint="b" * 64,
            trade_tools_verified=(
                "place_trade", "rollover_position", "sell_position"),
        )

    second = _build(
        tools=FakeTools(), store=store, resolver=rotated_resolver)
    _submit(second, client_order_id="training-check-2")

    wal_keys = [
        key for tenant, key in store.documents
        if tenant == "tenant-1"
        and key.startswith("iq_option_training_homologation::wal::")
    ]
    assert len(wal_keys) == 2
    assert any(key.endswith("a" * 64) for key in wal_keys)
    assert any(key.endswith("b" * 64) for key in wal_keys)


def test_so_certifica_apos_reconciliacao_e_baixa_total_sanitizada():
    store = Store()
    tools = FakeTools()
    runtime = _build(tools=tools, store=store)
    _submit(runtime)
    tools.history = {"positions": [{
        "position_id": "training-position-1",
        "amount": "1",
        "result": "win",
        "profit": "0.85",
    }]}

    proof = runtime.reconcile_and_certify("training-check-1")

    assert proof == {
        "schema": "dz23.homologacao-iq-training/1",
        "exchange": "iq_option",
        "mode": "training",
        "filled_external_order": True,
        "reconciled": True,
        "verified_at": NOW.isoformat(),
        "credential_fingerprint": "a" * 64,
        "external_order_fingerprint": proof["external_order_fingerprint"],
    }
    assert len(proof["external_order_fingerprint"]) == 64
    serialized = repr(store.documents)
    assert "training-position-1" in serialized  # somente no WAL privado
    saved_proof = store.documents[(
        "tenant-1", IQ_OPTION_TRAINING_PROOF_KEY)]
    assert "training-position-1" not in repr(saved_proof)
    assert "history:" not in repr(saved_proof)
    assert runtime.coordinator.ledger.binary_at_risk(
        "training-position-1") == 0


def test_nao_certifica_enquanto_resultado_remoto_nao_for_terminal():
    store = Store()
    runtime = _build(tools=FakeTools(), store=store)
    _submit(runtime)

    with pytest.raises(IqOptionTrainingHomologationError, match="terminal"):
        runtime.reconcile_and_certify("training-check-1")

    assert (
        "tenant-1", "iq_option_training_homologation::proof"
    ) not in store.documents


@pytest.mark.parametrize(
    ("result", "profit", "expected_cash"),
    [("loss", "-1", "0"), ("equal", "0", "1")],
)
def test_baixa_deriva_da_semantica_binaria_e_nao_de_retorno_digitado(
        result, profit, expected_cash):
    tools = FakeTools()
    runtime = _build(tools=tools)
    _submit(runtime)
    tools.history = {"positions": [{
        "position_id": "training-position-1",
        "amount": "1",
        "result": result,
        "profit": profit,
    }]}

    runtime.reconcile_and_certify("training-check-1")

    assert str(runtime.coordinator.ledger.cash_available()) == expected_cash
    assert runtime.coordinator.ledger.binary_at_risk(
        "training-position-1") == 0


@pytest.mark.parametrize(
    ("result", "profit", "message"),
    [
        ("win", "0.84", "payout contratado"),
        ("canceled", "0", "cancelamento"),
    ],
)
def test_lucro_divergente_ou_cancelamento_ambiguo_nao_certifica(
        result, profit, message):
    store = Store()
    tools = FakeTools()
    runtime = _build(tools=tools, store=store)
    _submit(runtime)
    tools.history = {"positions": [{
        "position_id": "training-position-1",
        "amount": "1",
        "result": result,
        "profit": profit,
    }]}

    with pytest.raises(IqOptionTrainingHomologationError, match=message):
        runtime.reconcile_and_certify("training-check-1")

    assert runtime.coordinator.ledger.binary_at_risk(
        "training-position-1") == 1
    assert (
        "tenant-1", "iq_option_training_homologation::proof"
    ) not in store.documents

"""Composição durável entre executor IQ Option e subledger binário."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest

from tradingagents.brokers.ccxt_bridge import LIVE_CONFIRM_PHRASE
from tradingagents.brokers.iq_option_binary import BinaryAmbiguousSubmission
from tradingagents.desk.binary_execution import (
    BinaryCoordinatorRecoveryRequired,
    BinaryDeskState,
    BinaryExecutionCoordinator,
    BinarySettlementProof,
)
from tradingagents.desk.ledger import Ledger

D = Decimal


def _assets():
    return {"assets": [{
        "asset_id": 76,
        "symbol": "EUR/USD",
        "is_open": True,
        "min_amount": "1",
        "max_amount": "1000",
        "profit_percent": 85,
        "expirations": [1_800_000_300],
    }]}


class FakeTools:
    def __init__(self, *, place_error=None):
        self.calls = []
        self.events = []
        self.place_error = place_error
        self.positions = {"positions": [{
            "position_id": "pos-7", "amount": "25.50",
        }]}
        self.history = {"positions": []}

    def __call__(self, name, arguments):
        self.calls.append((name, deepcopy(arguments)))
        self.events.append(f"call:{name}")
        if name == "list_assets":
            return _assets()
        if name == "place_trade":
            if self.place_error:
                raise self.place_error
            return {"position_id": "pos-7"}
        if name == "list_positions":
            return deepcopy(self.positions)
        if name == "get_trade_history":
            return deepcopy(self.history)
        raise AssertionError(name)


class DurableStore:
    def __init__(self, events=None):
        self.documents = []
        self.events = events if events is not None else []
        self.fail_intent_state = ""
        self.fail_executor_state_once = ""
        self.crash_executor_state = ""

    def __call__(self, document):
        intent_state = (
            document["intents"][-1]["state"]
            if document["intents"] else "empty"
        )
        submissions = document["executor"]["submissions"]
        executor_state = submissions[-1]["state"] if submissions else "empty"
        self.events.append(f"persist:{intent_state}:{executor_state}")
        if executor_state == self.fail_executor_state_once:
            self.fail_executor_state_once = ""
            raise OSError("disco indisponível no ACK")
        if intent_state == self.fail_intent_state:
            raise OSError("disco indisponível")
        self.documents.append(deepcopy(document))
        if executor_state == self.crash_executor_state:
            raise SystemExit("crash sintético depois da escrita")


def _ledger():
    ledger = Ledger(D("1000"), "USD")
    ledger.reserve(D("25.50"), ref="risk-r1", reservation_id="r1")
    return ledger


def _coordinator(tools, store, *, ledger=None, restored_state=None):
    return BinaryExecutionCoordinator(
        ledger=ledger,
        tool_caller=tools,
        persist=store,
        live_authorized=True,
        confirmation_phrase=LIVE_CONFIRM_PHRASE,
        max_order_value="100",
        max_daily_loss="50",
        restored_state=restored_state,
    )


def _submit(coordinator, **overrides):
    request = {
        "client_order_id": "iq-1",
        "reservation_id": "r1",
        "agent_id": "trader-trend",
        "balance_id": 912345,
        "instrument": "EUR/USD",
        "direction": "call",
        "amount": "25.50",
        "expiration": 1_800_000_300,
        "min_profit_percent": 80,
    }
    request.update(overrides)
    return coordinator.submit(**request)


def test_ack_abre_subledger_so_depois_do_wal_e_persiste_estado_open():
    tools = FakeTools()
    store = DurableStore(tools.events)
    coordinator = _coordinator(tools, store, ledger=_ledger())

    intent = _submit(coordinator)

    assert intent.state is BinaryDeskState.OPEN
    assert intent.position_id == "pos-7"
    assert coordinator.ledger.reserved_cash("r1") == D("0")
    assert coordinator.ledger.binary_at_risk("pos-7") == D("25.50")
    assert tools.events == [
        "persist:created:empty",
        "call:list_assets",
        "persist:created:prepared",
        "call:place_trade",
        "persist:created:acknowledged",
        "persist:acknowledged:acknowledged",
        "persist:open:acknowledged",
    ]
    assert coordinator.ledger.is_balanced()


def test_falha_da_primeira_escrita_nao_consulta_nem_envia():
    tools = FakeTools()
    store = DurableStore()
    store.fail_intent_state = "created"
    coordinator = _coordinator(tools, store, ledger=_ledger())

    with pytest.raises(BinaryCoordinatorRecoveryRequired, match="antes da rede"):
        _submit(coordinator)

    assert tools.calls == []
    assert coordinator.ledger.reserved_cash("r1") == D("25.50")


def test_timeout_mantem_reserva_e_restart_nunca_reenvia():
    tools = FakeTools(place_error=TimeoutError("sem resposta"))
    store = DurableStore()
    coordinator = _coordinator(tools, store, ledger=_ledger())

    with pytest.raises(BinaryAmbiguousSubmission):
        _submit(coordinator)
    assert coordinator.intent("iq-1").state is BinaryDeskState.UNKNOWN
    assert coordinator.ledger.reserved_cash("r1") == D("25.50")

    restored_tools = FakeTools()
    restored = _coordinator(
        restored_tools, DurableStore(), restored_state=store.documents[-1])
    assert restored.recover("iq-1").state is BinaryDeskState.UNKNOWN
    with pytest.raises(BinaryAmbiguousSubmission):
        _submit(restored)
    assert restored_tools.calls == []
    assert restored.ledger.reserved_cash("r1") == D("25.50")


def test_crash_apos_ack_duravel_recupera_abertura_sem_reenviar():
    tools = FakeTools()
    store = DurableStore()
    store.crash_executor_state = "acknowledged"
    coordinator = _coordinator(tools, store, ledger=_ledger())

    with pytest.raises(SystemExit, match="crash sintético"):
        _submit(coordinator)
    ack_snapshot = store.documents[-1]
    assert ack_snapshot["executor"]["submissions"][0]["position_id"] == "pos-7"
    assert ack_snapshot["intents"][0]["state"] == "created"

    restored_tools = FakeTools()
    restored_store = DurableStore()
    restored = _coordinator(
        restored_tools, restored_store, restored_state=ack_snapshot)
    recovered = restored.recover("iq-1")

    assert recovered.state is BinaryDeskState.OPEN
    assert restored_tools.calls == []
    assert restored.ledger.binary_at_risk("pos-7") == D("25.50")
    assert restored.ledger.reserved_cash("r1") == D("0")


def test_falha_de_escrita_apos_binary_open_e_replayavel_exatamente_uma_vez():
    tools = FakeTools()
    store = DurableStore()
    store.fail_intent_state = "open"
    coordinator = _coordinator(tools, store, ledger=_ledger())

    with pytest.raises(BinaryCoordinatorRecoveryRequired, match="abertura contábil"):
        _submit(coordinator)
    assert coordinator.ledger.binary_at_risk("pos-7") == D("25.50")
    durable_before_open = store.documents[-1]
    assert durable_before_open["intents"][0]["state"] == "acknowledged"

    restored = _coordinator(
        FakeTools(), DurableStore(), restored_state=durable_before_open)
    first = restored.recover("iq-1")
    transaction_count = len(restored.ledger.transactions)
    second = restored.recover("iq-1")

    assert first.state is second.state is BinaryDeskState.OPEN
    assert len(restored.ledger.transactions) == transaction_count
    assert restored.ledger.binary_at_risk("pos-7") == D("25.50")


def test_reuso_conflitante_da_identidade_falha_sem_rede():
    tools = FakeTools()
    coordinator = _coordinator(tools, DurableStore(), ledger=_ledger())
    _submit(coordinator)
    calls_before = len(tools.calls)

    with pytest.raises(BinaryCoordinatorRecoveryRequired, match="conteúdo diferente"):
        _submit(coordinator, agent_id="outro-trader")

    assert len(tools.calls) == calls_before


def test_snapshot_composto_nao_guarda_segredo_ou_resposta_remota():
    coordinator = _coordinator(FakeTools(), DurableStore(), ledger=_ledger())
    _submit(coordinator)

    serialized = repr(coordinator.snapshot_state())
    assert "Bearer" not in serialized
    assert "token" not in serialized.lower()


def test_ack_conhecido_mas_persistencia_falhou_reconcilia_sem_reenviar():
    tools = FakeTools()
    store = DurableStore()
    store.fail_executor_state_once = "acknowledged"
    coordinator = _coordinator(tools, store, ledger=_ledger())

    with pytest.raises(BinaryAmbiguousSubmission):
        _submit(coordinator)
    assert coordinator.intent("iq-1").state is BinaryDeskState.UNKNOWN
    assert coordinator.ledger.reserved_cash("r1") == D("25.50")
    place_calls = [name for name, _ in tools.calls].count("place_trade")

    reconciliation = coordinator.reconcile("iq-1")

    assert reconciliation.state.value == "open"
    assert coordinator.intent("iq-1").state is BinaryDeskState.OPEN
    assert coordinator.ledger.binary_at_risk("pos-7") == D("25.50")
    assert [name for name, _ in tools.calls].count("place_trade") == place_calls


def test_resultado_remoto_fica_pendente_ate_prova_do_retorno_total():
    tools = FakeTools()
    store = DurableStore()
    coordinator = _coordinator(tools, store, ledger=_ledger())
    _submit(coordinator)
    tools.positions = {"positions": []}
    tools.history = {"positions": [{
        "position_id": "pos-7", "amount": "25.50",
        "result": "win", "profit": "21.67",
    }]}

    reconciliation = coordinator.reconcile("iq-1")

    assert reconciliation.state.value == "settled"
    assert coordinator.intent("iq-1").state is BinaryDeskState.SETTLEMENT_PENDING
    assert coordinator.ledger.binary_at_risk("pos-7") == D("25.50")
    assert coordinator.ledger.cash_available() == D("974.50")

    restored_tools = FakeTools()
    restored = _coordinator(
        restored_tools, DurableStore(), restored_state=store.documents[-1])
    assert restored.recover("iq-1").state is BinaryDeskState.SETTLEMENT_PENDING
    assert restored_tools.calls == []


def test_prova_de_retorno_liquida_exatamente_uma_vez_apos_restart():
    tools = FakeTools()
    store = DurableStore()
    coordinator = _coordinator(tools, store, ledger=_ledger())
    _submit(coordinator)
    tools.positions = {"positions": []}
    tools.history = {"positions": [{
        "position_id": "pos-7", "amount": "25.50",
        "result": "win", "profit": "21.67",
    }]}
    coordinator.reconcile("iq-1")
    proof = BinarySettlementProof(
        position_id="pos-7",
        result="win",
        returned_amount=D("47.17"),
        source="iq_option_account_statement",
        reference="statement-line-77",
    )

    settled = coordinator.settle_verified("iq-1", proof)
    snapshot = coordinator.snapshot_state()
    restored = _coordinator(FakeTools(), DurableStore(), restored_state=snapshot)
    before = len(restored.ledger.transactions)
    same = restored.settle_verified("iq-1", proof)

    assert settled.state is same.state is BinaryDeskState.SETTLED
    assert len(restored.ledger.transactions) == before
    assert restored.ledger.binary_at_risk("pos-7") == D("0")
    assert restored.ledger.cash_available() == D("1021.67")
    assert restored.ledger.attribution()["trader-trend"] == D("21.67")


@pytest.mark.parametrize(
    "proof,message",
    [
        (BinarySettlementProof(
            "pos-7", "win", D("47.17"), "manual", "line-1"), "fonte"),
        (BinarySettlementProof(
            "outra", "win", D("47.17"),
            "iq_option_account_statement", "line-1"), "posição"),
        (BinarySettlementProof(
            "pos-7", "loss", D("0"),
            "iq_option_account_statement", "line-1"), "resultado"),
    ],
)
def test_prova_de_retorno_incompativel_falha_fechado(proof, message):
    tools = FakeTools()
    coordinator = _coordinator(tools, DurableStore(), ledger=_ledger())
    _submit(coordinator)
    tools.positions = {"positions": []}
    tools.history = {"positions": [{
        "position_id": "pos-7", "amount": "25.50",
        "result": "win", "profit": "21.67",
    }]}
    coordinator.reconcile("iq-1")

    with pytest.raises(BinaryCoordinatorRecoveryRequired, match=message):
        coordinator.settle_verified("iq-1", proof)

    assert coordinator.ledger.binary_at_risk("pos-7") == D("25.50")

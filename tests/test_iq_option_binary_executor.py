"""Executor binario IQ Option: envio selado, duravel e sem retry cego."""
from __future__ import annotations

from copy import deepcopy

import pytest

from tradingagents.brokers.ccxt_bridge import LIVE_CONFIRM_PHRASE
from tradingagents.brokers.iq_option_binary import (
    TRAINING_CONFIRM_PHRASE,
    BinaryAmbiguousSubmission,
    BinaryAuthorizationScope,
    BinaryContractError,
    BinaryExecutionSecurityError,
    BinaryReconciliationState,
    BinarySubmissionState,
    IqOptionBinaryExecutor,
)


def _assets(**overrides):
    asset = {
        "asset_id": 76,
        "symbol": "EUR/USD",
        "is_open": True,
        "min_amount": "1",
        "max_amount": "1000",
        "profit_percent": 85,
        "expirations": [1_800_000_300],
    }
    asset.update(overrides)
    return {"assets": [asset]}


def _request(executor, *, client_order_id="iq-1", **overrides):
    request = {
        "client_order_id": client_order_id,
        "balance_id": 912345,
        "instrument": "EUR/USD",
        "direction": "call",
        "amount": "25.50",
        "expiration": 1_800_000_300,
        "min_profit_percent": 80,
    }
    request.update(overrides)
    return executor.submit(**request)


class FakeTools:
    def __init__(self, *, assets=None, place_result=None, place_error=None):
        self.assets = assets or _assets()
        self.place_result = place_result or {"position_id": "pos-7"}
        self.place_error = place_error
        self.calls = []
        self.events = []
        self.positions = {"positions": []}
        self.history = {"positions": []}

    def __call__(self, name, arguments):
        self.calls.append((name, deepcopy(arguments)))
        self.events.append(f"call:{name}")
        if name == "list_assets":
            return deepcopy(self.assets)
        if name == "place_trade":
            if self.place_error:
                raise self.place_error
            return deepcopy(self.place_result)
        if name == "list_positions":
            return deepcopy(self.positions)
        if name == "get_trade_history":
            return deepcopy(self.history)
        raise AssertionError(f"ferramenta inesperada: {name}")


class DurableCapture:
    def __init__(self, events=None):
        self.documents = []
        self.events = events if events is not None else []
        self.fail_on_state = None

    def __call__(self, document):
        state = document["submissions"][-1]["state"]
        self.events.append(f"persist:{state}")
        if state == self.fail_on_state:
            raise OSError("disco indisponivel")
        self.documents.append(deepcopy(document))


def _executor(tools, durable, **overrides):
    options = {
        "tool_caller": tools,
        "persist": durable,
        "live_authorized": True,
        "confirmation_phrase": LIVE_CONFIRM_PHRASE,
        "max_order_value": "100",
        "max_daily_loss": "50",
    }
    options.update(overrides)
    return IqOptionBinaryExecutor(**options)


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"live_authorized": False}, "selo de codigo"),
        ({"confirmation_phrase": "sim"}, "frase"),
        ({"max_order_value": "0"}, "ordem"),
        ({"max_daily_loss": "0"}, "perda diaria"),
        ({"persist": None}, "persistencia"),
    ],
)
def test_executor_recusa_construcao_sem_todas_as_travas(overrides, message):
    with pytest.raises(BinaryExecutionSecurityError, match=message):
        _executor(FakeTools(), DurableCapture(), **overrides)


def test_frases_live_e_treinamento_sao_isoladas_e_persistidas():
    tools = FakeTools()
    durable = DurableCapture()

    with pytest.raises(BinaryExecutionSecurityError, match="treinamento"):
        _executor(
            tools,
            durable,
            authorization_scope=BinaryAuthorizationScope.TRAINING,
        )

    training = _executor(
        tools,
        durable,
        authorization_scope=BinaryAuthorizationScope.TRAINING,
        confirmation_phrase=TRAINING_CONFIRM_PHRASE,
        max_order_value="1",
    )
    snapshot = training.snapshot_state()
    assert snapshot["authorization_scope"] == "training"

    with pytest.raises(BinaryExecutionSecurityError, match="escopo"):
        _executor(FakeTools(), DurableCapture(), restored_state=snapshot)

    restored = _executor(
        FakeTools(),
        DurableCapture(),
        authorization_scope=BinaryAuthorizationScope.TRAINING,
        confirmation_phrase=TRAINING_CONFIRM_PHRASE,
        max_order_value="1",
        restored_state=snapshot,
    )
    assert restored.snapshot_state()["authorization_scope"] == "training"


def test_persiste_prepared_antes_do_unico_place_trade_e_ack_depois():
    tools = FakeTools()
    durable = DurableCapture(tools.events)
    executor = _executor(tools, durable)

    result = _request(executor)

    assert tools.events == [
        "call:list_assets",
        "persist:prepared",
        "call:place_trade",
        "persist:acknowledged",
    ]
    assert tools.calls[1] == ("place_trade", {
        "balance_id": 912345,
        "asset_id": 76,
        "direction": "call",
        "amount": 25.5,
        "profit_percent": 85,
        "expired": 1_800_000_300,
    })
    assert result.state is BinarySubmissionState.ACKNOWLEDGED
    assert result.position_id == "pos-7"

    assert _request(executor) is result
    assert [name for name, _ in tools.calls].count("place_trade") == 1


def test_falha_de_persistencia_previa_impede_qualquer_mutacao():
    tools = FakeTools()
    durable = DurableCapture(tools.events)
    durable.fail_on_state = "prepared"
    executor = _executor(tools, durable)

    with pytest.raises(BinaryExecutionSecurityError, match="antes do envio"):
        _request(executor)

    assert [name for name, _ in tools.calls] == ["list_assets"]


def test_timeout_vira_unknown_duravel_e_retry_nunca_reenvia():
    tools = FakeTools(place_error=TimeoutError("segredo remoto"))
    durable = DurableCapture(tools.events)
    executor = _executor(tools, durable)

    with pytest.raises(BinaryAmbiguousSubmission, match="reconciliacao"):
        _request(executor)
    assert durable.documents[-1]["submissions"][0]["state"] == "unknown"

    with pytest.raises(BinaryAmbiguousSubmission, match="reconciliacao"):
        _request(executor)
    assert [name for name, _ in tools.calls].count("place_trade") == 1

    restored_tools = FakeTools()
    restored = _executor(
        restored_tools, DurableCapture(),
        restored_state=durable.documents[-1],
    )
    with pytest.raises(BinaryAmbiguousSubmission, match="reconciliacao"):
        _request(restored)
    assert restored_tools.calls == []


def test_prepared_restaurado_e_ambiguo_e_nunca_e_reenviado():
    tools = FakeTools(place_error=TimeoutError("sem resposta"))
    durable = DurableCapture()
    executor = _executor(tools, durable)
    with pytest.raises(BinaryAmbiguousSubmission):
        _request(executor)

    prepared_snapshot = durable.documents[0]
    restored_tools = FakeTools()
    restored = _executor(
        restored_tools, DurableCapture(), restored_state=prepared_snapshot)

    with pytest.raises(BinaryAmbiguousSubmission, match="PREPARED"):
        _request(restored)
    assert restored_tools.calls == []


def test_resposta_sem_position_id_e_ambigua_e_nao_vaza_conteudo():
    tools = FakeTools(place_result={"status": "ok", "token": "NAO-VAZAR"})
    durable = DurableCapture()
    executor = _executor(tools, durable)

    with pytest.raises(BinaryAmbiguousSubmission) as captured:
        _request(executor)

    assert "NAO-VAZAR" not in str(captured.value)
    assert executor.submission("iq-1").state is BinarySubmissionState.UNKNOWN


def test_falha_ao_persistir_ack_mantem_operacao_ambigua_sem_retry():
    tools = FakeTools()
    durable = DurableCapture()
    durable.fail_on_state = "acknowledged"
    executor = _executor(tools, durable)

    with pytest.raises(BinaryAmbiguousSubmission, match="confirmacao duravel"):
        _request(executor)
    with pytest.raises(BinaryAmbiguousSubmission):
        _request(executor)

    assert [name for name, _ in tools.calls].count("place_trade") == 1
    assert executor.submission("iq-1").state is BinarySubmissionState.UNKNOWN


def test_reuso_conflitante_do_client_order_id_falha_sem_rede():
    tools = FakeTools()
    executor = _executor(tools, DurableCapture())
    _request(executor)
    calls_before = len(tools.calls)

    with pytest.raises(BinaryContractError, match="conteudo diferente"):
        _request(executor, amount="26")

    assert len(tools.calls) == calls_before


def test_catalogo_fresco_e_limites_de_risco_barram_antes_do_place_trade():
    tools = FakeTools(assets=_assets(profit_percent=70))
    executor = _executor(tools, DurableCapture())
    with pytest.raises(BinaryContractError, match="payout"):
        _request(executor)
    assert [name for name, _ in tools.calls] == ["list_assets"]

    tools = FakeTools()
    executor = _executor(tools, DurableCapture())
    with pytest.raises(BinaryExecutionSecurityError, match="teto por ordem"):
        _request(executor, amount="101")
    assert tools.calls == []

    executor.set_daily_loss("50")
    with pytest.raises(BinaryExecutionSecurityError, match="perda diaria"):
        _request(executor)
    assert tools.calls == []


def test_ack_concilia_por_position_id_com_ferramentas_fixadas():
    tools = FakeTools()
    executor = _executor(tools, DurableCapture())
    _request(executor)
    tools.positions = {"positions": []}
    tools.history = {"positions": [{
        "position_id": "pos-7",
        "amount": "25.50",
        "result": "win",
        "profit": "21.67",
    }]}

    result = executor.reconcile("iq-1")

    assert result.state is BinaryReconciliationState.SETTLED
    assert result.result == "win"
    assert tools.calls[-2:] == [
        ("list_positions", {"balance_id": 912345}),
        ("get_trade_history", {"skip": 0, "limit": 100}),
    ]


def test_unknown_sem_position_id_nao_inventa_busca_por_fingerprint():
    tools = FakeTools(place_error=ConnectionError("caiu"))
    executor = _executor(tools, DurableCapture())
    with pytest.raises(BinaryAmbiguousSubmission):
        _request(executor)
    calls_before = len(tools.calls)

    with pytest.raises(BinaryAmbiguousSubmission, match="position_id"):
        executor.reconcile("iq-1")

    assert len(tools.calls) == calls_before


def test_snapshot_nao_contem_credencial_nem_resposta_remota_arbitraria():
    tools = FakeTools(place_result={"position_id": "pos-7", "secret": "NAO-GUARDAR"})
    executor = _executor(tools, DurableCapture())
    _request(executor)

    serialized = repr(executor.snapshot_state())
    assert "NAO-GUARDAR" not in serialized
    assert "Bearer" not in serialized

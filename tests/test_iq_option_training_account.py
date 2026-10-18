"""Descoberta fail-closed da conta de treinamento oficial da IQ Option."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from service.iq_option_mcp import IQ_OPTION_MCP_TOOL_CONTRACTS
from service.iq_option_training import (
    IqOptionTrainingAccountError,
    resolve_iq_option_training_account_async,
)


def _run(coroutine):
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result()


def _env():
    return {
        "TRADINGAGENTS_EXCHANGE": "iq_option",
        "LIVE_EXECUTION_EXCHANGE": "iq_option",
        "IQ_OPTION_MCP_BEARER_TOKEN": "read-token",
        "IQ_OPTION_MCP_LIVE_BEARER_TOKEN": "live-token",
    }


def _tools(balance_types):
    read, write = IQ_OPTION_MCP_TOOL_CONTRACTS["binary-options"]
    tools = []
    for name in sorted(read | write):
        read_only = name in read
        properties = (
            {"types": {"type": "string", "enum": list(balance_types)}}
            if name == "list_balances" else
            {"value": {"type": "integer"}}
        )
        tools.append(SimpleNamespace(
            name=name,
            annotations=SimpleNamespace(
                read_only_hint=True if read_only else None,
                destructive_hint=not read_only,
                open_world_hint=False,
            ),
            input_schema={
                "type": "object",
                "properties": properties,
                "required": ([] if read_only else ["value"]),
                "additionalProperties": False,
            },
        ))
    return tuple(tools)


@pytest.mark.parametrize(("declared", "returned_type"), [
    (("NORMAL", "TRAINING", "TOURNAMENT"), "training"),
    (("NORMAL", "PRACTICE"), "practice"),
    (("NORMAL", "DEMO"), "demo"),
])
def test_resolvedor_descobre_filtro_de_treino_no_schema(
        declared, returned_type):
    async def session(_endpoint, _token):
        return _tools(declared), {"balances": [{
            "type": returned_type,
            "balance_id": "812345",
            "currency": "USD",
        }]}

    account = _run(resolve_iq_option_training_account_async(
        _env(), session_reader=session))

    assert account.balance_id == 812345
    assert account.currency == "USD"
    assert account.balance_filter == declared[1]
    assert "812345" not in repr(account)
    assert "balance_id" not in account.public_proof()


@pytest.mark.parametrize("declared", [
    ("NORMAL",),
    ("NORMAL", "TRAINING", "PRACTICE"),
    ("NORMAL", "training"),
    ("NORMAL", "SANDBOX"),
])
def test_resolvedor_recusa_schema_sem_um_filtro_de_treino_exato(declared):
    async def session(_endpoint, _token):
        return _tools(declared), {"balances": []}

    with pytest.raises(IqOptionTrainingAccountError, match="treinamento"):
        _run(resolve_iq_option_training_account_async(
            _env(), session_reader=session))


@pytest.mark.parametrize("balances", [
    [],
    [{"type": "regular", "balance_id": 1, "currency": "USD"}],
    [
        {"type": "training", "balance_id": 1, "currency": "USD"},
        {"type": "training", "balance_id": 2, "currency": "USD"},
    ],
    [{"type": "training", "balance_id": 0, "currency": "USD"}],
    [{"type": "training", "balance_id": 1, "currency": "US D"}],
])
def test_resolvedor_exige_uma_unica_conta_de_treinamento_valida(balances):
    async def session(_endpoint, _token):
        return _tools(("NORMAL", "TRAINING")), {"balances": balances}

    with pytest.raises(IqOptionTrainingAccountError):
        _run(resolve_iq_option_training_account_async(
            _env(), session_reader=session))


def test_resolvedor_recusa_tipo_de_saldo_diferente_do_filtro_declarado():
    async def session(_endpoint, _token):
        return _tools(("NORMAL", "TRAINING")), {"balances": [{
            "type": "practice",
            "balance_id": 812345,
            "currency": "USD",
        }]}

    with pytest.raises(IqOptionTrainingAccountError, match="corresponde"):
        _run(resolve_iq_option_training_account_async(
            _env(), session_reader=session))


def test_resolvedor_nao_aceita_token_live_reutilizado_como_read_only():
    env = _env()
    env["IQ_OPTION_MCP_LIVE_BEARER_TOKEN"] = env[
        "IQ_OPTION_MCP_BEARER_TOKEN"]
    calls = []

    async def session(*_args):
        calls.append("network")
        raise AssertionError("não deveria abrir sessão")

    with pytest.raises(IqOptionTrainingAccountError, match="separado"):
        _run(resolve_iq_option_training_account_async(
            env, session_reader=session))

    assert calls == []

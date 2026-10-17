"""Preparação da credencial LIVE IQ Option sem executar ferramenta mutável."""
from __future__ import annotations

import asyncio
import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from service.iq_option_execution import (
    IqOptionExecutionPreparationError,
    probe_iq_option_live_execution_account_async,
)
from service.iq_option_mcp import IQ_OPTION_MCP_TOOL_CONTRACTS
from tradingagents.brokers.ccxt_bridge import (
    broker_identity_fingerprint,
    live_execution_credential_fingerprint,
    read_only_credential_fingerprint,
)

H = {"Authorization": "Bearer s3nh4"}
ACCOUNT_ID = "private-balance-id"


def _run(coroutine):
    """Isola suítes que mantêm um event loop ativo na thread principal."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result()


def _tools(*, trading: bool = True, unknown: bool = False):
    read, write = IQ_OPTION_MCP_TOOL_CONTRACTS["binary-options"]
    names = set(read) | (set(write) if trading else set())
    if unknown:
        names.add("execute_arbitrary_order")
    tools = []
    for name in sorted(names):
        read_only = name in read
        schema = {
            "type": "object",
            "properties": ({"types": {"type": "string"}}
                           if name == "list_balances" else
                           {"required_value": {"type": "integer"}}),
            "required": ([] if name == "list_balances"
                         else ["required_value"]),
            "additionalProperties": False,
        }
        tools.append(SimpleNamespace(
            name=name,
            annotations=SimpleNamespace(
                read_only_hint=True if read_only else None,
                destructive_hint=not read_only,
                open_world_hint=False),
            input_schema=schema,
        ))
    return tuple(tools)


def _env(**overrides):
    env = {
        "TRADINGAGENTS_EXCHANGE": "iq_option",
        "LIVE_EXECUTION_EXCHANGE": "iq_option",
        "IQ_OPTION_MCP_LIVE_BEARER_TOKEN": "live-token-separado",
        "IQ_OPTION_MCP_BEARER_TOKEN": "read-token-separado",
        "LIVE_EXECUTION_ACCOUNT_ID": ACCOUNT_ID,
    }
    env.update(overrides)
    return env


def test_prova_live_iq_autentica_por_leitura_sem_chamar_trade():
    chamadas = []

    async def reader(endpoint, token, arguments):
        chamadas.append((endpoint, token, arguments))
        return _tools(), {
            "balances": [{"type": "regular", "balance_id": ACCOUNT_ID}]}

    result = _run(probe_iq_option_live_execution_account_async(
        _env(), session_reader=reader))

    assert result["exchange"] == "iq_option"
    assert result["authenticated_read"] is True
    assert result["broker_identity_verified"] is True
    assert result["trade_tools_verified"] == [
        "place_trade", "rollover_position", "sell_position"]
    assert result["read_methods"] == [
        "binary-options:tools/list",
        "binary-options:list_balances[NORMAL]",
    ]
    assert len(chamadas) == 1
    assert chamadas[0][2] == {"types": "NORMAL"}
    assert result["broker_identity_fingerprint"] == (
        broker_identity_fingerprint({
            "TRADINGAGENTS_EXCHANGE": "iq_option",
            "IQ_OPTION_MCP_BEARER_TOKEN": "read-token-separado",
        }, ACCOUNT_ID))
    serialized = json.dumps(result)
    assert "live-token-separado" not in serialized
    assert "read-token-separado" not in serialized
    assert ACCOUNT_ID not in serialized


@pytest.mark.parametrize("tools", [_tools(trading=False), _tools(unknown=True)])
def test_prova_live_iq_recusa_escopo_sem_trade_ou_contrato_desconhecido(
        tools):
    async def reader(_endpoint, _token, _arguments):
        return tools, {
            "balances": [{"type": "regular", "balance_id": ACCOUNT_ID}]}

    with pytest.raises(IqOptionExecutionPreparationError, match="contrato|trade"):
        _run(probe_iq_option_live_execution_account_async(
            _env(), session_reader=reader))


def test_prova_live_iq_recusa_reutilizar_token_somente_leitura():
    async def never(*_args):
        raise AssertionError("rede não poderia ser alcançada")

    with pytest.raises(IqOptionExecutionPreparationError, match="separado"):
        _run(probe_iq_option_live_execution_account_async(
            _env(IQ_OPTION_MCP_LIVE_BEARER_TOKEN="read-token-separado"),
            session_reader=never))


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import service.app as m
    importlib.reload(m)
    m._FAILS.clear()
    m._DESKS.clear()
    from fastapi.testclient import TestClient
    return m, TestClient(m.app)


def test_config_mascara_e_vincula_token_live_iq(tmp_path, monkeypatch):
    m, c = _app(tmp_path, monkeypatch)
    response = c.post("/v1/config", headers=H, json={"vars": {
        "TRADINGAGENTS_EXCHANGE": "iq_option",
        "IQ_OPTION_MCP_LIVE_BEARER_TOKEN": "live-token-separado",
    }})

    assert response.status_code == 200, response.text
    stored = m.load_cfg("legacy")
    assert stored["LIVE_EXECUTION_EXCHANGE"] == "iq_option"
    assert stored["LIVE_EXECUTION_CREDENTIAL_BOUND_AT"]
    public = c.get("/v1/config", headers=H).json()["config"]
    assert public["IQ_OPTION_MCP_LIVE_BEARER_TOKEN"]["set"] is True
    assert "value" not in public["IQ_OPTION_MCP_LIVE_BEARER_TOKEN"]


def test_rota_prepara_token_live_iq_sem_ordem(tmp_path, monkeypatch):
    m, c = _app(tmp_path, monkeypatch)
    assert c.post("/v1/config", headers=H, json={"vars": {
        "TRADINGAGENTS_EXCHANGE": "iq_option",
        "IQ_OPTION_MCP_BEARER_TOKEN": "read-token-separado",
        "IQ_OPTION_MCP_LIVE_BEARER_TOKEN": "live-token-separado",
    }}).status_code == 200
    cfg = m.load_cfg("legacy")
    read_env = m._env_leitura_real(cfg)
    read_proof = {
        "exchange": "iq_option", "conta": "principal",
        "credential_fingerprint": read_only_credential_fingerprint(read_env),
        "broker_identity_fingerprint": broker_identity_fingerprint(
            read_env, ACCOUNT_ID),
        "authenticated_read": True, "broker_identity_verified": True,
        "sem_trade_declarado": True, "sem_saque_declarado": True,
        "provider_mutating_tools_exposed": False,
    }
    monkeypatch.setattr(m, "_prova_leitura_real", lambda *_: (read_proof, True))

    async def probe(env):
        assert env["LIVE_EXECUTION_ACCOUNT_ID"] == ""
        assert env["IQ_OPTION_CONFIRM_UNIQUE_REGULAR_ACCOUNT"] is True
        return {
            "exchange": "iq_option", "authenticated_read": True,
            "read_methods": ["binary-options:tools/list",
                             "binary-options:list_balances[NORMAL]"],
            "trade_tools_verified": [
                "place_trade", "rollover_position", "sell_position"],
            "broker_identity_verified": True,
            "broker_identity_fingerprint": read_proof[
                "broker_identity_fingerprint"],
            "credential_fingerprint": (
                live_execution_credential_fingerprint(env)),
        }

    monkeypatch.setattr(
        m, "probe_iq_option_live_execution_account_async", probe)
    response = c.post("/v1/live/preparacao-execucao", headers=H, json={
        "conta": "principal",
        "confirmo_conta_regular_unica": True,
        "confirmo_trade": True,
        "confirmo_sem_movimentacao": True,
    })

    assert response.status_code == 200, response.text
    assert response.json()["preparacao"]["valida"] is True
    proof_path = next((tmp_path / "desks").rglob(
        "preparacao_real_execucao.json"))
    contents = proof_path.read_text(encoding="utf-8")
    for forbidden in (
            "live-token-separado", "read-token-separado", ACCOUNT_ID):
        assert forbidden not in contents

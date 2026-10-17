"""Preparação passiva da credencial de negociação oficial da IQ Option.

O módulo comprova o catálogo de ferramentas e a identidade da conta usando
somente ``tools/list`` e ``list_balances(NORMAL)``. Ferramentas mutáveis são
validadas como metadados, mas nunca chamadas aqui.
"""
from __future__ import annotations

import hmac
import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from service.iq_option_mcp import (
    IQ_OPTION_MCP_SERVERS,
    IQ_OPTION_MCP_TOOL_CONTRACTS,
)
from tradingagents.brokers.ccxt_bridge import (
    broker_identity_fingerprint,
    live_execution_credential_fingerprint,
)

IQ_OPTION_LIVE_TOKEN_ENV = "IQ_OPTION_MCP_LIVE_BEARER_TOKEN"
IQ_OPTION_LIVE_SERVER = "binary-options"


class IqOptionExecutionPreparationError(RuntimeError):
    """Falha fechada ao preparar uma credencial LIVE da IQ Option."""


SessionReader = Callable[
    [str, str, dict[str, str]],
    Awaitable[tuple[Sequence[Any], dict[str, Any]]],
]


def _annotation(tool: Any, name: str) -> Any:
    annotations = getattr(tool, "annotations", None)
    return getattr(annotations, name, None)


def _assert_trading_tool_contract(tools: Sequence[Any]) -> list[str]:
    expected_read, expected_write = IQ_OPTION_MCP_TOOL_CONTRACTS[
        IQ_OPTION_LIVE_SERVER]
    expected = expected_read | expected_write
    names = [str(getattr(tool, "name", "")) for tool in tools]
    if (len(names) != len(set(names)) or set(names) != expected
            or "" in names):
        raise IqOptionExecutionPreparationError(
            "contrato MCP de trade ausente, incompleto ou desconhecido")

    for tool in tools:
        name = str(getattr(tool, "name", ""))
        schema = getattr(tool, "input_schema", None)
        if not isinstance(schema, dict):
            schema = getattr(tool, "inputSchema", None)
        if (not isinstance(schema, dict) or schema.get("type") != "object"
                or schema.get("additionalProperties") is not False):
            raise IqOptionExecutionPreparationError(
                "contrato MCP de trade inválido")
        properties = schema.get("properties")
        required = schema.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list):
            raise IqOptionExecutionPreparationError(
                "contrato MCP de trade inválido")
        if name in expected_read:
            if (_annotation(tool, "read_only_hint") is not True
                    or _annotation(tool, "destructive_hint") is not False):
                raise IqOptionExecutionPreparationError(
                    "contrato MCP de leitura inválido")
            if name == "list_balances" and set(properties) != {"types"}:
                raise IqOptionExecutionPreparationError(
                    "contrato MCP de leitura inválido")
        else:
            if (_annotation(tool, "destructive_hint") is not True
                    or _annotation(tool, "open_world_hint") is not False
                    or not required or not set(required) <= set(properties)):
                raise IqOptionExecutionPreparationError(
                    "contrato MCP de trade inválido")
    return sorted(expected_write)


def _payload_from_result(result: Any) -> dict[str, Any]:
    if getattr(result, "is_error", False):
        raise IqOptionExecutionPreparationError(
            "consulta MCP passiva recusada")
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    content = getattr(result, "content", None)
    text = (getattr(content[0], "text", None)
            if isinstance(content, list) and len(content) == 1 else None)
    if not isinstance(text, str) or len(text) > 1_000_000:
        raise IqOptionExecutionPreparationError("resposta MCP passiva inválida")
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise IqOptionExecutionPreparationError(
            "resposta MCP passiva inválida") from exc
    if not isinstance(payload, dict):
        raise IqOptionExecutionPreparationError("resposta MCP passiva inválida")
    return payload


async def _read_session_remote(
        endpoint: str, token: str,
        arguments: dict[str, str]) -> tuple[Sequence[Any], dict[str, Any]]:
    """Lista metadados e chama exclusivamente ``list_balances``."""
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    if arguments != {"types": "NORMAL"}:
        raise IqOptionExecutionPreparationError("filtro de conta REAL inválido")
    http_client = httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx2.Timeout(10.0, read=30.0),
        follow_redirects=False,
        trust_env=False,
    )
    async with http_client:
        transport = streamable_http_client(
            endpoint, http_client=http_client, terminate_on_close=True)
        async with Client(transport, read_timeout_seconds=30.0) as client:
            listed = await client.list_tools(cache_mode="refresh")
            tools = tuple(listed.tools)
            _assert_trading_tool_contract(tools)
            result = await client.call_tool("list_balances", arguments)
            return tools, _payload_from_result(result)


async def probe_iq_option_live_execution_account_async(
        env: dict, *, session_reader: SessionReader = _read_session_remote,
        ) -> dict[str, object]:
    """Comprova credencial de trade sem executar ou simular uma ordem."""
    exchange = str(env.get("TRADINGAGENTS_EXCHANGE") or "").strip().lower()
    bound = str(env.get("LIVE_EXECUTION_EXCHANGE") or "").strip().lower()
    if exchange != "iq_option" or bound != exchange:
        raise IqOptionExecutionPreparationError(
            "token MCP LIVE não está vinculado à IQ Option selecionada")
    live_token = str(env.get(IQ_OPTION_LIVE_TOKEN_ENV) or "").strip()
    read_token = str(env.get("IQ_OPTION_MCP_BEARER_TOKEN") or "").strip()
    if not live_token or not read_token:
        raise IqOptionExecutionPreparationError(
            "a preparação exige tokens MCP de leitura e LIVE separados")
    if hmac.compare_digest(live_token, read_token):
        raise IqOptionExecutionPreparationError(
            "o token MCP LIVE deve ser separado do token somente leitura")

    expected_account_id = str(
        env.get("LIVE_EXECUTION_ACCOUNT_ID") or "").strip()
    discover_unique = env.get("IQ_OPTION_CONFIRM_UNIQUE_REGULAR_ACCOUNT") is True
    if not expected_account_id and not discover_unique:
        raise IqOptionExecutionPreparationError(
            "confirme a conta REAL regular única")

    endpoint = IQ_OPTION_MCP_SERVERS[IQ_OPTION_LIVE_SERVER]
    try:
        tools, payload = await session_reader(
            endpoint, live_token, {"types": "NORMAL"})
        write_tools = _assert_trading_tool_contract(tuple(tools))
    except IqOptionExecutionPreparationError:
        raise
    except Exception as exc:
        raise IqOptionExecutionPreparationError(
            f"consulta MCP passiva recusada ({type(exc).__name__})") from exc

    balances = payload.get("balances") if isinstance(payload, dict) else None
    if not isinstance(balances, list):
        raise IqOptionExecutionPreparationError("resposta MCP passiva inválida")
    ids: list[str] = []
    for balance in balances:
        if not isinstance(balance, dict) or balance.get("type") != "regular":
            raise IqOptionExecutionPreparationError("resposta MCP passiva inválida")
        balance_id = balance.get("balance_id")
        if (isinstance(balance_id, bool)
                or not isinstance(balance_id, (int, str))
                or not str(balance_id).strip()):
            raise IqOptionExecutionPreparationError("resposta MCP passiva inválida")
        ids.append(str(balance_id).strip())
    if discover_unique and not expected_account_id:
        if len(ids) != 1:
            raise IqOptionExecutionPreparationError(
                "a confirmação exige exatamente uma conta REAL regular")
        expected_account_id = ids[0]
    if not expected_account_id or not any(
            hmac.compare_digest(expected_account_id, item) for item in ids):
        raise IqOptionExecutionPreparationError(
            "a conta autenticada não corresponde à prova somente leitura")

    identity_env = {
        "TRADINGAGENTS_EXCHANGE": "iq_option",
        "IQ_OPTION_MCP_BEARER_TOKEN": read_token,
    }
    return {
        "exchange": "iq_option",
        "mcp_server": IQ_OPTION_LIVE_SERVER,
        "authenticated_read": True,
        "read_methods": [
            "binary-options:tools/list",
            "binary-options:list_balances[NORMAL]",
        ],
        "trade_tools_verified": write_tools,
        "broker_identity_verified": True,
        "broker_identity_fingerprint": broker_identity_fingerprint(
            identity_env, expected_account_id),
        "credential_fingerprint": live_execution_credential_fingerprint(env),
    }


__all__ = [
    "IQ_OPTION_LIVE_SERVER",
    "IQ_OPTION_LIVE_TOKEN_ENV",
    "IqOptionExecutionPreparationError",
    "probe_iq_option_live_execution_account_async",
]

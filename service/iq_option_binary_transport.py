"""Transporte MCP selado para o executor binário da IQ Option.

Este módulo não decide se LIVE está autorizado. Ele reduz a sessão remota a
quatro chamadas de contrato conhecido, renova ``tools/list`` na mesma sessão
antes de cada chamada e nunca inclui token, argumentos ou payload remoto em
erros. As travas financeiras e o WAL vivem em ``IqOptionBinaryExecutor``.
"""
from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from service.iq_option_execution import (
    IqOptionExecutionPreparationError,
    _assert_trading_tool_contract,
    _payload_from_result,
)
from service.iq_option_mcp import IQ_OPTION_MCP_SERVERS


class IqOptionBinaryTransportError(RuntimeError):
    """Falha sanitizada de contrato ou transporte MCP binário."""


SessionCaller = Callable[
    [str, str, str, dict[str, Any]],
    Awaitable[tuple[Sequence[Any], Any]],
]

_TARGET_PROPERTIES = {
    "list_assets": frozenset({"only_enabled"}),
    "place_trade": frozenset({
        "balance_id", "asset_id", "direction", "amount",
        "profit_percent", "expired",
    }),
    "list_positions": frozenset({"balance_id"}),
    "get_trade_history": frozenset({"skip", "limit"}),
}


def _positive_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


def _nonnegative_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _validate_arguments(name: str, arguments: Any) -> dict[str, Any]:
    expected = _TARGET_PROPERTIES.get(name)
    if expected is None:
        raise IqOptionBinaryTransportError(
            "ferramenta fora da superfície binária permitida")
    if not isinstance(arguments, dict) or set(arguments) != expected:
        raise IqOptionBinaryTransportError(
            "argumentos fora do contrato MCP binário")
    if name == "list_assets":
        valid = arguments["only_enabled"] is True
    elif name == "list_positions":
        valid = _positive_int(arguments["balance_id"])
    elif name == "get_trade_history":
        valid = (
            _nonnegative_int(arguments["skip"])
            and _positive_int(arguments["limit"])
            and arguments["limit"] <= 100
        )
    else:
        amount = arguments["amount"]
        valid_amount = (
            not isinstance(amount, bool)
            and isinstance(amount, (int, float))
            and math.isfinite(float(amount))
            and float(amount) > 0
        )
        payout = arguments["profit_percent"]
        valid = (
            _positive_int(arguments["balance_id"])
            and _positive_int(arguments["asset_id"])
            and arguments["direction"] in {"call", "put"}
            and valid_amount
            and not isinstance(payout, bool)
            and isinstance(payout, int)
            and 0 <= payout <= 100
            and _positive_int(arguments["expired"])
        )
    if not valid:
        raise IqOptionBinaryTransportError(
            "argumentos fora do contrato MCP binário")
    return dict(arguments)


def _assert_target_schema(tools: Sequence[Any], target: str) -> None:
    matches = [tool for tool in tools if getattr(tool, "name", None) == target]
    if len(matches) != 1:
        raise IqOptionBinaryTransportError(
            "schema da ferramenta MCP binária ausente ou duplicado")
    schema = getattr(matches[0], "input_schema", None)
    if not isinstance(schema, dict):
        schema = getattr(matches[0], "inputSchema", None)
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if (not isinstance(properties, dict)
            or set(properties) != _TARGET_PROPERTIES[target]):
        raise IqOptionBinaryTransportError(
            "schema da ferramenta MCP binária mudou")
    if target == "place_trade" and set(
            schema.get("required") or ()) != _TARGET_PROPERTIES[target]:
        raise IqOptionBinaryTransportError(
            "schema da ferramenta MCP binária mudou")


async def _remote_session_call(
        endpoint: str, token: str, name: str,
        arguments: dict[str, Any]) -> tuple[Sequence[Any], Any]:
    """Lista o catálogo e chama a ferramenta na mesma sessão autenticada."""
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    http_client = httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx2.Timeout(10.0, read=30.0),
        follow_redirects=False,
        trust_env=False,
    )
    async with http_client:
        stream = streamable_http_client(
            endpoint, http_client=http_client, terminate_on_close=True)
        async with Client(stream, read_timeout_seconds=30.0) as client:
            listed = await client.list_tools(cache_mode="refresh")
            tools = tuple(listed.tools)
            # Validação acontece ANTES da chamada mutável. A repetição no
            # consumidor protege também session_callers injetados em testes.
            _assert_trading_tool_contract(tools)
            _assert_target_schema(tools, name)
            result = await client.call_tool(name, arguments)
            return tools, result


class IqOptionBinaryMcpTransport:
    """Callable síncrono de superfície mínima para o executor binário."""

    def __init__(self, *, token: str,
                 session_caller: SessionCaller = _remote_session_call) -> None:
        secret = str(token or "").strip()
        if not secret:
            raise IqOptionBinaryTransportError(
                "token MCP LIVE da IQ Option ausente")
        if not callable(session_caller):
            raise IqOptionBinaryTransportError("sessão MCP inválida")
        self._token = secret
        self._session_caller = session_caller
        self._endpoint = IQ_OPTION_MCP_SERVERS["binary-options"]

    def __repr__(self) -> str:
        return "IqOptionBinaryMcpTransport(server='binary-options', token=<redacted>)"

    async def _call_async(self, name: str,
                          arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            tools, result = await self._session_caller(
                self._endpoint, self._token, name, arguments)
            try:
                _assert_trading_tool_contract(tuple(tools))
            except IqOptionExecutionPreparationError:
                raise IqOptionBinaryTransportError(
                    "catálogo MCP binário mudou; chamada recusada") from None
            _assert_target_schema(tuple(tools), name)
            return _payload_from_result(result)
        except IqOptionBinaryTransportError:
            raise
        except IqOptionExecutionPreparationError:
            raise IqOptionBinaryTransportError(
                "resposta MCP binária recusada") from None
        except Exception as exc:
            raise IqOptionBinaryTransportError(
                f"sessão MCP binária falhou ({type(exc).__name__})") from None

    def __call__(self, name: str,
                 arguments: dict[str, Any]) -> dict[str, Any]:
        safe_arguments = _validate_arguments(name, arguments)

        def execute() -> dict[str, Any]:
            return asyncio.run(self._call_async(name, safe_arguments))

        # Sempre usar uma thread curta evita depender do estado global do loop
        # deixado por outros hosts/plugins no processo. Rede domina o custo e
        # a interface síncrona fica idêntica dentro ou fora de um event loop.
        with ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="iq-binary-mcp") as pool:
            return pool.submit(execute).result()


__all__ = [
    "IqOptionBinaryMcpTransport",
    "IqOptionBinaryTransportError",
]

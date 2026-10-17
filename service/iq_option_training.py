"""Descoberta passiva e fail-closed da conta de treinamento IQ Option.

O valor aceito por ``list_balances`` nao e presumido. Ele precisa aparecer de
forma inequivoca no enum do schema MCP oficial da mesma sessao autenticada.
Construir esta prova chama somente ``tools/list`` e ``list_balances``.
"""
from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from service.iq_option_execution import (
    IQ_OPTION_LIVE_SERVER,
    IQ_OPTION_LIVE_TOKEN_ENV,
    IqOptionExecutionPreparationError,
    _assert_trading_tool_contract,
    _parse_balance_id,
    _parse_currency,
    _payload_from_result,
)
from service.iq_option_mcp import IQ_OPTION_MCP_SERVERS
from tradingagents.brokers.ccxt_bridge import (
    live_execution_credential_fingerprint,
)

_TRAINING_FILTERS = frozenset({"TRAINING", "PRACTICE", "DEMO"})


class IqOptionTrainingAccountError(RuntimeError):
    """A conta de treinamento nao pode ser provada pelo contrato observado."""


TrainingSessionReader = Callable[
    [str, str], Awaitable[tuple[Sequence[Any], dict[str, Any]]]
]


@dataclass(frozen=True, repr=False, slots=True)
class IqOptionResolvedTrainingAccount:
    balance_id: int
    currency: str
    balance_filter: str
    credential_fingerprint: str
    trade_tools_verified: tuple[str, ...]

    def __repr__(self) -> str:
        return (
            "IqOptionResolvedTrainingAccount(balance_id=<redacted>, "
            "credential=<redacted>)"
        )

    def public_proof(self) -> dict[str, object]:
        return {
            "exchange": "iq_option",
            "account_kind": "training",
            "mcp_server": IQ_OPTION_LIVE_SERVER,
            "authenticated_read": True,
            "read_methods": [
                "binary-options:tools/list",
                f"binary-options:list_balances[{self.balance_filter}]",
            ],
            "trade_tools_verified": list(self.trade_tools_verified),
            "credential_fingerprint": self.credential_fingerprint,
        }


def _input_schema(tool: Any) -> dict[str, Any] | None:
    schema = getattr(tool, "input_schema", None)
    if not isinstance(schema, dict):
        schema = getattr(tool, "inputSchema", None)
    return schema if isinstance(schema, dict) else None


def _training_filter(tools: Sequence[Any]) -> str:
    try:
        _assert_trading_tool_contract(tuple(tools))
    except IqOptionExecutionPreparationError as exc:
        raise IqOptionTrainingAccountError(
            "contrato MCP de treinamento invalido") from exc
    matches = [
        tool for tool in tools
        if str(getattr(tool, "name", "")) == "list_balances"
    ]
    if len(matches) != 1:
        raise IqOptionTrainingAccountError(
            "filtro MCP de treinamento ausente ou ambiguo")
    schema = _input_schema(matches[0])
    properties = schema.get("properties") if schema else None
    types_schema = properties.get("types") if isinstance(properties, dict) else None
    declared = types_schema.get("enum") if isinstance(types_schema, dict) else None
    candidates = (
        [value for value in declared if value in _TRAINING_FILTERS]
        if isinstance(declared, list)
        and all(isinstance(value, str) for value in declared)
        else []
    )
    if len(candidates) != 1:
        raise IqOptionTrainingAccountError(
            "schema nao declara um unico filtro de treinamento")
    return candidates[0]


async def _read_training_session_remote(
        endpoint: str, token: str,
        ) -> tuple[Sequence[Any], dict[str, Any]]:
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
        transport = streamable_http_client(
            endpoint, http_client=http_client, terminate_on_close=True)
        async with Client(transport, read_timeout_seconds=30.0) as client:
            listed = await client.list_tools(cache_mode="refresh")
            tools = tuple(listed.tools)
            balance_filter = _training_filter(tools)
            result = await client.call_tool(
                "list_balances", {"types": balance_filter})
            return tools, _payload_from_result(result)


async def resolve_iq_option_training_account_async(
        env: dict[str, Any], *,
        session_reader: TrainingSessionReader = _read_training_session_remote,
        ) -> IqOptionResolvedTrainingAccount:
    exchange = str(env.get("TRADINGAGENTS_EXCHANGE") or "").strip().lower()
    bound = str(env.get("LIVE_EXECUTION_EXCHANGE") or "").strip().lower()
    if exchange != "iq_option" or bound != exchange:
        raise IqOptionTrainingAccountError(
            "token MCP LIVE nao esta vinculado a IQ Option")
    live_token = str(env.get(IQ_OPTION_LIVE_TOKEN_ENV) or "").strip()
    read_token = str(env.get("IQ_OPTION_MCP_BEARER_TOKEN") or "").strip()
    if not live_token or not read_token:
        raise IqOptionTrainingAccountError(
            "tokens MCP de leitura e LIVE sao obrigatorios")
    if hmac.compare_digest(live_token, read_token):
        raise IqOptionTrainingAccountError(
            "token MCP LIVE deve ser separado do token somente leitura")
    try:
        tools, payload = await session_reader(
            IQ_OPTION_MCP_SERVERS[IQ_OPTION_LIVE_SERVER], live_token)
        balance_filter = _training_filter(tuple(tools))
        write_tools = _assert_trading_tool_contract(tuple(tools))
        balances = payload.get("balances") if isinstance(payload, dict) else None
        if not isinstance(balances, list) or len(balances) != 1:
            raise IqOptionTrainingAccountError(
                "a prova exige exatamente uma conta de treinamento")
        balance = balances[0]
        if not isinstance(balance, dict):
            raise IqOptionTrainingAccountError(
                "resposta nao representa conta de treinamento")
        returned_type = str(balance.get("type") or "").strip().lower()
        if returned_type != balance_filter.lower():
            raise IqOptionTrainingAccountError(
                "tipo de saldo nao corresponde ao filtro de treinamento")
        balance_id = _parse_balance_id(balance.get("balance_id"))
        currency = _parse_currency(balance.get("currency"))
    except IqOptionTrainingAccountError:
        raise
    except IqOptionExecutionPreparationError as exc:
        raise IqOptionTrainingAccountError(
            "resposta MCP de treinamento invalida") from exc
    except Exception as exc:
        raise IqOptionTrainingAccountError(
            f"consulta MCP de treinamento recusada ({type(exc).__name__})") \
            from None
    return IqOptionResolvedTrainingAccount(
        balance_id=balance_id,
        currency=currency,
        balance_filter=balance_filter,
        credential_fingerprint=live_execution_credential_fingerprint(env),
        trade_tools_verified=tuple(write_tools),
    )


__all__ = [
    "IqOptionResolvedTrainingAccount",
    "IqOptionTrainingAccountError",
    "resolve_iq_option_training_account_async",
]

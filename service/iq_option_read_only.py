"""Prova de identidade da conta IQ Option via uma única consulta MCP.

Este módulo é deliberadamente separado da sonda de metadados e não aceita o
nome da ferramenta como entrada. A única chamada remota possível é
``list_balances`` com filtro ``NORMAL``; valores monetários, moeda, IDs brutos
e o token nunca saem da função pública.
"""
from __future__ import annotations

import hmac
import json
from collections.abc import Awaitable, Callable
from typing import Any

from service.iq_option_mcp import (
    IQ_OPTION_MCP_SERVERS,
    IQ_OPTION_MCP_TOOL_CONTRACTS,
)
from tradingagents.brokers.ccxt_bridge import (
    _broker_identity_fingerprint,
    read_only_credential_fingerprint,
)

IQ_OPTION_READ_ONLY_SERVER = "binary-options"
IQ_OPTION_TOKEN_ENV = "IQ_OPTION_MCP_BEARER_TOKEN"

BalanceReader = Callable[[str, str, dict[str, str]], Awaitable[dict[str, Any]]]


class IqOptionReadOnlyError(RuntimeError):
    """Recusa fail-closed sem incluir conteúdo remoto ou credenciais."""


def _tool_contract_is_exact(tools: tuple[Any, ...]) -> bool:
    """Aceita somente os dois catálogos oficiais conhecidos.

    Um token somente leitura anuncia apenas ``expected_read``. Tokens com
    negociação habilitada anunciam também ``expected_write``; esse catálogo
    ainda é reconhecido para produzir um erro específico, mas não é aceito
    por :func:`_assert_read_only_tool_contract` para consultar a conta.
    """
    expected_read, expected_write = IQ_OPTION_MCP_TOOL_CONTRACTS[
        IQ_OPTION_READ_ONLY_SERVER]
    names = [str(getattr(tool, "name", "")) for tool in tools]
    catalog = set(names)
    if (len(names) != len(catalog) or catalog not in (
            expected_read, expected_read | expected_write)):
        return False
    target = next((tool for tool in tools if tool.name == "list_balances"), None)
    if target is None:
        return False
    annotations = getattr(target, "annotations", None)
    input_schema = getattr(target, "input_schema", None)
    return bool(
        annotations is not None
        and getattr(annotations, "read_only_hint", None) is True
        and getattr(annotations, "destructive_hint", None) is False
        and isinstance(input_schema, dict)
        and input_schema.get("type") == "object"
        and input_schema.get("additionalProperties") is False
        and set((input_schema.get("properties") or {}).keys()) == {"types"}
    )


def _assert_read_only_tool_contract(tools: tuple[Any, ...]) -> None:
    """Recusa escopo mutável antes de qualquer consulta de conta.

    O bloqueio local de nomes de ferramentas não reduz as permissões do token
    no provedor. Por isso, reconhecer o catálogo completo não basta: a prova
    REAL só continua quando ``tools/list`` anuncia exatamente o subconjunto
    oficial de leitura.
    """
    if not _tool_contract_is_exact(tools):
        raise IqOptionReadOnlyError("contrato MCP mudou; leitura recusada")
    expected_read, expected_write = IQ_OPTION_MCP_TOOL_CONTRACTS[
        IQ_OPTION_READ_ONLY_SERVER]
    names = {str(getattr(tool, "name", "")) for tool in tools}
    if names & expected_write:
        raise IqOptionReadOnlyError(
            "token MCP possui permissão de negociação; crie um token com "
            "Read access ligado e todos os Trading access desligados")
    if names != expected_read:
        raise IqOptionReadOnlyError("contrato MCP mudou; leitura recusada")


def _payload_from_result(result: Any) -> dict[str, Any]:
    if getattr(result, "is_error", False):
        raise IqOptionReadOnlyError("consulta MCP somente leitura recusada")
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    content = getattr(result, "content", None)
    if not isinstance(content, list) or len(content) != 1:
        raise IqOptionReadOnlyError("resposta MCP somente leitura inválida")
    text = getattr(content[0], "text", None)
    if not isinstance(text, str) or len(text) > 1_000_000:
        raise IqOptionReadOnlyError("resposta MCP somente leitura inválida")
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise IqOptionReadOnlyError(
            "resposta MCP somente leitura inválida") from exc
    if not isinstance(payload, dict):
        raise IqOptionReadOnlyError("resposta MCP somente leitura inválida")
    return payload


async def _call_list_balances_with_read_only_scope(
        client: Any, arguments: dict[str, str]) -> dict[str, Any]:
    """Valida o escopo do token antes da única execução de ferramenta."""
    listed = await client.list_tools(cache_mode="refresh")
    _assert_read_only_tool_contract(tuple(listed.tools))
    result = await client.call_tool("list_balances", arguments)
    return _payload_from_result(result)


async def _list_normal_balances_remote(
        endpoint: str, token: str,
        arguments: dict[str, str]) -> dict[str, Any]:
    """Valida o contrato e executa somente ``list_balances(NORMAL)``."""
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    if arguments != {"types": "NORMAL"}:
        raise IqOptionReadOnlyError("filtro de conta REAL inválido")
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
            return await _call_list_balances_with_read_only_scope(
                client, arguments)


async def probe_iq_option_read_only_account_async(
        env: dict, *,
        balance_reader: BalanceReader = _list_normal_balances_remote,
        ) -> dict[str, object]:
    """Comprova uma conta regular sem devolver saldo ou identidade bruta."""
    exchange = str(env.get("TRADINGAGENTS_EXCHANGE") or "").strip().lower()
    bound = str(env.get("REAL_READ_ONLY_EXCHANGE") or "").strip().lower()
    if exchange != "iq_option" or bound != exchange:
        raise IqOptionReadOnlyError(
            "token MCP não está vinculado à IQ Option selecionada")
    token = str(env.get(IQ_OPTION_TOKEN_ENV) or "").strip()
    if not token:
        raise IqOptionReadOnlyError("a prova da IQ Option exige token MCP")
    expected_account_id = str(
        env.get("REAL_READ_ONLY_ACCOUNT_ID") or "").strip()
    discover_unique_account = (
        env.get("IQ_OPTION_CONFIRM_UNIQUE_REGULAR_ACCOUNT") is True)
    if not expected_account_id and not discover_unique_account:
        raise IqOptionReadOnlyError(
            "informe o identificador real exibido pela IQ Option")

    endpoint = IQ_OPTION_MCP_SERVERS[IQ_OPTION_READ_ONLY_SERVER]
    try:
        payload = await balance_reader(
            endpoint, token, {"types": "NORMAL"})
    except IqOptionReadOnlyError:
        raise
    except Exception as exc:
        raise IqOptionReadOnlyError(
            "consulta MCP somente leitura recusada "
            f"({type(exc).__name__})") from exc

    balances = payload.get("balances") if isinstance(payload, dict) else None
    if not isinstance(balances, list):
        raise IqOptionReadOnlyError("resposta MCP somente leitura inválida")
    ids: list[str] = []
    for balance in balances:
        if not isinstance(balance, dict):
            raise IqOptionReadOnlyError(
                "resposta MCP somente leitura inválida")
        balance_type = balance.get("type")
        balance_id = balance.get("balance_id")
        if balance_type != "regular":
            if balance_type == "training":
                raise IqOptionReadOnlyError(
                    "filtro NORMAL devolveu conta de treinamento; "
                    "resposta MCP somente leitura inválida")
            raise IqOptionReadOnlyError(
                "resposta MCP somente leitura inválida")
        if isinstance(balance_id, bool) or not isinstance(
                balance_id, (int, str)) or not str(balance_id).strip():
            raise IqOptionReadOnlyError(
                "resposta MCP somente leitura inválida")
        ids.append(str(balance_id).strip())
    if not ids:
        raise IqOptionReadOnlyError(
            "nenhuma conta REAL regular foi devolvida pela IQ Option")
    if discover_unique_account and not expected_account_id:
        if len(ids) != 1:
            raise IqOptionReadOnlyError(
                "a confirmação automática exige exatamente uma conta "
                "REAL regular")
        expected_account_id = ids[0]
    if not any(hmac.compare_digest(expected_account_id, item) for item in ids):
        raise IqOptionReadOnlyError(
            "o identificador informado não corresponde à conta autenticada")

    return {
        "exchange": "iq_option",
        "mcp_server": IQ_OPTION_READ_ONLY_SERVER,
        "authenticated_read": True,
        "read_methods": ["binary-options:list_balances[NORMAL]"],
        # A chamada só ocorre depois de tools/list provar o escopo read-only.
        "provider_mutating_tools_exposed": False,
        "broker_identity_verified": True,
        "broker_identity_fingerprint": _broker_identity_fingerprint(
            env, expected_account_id),
        "credential_fingerprint": read_only_credential_fingerprint(env),
    }


__all__ = [
    "IQ_OPTION_READ_ONLY_SERVER",
    "IQ_OPTION_TOKEN_ENV",
    "IqOptionReadOnlyError",
    "probe_iq_option_read_only_account_async",
]

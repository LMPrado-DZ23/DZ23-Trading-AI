"""Sonda autenticada e estritamente passiva dos MCPs oficiais da IQ Option.

Esta integração existe somente para provar o handshake e enumerar metadados.
Ela deliberadamente não expõe ``call_tool``: nenhum nome de ferramenta vindo
do servidor é tratado como autorização para consultar conta ou negociar.
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from service.personal_exchange_catalog import IQ_OPTION_MCP_ENDPOINTS

IQ_OPTION_TOKEN_ENV = "IQ_OPTION_MCP_BEARER_TOKEN"
IQ_OPTION_MCP_SERVERS = {
    endpoint.removeprefix("https://").split(".", 1)[0]: endpoint
    for endpoint in IQ_OPTION_MCP_ENDPOINTS
}

ToolLister = Callable[[str, str], Awaitable[Sequence[Any]]]


def _tool_metadata(tool: Any) -> dict[str, str]:
    """Retorna somente metadados pequenos; nunca argumentos ou resultados."""
    nome = str(getattr(tool, "name", ""))[:120]
    descricao = str(getattr(tool, "description", "") or "")[:240]
    return {"name": nome, "description": descricao}


async def _list_remote_tools(endpoint: str, token: str) -> Sequence[Any]:
    """Inicializa um MCP oficial e executa apenas ``tools/list``."""
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
            result = await client.list_tools(cache_mode="refresh")
            return tuple(result.tools)


async def probe_iq_option_mcp_async(
        *, token: str | None = None,
        lister: ToolLister = _list_remote_tools) -> dict[str, object]:
    """Prova os sete handshakes sem executar nenhuma ferramenta remota."""
    segredo = (token if token is not None
               else os.getenv(IQ_OPTION_TOKEN_ENV, "")).strip()
    if not segredo:
        return {
            "id": "iq_option",
            "status": "CREDENTIAL_REQUIRED",
            "ok": False,
            "credential_env": IQ_OPTION_TOKEN_ENV,
            "credential_present": False,
            "metadata_only": True,
            "tool_execution_enabled": False,
            "orders_enabled": False,
            "probes": [
                {"server": nome, "endpoint": endpoint,
                 "status": "CREDENTIAL_REQUIRED", "ok": False}
                for nome, endpoint in IQ_OPTION_MCP_SERVERS.items()
            ],
        }

    async def provar(nome: str, endpoint: str) -> dict[str, object]:
        try:
            tools = tuple(await lister(endpoint, segredo))
            metadados = [_tool_metadata(tool) for tool in tools[:200]]
            return {
                "server": nome,
                "endpoint": endpoint,
                "status": "MCP_METADATA_CONNECTED",
                "ok": True,
                "tool_count": len(tools),
                "tools": metadados,
                "callable_tool_count": 0,
                "all_tools_blocked": True,
            }
        except Exception as exc:  # falha fechada; não vaza resposta/credencial
            return {
                "server": nome,
                "endpoint": endpoint,
                "status": "MCP_UNAVAILABLE",
                "ok": False,
                "error_type": type(exc).__name__,
                "detail": "falha no handshake autenticado ou em tools/list",
            }

    probes = list(await asyncio.gather(*(
        provar(nome, endpoint)
        for nome, endpoint in IQ_OPTION_MCP_SERVERS.items()
    )))
    conectados = sum(bool(item["ok"]) for item in probes)
    if conectados == len(probes):
        status = "MCP_METADATA_CONNECTED"
    elif conectados:
        status = "MCP_PARTIAL"
    else:
        status = "MCP_UNAVAILABLE"
    return {
        "id": "iq_option",
        "status": status,
        "ok": conectados == len(probes),
        "connected": conectados,
        "expected": len(probes),
        "credential_env": IQ_OPTION_TOKEN_ENV,
        "credential_present": True,
        "metadata_only": True,
        "tool_execution_enabled": False,
        "orders_enabled": False,
        "probes": probes,
    }


def probe_iq_option_mcp(*, token: str | None = None,
                        lister: ToolLister = _list_remote_tools
                        ) -> dict[str, object]:
    """Entrada síncrona para a rota FastAPI (que roda em worker thread)."""
    def executar() -> dict[str, object]:
        return asyncio.run(probe_iq_option_mcp_async(
            token=token, lister=lister))

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return executar()
    # Testes/embeddings podem chamar a API síncrona dentro de um loop já vivo.
    # Um loop não pode ser aninhado na mesma thread; a thread curta preserva a
    # interface síncrona sem reaproveitar o loop do chamador.
    with ThreadPoolExecutor(max_workers=1,
                            thread_name_prefix="iq-mcp-probe") as pool:
        return pool.submit(executar).result()


__all__ = [
    "IQ_OPTION_MCP_SERVERS",
    "IQ_OPTION_TOKEN_ENV",
    "probe_iq_option_mcp",
    "probe_iq_option_mcp_async",
]

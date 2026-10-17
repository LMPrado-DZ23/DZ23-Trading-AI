"""Transporte MCP binário: superfície fixa, catálogo renovado e segredo opaco."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from service.iq_option_binary_transport import (
    IqOptionBinaryMcpTransport,
    IqOptionBinaryTransportError,
)
from service.iq_option_mcp import IQ_OPTION_MCP_TOOL_CONTRACTS

_PROPERTIES = {
    "list_assets": {"only_enabled"},
    "place_trade": {
        "balance_id", "asset_id", "direction", "amount",
        "profit_percent", "expired",
    },
    "list_positions": {"balance_id"},
    "get_trade_history": {"skip", "limit"},
}


def _tools(*, unknown=False, drift_target=False):
    reads, writes = IQ_OPTION_MCP_TOOL_CONTRACTS["binary-options"]
    names = set(reads) | set(writes)
    if unknown:
        names.add("execute_anything")
    tools = []
    for name in sorted(names):
        properties = _PROPERTIES.get(
            name, {"types"} if name == "list_balances" else {"known_argument"})
        if drift_target and name == "place_trade":
            properties = properties | {"withdraw_to"}
        read_only = name in reads
        tools.append(SimpleNamespace(
            name=name,
            annotations=SimpleNamespace(
                read_only_hint=True if read_only else None,
                destructive_hint=not read_only,
                open_world_hint=False,
            ),
            input_schema={
                "type": "object",
                "properties": {key: {"type": "number"} for key in properties},
                "required": sorted(properties) if not read_only else [],
                "additionalProperties": False,
            },
        ))
    return tuple(tools)


def _result(payload, *, error=False):
    return SimpleNamespace(
        is_error=error,
        structured_content=payload,
        content=[],
    )


def test_chama_somente_servidor_binario_com_token_opaco_e_catalogo_fresco():
    calls = []

    async def session(endpoint, token, name, arguments):
        calls.append((endpoint, token, name, arguments))
        return _tools(), _result({"assets": []})

    transport = IqOptionBinaryMcpTransport(
        token="TOKEN-SECRETO", session_caller=session)

    assert transport("list_assets", {"only_enabled": True}) == {"assets": []}
    assert calls == [(
        "https://binary-options.mcp.iqoption.com/mcp",
        "TOKEN-SECRETO",
        "list_assets",
        {"only_enabled": True},
    )]
    assert "TOKEN-SECRETO" not in repr(transport)


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("sell_position", {"position_id": 7}),
        ("place_trade", {"balance_id": 1}),
        ("list_assets", {"only_enabled": False}),
        ("list_positions", {"balance_id": True}),
        ("get_trade_history", {"skip": -1, "limit": 100}),
    ],
)
def test_nome_ou_argumento_fora_da_superficie_falha_antes_da_rede(
        name, arguments):
    async def forbidden(*_args):
        raise AssertionError("rede não deveria ser alcançada")

    transport = IqOptionBinaryMcpTransport(
        token="segredo", session_caller=forbidden)

    with pytest.raises(IqOptionBinaryTransportError):
        transport(name, arguments)


@pytest.mark.parametrize(
    "tools,message",
    [
        (_tools(unknown=True), "catálogo"),
        (_tools(drift_target=True), "schema"),
    ],
)
def test_drift_de_catalogo_ou_schema_recusa_sem_executar_resultado(tools, message):
    async def session(_endpoint, _token, _name, _arguments):
        return tools, _result({"position_id": "pos-1"})

    transport = IqOptionBinaryMcpTransport(
        token="segredo", session_caller=session)

    with pytest.raises(IqOptionBinaryTransportError, match=message):
        transport("place_trade", {
            "balance_id": 1,
            "asset_id": 76,
            "direction": "call",
            "amount": 10.0,
            "profit_percent": 85,
            "expired": 1_800_000_300,
        })


def test_erro_remoto_e_excecao_nao_vazam_token_nem_payload():
    async def rejected(_endpoint, token, _name, _arguments):
        raise RuntimeError(f"Bearer {token} payload=NAO-VAZAR")

    transport = IqOptionBinaryMcpTransport(
        token="TOKEN-NAO-VAZAR", session_caller=rejected)
    with pytest.raises(IqOptionBinaryTransportError) as captured:
        transport("list_assets", {"only_enabled": True})
    assert "TOKEN-NAO-VAZAR" not in str(captured.value)
    assert "NAO-VAZAR" not in str(captured.value)

    async def remote_error(_endpoint, _token, _name, _arguments):
        return _tools(), _result({"secret": "NAO-VAZAR"}, error=True)

    transport = IqOptionBinaryMcpTransport(
        token="segredo", session_caller=remote_error)
    with pytest.raises(IqOptionBinaryTransportError) as captured:
        transport("list_assets", {"only_enabled": True})
    assert "NAO-VAZAR" not in str(captured.value)


def test_interface_sincrona_funciona_quando_ja_existe_event_loop():
    async def session(_endpoint, _token, _name, _arguments):
        return _tools(), _result({"positions": []})

    transport = IqOptionBinaryMcpTransport(
        token="segredo", session_caller=session)

    async def inside_loop():
        return transport("list_positions", {"balance_id": 912345})

    # A suíte completa mantém hosts assíncronos vivos na thread principal.
    # A thread isolada prova o cenário pretendido: o transporte é chamado de
    # dentro de um loop realmente ativo, sem herdar estado de outro teste.
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(asyncio.run, inside_loop()).result()
    assert result == {"positions": []}

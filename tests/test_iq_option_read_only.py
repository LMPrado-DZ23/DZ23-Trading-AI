"""IQ Option MCP: prova de conta REAL estritamente somente leitura."""
from __future__ import annotations

import asyncio
import inspect
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from service.iq_option_mcp import IQ_OPTION_MCP_TOOL_CONTRACTS
from service.iq_option_read_only import (
    IqOptionReadOnlyError,
    _tool_contract_is_exact,
    probe_iq_option_read_only_account_async,
)

TOKEN = "token-opaco-apenas-em-memoria"
ACCOUNT_ID = "123456789"


def _run(coroutine):
    """Isola o loop de testes que deixam um event loop ativo na thread."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result()


def _env(**updates):
    base = {
        "TRADINGAGENTS_EXCHANGE": "iq_option",
        "REAL_READ_ONLY_EXCHANGE": "iq_option",
        "IQ_OPTION_MCP_BEARER_TOKEN": TOKEN,
        "REAL_READ_ONLY_ACCOUNT_ID": ACCOUNT_ID,
    }
    base.update(updates)
    return base


def test_prova_chama_apenas_list_balances_normal_e_nao_expoe_dados():
    chamadas = []

    async def listar(endpoint, token, arguments):
        chamadas.append((endpoint, token, arguments))
        return {"balances": [{
            "balance_id": 123456789,
            "type": "regular",
            "currency": "BRL",
            "amount": 9876.54,
            "bonus_amount": 0,
        }]}

    resultado = _run(probe_iq_option_read_only_account_async(
        _env(), balance_reader=listar))

    assert len(chamadas) == 1
    assert chamadas[0][0] == "https://binary-options.mcp.iqoption.com/mcp"
    assert chamadas[0][1] == TOKEN
    assert chamadas[0][2] == {"types": "NORMAL"}
    assert resultado["exchange"] == "iq_option"
    assert resultado["authenticated_read"] is True
    assert resultado["broker_identity_verified"] is True
    assert resultado["provider_mutating_tools_exposed"] is True
    assert resultado["read_methods"] == [
        "binary-options:list_balances[NORMAL]"]
    serializado = repr(resultado)
    assert TOKEN not in serializado
    assert ACCOUNT_ID not in serializado
    assert "9876.54" not in serializado
    assert "BRL" not in serializado


def test_prova_recusa_conta_training_e_identidade_diferente():
    async def training(_endpoint, _token, _arguments):
        return {"balances": [{"balance_id": 123456789,
                              "type": "training"}]}

    with pytest.raises(IqOptionReadOnlyError, match="filtro NORMAL"):
        _run(probe_iq_option_read_only_account_async(
            _env(), balance_reader=training))

    async def outra(_endpoint, _token, _arguments):
        return {"balances": [{"balance_id": 999, "type": "regular"}]}

    with pytest.raises(IqOptionReadOnlyError, match="não corresponde"):
        _run(probe_iq_option_read_only_account_async(
            _env(), balance_reader=outra))

    async def mistura(_endpoint, _token, _arguments):
        return {"balances": [
            {"balance_id": 123456789, "type": "regular"},
            {"balance_id": 987654321, "type": "training"},
        ]}

    with pytest.raises(IqOptionReadOnlyError, match="filtro NORMAL"):
        _run(probe_iq_option_read_only_account_async(
            _env(), balance_reader=mistura))


@pytest.mark.parametrize("payload", [
    {},
    {"balances": None},
    {"balances": "não-é-lista"},
    {"balances": [{"balance_id": "", "type": "regular"}]},
    {"balances": [{"balance_id": 123456789, "type": "REGULAR"}]},
])
def test_prova_recusa_resposta_remota_malformada(payload):
    async def resposta(_endpoint, _token, _arguments):
        return payload

    with pytest.raises(IqOptionReadOnlyError, match="resposta.*inválida"):
        _run(probe_iq_option_read_only_account_async(
            _env(), balance_reader=resposta))


def test_sem_token_falha_antes_da_rede():
    chamado = False

    async def rede_proibida(*_args):
        nonlocal chamado
        chamado = True
        return {}

    with pytest.raises(IqOptionReadOnlyError, match="token MCP"):
        _run(probe_iq_option_read_only_account_async(
            _env(IQ_OPTION_MCP_BEARER_TOKEN=""),
            balance_reader=rede_proibida))
    assert chamado is False


def test_erro_remoto_nao_vaza_token_nem_resposta():
    async def falhar(_endpoint, token, _arguments):
        raise RuntimeError(
            f"Authorization: Bearer {token}; saldo=9876.54; id={ACCOUNT_ID}")

    with pytest.raises(IqOptionReadOnlyError) as capturado:
        _run(probe_iq_option_read_only_account_async(
            _env(), balance_reader=falhar))

    mensagem = str(capturado.value)
    assert TOKEN not in mensagem
    assert ACCOUNT_ID not in mensagem
    assert "9876.54" not in mensagem
    assert "Authorization" not in mensagem


def test_modulo_remoto_tem_uma_unica_ferramenta_literal_somente_leitura():
    import service.iq_option_read_only as modulo

    fonte = inspect.getsource(modulo)
    assert fonte.count(".call_tool(") == 1
    assert '.call_tool("list_balances"' in fonte
    for mutavel in (
        "place_trade", "sell_position", "rollover_position",
        "place_market_order", "place_limit_order", "place_stop_order",
        "close_position", "cancel_pending_order",
        "change_position_stop_loss", "change_position_take_profit",
    ):
        assert mutavel not in fonte


def test_contrato_remoto_exige_catalogo_exato_e_anotacao_read_only():
    read, write = IQ_OPTION_MCP_TOOL_CONTRACTS["binary-options"]
    tools = []
    for name in sorted(read | write):
        annotations = SimpleNamespace(
            read_only_hint=name in read,
            destructive_hint=name in write,
        )
        schema = ({"type": "object", "properties": {"types": {}},
                   "additionalProperties": False}
                  if name == "list_balances" else {"type": "object"})
        tools.append(SimpleNamespace(
            name=name, annotations=annotations, input_schema=schema))

    assert _tool_contract_is_exact(tuple(tools)) is True

    list_balances = next(x for x in tools if x.name == "list_balances")
    list_balances.annotations.read_only_hint = False
    assert _tool_contract_is_exact(tuple(tools)) is False
    list_balances.annotations.read_only_hint = True
    tools.append(SimpleNamespace(
        name="unexpected_tool", annotations=None, input_schema={}))
    assert _tool_contract_is_exact(tuple(tools)) is False

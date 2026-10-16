"""IQ Option MCP: autenticação passiva sem qualquer ferramenta executável."""
from __future__ import annotations

import asyncio
import inspect
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from fastapi.routing import APIRoute

from service.app import app, auth
from service.iq_option_mcp import (
    IQ_OPTION_MCP_SERVERS,
    probe_iq_option_mcp,
)

_OPTION_READ = {
    "get_candles", "get_capabilities", "get_limits", "get_trade_history",
    "list_assets", "list_balances", "list_positions",
}
_OPTION_WRITE = {"place_trade", "rollover_position", "sell_position"}
_MARGINAL_READ = {
    "calculate_order_size", "get_candles", "get_capabilities",
    "get_instruments", "get_limits", "get_orders", "get_trade_history",
    "list_assets", "list_balances", "list_positions",
}
_MARGINAL_WRITE = {
    "cancel_pending_order", "change_position_stop_loss",
    "change_position_take_profit", "close_position", "place_limit_order",
    "place_market_order", "place_stop_order",
}


def _nomes_esperados(server: str) -> set[str]:
    if server.startswith("marginal-"):
        return _MARGINAL_READ | _MARGINAL_WRITE
    if server == "digital-options":
        return ((_OPTION_READ | {"get_instruments", "get_prices"}
                 | {"place_trade", "sell_position"})
                - {"rollover_position"})
    return _OPTION_READ | _OPTION_WRITE


class TestSondaPassiva:
    def test_modulo_nao_possui_caminho_de_call_tool(self):
        import service.iq_option_mcp as modulo
        assert ".call_tool(" not in inspect.getsource(modulo)

    def test_sem_credencial_falha_antes_da_rede(self, monkeypatch):
        monkeypatch.delenv("IQ_OPTION_MCP_BEARER_TOKEN", raising=False)

        async def rede_proibida(_endpoint, _token):
            raise AssertionError("não deveria conectar")

        resultado = probe_iq_option_mcp(lister=rede_proibida)
        assert resultado["status"] == "CREDENTIAL_REQUIRED"
        assert resultado["credential_present"] is False
        assert resultado["tool_execution_enabled"] is False
        assert resultado["orders_enabled"] is False
        assert len(resultado["probes"]) == 7

    def test_entrada_sincrona_funciona_com_loop_ja_ativo(self, monkeypatch):
        monkeypatch.delenv("IQ_OPTION_MCP_BEARER_TOKEN", raising=False)

        async def dentro_do_loop():
            return probe_iq_option_mcp()

        # Hermético mesmo quando outro teste deixou um loop na thread principal.
        with ThreadPoolExecutor(max_workers=1) as pool:
            resultado = pool.submit(asyncio.run, dentro_do_loop()).result()
        assert resultado["status"] == "CREDENTIAL_REQUIRED"

    def test_sete_handshakes_listam_metadados_sem_chamar_tools(self):
        chamadas = []

        async def listar(endpoint, token):
            chamadas.append((endpoint, token))
            server = next(
                name for name, candidate in IQ_OPTION_MCP_SERVERS.items()
                if candidate == endpoint)
            return [SimpleNamespace(name=name, description="metadata")
                    for name in sorted(_nomes_esperados(server))]

        resultado = probe_iq_option_mcp(token="credencial-em-memoria",
                                        lister=listar)
        assert resultado["ok"] is True
        assert resultado["connected"] == 7
        assert {x[0] for x in chamadas} == set(IQ_OPTION_MCP_SERVERS.values())
        assert all(x[1] == "credencial-em-memoria" for x in chamadas)
        for prova in resultado["probes"]:
            assert prova["status"] == "MCP_METADATA_CONNECTED"
            assert prova["contract_validated"] is True
            assert prova["tool_count"] == len(_nomes_esperados(prova["server"]))
            assert (prova["read_only_tool_count"]
                    + prova["mutating_tool_count"] == prova["tool_count"])
            assert prova["unknown_tool_count"] == 0
            assert prova["callable_tool_count"] == 0
            assert prova["all_tools_blocked"] is True

    def test_catalogo_remoto_desconhecido_falha_fechado(self):
        async def listar(endpoint, _token):
            server = next(
                name for name, candidate in IQ_OPTION_MCP_SERVERS.items()
                if candidate == endpoint)
            nomes = _nomes_esperados(server) | {"execute_arbitrary_order"}
            return [SimpleNamespace(name=name, description="metadata")
                    for name in sorted(nomes)]

        resultado = probe_iq_option_mcp(token="credencial-em-memoria",
                                        lister=listar)

        assert resultado["ok"] is False
        assert resultado["status"] == "MCP_CONTRACT_DRIFT"
        assert resultado["connected"] == 0
        assert all(p["contract_validated"] is False
                   and p["unknown_tool_count"] == 1
                   and p["callable_tool_count"] == 0
                   and p["all_tools_blocked"] is True
                   for p in resultado["probes"])

    def test_erro_nao_vaza_token_nem_resposta_remota(self):
        async def falhar(_endpoint, token):
            raise RuntimeError(f"Authorization: Bearer {token}")

        resultado = probe_iq_option_mcp(token="SEGREDO-NAO-VAZAR",
                                        lister=falhar)
        serializado = repr(resultado)
        assert "SEGREDO-NAO-VAZAR" not in serializado
        assert "Authorization" not in serializado
        assert resultado["status"] == "MCP_UNAVAILABLE"


class TestRotaMcp:
    def test_rota_e_get_only_autenticada(self):
        rotas = [r for r in app.routes if isinstance(r, APIRoute)
                 and r.path == "/v1/personal/exchanges/iq_option/mcp-health"]
        assert len(rotas) == 1
        assert rotas[0].methods == {"GET"}
        assert any(d.call is auth for d in rotas[0].dependant.dependencies)

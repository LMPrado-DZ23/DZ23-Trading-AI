"""Contrato do servidor MCP: read-only, ciclo PAPER opt-in, erros honestos."""

from __future__ import annotations

import httpx
import pytest

import mcp_server


def _instalar(monkeypatch, handler):
    def fabrica() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler),
                            base_url="http://test")
    monkeypatch.setattr(mcp_server, "_client", fabrica)


def test_headers_exigem_senha(monkeypatch):
    monkeypatch.delenv("DZ23_ACCESS_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="DZ23_ACCESS_PASSWORD"):
        mcp_server._headers()


def test_headers_usam_bearer(monkeypatch):
    monkeypatch.setenv("DZ23_ACCESS_PASSWORD", "s3nh4")
    assert mcp_server._headers() == {"Authorization": "Bearer s3nh4"}


def test_desk_status_proxia_api(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/desk/status"
        return httpx.Response(200, json={"mandate": {"mode": "paper"}})

    _instalar(monkeypatch, handler)
    assert mcp_server.desk_status()["mandate"]["mode"] == "paper"


def test_erro_de_api_nao_vira_excecao(monkeypatch):
    _instalar(monkeypatch, lambda request: httpx.Response(503, json={"x": 1}))
    out = mcp_server.desk_status()
    assert out["ok"] is False and "503" in out["error"]


def test_run_paper_cycle_desabilitado_por_padrao(monkeypatch):
    monkeypatch.delenv("DZ23_MCP_ENABLE_CYCLE", raising=False)
    chamou = {"n": 0}

    def handler(request):
        chamou["n"] += 1
        return httpx.Response(200, json={})

    _instalar(monkeypatch, handler)
    out = mcp_server.run_paper_cycle()
    assert out["ok"] is False and "DZ23_MCP_ENABLE_CYCLE" in out["error"]
    assert chamou["n"] == 0


def test_run_paper_cycle_recusa_fora_de_paper(monkeypatch):
    monkeypatch.setenv("DZ23_MCP_ENABLE_CYCLE", "1")

    def handler(request):
        assert request.url.path == "/v1/desk/status"
        return httpx.Response(200, json={
            "mandate": {"mode": "live"}, "fonte": {"corretora": "live"}})

    _instalar(monkeypatch, handler)
    out = mcp_server.run_paper_cycle()
    assert out["ok"] is False and "PAPER" in out["error"]


def test_run_paper_cycle_dispara_em_paper(monkeypatch):
    monkeypatch.setenv("DZ23_MCP_ENABLE_CYCLE", "1")
    chamadas: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append((request.method, request.url.path))
        if request.url.path == "/v1/desk/status":
            return httpx.Response(200, json={
                "mandate": {"mode": "paper"},
                "fonte": {"corretora": "paper"}})
        if request.url.path == "/v1/desk/cycle":
            return httpx.Response(200, json={"ok": True, "ordens": []})
        return httpx.Response(404)

    _instalar(monkeypatch, handler)
    out = mcp_server.run_paper_cycle()
    assert out["ok"] is True
    assert ("POST", "/v1/desk/cycle") in chamadas


def test_nao_existe_ferramenta_de_ordem():
    publicos = {nome for nome in dir(mcp_server) if not nome.startswith("_")}
    assert "place_order" not in publicos
    assert "execute_order" not in publicos

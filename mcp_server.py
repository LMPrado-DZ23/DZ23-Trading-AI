"""Servidor MCP da mesa DZ23 — ponte segura entre o DS Bot e a API local.

Princípios:

* **Read-only por padrão.** As ferramentas leem status, risco, posições,
  ciclos e o checklist de dinheiro real. Não existe ferramenta de ordem.
* **Ciclo PAPER é opt-in.** `run_paper_cycle` só funciona com
  ``DZ23_MCP_ENABLE_CYCLE=1`` e recusa se a mesa não estiver em PAPER ou se a
  corretora estiver em modo ``live``.
* **Sem segredo no bot.** O token vem de ``DZ23_ACCESS_PASSWORD`` no ambiente
  do processo MCP; ele nunca é devolvido em resultado de ferramenta.
* **Log em stderr.** stdout é o canal JSON-RPC do MCP.

Uso (stdio), no perfil do DS Bot::

    DZ23_API_URL=http://127.0.0.1:8080
    DZ23_ACCESS_PASSWORD=...
    DZ23_MCP_ENABLE_CYCLE=0
    command: <venv>\\Scripts\\python.exe
    args:    <repo>\\mcp_server.py
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

LOGGER = logging.getLogger("dz23.mcp")

mcp = FastMCP("dz23-trading")

# Limite duro de itens devolvidos: um agente não deve despejar o histórico
# inteiro no contexto (custo e ruído).
_MAX_ITENS = 20


def _base_url() -> str:
    return (os.environ.get("DZ23_API_URL") or "http://127.0.0.1:8080").rstrip("/")


def _headers() -> dict[str, str]:
    senha = (os.environ.get("DZ23_ACCESS_PASSWORD") or "").strip()
    if not senha:
        raise RuntimeError(
            "DZ23_ACCESS_PASSWORD não configurada no processo MCP")
    return {"Authorization": f"Bearer {senha}"}


def _timeout() -> float:
    try:
        return max(1.0, float(os.environ.get("DZ23_HTTP_TIMEOUT") or "30"))
    except ValueError:
        return 30.0


def _client() -> httpx.Client:
    """Cliente HTTP da API local. Injetável nos testes."""
    return httpx.Client(base_url=_base_url(), headers=_headers(),
                        timeout=_timeout())


def _get(path: str, params: dict | None = None) -> dict[str, Any]:
    try:
        with _client() as cliente:
            resposta = cliente.get(path, params=params)
            resposta.raise_for_status()
            dados = resposta.json()
            return dados if isinstance(dados, dict) else {"dados": dados}
    except Exception as exc:  # API fora do ar, 401, JSON inválido...
        LOGGER.warning("GET %s falhou: %s", path, type(exc).__name__)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "endpoint": path}


def _post(path: str, body: dict | None = None) -> dict[str, Any]:
    try:
        with _client() as cliente:
            resposta = cliente.post(path, json=body or {})
            resposta.raise_for_status()
            dados = resposta.json()
            return dados if isinstance(dados, dict) else {"dados": dados}
    except Exception as exc:
        LOGGER.warning("POST %s falhou: %s", path, type(exc).__name__)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "endpoint": path}


def _status_da_mesa() -> dict[str, Any]:
    return _get("/v1/desk/status")


@mcp.tool()
def platform_status() -> dict[str, Any]:
    """Capacidades públicas da plataforma (LIVE bloqueado, PAPER, versão)."""
    return _get("/v1/status/operacao")


@mcp.tool()
def health() -> dict[str, Any]:
    """Saúde do servidor e prontidão (kill switch, paper, reconciliação)."""
    return {"health": _get("/healthz"), "ready": _get("/readyz")}


@mcp.tool()
def desk_status() -> dict[str, Any]:
    """Estado da mesa: modo, agentes, ordens, posições, ciclos e contabilidade."""
    return _status_da_mesa()


@mcp.tool()
def risk_summary() -> dict[str, Any]:
    """Resumo de risco: disponível, reservado, exposição e pendências."""
    estado = _status_da_mesa()
    if estado.get("ok") is False:
        return estado
    return {
        "risk": estado.get("risk"),
        "contabilidade": estado.get("contabilidade"),
        "posicoes": estado.get("posicoes"),
        "pendencias_reconciliacao": estado.get("pendencias_reconciliacao"),
        "modo": estado.get("modo"),
        "agendador": estado.get("agendador"),
        "fonte": estado.get("fonte"),
    }


@mcp.tool()
def open_positions() -> dict[str, Any]:
    """Posições abertas e quem as guarda (sem dados de conta/segredo)."""
    estado = _status_da_mesa()
    if estado.get("ok") is False:
        return estado
    return {"posicoes": estado.get("posicoes"),
            "guardiao": estado.get("guardiao"),
            "ordens": estado.get("orders")}


@mcp.tool()
def recent_cycles(limit: int = 5) -> dict[str, Any]:
    """Últimos ciclos da mesa (1..20), com resumo e decisão."""
    try:
        n = max(1, min(_MAX_ITENS, int(limit)))
    except (TypeError, ValueError):
        n = 5
    estado = _status_da_mesa()
    if estado.get("ok") is False:
        return estado
    return {"ciclos": (estado.get("ciclos") or [])[-n:],
            "decisao": estado.get("decisao"),
            "modo": estado.get("modo")}


@mcp.tool()
def live_checklist() -> dict[str, Any]:
    """Checklist de dinheiro real. Não liga nada; diz o que falta."""
    return _get("/v1/live/status")


@mcp.tool()
def traders() -> dict[str, Any]:
    """Traders, estratégias, estágios de evidência e último motivo."""
    estado = _status_da_mesa()
    if estado.get("ok") is False:
        return estado
    return {"agents": estado.get("agents"),
            "escola": estado.get("escola"),
            "ciclos": estado.get("ciclos")}


@mcp.tool()
def run_paper_cycle() -> dict[str, Any]:
    """Roda UM ciclo em PAPER — opt-in e recusado fora de PAPER.

    Não envia ordem a dinheiro real: a mesa precisa estar em ``paper`` e a
    fonte da corretora não pode ser ``live``. Para habilitar, o operador
    define ``DZ23_MCP_ENABLE_CYCLE=1`` no processo MCP.
    """
    if (os.environ.get("DZ23_MCP_ENABLE_CYCLE") or "").strip() != "1":
        return {"ok": False, "error": (
            "ciclo via MCP desabilitado; defina DZ23_MCP_ENABLE_CYCLE=1 "
            "no processo MCP (somente PAPER)")}
    estado = _status_da_mesa()
    if estado.get("ok") is False:
        return estado
    mandate = estado.get("mandate") or {}
    fonte = estado.get("fonte") or {}
    if mandate.get("mode") != "paper":
        return {"ok": False, "error": (
            f"mesa não está em PAPER (mode={mandate.get('mode')!r}); "
            "o MCP não executa ciclo fora de PAPER")}
    if (fonte.get("corretora") or "").strip().lower() == "live":
        return {"ok": False, "error": (
            "corretora em LIVE; o MCP não dispara ciclo nesse modo")}
    return _post("/v1/desk/cycle")


def main() -> None:
    logging.basicConfig(
        level=(os.environ.get("DZ23_MCP_LOG_LEVEL") or "INFO").upper(),
        stream=sys.stderr,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    LOGGER.info("DZ23 MCP iniciando (stdio) em %s", _base_url())
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

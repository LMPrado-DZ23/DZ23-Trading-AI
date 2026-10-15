"""V9-SEC-05 / V9-SEC-07 — contraprovas exigidas da auditoria V9.

V9-SEC-05 (identificador de inquilino):
  "Requisição anônima desabilitada não escreve; identificador não pode
  escapar da raiz".

V9-SEC-07 (revogação de credencial de IA):
  "Transportes fake não podem reutilizar chave removida; nenhuma chamada
  paga".

RED antes do fix: `tenant_cfg_path` criava diretórios em TODA chamada —
inclusive no caminho de leitura de uma página anônima desabilitada — e o
identificador vindo da URL não era validado. Além disso, os closures de IA
(mesa e revisor) guardavam o snapshot da config do boot: chave removida no
painel continuava sendo usada até reiniciar o processo.
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

H = {"Authorization": "Bearer s3nh4"}

_ENV_IA = (
    "TRADINGAGENTS_LLM_PROVIDER",
    "TRADINGAGENTS_QUICK_THINK_LLM",
    "TRADINGAGENTS_DEEP_THINK_LLM",
    "TRADINGAGENTS_BACKEND_URL",
    "TRADINGAGENTS_LLM_FALLBACK",
    "TRADINGAGENTS_IA_ORCAMENTO_USD",
    "TRADINGAGENTS_IA_ORCAMENTO_BLOQUEIO",
    "GROQ_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "OPENROUTER_API_KEY",
    "MISTRAL_API_KEY",
    "TRANSPARENCIA_PUBLICA",
)


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # O ambiente real do dono tem chaves de IA: o teste precisa de um
    # mundo sem elas, senão "remover a chave" não removeria nada.
    for nome in _ENV_IA:
        monkeypatch.delenv(nome, raising=False)
    import service.app as m
    importlib.reload(m)
    m._FAILS.clear()
    m._DESKS.clear()
    from fastapi.testclient import TestClient
    return TestClient(m.app), m


def _configurar_ia(c, *, chave=None):
    vars_ = {
        "TRADINGAGENTS_LLM_PROVIDER": "groq",
        "TRADINGAGENTS_QUICK_THINK_LLM": "openai/gpt-oss-20b",
    }
    if chave:
        vars_["GROQ_API_KEY"] = chave
    r = c.post("/v1/config", headers=H, json={"vars": vars_})
    assert r.status_code == 200, r.text


def _remover(c, *chaves):
    r = c.post("/v1/config", headers=H, json={"remover": list(chaves)})
    assert r.status_code == 200, r.text


def _fake_invocar(chamadas: list):
    def _invocar(candidato, prompt, criar_cliente):
        chamadas.append(candidato.get("chave") or candidato.get("painel"))
        return "texto-fake", None

    return _invocar


class TestSec05TenantNaoEscapa:
    def test_anonimo_desabilitado_nao_cria_diretorio(self, tmp_path, monkeypatch):
        c, m = _client(tmp_path, monkeypatch)
        r = c.get("/publico/v9-fantasma")
        assert r.status_code == 404
        # Contraprova: leitura de página pública desligada não ESCREVE.
        assert not (tmp_path / "tenants").exists()

    @pytest.mark.parametrize(
        "tenant_id", ["../fuga", "..", "a/b", "", "x y", "a" * 65])
    def test_identificador_invalido_recusado(self, tmp_path, monkeypatch, tenant_id):
        c, m = _client(tmp_path, monkeypatch)
        with pytest.raises(ValueError):
            m.tenant_cfg_path(tenant_id)

    def test_pagina_publica_nao_escapa_da_raiz(self, tmp_path, monkeypatch):
        c, m = _client(tmp_path, monkeypatch)
        with pytest.raises(HTTPException) as exc:
            m.pagina_publica("../fuga")
        assert exc.value.status_code == 404
        assert not (tmp_path / "fuga").exists()
        assert not (tmp_path / "tenants").exists()

    def test_caminhos_validos_intactos(self, tmp_path, monkeypatch):
        c, m = _client(tmp_path, monkeypatch)
        assert m.tenant_cfg_path("legacy") == m.CFG_PATH
        esperado = m.DATA / "tenants" / "ten_v9" / "config.json"
        assert m.tenant_cfg_path("ten_v9") == esperado
        # Escrita cria o diretório sozinha (escrever_json_atomico): a
        # validação não pode ter quebrado o fluxo legítimo.
        m.save_cfg({"TRANSPARENCIA_PUBLICA": "true"}, "ten_v9")
        assert esperado.exists()
        assert m.load_cfg("ten_v9")["TRANSPARENCIA_PUBLICA"] == "true"


class TestSec07MesaNaoReusaChaveRemovida:
    def test_analista_da_mesa_le_credencial_viva(self, tmp_path, monkeypatch):
        import service.api.ia_provedores as iap

        c, m = _client(tmp_path, monkeypatch)
        _configurar_ia(c, chave="k-v9-a")
        d = m._DESKS.get("legacy") or m._desk_boot("legacy")
        chamadas: list = []
        monkeypatch.setattr(iap, "_invocar", _fake_invocar(chamadas))
        cliente = d["orchestrator"].analista.cliente

        assert cliente("pergunta") == "texto-fake"
        assert chamadas == ["k-v9-a"]

        _remover(c, "GROQ_API_KEY")
        with pytest.raises(RuntimeError):
            cliente("pergunta de novo")
        # Nenhuma chamada com a credencial removida.
        assert chamadas == ["k-v9-a"]


class _AgenciaFalsa:
    """Runtime mínimo da agência: só o que configure_agency_llm enxerga."""

    def __init__(self):
        self.executor = SimpleNamespace(handlers={})
        self._agencia_llm_assinatura = None

    def register_task_handler(self, agent_id, handler):
        self.executor.handlers[agent_id] = handler


class TestSec07AgenciaNaoReusaChaveRemovida:
    def test_rebuild_ao_remover_e_ao_trocar_chave(self, tmp_path, monkeypatch):
        c, m = _client(tmp_path, monkeypatch)
        _configurar_ia(c, chave="k-v9-a")

        def _fake_build(*, provider, model, role, base_url=None, api_key=None):
            return SimpleNamespace(api_key=api_key, role=role)

        monkeypatch.setattr(m, "build_llm_handler", _fake_build)
        runtime = SimpleNamespace(agency=_AgenciaFalsa())

        assert m.configure_agency_llm(runtime, "legacy") is None
        handlers = runtime.agency.executor.handlers
        assert set(m._AGENCY_LLM_AGENT_IDS) <= set(handlers)
        assert {h.api_key for h in handlers.values()} == {"k-v9-a"}

        # Chave removida: nenhum handler pode continuar servindo a antiga.
        _remover(c, "GROQ_API_KEY")
        diag = m.configure_agency_llm(runtime, "legacy")
        assert diag is not None
        assert all(getattr(h, "api_key", None) != "k-v9-a"
                   for h in handlers.values())
        with pytest.raises(RuntimeError):
            handlers["research-agent"](None)

        # Chave nova: reconstrói com a credencial viva.
        _configurar_ia(c, chave="k-v9-b")
        assert m.configure_agency_llm(runtime, "legacy") is None
        assert {h.api_key for h in runtime.agency.executor.handlers.values()} == {
            "k-v9-b"}


class TestSec07RevisorNaoReusaChaveRemovida:
    def test_revisao_usa_credencial_viva(self, tmp_path, monkeypatch):
        import service.api.ia_provedores as iap

        c, m = _client(tmp_path, monkeypatch)
        _configurar_ia(c, chave="k-v9-a")
        chamadas: list = []
        monkeypatch.setattr(iap, "_invocar", _fake_invocar(chamadas))
        revisar = m._revisor_de_ia("legacy", m.load_cfg("legacy"))
        assert revisar is not None

        assert revisar({"preco": "100"}) == "texto-fake"
        assert chamadas == ["k-v9-a"]

        _remover(c, "GROQ_API_KEY")
        with pytest.raises(RuntimeError):
            revisar({"preco": "101"})

        # Provedor removido por completo: recusa explícita, sem chamada.
        _remover(c, "TRADINGAGENTS_LLM_PROVIDER")
        with pytest.raises(RuntimeError):
            revisar({"preco": "102"})
        assert chamadas == ["k-v9-a"]

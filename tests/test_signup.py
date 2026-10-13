"""Cadastro self-service: DESATIVADO no aplicativo pessoal (dono único).

Este produto deixou de ter cadastro, trial e multi-tenant. Esta suíte,
originalmente escrita para PROVAR que o cadastro funcionava, agora prova o
oposto — que ele está inalcançável — e é complementada pela prova adversarial
em test_single_owner_g2.py. A lógica de isolamento de ambiente no job (que
continua importando num app pessoal) é preservada.
"""
from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


def _app(tmp_path, monkeypatch, **env):
    # Liga TODO interruptor de SaaS de propósito: nada disto pode reabrir a
    # superfície num app de dono único (a trava é de código, não de flag).
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MULTI_TENANT_ENABLED", "true")
    monkeypatch.setenv("TENANT_BOOTSTRAP_TOKEN", "boot-token-de-teste")
    monkeypatch.setenv("TENANT_TOKEN_SIGNING_KEY", "chave-de-assinatura-de-teste-0123456789")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import service.app as m
    importlib.reload(m)
    m._FAILS.clear()
    m._SIGNUP_ATTEMPTS.clear()
    return m


def conta(n=1):
    return {"agencia": f"Agência {n}", "email": f"dono{n}@exemplo.com",
            "senha": "uma-senha-bem-longa-2026"}


class TestCadastroDesativado:
    """A superfície de cadastro não existe no produto pessoal."""

    def test_signup_status_is_404(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch)
        assert TestClient(m.app).get("/v1/auth/signup-status").status_code == 404

    def test_signup_post_is_404_even_with_valid_body(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch)
        r = TestClient(m.app).post("/v1/auth/signup", json=conta())
        assert r.status_code == 404

    def test_signup_stays_404_even_when_flag_tries_to_enable_it(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch, SIGNUP_ENABLED="true")
        c = TestClient(m.app)
        assert c.get("/v1/auth/signup-status").status_code == 404
        assert c.post("/v1/auth/signup", json=conta()).status_code == 404

    def test_tenant_login_is_404(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch)
        r = TestClient(m.app).post("/v1/auth/login", json={
            "tenant_id": "x", "email": "a@b.com", "password": "123"})
        assert r.status_code == 404

    def test_password_from_blocked_signup_is_never_echoed(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch)
        r = TestClient(m.app).post("/v1/auth/signup", json=conta())
        assert "uma-senha-bem-longa-2026" not in r.text


class TestIsolamentoDeAmbienteNoJob:
    """Mesmo sem inquilinos, o job precisa restaurar o ambiente do processo:
    uma chave não pode sobrar entre execuções."""

    def test_job_restores_process_environment(self):
        import inspect

        import service.app as m
        fonte = inspect.getsource(m.run_job)
        assert "_env_anterior" in fonte and "finally:" in fonte
        assert "os.environ.pop(_k, None)" in fonte


class TestTelaDeCadastro:
    def test_signup_form_exists_in_panel(self):
        from pathlib import Path
        html = Path("service/static/index.html").read_text(encoding="utf-8")
        for needle in ['id="signupForm"', "criarConta()", "/v1/auth/signup",
                       "/v1/auth/signup-status", "mostrarLogin()"]:
            assert needle in html, f"faltou: {needle}"


class TestConfiguracaoRuimNaoDerruba:
    """O dono não pode receber 502 mudo por uma variável mal preenchida — e,
    num app de dono único, qualquer pedido de multi-tenant é simplesmente
    ignorado (forçado a desligado), sem derrubar o serviço."""

    def test_stray_multitenant_flag_is_ignored_and_service_stays_up(
            self, tmp_path, monkeypatch):
        import importlib
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("MULTI_TENANT_ENABLED", "true")
        monkeypatch.setenv("TENANT_TOKEN_SIGNING_KEY", "curta")
        import service.app as m
        importlib.reload(m)                       # não pode explodir
        assert m.SINGLE_OWNER_ENFORCED is True
        assert m.MULTI_TENANT_ENABLED is False    # o pedido foi ignorado
        assert m.TENANT_STORE is None
        c = TestClient(m.app)
        assert c.get("/").status_code == 200
        assert c.get("/healthz").status_code == 200

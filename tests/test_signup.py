"""Cadastro self-service: sem isto não há produto para vender."""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


def _app(tmp_path, monkeypatch, **env):
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


class TestCadastro:
    def test_status_is_public_and_shows_plans(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch)
        d = TestClient(m.app).get("/v1/auth/signup-status").json()
        assert d["habilitado"] is True and d["dias_de_teste"] > 0
        assert any(p["codigo"] == "starter" for p in d["planos"])

    def test_signup_creates_tenant_trial_and_session(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        r = c.post("/v1/auth/signup", json=conta())
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["tenant_id"] and d["access_token"] and d["plano"] == "trial"
        # a sessão devolvida já funciona:
        me = c.get("/v1/auth/me", headers={"Authorization": f"Bearer {d['access_token']}"})
        assert me.status_code == 200 and me.json()["role"] == "admin"

    def test_new_tenant_can_use_the_desk_immediately(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        tok = c.post("/v1/auth/signup", json=conta()).json()["access_token"]
        r = c.get("/v1/desk/status", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200

    def test_two_tenants_are_isolated(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        a = c.post("/v1/auth/signup", json=conta(1)).json()
        b = c.post("/v1/auth/signup", json=conta(2)).json()
        assert a["tenant_id"] != b["tenant_id"]
        ha = {"Authorization": f"Bearer {a['access_token']}"}
        hb = {"Authorization": f"Bearer {b['access_token']}"}
        c.post("/v1/config", headers=ha, json={"vars": {"GROQ_API_KEY": "gsk-do-A"}})
        cfg_b = c.get("/v1/config", headers=hb).json()["config"]["GROQ_API_KEY"]
        assert cfg_b["set"] is False        # B não enxerga a chave de A

    def test_duplicate_email_is_refused(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        c.post("/v1/auth/signup", json=conta())
        assert c.post("/v1/auth/signup", json=conta()).status_code == 409

    @pytest.mark.parametrize("campo,valor", [
        ("senha", "curta"), ("email", "nao-e-email"), ("agencia", ""),
    ])
    def test_invalid_input_is_refused(self, tmp_path, monkeypatch, campo, valor):
        m = _app(tmp_path, monkeypatch)
        dados = conta()
        dados[campo] = valor
        assert TestClient(m.app).post("/v1/auth/signup", json=dados).status_code == 422

    def test_rate_limit_blocks_account_factory(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        for i in range(5):
            c.post("/v1/auth/signup", json=conta(i))
        assert c.post("/v1/auth/signup", json=conta(99)).status_code == 429

    def test_signup_can_be_disabled_for_private_deployments(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch, SIGNUP_ENABLED="false")
        c = TestClient(m.app)
        assert c.get("/v1/auth/signup-status").json()["habilitado"] is False
        assert c.post("/v1/auth/signup", json=conta()).status_code == 404

    def test_password_is_never_returned_or_logged(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch)
        r = TestClient(m.app).post("/v1/auth/signup", json=conta())
        assert "uma-senha-bem-longa-2026" not in r.text


class TestIsolamentoDeSegredos:
    """Isolamento real: a chave de um inquilino não pode aparecer para outro,
    nem sobrar no processo depois da análise."""

    def test_env_of_the_server_does_not_leak_into_tenants(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "gsk-do-SERVIDOR")
        m = _app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        tok = c.post("/v1/auth/signup", json=conta()).json()["access_token"]
        cfg = c.get("/v1/config", headers={"Authorization": f"Bearer {tok}"}).json()
        assert cfg["config"]["GROQ_API_KEY"]["set"] is False

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
    """O dono não pode receber 502 mudo por uma variável mal preenchida."""

    def test_short_signing_key_disables_multitenant_but_keeps_service_up(
            self, tmp_path, monkeypatch):
        import importlib
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("MULTI_TENANT_ENABLED", "true")
        monkeypatch.setenv("TENANT_TOKEN_SIGNING_KEY", "curta")
        import service.app as m
        importlib.reload(m)                       # não pode explodir
        assert m.MULTI_TENANT_ENABLED is False
        assert any("multi-tenant desligado" in a for a in m.HEALTH["avisos"])
        c = TestClient(m.app)
        assert c.get("/").status_code == 200
        body = c.get("/healthz").json()
        assert body["status"] == "degraded"
        assert "TENANT_TOKEN_SIGNING_KEY" in " ".join(body["avisos"])

"""Login social: o provedor prova a identidade; o acesso continua sendo nosso."""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from tradingagents.identity.oauth import (
    PROVIDERS,
    OAuthError,
    StateStore,
    authorize_url,
    challenge_from,
    email_of,
    exchange_code,
)

ENV = {"GOOGLE_CLIENT_ID": "id-google", "GOOGLE_CLIENT_SECRET": "SEGREDO-GOOGLE",
       "GOOGLE_REDIRECT_URI": "https://api.dz23.com.br/api/oauth/google/callback"}


def _app(tmp_path, monkeypatch, **extra):
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MULTI_TENANT_ENABLED", "true")
    monkeypatch.setenv("TENANT_TOKEN_SIGNING_KEY", "chave-de-assinatura-bem-longa-0123456789")
    for k, v in {**ENV, **extra}.items():
        monkeypatch.setenv(k, v)
    import service.app as m
    importlib.reload(m)
    m._FAILS.clear()
    m._SIGNUP_ATTEMPTS.clear()
    return m


def transporte(email="novo@exemplo.com", verified=True, name="Cliente"):
    def _t(provider, payload):
        assert payload["client_secret"], "segredo deve ser enviado ao provedor"
        return {"email": email, "email_verified": verified, "name": name}
    return _t


class TestUrlDeAutorizacao:
    def test_url_has_pkce_and_no_secret(self):
        st = StateStore()
        state, verifier = st.new("google")
        url = authorize_url(PROVIDERS["google"], state, verifier, ENV)
        assert "code_challenge=" in url and "S256" in url
        assert challenge_from(verifier) in url
        assert "SEGREDO-GOOGLE" not in url          # segredo jamais no navegador

    def test_unconfigured_provider_refuses(self):
        st = StateStore()
        s, v = st.new("microsoft")
        with pytest.raises(OAuthError, match="não está configurado"):
            authorize_url(PROVIDERS["microsoft"], s, v, {})


class TestStateAntiCsrf:
    def test_state_is_single_use(self):
        st = StateStore()
        s, _ = st.new("google")
        st.consume(s, "google")
        with pytest.raises(OAuthError, match="já utilizado"):
            st.consume(s, "google")

    def test_state_of_another_provider_is_refused(self):
        st = StateStore()
        s, _ = st.new("google")
        with pytest.raises(OAuthError, match="outro provedor"):
            st.consume(s, "microsoft")

    def test_expired_state_is_refused(self):
        st = StateStore(ttl_seconds=0)
        s, _ = st.new("google")
        import time
        time.sleep(0.01)
        with pytest.raises(OAuthError):
            st.consume(s, "google")


class TestPerfil:
    def test_unverified_email_is_refused(self):
        with pytest.raises(OAuthError, match="não verificado"):
            email_of({"email": "a@x.com", "email_verified": False})

    def test_missing_email_is_refused(self):
        with pytest.raises(OAuthError, match="e-mail"):
            email_of({"name": "sem email"})

    def test_microsoft_preferred_username_is_accepted(self):
        assert email_of({"preferred_username": "A@X.com"}) == "a@x.com"

    def test_secret_never_leaks_in_error(self):
        def explode(provider, payload):
            raise RuntimeError("falha de rede com SEGREDO-GOOGLE dentro")
        with pytest.raises(OAuthError) as exc:
            exchange_code(PROVIDERS["google"], "code", "verifier", env=ENV,
                          transport=explode)
        assert "SEGREDO-GOOGLE" not in str(exc.value)


class TestLoginSocialDesativadoNoServico:
    """O login social é superfície de cadastro multi-tenant: não existe no app
    pessoal. Toda a rota /v1/auth/oauth/* responde 404, mesmo com as
    credenciais do provedor configuradas. A biblioteca OAuth acima (PKCE,
    anti-CSRF, extração de e-mail) segue testada e preservada."""

    def test_providers_route_is_404(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch)
        assert TestClient(m.app).get("/v1/auth/oauth/providers").status_code == 404

    def test_start_route_is_404(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch)
        assert TestClient(m.app).get("/v1/auth/oauth/google/start").status_code == 404

    def test_callback_route_is_404_and_creates_no_account(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch)
        m._OAUTH_TRANSPORT = transporte()
        c = TestClient(m.app)
        r = c.get("/v1/auth/oauth/google/callback?code=abc&state=qualquer")
        assert r.status_code == 404
        # Nenhuma conta/assinatura foi criada por um callback forjado.
        assert m.TENANT_STORE is None

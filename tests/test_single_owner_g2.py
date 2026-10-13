"""GATE G2 — prova adversarial de DONO ÚNICO (single-owner).

Objetivo: provar que NENHUM recurso de SaaS pode ser acessado ou executado no
aplicativo pessoal, INDEPENDENTEMENTE de:
  - flags de ambiente incorretas (MULTI_TENANT_ENABLED, SIGNUP_ENABLED, chaves
    Woovi e tenant todas LIGADAS de propósito aqui);
  - método HTTP (GET/POST/PUT/DELETE/PATCH);
  - autenticação (com e sem o Bearer do dono).

A trava é FAIL-CLOSED por construção (constante de código, não flag), então
este arquivo carrega o app no PIOR cenário possível — tudo que, num SaaS,
abriria a superfície — e mesmo assim exige 404 em toda ela. Também prova que
um webhook Woovi ASSINADO e VÁLIDO (que ativaria um plano se a rota existisse)
não altera nenhuma assinatura, e que vizinhos legítimos continuam acessíveis.
"""
from __future__ import annotations

import base64
import importlib
import json

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

SENHA = "senha-do-dono-bem-longa-2026"

# Chave usada para FORJAR um webhook Woovi perfeitamente válido: se a rota
# existisse, ativaria um plano. Tem de ser inútil contra a parede do 404.
_CHAVE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUB_PEM = _CHAVE.public_key().public_bytes(
    serialization.Encoding.PEM,
    serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def _assinar(corpo: bytes) -> str:
    return base64.b64encode(
        _CHAVE.sign(corpo, padding.PKCS1v15(), hashes.SHA256())).decode()


def _evento_pago(tenant="legacy", plano="pro", charge="chg-forjada") -> bytes:
    return json.dumps({"event": "OPENPIX:CHARGE_COMPLETED",
                       "charge": {"status": "COMPLETED", "identifier": charge,
                                  "correlationID": f"dz23-{tenant}-{plano}-202610"}}
                      ).encode()


def _app_adversarial(tmp_path, monkeypatch):
    """Liga TODO interruptor de SaaS que existe. Em um produto SaaS isto abriria
    cadastro, cobrança, login social e multi-tenant. Aqui não pode abrir nada."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ACCESS_PASSWORD", SENHA)
    monkeypatch.setenv("MULTI_TENANT_ENABLED", "true")
    monkeypatch.setenv("SIGNUP_ENABLED", "true")
    monkeypatch.setenv("TENANT_TOKEN_SIGNING_KEY", "k" * 48)
    monkeypatch.setenv("TENANT_BOOTSTRAP_TOKEN", "boot-token")
    monkeypatch.setenv("WOOVI_WEBHOOK_PUBLIC_KEY", _PUB_PEM)
    monkeypatch.setenv("WOOVI_TOKEN", "token-de-teste")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id-google")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "segredo-google")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://exemplo/callback")
    import service.app as m
    importlib.reload(m)
    m._FAILS.clear()
    m._SIGNUP_ATTEMPTS.clear()
    return m


# Caminho -> métodos que devem TODOS responder 404. Representa a superfície SaaS
# inteira, com e sem corpo, da leitura pública à ação autenticada.
_ROTAS_SAAS = {
    "/v1/auth/signup-status": ["GET", "POST", "PUT", "DELETE", "PATCH"],
    "/v1/auth/signup": ["GET", "POST", "PUT", "DELETE", "PATCH"],
    "/v1/auth/login": ["GET", "POST", "PUT", "DELETE", "PATCH"],
    "/v1/auth/bootstrap": ["GET", "POST", "PUT", "DELETE", "PATCH"],
    "/v1/auth/oauth/providers": ["GET", "POST", "PUT", "DELETE", "PATCH"],
    "/v1/auth/oauth/google/start": ["GET", "POST", "PUT", "DELETE", "PATCH"],
    "/v1/auth/oauth/google/callback": ["GET", "POST", "PUT", "DELETE", "PATCH"],
    "/v1/admin/tenants": ["GET", "POST", "PUT", "DELETE", "PATCH"],
    "/v1/admin/users": ["GET", "POST", "PUT", "DELETE", "PATCH"],
    "/v1/billing/planos": ["GET", "POST", "PUT", "DELETE", "PATCH"],
    "/v1/billing/status": ["GET", "POST", "PUT", "DELETE", "PATCH"],
    "/v1/billing/checkout": ["GET", "POST", "PUT", "DELETE", "PATCH"],
    "/v1/webhooks/woovi": ["GET", "POST", "PUT", "DELETE", "PATCH"],
}


@pytest.fixture()
def app_mod(tmp_path, monkeypatch):
    return _app_adversarial(tmp_path, monkeypatch)


@pytest.fixture()
def client(app_mod):
    return TestClient(app_mod.app)


class TestMultiTenantForcadoDesligado:
    def test_single_owner_flag_is_hardcoded_true(self, app_mod):
        assert app_mod.SINGLE_OWNER_ENFORCED is True

    def test_env_true_is_ignored_multitenant_stays_off(self, app_mod):
        # O ambiente PEDIU multi-tenant; o modo dono único o ignora.
        assert app_mod.MULTI_TENANT_ENABLED is False
        assert app_mod.MULTI_TENANT_REQUESTED is False
        assert app_mod.TENANT_STORE is None


class TestSuperficieSaasInalcancavel:
    @pytest.mark.parametrize("caminho,metodos", list(_ROTAS_SAAS.items()))
    def test_blocked_without_auth(self, client, caminho, metodos):
        for metodo in metodos:
            r = client.request(metodo, caminho)
            assert r.status_code == 404, f"{metodo} {caminho} -> {r.status_code}"

    @pytest.mark.parametrize("caminho,metodos", list(_ROTAS_SAAS.items()))
    def test_blocked_with_owner_auth(self, client, caminho, metodos):
        h = {"Authorization": f"Bearer {SENHA}"}
        for metodo in metodos:
            r = client.request(metodo, caminho, headers=h)
            assert r.status_code == 404, f"{metodo} {caminho} (auth) -> {r.status_code}"

    def test_signup_post_with_valid_body_is_404(self, client):
        r = client.post("/v1/auth/signup", json={
            "agencia": "Qualquer", "email": "x@exemplo.com",
            "senha": "uma-senha-bem-longa-2026"})
        assert r.status_code == 404

    def test_404_body_does_not_leak_saas_internals(self, client):
        # 404 genérico: não confirma existência de cadastro/cobrança/tenant.
        texto = client.get("/v1/billing/planos").text.lower()
        for vazamento in ("woovi", "tenant", "trial", "stripe", "pix"):
            assert vazamento not in texto


class TestWebhookWooviAssinadoNaoAtiva:
    def test_valid_signed_webhook_is_404_and_activates_nothing(self, app_mod):
        c = TestClient(app_mod.app)
        antes = app_mod.SUBS.ensure("legacy")
        plano_antes, exp_antes = antes.plan_code, antes.expires_at
        corpo = _evento_pago(tenant="legacy", plano="pro")
        r = c.post("/v1/webhooks/woovi", content=corpo,
                   headers={"x-webhook-signature": _assinar(corpo)})
        assert r.status_code == 404, r.text
        depois = app_mod.SUBS.get("legacy")
        # Nada foi ativado nem estendido: a assinatura é idêntica.
        assert depois.plan_code == plano_antes
        assert depois.expires_at == exp_antes
        assert depois.plan_code != "pro"


class TestVizinhosLegitimosContinuamVivos:
    def test_health_is_reachable(self, client):
        assert client.get("/healthz").status_code == 200

    def test_auth_status_is_public_and_honest(self, client):
        d = client.get("/v1/auth/status").json()
        assert d["multi_tenant"] is False
        assert d["live_trading"] is False

    def test_admin_database_is_not_caught_by_the_block(self, client):
        # Vizinho de prefixo de /v1/admin/tenants, mas é inspeção do próprio
        # banco pelo dono: NÃO pode ser bloqueado (não é 404 por bloqueio).
        h = {"Authorization": f"Bearer {SENHA}"}
        assert client.get("/v1/admin/database", headers=h).status_code != 404

    def test_owner_can_reach_the_desk_without_a_plan_gate(self, client):
        h = {"Authorization": f"Bearer {SENHA}"}
        assert client.get("/v1/desk/status", headers=h).status_code == 200

    def test_expired_subscription_does_not_block_the_owner_desk(self, app_mod):
        from datetime import datetime, timedelta, timezone
        c = TestClient(app_mod.app)
        h = {"Authorization": f"Bearer {SENHA}"}
        sub = app_mod.SUBS.ensure("legacy")
        sub.expires_at = datetime.now(timezone.utc) - timedelta(days=30)
        sub.status = "expired"
        app_mod.SUBS.save(sub)
        # Sem portão de plano: o dono nunca recebe 402.
        assert c.get("/v1/desk/status", headers=h).status_code == 200


class TestHelperDeBloqueio:
    def test_blocks_every_saas_prefix(self, app_mod):
        for caminho in _ROTAS_SAAS:
            assert app_mod.rota_saas_bloqueada(caminho), caminho

    def test_does_not_block_legitimate_neighbors(self, app_mod):
        for caminho in ("/v1/admin/database", "/v1/auth/status", "/v1/auth/me",
                        "/v1/auth/logout", "/v1/jobs", "/v1/desk/status",
                        "/healthz", "/v1/config", "/billinghack", "/v1/billingx"):
            assert not app_mod.rota_saas_bloqueada(caminho), caminho

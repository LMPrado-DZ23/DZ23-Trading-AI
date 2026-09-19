"""E2E do patch crítico: entitlements, trial, billing fail-closed, catálogo,
signup fail-closed, OAuth/login, analytics endurecido e isolamento — testados
via HTTP real (FastAPI TestClient), não só lógica pura (§7).

Requer fastapi + cryptography (roda no CI Docker; o py3.14 local não tem os
wheels pesados). O app é recarregado em modo MULTI-TENANT com SQLite local.
"""
from __future__ import annotations

import base64
import importlib
import json

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

SENHA = "senhabemgrande"
CHAVE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUB_PEM = CHAVE.public_key().public_bytes(
    serialization.Encoding.PEM,
    serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def _assinar(corpo: bytes) -> str:
    return base64.b64encode(
        CHAVE.sign(corpo, padding.PKCS1v15(), hashes.SHA256())).decode()


def _webhook_body(tenant: str, plano: str, charge: str = "chg-1") -> bytes:
    return json.dumps({"event": "OPENPIX:CHARGE_COMPLETED",
                       "charge": {"status": "COMPLETED", "identifier": charge,
                                  "correlationID": f"dz23-{tenant}-{plano}-202609"}}
                      ).encode()


def _reload_multitenant(tmp_path, monkeypatch):
    monkeypatch.setenv("MULTI_TENANT_ENABLED", "true")
    monkeypatch.setenv("TENANT_TOKEN_SIGNING_KEY", "k" * 40)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ACCESS_PASSWORD", raising=False)
    monkeypatch.delenv("SIGNUP_ENABLED", raising=False)     # ausente => desligado
    import service.app as m
    importlib.reload(m)
    assert m.MULTI_TENANT_ENABLED, "multi-tenant deveria estar ligado no teste"
    return m


def _tenant(m, tid: str, email: str):
    m.TENANT_STORE.create_account("Agência", email, SENHA, tenant_id=tid)
    return {"Authorization": "Bearer " + m.TENANT_STORE.login(email, SENHA, tid)}


@pytest.fixture()
def mt(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    m = _reload_multitenant(tmp_path, monkeypatch)
    return m, TestClient(m.app)


class TestEstadoEEscrita:
    def test_conta_nova_e_somente_leitura_ate_iniciar_trial(self, mt):
        m, c = mt
        h = _tenant(m, "tena", "a@a.com")
        # Sem trial iniciado: NOT_STARTED, somente-leitura.
        st = c.get("/v1/account/state", headers=h).json()
        assert st["state"] == m.ACCT.NOT_STARTED and st["read_only"] is True
        # Leitura passa; escrita é bloqueada com 402 pelo portão central.
        assert c.get("/v1/billing/status", headers=h).status_code == 200
        r = c.post("/v1/config", headers=h, json={"vars": {"TRADINGAGENTS_OUTPUT_LANGUAGE": "pt"}})
        assert r.status_code == 402 and r.json()["estado"] == m.ACCT.NOT_STARTED

    def test_trial_explicito_libera_escrita_e_e_idempotente(self, mt):
        m, c = mt
        h = _tenant(m, "tena", "a@a.com")
        r1 = c.post("/v1/trial/start", headers=h)
        assert r1.status_code == 200 and r1.json()["state"] == m.ACCT.TRIALING
        fim1 = r1.json()["expires_at"]
        # Idempotente: segunda chamada NÃO estende o prazo.
        r2 = c.post("/v1/trial/start", headers=h)
        assert r2.json()["expires_at"] == fim1
        # Agora a escrita passa.
        assert c.post("/v1/config", headers=h,
                      json={"vars": {"TRADINGAGENTS_OUTPUT_LANGUAGE": "pt"}}).status_code == 200

    def test_get_e_login_nunca_iniciam_trial(self, mt):
        m, c = mt
        h = _tenant(m, "tena", "a@a.com")
        c.get("/v1/account/state", headers=h)
        c.get("/v1/billing/status", headers=h)
        assert m.SUBS.get("tena") is None            # nenhum GET criou assinatura

    def test_trial_expirado_volta_a_somente_leitura(self, mt):
        from datetime import datetime, timedelta, timezone
        m, c = mt
        h = _tenant(m, "tena", "a@a.com")
        c.post("/v1/trial/start", headers=h)
        sub = m.SUBS.get("tena")
        sub.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        m.SUBS.save(sub)
        assert c.get("/v1/account/state", headers=h).json()["state"] == m.ACCT.TRIAL_EXPIRED
        assert c.post("/v1/config", headers=h,
                      json={"vars": {"TRADINGAGENTS_OUTPUT_LANGUAGE": "pt"}}).status_code == 402


class TestBillingFailClosed:
    def test_billing_indisponivel_vira_unknown_somente_leitura(self, mt):
        m, c = mt
        h = _tenant(m, "tena", "a@a.com")
        c.post("/v1/trial/start", headers=h)               # tinha acesso...
        m.SUBS = m._UnavailableStore()                     # ...banco cai
        assert c.get("/v1/billing/status", headers=h).json()["estado"] == m.ACCT.UNKNOWN_BILLING
        # Fail-closed: escrita bloqueada; NÃO inventa trial novo.
        assert c.post("/v1/config", headers=h,
                      json={"vars": {"TRADINGAGENTS_OUTPUT_LANGUAGE": "pt"}}).status_code == 402


class TestCatalogoECheckout:
    def test_planos_expoem_o_catalogo_canonico(self, mt):
        m, c = mt
        h = _tenant(m, "tena", "a@a.com")
        codigos = {p["codigo"] for p in c.get("/v1/billing/planos", headers=h).json()["planos"]}
        assert codigos == set(m.CAT.CATALOGO)              # nada de starter/pro legado

    def test_checkout_recusa_fora_do_catalogo_mas_e_acessivel_em_readonly(self, mt):
        m, c = mt
        h = _tenant(m, "tena", "a@a.com")                  # NOT_STARTED (read-only)
        # Código inválido: 422 (não 402) — pagamento é permitido em read-only.
        assert c.post("/v1/billing/checkout", headers=h, json={"plano": "starter"}).status_code == 422
        # Código válido chega ao gateway (sem WOOVI_TOKEN => 503), nunca 402.
        r = c.post("/v1/billing/checkout", headers=h, json={"plano": "dz23_monthly"})
        assert r.status_code == 503

    def test_webhook_assinado_ativa_o_plano(self, mt, monkeypatch):
        m, c = mt
        monkeypatch.setenv("WOOVI_WEBHOOK_PUBLIC_KEY", PUB_PEM)
        h = _tenant(m, "tenwh", "wh@a.com")
        corpo = _webhook_body("tenwh", "dz23_monthly")
        r = c.post("/v1/webhooks/woovi", content=corpo,
                   headers={"x-webhook-signature": _assinar(corpo)})
        assert r.status_code == 200 and r.json()["plano"] == "dz23_monthly"
        assert c.get("/v1/billing/status", headers=h).json()["ativo"] is True

    def test_webhook_sem_assinatura_nao_ativa_nada(self, mt, monkeypatch):
        m, c = mt
        monkeypatch.setenv("WOOVI_WEBHOOK_PUBLIC_KEY", PUB_PEM)
        _tenant(m, "tenwh", "wh@a.com")
        r = c.post("/v1/webhooks/woovi", content=_webhook_body("tenwh", "dz23_monthly"))
        assert r.status_code == 401
        assert m.SUBS.get("tenwh") is None


class TestSignupELogin:
    def test_signup_desligado_por_padrao_responde_404(self, mt):
        m, c = mt
        r = c.post("/v1/auth/signup", json={"agencia": "Nova", "email": "n@n.com", "senha": SENHA})
        assert r.status_code == 404                        # fail-closed sem env

    def test_login_sem_tenant_id_emite_sessao(self, mt):
        m, c = mt
        _tenant(m, "tena", "a@a.com")
        r = c.post("/v1/auth/login", json={"email": "a@a.com", "password": SENHA})
        assert r.status_code == 200 and r.json()["access_token"]

    def test_login_errado_e_generico(self, mt):
        m, c = mt
        _tenant(m, "tena", "a@a.com")
        r = c.post("/v1/auth/login", json={"email": "a@a.com", "password": "errada-errada"})
        assert r.status_code == 401


class TestAnalyticsEndurecido:
    def test_evento_valido_responde_204(self, mt):
        m, c = mt
        r = c.post("/v1/analytics/event", json={"event": "page_view", "source": "hero"})
        assert r.status_code == 204 and not r.content

    def test_content_type_errado_e_415(self, mt):
        m, c = mt
        r = c.post("/v1/analytics/event", content="event=page_view",
                   headers={"content-type": "text/plain"})
        assert r.status_code == 415

    def test_corpo_grande_demais_e_413(self, mt):
        m, c = mt
        r = c.post("/v1/analytics/event",
                   json={"event": "page_view", "source": "x" * 3000})
        assert r.status_code == 413

    def test_rate_limit_responde_429(self, mt, monkeypatch):
        m, c = mt
        monkeypatch.setattr(m, "_ANALYTICS_MAX", 2)
        assert c.post("/v1/analytics/event", json={"event": "page_view"}).status_code == 204
        assert c.post("/v1/analytics/event", json={"event": "page_view"}).status_code == 204
        assert c.post("/v1/analytics/event", json={"event": "page_view"}).status_code == 429


class TestIsolamento:
    def test_estado_de_billing_e_por_tenant(self, mt):
        m, c = mt
        ha = _tenant(m, "tena", "a@a.com")
        hb = _tenant(m, "tenb", "b@b.com")
        c.post("/v1/trial/start", headers=ha)              # só A inicia trial
        assert c.get("/v1/account/state", headers=ha).json()["state"] == m.ACCT.TRIALING
        assert c.get("/v1/account/state", headers=hb).json()["state"] == m.ACCT.NOT_STARTED
        assert m.SUBS.get("tenb") is None                  # B não herdou nada de A


class TestBannerSomenteLeitura:
    def test_index_tem_banner_e_le_estado_da_conta(self):
        from pathlib import Path
        html = Path("service/static/index.html").read_text(encoding="utf-8")
        assert "roBanner" in html
        assert "/v1/account/state" in html and "read_only" in html

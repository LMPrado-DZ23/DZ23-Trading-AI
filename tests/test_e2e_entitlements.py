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


def _webhook_body(tenant: str, plano: str, charge: str = "chg-1",
                  value: int = 15000, currency: str = "BRL") -> bytes:
    return json.dumps({"event": "OPENPIX:CHARGE_COMPLETED",
                       "charge": {"status": "COMPLETED", "identifier": charge,
                                  "value": value, "currency": currency,
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

    def test_webhook_tenant_desconhecido_e_recusado(self, mt, monkeypatch):
        m, c = mt
        monkeypatch.setenv("WOOVI_WEBHOOK_PUBLIC_KEY", PUB_PEM)
        # assinado e válido em tudo, mas o tenant não existe -> 404 (§8)
        corpo = _webhook_body("tenantfantasma", "dz23_monthly")
        r = c.post("/v1/webhooks/woovi", content=corpo,
                   headers={"x-webhook-signature": _assinar(corpo)})
        assert r.status_code == 404

    def test_webhook_replay_e_idempotente(self, mt, monkeypatch):
        m, c = mt
        monkeypatch.setenv("WOOVI_WEBHOOK_PUBLIC_KEY", PUB_PEM)
        h = _tenant(m, "tenwh", "wh@a.com")
        corpo = _webhook_body("tenwh", "dz23_monthly", charge="chg-rp")
        h_sig = {"x-webhook-signature": _assinar(corpo)}
        r1 = c.post("/v1/webhooks/woovi", content=corpo, headers=h_sig)
        assert r1.status_code == 200 and not r1.json().get("replay")
        exp1 = m.SUBS.get("tenwh").expires_at
        r2 = c.post("/v1/webhooks/woovi", content=corpo, headers=h_sig)
        assert r2.status_code == 200 and r2.json().get("replay") is True
        assert m.SUBS.get("tenwh").expires_at == exp1
        assert c.get("/v1/billing/status", headers=h).json()["ativo"] is True


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


def _expira(m, tid: str):
    """Inicia e VENCE o trial do tenant (conta somente-leitura por expiração)."""
    from datetime import datetime, timedelta, timezone
    m.SUBS.start_trial_for(tid)
    sub = m.SUBS.get(tid)
    sub.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    m.SUBS.save(sub)


class TestGuardActionAware:
    """§4: o guard é ciente da AÇÃO. De-escalação/segurança passam em conta
    vencida; abrir exposição e resetar o kill-switch são bloqueados."""

    def test_expired_pode_engatar_mas_nao_resetar_kill_switch(self, mt):
        m, c = mt
        h = _tenant(m, "tena", "a@a.com")
        _expira(m, "tena")
        eng = c.post("/v1/execution/kill-switch", headers=h, json={"action": "engage"})
        assert eng.status_code == 200 and eng.json()["enabled"] is True
        rst = c.post("/v1/execution/kill-switch", headers=h, json={"action": "reset"})
        assert rst.status_code == 402                # reset reabilita execução -> bloqueado

    def test_expired_pode_cancelar_job(self, mt):
        m, c = mt
        h = _tenant(m, "tena", "a@a.com")
        _expira(m, "tena")
        assert c.post("/v1/jobs/qualquer-id/cancel", headers=h).status_code == 200

    def test_expired_pode_reconciliar(self, mt):
        m, c = mt
        h = _tenant(m, "tena", "a@a.com")
        _expira(m, "tena")
        # O guard NÃO bloqueia (de-escalação/segurança); o handler pode ou não
        # ter provider remoto, mas nunca deve devolver 402 por entitlements.
        assert c.post("/v1/execution/reconciliation", headers=h).status_code != 402

    def test_expired_pode_parada_de_emergencia(self, mt):
        m, c = mt
        h = _tenant(m, "tena", "a@a.com")
        _expira(m, "tena")
        assert c.post("/v1/live/parada-emergencia", headers=h).status_code != 402

    def test_expired_nao_pode_abrir_posicao_paper(self, mt):
        m, c = mt
        h = _tenant(m, "tena", "a@a.com")
        _expira(m, "tena")
        assert c.post("/v1/execution/paper", headers=h, json={}).status_code == 402

    def test_expired_nao_pode_criar_job(self, mt):
        m, c = mt
        h = _tenant(m, "tena", "a@a.com")
        _expira(m, "tena")
        r = c.post("/v1/jobs", headers=h, json={"ticker": "AAPL", "trade_date": "2026-01-02"})
        assert r.status_code == 402


class TestTrialSoPorAcaoExplicita:
    """§3: o trial NUNCA começa sozinho; só POST /v1/trial/start, uma vez."""

    def test_trial_start_exige_admin(self, mt):
        m, c = mt
        m.TENANT_STORE.create_account("Ag", "admin@a.com", SENHA, tenant_id="tena")
        uid = m.TENANT_STORE.create_user("viewer@a.com", SENHA)
        m.TENANT_STORE.add_member("tena", uid, "viewer")
        tok = m.TENANT_STORE.login("viewer@a.com", SENHA, "tena")
        r = c.post("/v1/trial/start", headers={"Authorization": "Bearer " + tok})
        assert r.status_code == 403                  # viewer não inicia o teste

    def test_expirado_nao_reinicia(self, mt):
        m, c = mt
        h = _tenant(m, "tena", "a@a.com")
        _expira(m, "tena")
        # §4: teste vencido NÃO reinicia — 409, e a assinatura segue vencida.
        r = c.post("/v1/trial/start", headers=h)
        assert r.status_code == 409
        assert m.SUBS.get("tena").is_active() is False

    def test_trial_start_e_idempotente_em_trialing(self, mt):
        m, c = mt
        h = _tenant(m, "tena", "a@a.com")
        fim = c.post("/v1/trial/start", headers=h).json()["expires_at"]
        again = c.post("/v1/trial/start", headers=h)
        assert again.status_code == 200 and again.json()["idempotent"] is True
        assert again.json()["expires_at"] == fim        # não estende

    def test_trial_start_recusado_para_conta_ativa(self, mt):
        m, c = mt
        h = _tenant(m, "tena", "a@a.com")
        # paga (ativa) e então tenta iniciar trial -> 409 (nunca reinicia)
        from tradingagents.billing.woovi import apply_paid_event
        m.SUBS.save(apply_paid_event(None, "tena", "dz23_monthly", "c1"))
        assert c.post("/v1/trial/start", headers=h).status_code == 409

    def test_restart_nao_estende_o_trial(self, tmp_path, monkeypatch):
        import importlib

        from fastapi.testclient import TestClient
        m = _reload_multitenant(tmp_path, monkeypatch)
        h = _tenant(m, "tena", "a@a.com")
        fim = TestClient(m.app).post("/v1/trial/start", headers=h).json()["expires_at"]
        # "reinício": recarrega o módulo apontando pro MESMO DATA_DIR; a
        # assinatura persiste e o trial NÃO é estendido nem recriado.
        import service.app as m2
        importlib.reload(m2)
        h2 = {"Authorization": "Bearer " + m2.TENANT_STORE.login("a@a.com", SENHA, "tena")}
        fim2 = TestClient(m2.app).post("/v1/trial/start", headers=h2).json()["expires_at"]
        assert fim2 == fim

"""Cobrança PIX via Woovi: ninguém libera assinatura sem pagamento provado."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from tradingagents.billing.store import SubscriptionStore
from tradingagents.billing.woovi import (
    PLANS,
    BillingError,
    WebhookRejected,
    WooviClient,
    apply_paid_event,
    charge_payload,
    start_trial,
    verify_webhook,
)

CHAVE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUB_PEM = CHAVE.public_key().public_bytes(
    serialization.Encoding.PEM,
    serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def assinar(corpo: bytes) -> str:
    return base64.b64encode(
        CHAVE.sign(corpo, padding.PKCS1v15(), hashes.SHA256())).decode()


def evento(tenant="t1", plano="starter", charge="chg-1", status="COMPLETED") -> bytes:
    return json.dumps({"event": "OPENPIX:CHARGE_COMPLETED",
                       "charge": {"status": status, "identifier": charge,
                                  "correlationID": f"dz23-{tenant}-{plano}-202609"}}
                      ).encode()


class TestAssinaturaDoWebhook:
    def test_valid_signature_is_accepted(self):
        corpo = evento()
        assert verify_webhook(corpo, assinar(corpo), PUB_PEM)["event"]

    def test_public_key_in_base64_form_also_works(self):
        corpo = evento()
        b64 = base64.b64encode(PUB_PEM.encode()).decode()
        assert verify_webhook(corpo, assinar(corpo), b64)

    def test_tampered_body_is_rejected(self):
        corpo = evento()
        assinatura = assinar(corpo)
        adulterado = corpo.replace(b"starter", b"pro")      # tenta plano melhor
        with pytest.raises(WebhookRejected, match="assinatura"):
            verify_webhook(adulterado, assinatura, PUB_PEM)

    def test_missing_signature_is_rejected(self):
        with pytest.raises(WebhookRejected, match="sem assinatura"):
            verify_webhook(evento(), "", PUB_PEM)

    def test_missing_public_key_is_rejected(self):
        corpo = evento()
        with pytest.raises(WebhookRejected, match="chave pública"):
            verify_webhook(corpo, assinar(corpo), "")

    def test_signature_from_another_key_is_rejected(self):
        outra = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        corpo = evento()
        falsa = base64.b64encode(
            outra.sign(corpo, padding.PKCS1v15(), hashes.SHA256())).decode()
        with pytest.raises(WebhookRejected):
            verify_webhook(corpo, falsa, PUB_PEM)


class TestAssinatura:
    def test_new_tenant_starts_on_trial(self, tmp_path):
        st = SubscriptionStore(tmp_path / "s.db")
        sub = st.ensure("t1")
        assert sub.plan_code == "trial" and sub.is_active()

    def test_payment_activates_and_extends(self, tmp_path):
        st = SubscriptionStore(tmp_path / "s.db")
        sub = st.save(apply_paid_event(st.ensure("t1"), "t1", "starter", "c1"))
        assert sub.status == "active" and sub.plan_code == "starter"
        depois = st.save(apply_paid_event(sub, "t1", "starter", "c2"))
        assert depois.expires_at > sub.expires_at          # renovou

    def test_repeated_webhook_does_not_extend_twice(self, tmp_path):
        st = SubscriptionStore(tmp_path / "s.db")
        primeiro = st.save(apply_paid_event(st.ensure("t1"), "t1", "starter", "c1"))
        repetido = apply_paid_event(primeiro, "t1", "starter", "c1")
        assert repetido.expires_at == primeiro.expires_at

    def test_expired_subscription_is_not_active(self, tmp_path):
        st = SubscriptionStore(tmp_path / "s.db")
        sub = start_trial("t1")
        sub.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        st.save(sub)
        assert st.get("t1").is_active() is False

    def test_tenants_do_not_share_subscription(self, tmp_path):
        st = SubscriptionStore(tmp_path / "s.db")
        st.save(apply_paid_event(st.ensure("a"), "a", "pro", "c1"))
        assert st.ensure("b").plan_code == "trial"


class TestCobranca:
    def test_charge_payload_is_idempotent_by_correlation(self):
        p = charge_payload("t1", PLANS["starter"], "dz23-t1-starter-202609")
        assert p["correlationID"] == "dz23-t1-starter-202609"
        assert p["value"] == PLANS["starter"].price_cents

    def test_free_plan_cannot_be_charged(self):
        with pytest.raises(BillingError, match="não é cobrável"):
            WooviClient(token="x").create_charge("t1", "trial", "c1")

    def test_missing_token_fails_closed_with_clear_message(self, monkeypatch):
        monkeypatch.delenv("WOOVI_TOKEN", raising=False)
        with pytest.raises(BillingError, match="WOOVI_TOKEN"):
            WooviClient().create_charge("t1", "starter", "c1")

    def test_token_never_appears_in_error(self, monkeypatch):
        def explode(payload):
            raise RuntimeError("erro de rede")
        c = WooviClient(token="Q2xpZW50X0lkX1NFR1JFRE8=", transport=explode)
        with pytest.raises(BillingError) as exc:
            c.create_charge("t1", "starter", "c1")
        assert "Q2xpZW50" not in str(exc.value)

    def test_charge_view_exposes_only_payment_data(self):
        view = WooviClient.charge_view({"charge": {
            "correlationID": "dz23-t1-starter-202609", "status": "ACTIVE",
            "value": 9900, "brCode": "0002012636...", "qrCodeImage": "https://x/qr.png",
            "paymentLinkUrl": "https://pay/x", "secretField": "não deve aparecer"}})
        assert "secretField" not in json.dumps(view)
        assert view["brcode"] and view["qrcode_imagem"]


class TestCobrancaDesativadaNoAppPessoal:
    """Num app de dono único não há cobrança: as rotas de billing e o webhook
    de pagamento não existem (404), e o desk NUNCA é bloqueado por assinatura
    vencida — não há plano a pagar. A lógica de assinatura/cripto acima segue
    testada como biblioteca preservada.

    (Resolução de merge: a base refatorou esta classe para um fluxo SaaS
    multi-tenant com login de inquilino; por DECISÃO ARQUITETURAL o modo dono
    único é permanente e nenhuma flag reabre esse fluxo, então as asserções
    corretas aqui são de bloqueio.)"""

    def _app(self, tmp_path, monkeypatch):
        import importlib
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        # Flags SaaS ligadas de propósito: o modo dono único as ignora.
        monkeypatch.setenv("MULTI_TENANT_ENABLED", "true")
        monkeypatch.setenv("TENANT_TOKEN_SIGNING_KEY",
                           "chave-de-assinatura-bem-longa-0123456789")
        import service.app as m
        importlib.reload(m)
        m._FAILS.clear()
        return m

    def test_desk_is_not_plan_gated_even_when_subscription_expired(self, tmp_path, monkeypatch):
        from datetime import datetime, timedelta, timezone

        from fastapi.testclient import TestClient
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        h = {"Authorization": "Bearer s3nh4"}
        sub = m.SUBS.ensure("legacy")
        sub.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        sub.status = "expired"
        m.SUBS.save(sub)
        # O dono tem direito pleno: sem 402.
        assert c.get("/v1/desk/status", headers=h).status_code == 200

    def test_billing_routes_are_404(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        h = {"Authorization": "Bearer s3nh4"}
        assert c.get("/v1/billing/status", headers=h).status_code == 404
        assert c.get("/v1/billing/planos", headers=h).status_code == 404
        assert c.post("/v1/billing/checkout", headers=h,
                      json={"plano": "starter"}).status_code == 404

    def test_woovi_webhook_route_is_404(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        r = c.post("/v1/webhooks/woovi", content=evento())
        assert r.status_code == 404

    def test_even_a_validly_signed_webhook_activates_nothing(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        monkeypatch.setenv("WOOVI_WEBHOOK_PUBLIC_KEY", PUB_PEM)
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        corpo = evento(tenant="legacy", plano="starter", charge="chg-x")
        r = c.post("/v1/webhooks/woovi", content=corpo,
                   headers={"x-webhook-signature": assinar(corpo)})
        assert r.status_code == 404                              # rota não existe
        assert m.SUBS.ensure("legacy").plan_code == "trial"      # nada ativado


class TestPainelDePagamento:
    def test_payment_page_exists_with_handlers(self):
        from pathlib import Path
        html = Path("service/static/index.html").read_text(encoding="utf-8")
        for needle in ['id="page-plano"', "loadPlano()", "pagar('", 'id="pixBox"',
                       "/v1/billing/checkout", "/v1/billing/planos"]:
            assert needle in html, f"faltou no painel: {needle}"

    def test_panel_never_embeds_the_woovi_token(self):
        from pathlib import Path
        html = Path("service/static/index.html").read_text(encoding="utf-8")
        assert "WOOVI_TOKEN" not in html and "Authorization: Client_Id" not in html

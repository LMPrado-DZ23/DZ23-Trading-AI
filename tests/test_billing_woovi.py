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


def evento(tenant="t1", plano="dz23_monthly", charge="chg-1", status="COMPLETED",
           value=15000, currency="BRL") -> bytes:
    # value bate com o preço do plano mensal do catálogo (R$ 150,00).
    return json.dumps({"event": "OPENPIX:CHARGE_COMPLETED",
                       "charge": {"status": status, "identifier": charge,
                                  "value": value, "currency": currency,
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
        adulterado = corpo.replace(b"dz23_monthly", b"dz23_annual")   # tenta plano melhor
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
    def test_new_tenant_has_no_subscription(self, tmp_path):
        st = SubscriptionStore(tmp_path / "s.db")
        assert st.get("t1") is None            # NÃO inicia trial sozinho
        sub = st.start_trial_for("t1")         # trial só por ação explícita
        assert sub.plan_code == "trial" and sub.is_active()

    def test_payment_activates_and_extends(self, tmp_path):
        st = SubscriptionStore(tmp_path / "s.db")
        sub = st.save(apply_paid_event(st.get("t1"), "t1", "dz23_monthly", "c1"))
        assert sub.status == "active" and sub.plan_code == "dz23_monthly"
        depois = st.save(apply_paid_event(sub, "t1", "dz23_monthly", "c2"))
        assert depois.expires_at > sub.expires_at          # renovou

    def test_repeated_webhook_does_not_extend_twice(self, tmp_path):
        st = SubscriptionStore(tmp_path / "s.db")
        primeiro = st.save(apply_paid_event(st.get("t1"), "t1", "dz23_monthly", "c1"))
        repetido = apply_paid_event(primeiro, "t1", "dz23_monthly", "c1")
        assert repetido.expires_at == primeiro.expires_at

    def test_webhook_rejeita_codigo_desconhecido(self):
        with pytest.raises(BillingError, match="não é cobrável"):
            apply_paid_event(None, "t1", "codigo_que_nao_existe", "c9")

    def test_expired_subscription_is_not_active(self, tmp_path):
        st = SubscriptionStore(tmp_path / "s.db")
        sub = start_trial("t1")
        sub.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        st.save(sub)
        assert st.get("t1").is_active() is False

    def test_tenants_do_not_share_subscription(self, tmp_path):
        st = SubscriptionStore(tmp_path / "s.db")
        st.save(apply_paid_event(st.get("a"), "a", "dz23_annual", "c1"))
        assert st.get("b") is None             # b não ganha trial por acesso


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


class TestPortaoDeAcesso:
    """Assinatura vencida bloqueia o que custa dinheiro — mas nunca a página
    de pagamento (senão o cliente não consegue nem voltar a pagar)."""

    def _app(self, tmp_path, monkeypatch):
        import importlib
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        import service.app as m
        importlib.reload(m)
        m._FAILS.clear()
        return m

    def test_trial_allows_the_desk(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        r = c.get("/v1/desk/status", headers={"Authorization": "Bearer s3nh4"})
        assert r.status_code == 200

    def test_billing_pages_stay_reachable_in_legacy(self, tmp_path, monkeypatch):
        # No modo self-host (ACCESS_PASSWORD) NÃO há cobrança: o desk não é
        # bloqueado por assinatura. As páginas de pagamento seguem acessíveis.
        # O bloqueio 402 por assinatura vencida é do modo multi-tenant e está
        # coberto nos testes de entitlements (e2e).
        from fastapi.testclient import TestClient
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        h = {"Authorization": "Bearer s3nh4"}
        assert c.get("/v1/desk/status", headers=h).status_code == 200
        assert c.get("/v1/billing/status", headers=h).status_code == 200
        assert c.get("/v1/billing/planos", headers=h).status_code == 200

    def test_webhook_route_is_public_but_requires_signature(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        r = c.post("/v1/webhooks/woovi", content=evento())      # sem assinatura
        assert r.status_code == 401

    def test_signed_webhook_activates_the_plan(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        monkeypatch.setenv("WOOVI_WEBHOOK_PUBLIC_KEY", PUB_PEM)
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        corpo = evento(tenant="legacy", charge="chg-x")     # dz23_monthly, R$150
        r = c.post("/v1/webhooks/woovi", content=corpo,
                   headers={"x-webhook-signature": assinar(corpo)})
        assert r.status_code == 200 and r.json()["plano"] == "dz23_monthly"
        assert m.SUBS.get("legacy").status == "active"
        # e o acesso volta:
        assert c.get("/v1/desk/status",
                     headers={"Authorization": "Bearer s3nh4"}).status_code == 200

    def test_forged_webhook_does_not_activate_anything(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        monkeypatch.setenv("WOOVI_WEBHOOK_PUBLIC_KEY", PUB_PEM)
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        corpo = evento(tenant="legacy", plano="pro", charge="gratis")
        r = c.post("/v1/webhooks/woovi", content=corpo,
                   headers={"x-webhook-signature": base64.b64encode(b"lixo").decode()})
        assert r.status_code == 401
        assert m.SUBS.get("legacy") is None                   # nada foi ativado


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


class TestWebhookEndurecido:
    """§8: assinatura + moeda + valor + plano + tenant + charge id + replay +
    idempotência durável, e 5xx quando o banco cai depois do pagamento."""

    def _app(self, tmp_path, monkeypatch):
        import importlib
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("WOOVI_WEBHOOK_PUBLIC_KEY", PUB_PEM)
        import service.app as m
        importlib.reload(m)
        m._FAILS.clear()
        return m

    def _post(self, c, corpo):
        return c.post("/v1/webhooks/woovi", content=corpo,
                      headers={"x-webhook-signature": assinar(corpo)})

    def test_replay_nao_credita_duas_vezes(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        corpo = evento(tenant="legacy", charge="chg-1")
        r1 = self._post(c, corpo)
        assert r1.status_code == 200 and not r1.json().get("replay")
        exp1 = m.SUBS.get("legacy").expires_at
        r2 = self._post(c, corpo)                       # MESMO charge_id
        assert r2.status_code == 200 and r2.json().get("replay") is True
        assert m.SUBS.get("legacy").expires_at == exp1  # não estendeu (idempotente)

    def test_valor_errado_e_recusado(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        assert self._post(c, evento(tenant="legacy", value=100)).status_code == 422
        assert m.SUBS.get("legacy") is None             # não ativou nada

    def test_moeda_nao_brl_e_recusada(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        assert self._post(c, evento(tenant="legacy", currency="USD")).status_code == 422

    def test_moeda_ausente_e_recusada(self, tmp_path, monkeypatch):
        # §5 fail-closed: sem currency, NÃO assume BRL — recusa.
        from fastapi.testclient import TestClient
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        assert self._post(c, evento(tenant="legacy", currency=None)).status_code == 422

    def test_mesmo_charge_id_payload_divergente_e_conflito(self, tmp_path, monkeypatch):
        # §5: mesmo charge_id com plano/valor diferentes => 409, nunca 'replay ok'.
        from fastapi.testclient import TestClient
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        primeiro = evento(tenant="legacy", plano="dz23_monthly", charge="chg-c", value=15000)
        assert self._post(c, primeiro).status_code == 200
        divergente = evento(tenant="legacy", plano="dz23_annual", charge="chg-c", value=120000)
        assert self._post(c, divergente).status_code == 409

    def test_plano_fora_do_catalogo_e_recusado(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        corpo = evento(tenant="legacy", plano="starter", value=15000)
        assert self._post(c, corpo).status_code == 422

    def test_sem_identificador_e_recusado(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        assert self._post(c, evento(tenant="legacy", charge="")).status_code == 422

    def test_db_indisponivel_depois_do_pagamento_responde_5xx(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from tradingagents.billing.store import BillingUnavailable
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)

        class _Boom:
            def record_paid_charge(self, *a, **k):
                raise BillingUnavailable("db down")
        m.SUBS = _Boom()
        r = self._post(c, evento(tenant="legacy", charge="chg-9"))
        assert r.status_code == 503                      # Woovi reenvia; nada perdido

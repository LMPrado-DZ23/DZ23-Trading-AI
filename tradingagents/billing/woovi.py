"""Cobrança PIX via Woovi (TA-BILL).

Decisões registradas (autorizadas pelo dono):
- DECISÃO ASSUMIDA: pagamento por PIX/Woovi, cobrança por tenant, plano
  mensal. Motivo: é o gateway que o dono já usa e PIX é o meio dominante no
  Brasil para SaaS de pequeno porte.
- DECISÃO ASSUMIDA: o servidor NUNCA confia no navegador para liberar
  assinatura. Só o webhook assinado pela Woovi (verificado com a chave
  pública) ou uma consulta à API ativa um plano.
- DECISÃO ASSUMIDA: segredos apenas por ambiente/secret store. Este módulo
  jamais registra token, assinatura ou corpo bruto em log.

Fail-closed: assinatura ausente, chave pública ausente ou corpo adulterado ⇒
webhook REJEITADO. Não existe modo "aceita sem verificar" em produção.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

WOOVI_API = "https://api.openpix.com.br/api/v1"


class BillingError(RuntimeError):
    """Falha de cobrança — nunca libera acesso por acidente."""


class WebhookRejected(BillingError):
    """Webhook não confiável: assinatura inválida, ausente ou corpo alterado."""


@dataclass(frozen=True)
class Plan:
    code: str
    name: str
    price_cents: int
    period_days: int
    max_desks: int
    features: tuple[str, ...]

    @property
    def price_brl(self) -> str:
        return f"R$ {self.price_cents / 100:.2f}".replace(".", ",")


PLANS: dict[str, Plan] = {
    "trial": Plan("trial", "Teste", 0, 7, 1,
                  ("análises", "mesa em modo treino")),
    "starter": Plan("starter", "Starter", 9900, 30, 1,
                    ("análises ilimitadas", "mesa autônoma", "corretora testnet")),
    "pro": Plan("pro", "Pro", 24900, 30, 3,
                ("tudo do Starter", "3 mesas", "feed de mercado real",
                 "backtest e avaliação")),
}


@dataclass
class Subscription:
    tenant_id: str
    plan_code: str
    status: str                    # trialing | active | expired | canceled
    expires_at: datetime
    last_charge_id: str = ""

    @property
    def plan(self) -> Plan:
        return PLANS.get(self.plan_code, PLANS["trial"])

    def is_active(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return self.status in ("trialing", "active") and now < self.expires_at

    def to_row(self) -> dict:
        return {"tenant_id": self.tenant_id, "plan_code": self.plan_code,
                "status": self.status, "expires_at": self.expires_at.isoformat(),
                "last_charge_id": self.last_charge_id}

    @classmethod
    def from_row(cls, row: dict) -> Subscription:
        return cls(row["tenant_id"], row["plan_code"], row["status"],
                   datetime.fromisoformat(row["expires_at"]),
                   row.get("last_charge_id", ""))


def verify_webhook(raw_body: bytes, signature_b64: str, public_key_pem: str) -> dict:
    """Verifica a assinatura RSA-SHA256 da Woovi sobre o CORPO BRUTO.

    Verificar o JSON re-serializado não vale: qualquer diferença de espaço
    invalida — e aceitar sem verificar é o que permite alguém liberar
    assinatura de graça mandando um POST.
    """
    if not public_key_pem:
        raise WebhookRejected("chave pública do webhook não configurada")
    if not signature_b64:
        raise WebhookRejected("webhook sem assinatura")
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    pem = public_key_pem.strip()
    if "BEGIN PUBLIC KEY" not in pem:              # aceita a forma base64
        try:
            pem = base64.b64decode(pem).decode()
        except Exception as exc:
            raise WebhookRejected("chave pública inválida") from exc
    try:
        key = load_pem_public_key(pem.encode())
    except Exception as exc:
        raise WebhookRejected("chave pública ilegível") from exc
    try:
        key.verify(base64.b64decode(signature_b64), raw_body,
                   padding.PKCS1v15(), hashes.SHA256())
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise WebhookRejected("assinatura do webhook não confere") from exc
    try:
        return json.loads(raw_body.decode())
    except ValueError as exc:
        raise WebhookRejected("corpo do webhook não é JSON") from exc


def charge_payload(tenant_id: str, plan: Plan, correlation_id: str) -> dict:
    """Cobrança PIX. correlationID é a chave idempotente: reenviar não cria
    duas cobranças nem dois meses de assinatura."""
    return {"correlationID": correlation_id,
            "value": plan.price_cents,
            "comment": f"DZ23 Trading — plano {plan.name} ({tenant_id})"}


class WooviClient:
    """Cliente mínimo. O token vem SEMPRE do ambiente; nunca de parâmetro de
    requisição, nunca de arquivo versionado, nunca para log."""

    def __init__(self, token: str | None = None, base_url: str = WOOVI_API,
                 transport=None):
        self.token = (token or os.environ.get("WOOVI_TOKEN") or "").strip()
        self.base_url = base_url.rstrip("/")
        self._transport = transport            # injetável para teste

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def create_charge(self, tenant_id: str, plan_code: str,
                      correlation_id: str) -> dict:
        plan = PLANS.get(plan_code)
        if plan is None or plan.price_cents <= 0:
            raise BillingError(f"plano {plan_code!r} não é cobrável")
        if not self.configured:
            raise BillingError(
                "WOOVI_TOKEN não configurado no servidor — sem ele não há como "
                "gerar cobrança PIX")
        payload = charge_payload(tenant_id, plan, correlation_id)
        if self._transport is not None:
            try:
                return self._transport(payload)
            except Exception as exc:   # mesma proteção do caminho real
                raise BillingError(
                    f"falha ao criar cobrança ({type(exc).__name__})") from exc
        import httpx
        try:
            r = httpx.post(f"{self.base_url}/charge", json=payload,
                           headers={"Authorization": self.token}, timeout=20)
            r.raise_for_status()
            return r.json()
        except Exception as exc:               # mensagem sem token, sem corpo
            raise BillingError(
                f"falha ao criar cobrança na Woovi ({type(exc).__name__})") from exc

    @staticmethod
    def charge_view(resposta: dict) -> dict:
        """O que o painel pode mostrar — QR e link, nunca credencial."""
        charge = (resposta or {}).get("charge") or resposta or {}
        return {"charge_id": charge.get("correlationID") or charge.get("identifier", ""),
                "status": charge.get("status", "ACTIVE"),
                "valor": charge.get("value"),
                "brcode": charge.get("brCode") or charge.get("paymentLinkUrl", ""),
                "qrcode_imagem": charge.get("qrCodeImage", ""),
                "link": charge.get("paymentLinkUrl", "")}


def apply_paid_event(sub: Subscription | None, tenant_id: str, plan_code: str,
                     charge_id: str, now: datetime | None = None) -> Subscription:
    """Aplica um pagamento confirmado. Reentrante: o MESMO charge_id não
    estende o plano duas vezes (webhooks repetem, é normal)."""
    now = now or datetime.now(timezone.utc)
    plan = PLANS.get(plan_code, PLANS["starter"])
    if sub and sub.last_charge_id and sub.last_charge_id == charge_id:
        return sub                                   # já creditado
    base = sub.expires_at if (sub and sub.is_active(now)) else now
    return Subscription(tenant_id=tenant_id, plan_code=plan.code, status="active",
                        expires_at=base + timedelta(days=plan.period_days),
                        last_charge_id=charge_id)


def start_trial(tenant_id: str, now: datetime | None = None) -> Subscription:
    # Teste grátis de 24 HORAS (modelo comercial atual). Depois disso a área
    # fica somente-leitura até a assinatura — sem apagar dados.
    now = now or datetime.now(timezone.utc)
    return Subscription(tenant_id, "trial", "trialing",
                        now + timedelta(hours=24))


__all__ = ["PLANS", "Plan", "Subscription", "WooviClient", "BillingError",
           "WebhookRejected", "verify_webhook", "apply_paid_event", "start_trial",
           "charge_payload"]

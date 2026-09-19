"""Estado da conta (server-side) — a fonte da verdade sobre o que a área pode
fazer. Deriva-se da assinatura + do relógio; nunca do navegador.

Estados canônicos:
  NOT_STARTED    — sem assinatura/trial ainda.
  TRIALING       — dentro das 24h de teste grátis.
  TRIAL_EXPIRED  — teste acabou, sem assinatura paga -> somente-leitura.
  ACTIVE         — assinatura paga vigente.
  PAST_DUE       — assinatura paga venceu -> somente-leitura.
  SUSPENDED      — cancelada/suspensa -> somente-leitura.

Somente-leitura NÃO apaga dados: bloqueia ESCRITA (novas análises, mudança de
configuração, ligar a mesa, envio), mantendo leitura e exportação.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

NOT_STARTED = "NOT_STARTED"
TRIALING = "TRIALING"
TRIAL_EXPIRED = "TRIAL_EXPIRED"
ACTIVE = "ACTIVE"
PAST_DUE = "PAST_DUE"
SUSPENDED = "SUSPENDED"
# Billing indisponível (banco canônico fora do ar). FAIL-CLOSED: sem saber se
# a conta pode escrever, NÃO deixa escrever — nunca concede acesso por outage.
UNKNOWN_BILLING = "UNKNOWN_BILLING"

# Só estes estados PODEM escrever. Qualquer outro é somente-leitura — o
# default seguro é negar a escrita, não permitir.
WRITABLE = frozenset({TRIALING, ACTIVE})

# Teste grátis de 24 horas (o produto comercial).
TRIAL_HOURS = 24


def derive_state(sub: Any, now: datetime | None = None) -> str:
    """Mapeia a assinatura (ou a ausência dela) para um estado canônico.

    `sub` é duck-typed (qualquer objeto com status/plan_code/expires_at, ou
    None) para não acoplar este módulo puro ao dataclass de cobrança."""
    now = now or datetime.now(timezone.utc)
    if sub is None:
        return NOT_STARTED
    status = (getattr(sub, "status", "") or "").strip().lower()
    is_trial = getattr(sub, "plan_code", "") == "trial"
    expires_at = getattr(sub, "expires_at", None)
    vigente = expires_at is not None and now < expires_at
    if status in ("suspended", "canceled", "cancelled"):
        return SUSPENDED
    if status == "trialing":
        return TRIALING if vigente else TRIAL_EXPIRED
    if status == "active":
        return ACTIVE if vigente else PAST_DUE
    if status == "expired":
        return TRIAL_EXPIRED if is_trial else PAST_DUE
    # Existe assinatura, mas com status IRRECONHECÍVEL (corrompido/adulterado):
    # trava (fail-closed). NUNCA vira NOT_STARTED — senão a conta pareceria nova
    # e poderia iniciar um trial por cima de uma assinatura existente.
    return SUSPENDED


def is_read_only(state: str) -> bool:
    """Somente-leitura para todo estado que não pode escrever (default seguro)."""
    return state not in WRITABLE


def describe(state: str) -> dict:
    """Resumo para a UI (sem PII): estado, se é somente-leitura e um rótulo."""
    rotulos = {
        NOT_STARTED: "Sem teste iniciado",
        TRIALING: "Teste grátis (24h)",
        TRIAL_EXPIRED: "Teste expirado — somente leitura",
        ACTIVE: "Assinatura ativa",
        PAST_DUE: "Pagamento vencido — somente leitura",
        SUSPENDED: "Conta suspensa — somente leitura",
        UNKNOWN_BILLING: "Cobrança indisponível — somente leitura",
    }
    return {"state": state, "read_only": is_read_only(state),
            "label": rotulos.get(state, state)}


__all__ = ["NOT_STARTED", "TRIALING", "TRIAL_EXPIRED", "ACTIVE", "PAST_DUE",
           "SUSPENDED", "UNKNOWN_BILLING", "WRITABLE", "TRIAL_HOURS",
           "derive_state", "is_read_only", "describe"]

"""Estado da conta server-side + teste de 24h + somente-leitura (F/site #16-17)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tradingagents.billing import account_state as A
from tradingagents.billing.woovi import Subscription, start_trial

NOW = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)


def _sub(status, plan_code="trial", horas=1):
    return Subscription("ten_1", plan_code, status, NOW + timedelta(hours=horas))


def test_sem_assinatura_e_not_started_somente_leitura():
    assert A.derive_state(None, NOW) == A.NOT_STARTED
    assert A.is_read_only(A.NOT_STARTED)


def test_trialing_dentro_da_janela_pode_escrever():
    st = A.derive_state(_sub("trialing", horas=5), NOW)
    assert st == A.TRIALING
    assert not A.is_read_only(st)


def test_trial_expirado_e_somente_leitura():
    st = A.derive_state(_sub("trialing", horas=-1), NOW)  # já venceu
    assert st == A.TRIAL_EXPIRED
    assert A.is_read_only(st)


def test_active_vigente_pode_escrever_vencido_past_due():
    assert A.derive_state(_sub("active", "pro", horas=48), NOW) == A.ACTIVE
    assert not A.is_read_only(A.ACTIVE)
    st = A.derive_state(_sub("active", "pro", horas=-1), NOW)
    assert st == A.PAST_DUE and A.is_read_only(st)


def test_expired_trial_vs_pago():
    assert A.derive_state(_sub("expired", "trial", horas=-1), NOW) == A.TRIAL_EXPIRED
    assert A.derive_state(_sub("expired", "pro", horas=-1), NOW) == A.PAST_DUE


def test_suspensa_ou_cancelada_e_somente_leitura():
    for s in ("suspended", "canceled", "cancelled"):
        st = A.derive_state(_sub(s), NOW)
        assert st == A.SUSPENDED and A.is_read_only(st)


def test_start_trial_dura_exatamente_24h():
    sub = start_trial("ten_1", now=NOW)
    assert sub.expires_at == NOW + timedelta(hours=24)
    # dentro das 24h -> TRIALING; depois -> TRIAL_EXPIRED (read-only)
    assert A.derive_state(sub, NOW + timedelta(hours=23)) == A.TRIALING
    assert A.derive_state(sub, NOW + timedelta(hours=25)) == A.TRIAL_EXPIRED


def test_describe_expoe_read_only_sem_pii():
    d = A.describe(A.TRIAL_EXPIRED)
    assert d["read_only"] is True and d["state"] == A.TRIAL_EXPIRED
    assert "leitura" in d["label"].lower()

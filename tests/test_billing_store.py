"""Billing fail-closed + trial explícito, exatamente uma vez (patch crítico)."""
from datetime import datetime, timedelta, timezone

from tradingagents.billing import account_state as A
from tradingagents.billing.store import SubscriptionStore
from tradingagents.billing.woovi import Subscription


def _store(tmp_path):
    return SubscriptionStore(tmp_path / "subs.db", None)


def test_get_sem_linha_e_none_nao_cria_trial(tmp_path):
    s = _store(tmp_path)
    assert s.get("ten_1") is None          # NÃO inventa trial
    assert s.get("ten_1") is None          # continua sem linha


def test_start_trial_for_cria_uma_vez_e_e_idempotente(tmp_path):
    s = _store(tmp_path)
    a = s.start_trial_for("ten_1")
    assert a.status == "trialing"
    # ~24h de janela
    assert a.expires_at - datetime.now(timezone.utc) > timedelta(hours=23)
    b = s.start_trial_for("ten_1")         # 2a chamada NÃO renova
    assert b.expires_at == a.expires_at


def test_trial_expirado_nao_volta_a_trialing(tmp_path):
    s = _store(tmp_path)
    # linha já existente, expirada
    s.save(Subscription("ten_1", "trial", "trialing",
                        datetime.now(timezone.utc) - timedelta(hours=1)))
    voltou = s.start_trial_for("ten_1")    # não recria trial
    assert voltou.expires_at < datetime.now(timezone.utc)
    assert A.derive_state(voltou) == A.TRIAL_EXPIRED


def test_pago_nao_vira_trial(tmp_path):
    s = _store(tmp_path)
    s.save(Subscription("ten_1", "pro", "active",
                        datetime.now(timezone.utc) + timedelta(days=30)))
    r = s.start_trial_for("ten_1")
    assert r.plan_code == "pro" and r.status == "active"


def test_unknown_billing_e_somente_leitura():
    assert A.is_read_only(A.UNKNOWN_BILLING)
    assert A.UNKNOWN_BILLING not in A.WRITABLE
    assert A.describe(A.UNKNOWN_BILLING)["read_only"] is True

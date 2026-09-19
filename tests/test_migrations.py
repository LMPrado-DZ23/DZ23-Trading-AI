"""Migrações REAIS de schema (§1): um banco ANTIGO ganha as colunas novas,
verificado e idempotente. CREATE IF NOT EXISTS não faria isso."""
from __future__ import annotations

import sqlite3

from tradingagents.billing.store import SubscriptionStore
from tradingagents.job_store import JobStore


def _cols(db, table):
    with sqlite3.connect(db) as con:
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def test_webhook_events_antigo_ganha_colunas_e_paga(tmp_path):
    db = tmp_path / "subs.db"
    with sqlite3.connect(db) as con:                     # schema ANTIGO
        con.execute("CREATE TABLE webhook_events (charge_id TEXT PRIMARY KEY, "
                    "tenant_id TEXT NOT NULL, plan_code TEXT NOT NULL, "
                    "amount_cents INTEGER NOT NULL, processed_at TEXT NOT NULL)")
        con.commit()
    st = SubscriptionStore(db)                           # roda migração no _init
    novas = {"currency", "provider", "event_id", "correlation", "payload_hash", "fingerprint"}
    assert novas <= _cols(db, "webhook_events")
    ok, sub = st.record_paid_charge("c1", "t", "dz23_monthly", 15000, "BRL")
    assert ok and sub.status == "active"                 # pagamento funciona pós-migração


def test_jobs_antigo_ganha_cancel_requested(tmp_path):
    db = tmp_path / "jobs.db"
    with sqlite3.connect(db) as con:                     # schema ANTIGO (sem a coluna)
        con.execute("CREATE TABLE jobs(id TEXT PRIMARY KEY, idem TEXT NOT NULL, "
                    "ticker TEXT NOT NULL, trade_date TEXT NOT NULL, status TEXT NOT NULL, "
                    "error TEXT, decision TEXT, result TEXT, created REAL NOT NULL, "
                    "updated REAL NOT NULL, tenant_id TEXT NOT NULL)")
        con.execute("CREATE UNIQUE INDEX idx_jobs_tenant_idem ON jobs(tenant_id, idem)")
        con.commit()
    js = JobStore(db)
    assert "cancel_requested" in _cols(db, "jobs")
    js.insert(job_id="a", idem="i", ticker="BTC", trade_date="2026-01-01", tenant_id="t")
    assert js.request_cancel("a", "t") == 1 and js.is_cancelled("a", "t") is True


def test_migracao_idempotente_registra_versao(tmp_path):
    db = tmp_path / "subs.db"
    SubscriptionStore(db)
    SubscriptionStore(db)                                # 2ª vez: sem erro, sem duplicar
    with sqlite3.connect(db) as con:
        vs = sorted(r[0] for r in con.execute("SELECT version FROM schema_migrations"))
    assert vs == [1, 2]      # v1 colunas, v2 índice único (provider,event_id)

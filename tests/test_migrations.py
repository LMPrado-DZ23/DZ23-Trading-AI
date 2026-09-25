"""Migrações REAIS de schema (§1): um banco ANTIGO ganha as colunas novas,
verificado e idempotente. CREATE IF NOT EXISTS não faria isso."""
from __future__ import annotations

import sqlite3

import pytest

from tradingagents.billing.store import SubscriptionStore
from tradingagents.job_store import JobStore
from tradingagents.tenancy import TenantStore


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
        vs = sorted(r[0] for r in con.execute(
            "SELECT version FROM schema_versions WHERE component='billing'"))
    assert vs == [1, 2]      # v1 colunas, v2 índice único (provider,event_id)


def _legacy_shared_db(db):
    """Banco ANTIGO compartilhado: tabelas sem as colunas novas + um registro
    GLOBAL legado version=1 (o estado que causava a colisão)."""
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE users(id TEXT PRIMARY KEY, email TEXT, "
                    "password_hash TEXT, created_at REAL)")
        con.execute("CREATE TABLE webhook_events(charge_id TEXT PRIMARY KEY, "
                    "tenant_id TEXT NOT NULL, plan_code TEXT NOT NULL, "
                    "amount_cents INTEGER NOT NULL, processed_at TEXT NOT NULL)")
        con.execute("CREATE TABLE jobs(id TEXT PRIMARY KEY, idem TEXT NOT NULL, "
                    "ticker TEXT NOT NULL, trade_date TEXT NOT NULL, status TEXT, "
                    "error TEXT, decision TEXT, result TEXT, created REAL NOT NULL, "
                    "updated REAL NOT NULL, tenant_id TEXT NOT NULL)")
        con.execute("CREATE UNIQUE INDEX idx_jobs_tenant_idem ON jobs(tenant_id, idem)")
        con.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, "
                    "applied_at REAL NOT NULL)")
        con.execute("INSERT INTO schema_migrations(version,applied_at) VALUES(1, 0)")
        con.commit()


def _init_all(db, order):
    builders = {
        "jobs": lambda: JobStore(db),
        "identity": lambda: TenantStore(db, signing_key="k" * 40),
        "billing": lambda: SubscriptionStore(db),
    }
    for name in order:
        builders[name]()


@pytest.mark.parametrize("order", [
    ("jobs", "identity", "billing"),          # ordem real do serviço
    ("billing", "identity", "jobs"),          # ordem alternativa
    ("identity", "billing", "jobs"),
])
def test_migracoes_por_componente_nao_colidem(tmp_path, order):
    # §6: banco ANTIGO compartilhado + registro legado version=1 não faz um
    # componente pular a própria migração. Cada domínio migra sob seu component.
    db = tmp_path / "shared.db"
    _legacy_shared_db(db)
    _init_all(db, order)
    assert "cancel_requested" in _cols(db, "jobs")
    assert "oauth_only" in _cols(db, "users")
    assert "provider" in _cols(db, "webhook_events")     # billing NÃO falha mais
    # e cada componente registrou a própria versão, isolado do legado global
    with sqlite3.connect(db) as con:
        comps = {r[0] for r in con.execute("SELECT DISTINCT component FROM schema_versions")}
    assert {"jobs", "identity", "billing"} <= comps


def test_migracoes_idempotentes_em_reinicio(tmp_path):
    # §6: reiniciar tudo de novo (repetição) não quebra nem duplica.
    db = tmp_path / "shared.db"
    _legacy_shared_db(db)
    _init_all(db, ("jobs", "identity", "billing"))
    _init_all(db, ("jobs", "identity", "billing"))       # 2ª rodada idempotente
    assert "provider" in _cols(db, "webhook_events")
    with sqlite3.connect(db) as con:
        n = con.execute("SELECT COUNT(*) FROM schema_versions").fetchone()[0]
    assert n == 4       # jobs:1 + identity:1 + billing:2, sem duplicar

import sqlite3

from tradingagents.job_store import JobStore


def test_sqlite_job_store_is_tenant_scoped_and_idempotent(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    store.insert(job_id="a1", idem="idem-1", ticker="BTC-USD", trade_date="2026-01-01", tenant_id="tenant-a")
    assert store.find_idempotent("tenant-a", "idem-1") == "a1"
    assert store.find_idempotent("tenant-b", "idem-1") is None
    assert store.list("tenant-a")[0]["id"] == "a1"
    assert store.get("a1", "tenant-b") is None
    store.update("a1", status="completed", result='{"ok": true}')
    assert store.get("a1", "tenant-a")["result"] == {"ok": True}
    assert store.healthcheck() == {"backend": "sqlite", "status": "ok"}


def test_legacy_sqlite_schema_is_migrated(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE jobs(
            id TEXT PRIMARY KEY, idem TEXT UNIQUE, ticker TEXT, trade_date TEXT,
            status TEXT, error TEXT, decision TEXT, result TEXT,
            created REAL, updated REAL, tenant_id TEXT NOT NULL DEFAULT 'legacy')""")
        conn.execute("INSERT INTO jobs VALUES ('old','same','BTC','2026-01-01','queued',NULL,NULL,NULL,1,1,'tenant-a')")
    store = JobStore(path)
    store.insert(job_id="new", idem="same", ticker="ETH", trade_date="2026-01-01", tenant_id="tenant-b")
    assert store.find_idempotent("tenant-b", "same") == "new"

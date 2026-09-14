from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class JobStore:
    """Durable tenant-scoped job ledger for local and Enterprise deployments."""

    def __init__(self, sqlite_path: Path, database_url: str | None = None):
        self.backend = "postgres" if database_url else "sqlite"
        self._dsn = database_url
        self._sqlite_path = sqlite_path
        if self.backend == "postgres":
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("psycopg[binary] é necessário para DATABASE_URL") from exc
            self._psycopg = psycopg
            self._dict_row = dict_row
        self._init()

    def _connect(self):
        if self.backend == "postgres":
            return self._psycopg.connect(self._dsn, row_factory=self._dict_row)
        conn = sqlite3.connect(self._sqlite_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _sql(self, query: str) -> str:
        return query.replace("%s", "?") if self.backend == "sqlite" else query

    def _init(self):
        with self._connect() as c:
            if self.backend == "sqlite":
                legacy = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone()
                if legacy and "idem TEXT UNIQUE" in (legacy[0] or ""):
                    c.execute("ALTER TABLE jobs RENAME TO jobs_legacy")
            c.execute(self._sql("""
                CREATE TABLE IF NOT EXISTS jobs(
                    id TEXT PRIMARY KEY, idem TEXT NOT NULL, ticker TEXT NOT NULL,
                    trade_date TEXT NOT NULL, status TEXT NOT NULL, error TEXT,
                    decision TEXT, result TEXT, created DOUBLE PRECISION NOT NULL,
                    updated DOUBLE PRECISION NOT NULL, tenant_id TEXT NOT NULL
                )
            """))
            c.execute(self._sql("CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_tenant_idem ON jobs(tenant_id, idem)"))
            c.execute(self._sql("CREATE INDEX IF NOT EXISTS idx_jobs_tenant_created ON jobs(tenant_id, created DESC)"))
            if self.backend == "sqlite" and legacy and "idem TEXT UNIQUE" in (legacy[0] or ""):
                c.execute("""INSERT INTO jobs(id,idem,ticker,trade_date,status,error,decision,result,created,updated,tenant_id)
                    SELECT id,idem,ticker,trade_date,status,error,decision,result,created,updated,tenant_id
                    FROM jobs_legacy""")
                c.execute("DROP TABLE jobs_legacy")

    def find_idempotent(self, tenant_id: str, idem: str) -> str | None:
        with self._connect() as c:
            row = c.execute(self._sql("SELECT id FROM jobs WHERE idem=%s AND tenant_id=%s"), (idem, tenant_id)).fetchone()
        return row["id"] if row else None

    def insert(self, *, job_id: str, idem: str, ticker: str, trade_date: str, tenant_id: str) -> None:
        now = time.time()
        with self._connect() as c:
            c.execute(self._sql("""INSERT INTO jobs(id,idem,ticker,trade_date,status,created,updated,tenant_id)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s)"""), (job_id, idem, ticker, trade_date, "queued", now, now, tenant_id))

    def update(self, job_id: str, **values: Any) -> None:
        values["updated"] = time.time()
        keys = list(values)
        query = f"UPDATE jobs SET {','.join(f'{key}=%s' for key in keys)} WHERE id=%s"
        with self._connect() as c:
            c.execute(self._sql(query), (*[values[key] for key in keys], job_id))

    def list(self, tenant_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as c:
            rows = c.execute(self._sql("""SELECT id,ticker,trade_date,status,decision,created
                FROM jobs WHERE tenant_id=%s ORDER BY created DESC LIMIT %s"""), (tenant_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def get(self, job_id: str, tenant_id: str) -> dict[str, Any] | None:
        with self._connect() as c:
            row = c.execute(self._sql("SELECT * FROM jobs WHERE id=%s AND tenant_id=%s"), (job_id, tenant_id)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["result"] = json.loads(result["result"]) if result.get("result") else None
        return result

    def status(self, job_id: str, tenant_id: str) -> str | None:
        with self._connect() as c:
            row = c.execute(self._sql("SELECT status FROM jobs WHERE id=%s AND tenant_id=%s"), (job_id, tenant_id)).fetchone()
        return row["status"] if row else None

    def healthcheck(self) -> dict[str, str]:
        with self._connect() as c:
            c.execute(self._sql("SELECT 1")).fetchone()
        return {"backend": self.backend, "status": "ok"}

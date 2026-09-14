"""Persistência de assinaturas (uma linha por inquilino).

Usa o mesmo banco do resto: Postgres quando houver DATABASE_URL, senão
SQLite no volume. Nenhum segredo entra aqui — só plano, status e validade.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .woovi import Subscription, start_trial


class SubscriptionStore:
    def __init__(self, sqlite_path: str | Path, database_url: str | None = None):
        self.sqlite_path = str(sqlite_path)
        self.dsn = (database_url or "").strip() or None
        self._init()

    def _connect(self):
        if self.dsn:
            import psycopg
            from psycopg.rows import dict_row
            return psycopg.connect(self.dsn, row_factory=dict_row)
        conn = sqlite3.connect(self.sqlite_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        ddl = ("CREATE TABLE IF NOT EXISTS subscriptions ("
               "tenant_id TEXT PRIMARY KEY, plan_code TEXT NOT NULL, "
               "status TEXT NOT NULL, expires_at TEXT NOT NULL, "
               "last_charge_id TEXT DEFAULT '')")
        with self._connect() as c:
            if self.dsn:
                with c.cursor() as cur:
                    cur.execute(ddl)
            else:
                c.execute(ddl)
                c.commit()

    def get(self, tenant_id: str) -> Subscription | None:
        with self._connect() as c:
            if self.dsn:
                with c.cursor() as cur:
                    cur.execute("SELECT * FROM subscriptions WHERE tenant_id=%s",
                                (tenant_id,))
                    row = cur.fetchone()
            else:
                row = c.execute("SELECT * FROM subscriptions WHERE tenant_id=?",
                                (tenant_id,)).fetchone()
        return Subscription.from_row(dict(row)) if row else None

    def save(self, sub: Subscription) -> Subscription:
        r = sub.to_row()
        with self._connect() as c:
            if self.dsn:
                with c.cursor() as cur:
                    cur.execute(
                        "INSERT INTO subscriptions (tenant_id,plan_code,status,"
                        "expires_at,last_charge_id) VALUES (%s,%s,%s,%s,%s) "
                        "ON CONFLICT (tenant_id) DO UPDATE SET plan_code=EXCLUDED.plan_code,"
                        "status=EXCLUDED.status,expires_at=EXCLUDED.expires_at,"
                        "last_charge_id=EXCLUDED.last_charge_id",
                        (r["tenant_id"], r["plan_code"], r["status"],
                         r["expires_at"], r["last_charge_id"]))
            else:
                c.execute(
                    "INSERT INTO subscriptions (tenant_id,plan_code,status,"
                    "expires_at,last_charge_id) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(tenant_id) DO UPDATE SET plan_code=excluded.plan_code,"
                    "status=excluded.status,expires_at=excluded.expires_at,"
                    "last_charge_id=excluded.last_charge_id",
                    (r["tenant_id"], r["plan_code"], r["status"],
                     r["expires_at"], r["last_charge_id"]))
                c.commit()
        return sub

    def ensure(self, tenant_id: str) -> Subscription:
        """Todo inquilino novo começa em teste — ninguém fica travado na porta."""
        sub = self.get(tenant_id)
        if sub is None:
            sub = self.save(start_trial(tenant_id))
        return sub


__all__ = ["SubscriptionStore"]

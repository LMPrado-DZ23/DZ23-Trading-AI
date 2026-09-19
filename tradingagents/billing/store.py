"""Persistência de assinaturas (uma linha por inquilino).

FONTE CANÔNICA ÚNICA: Postgres quando houver DATABASE_URL, senão SQLite no
volume — nunca os dois como fontes concorrentes. Nenhum segredo entra aqui.

FAIL-CLOSED: se o banco canônico estiver indisponível, a leitura levanta
`BillingUnavailable` — quem consome trata como somente-leitura. NUNCA se
inventa um trial por outage/restart. O trial só começa por ação explícita
(`start_trial_for`), idempotente e exatamente-uma-vez por inquilino.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .woovi import Subscription, apply_paid_event, start_trial


class BillingUnavailable(RuntimeError):
    """Banco de cobrança indisponível — o estado da conta é DESCONHECIDO."""


class SubscriptionStore:
    def __init__(self, sqlite_path: str | Path, database_url: str | None = None):
        self.sqlite_path = str(sqlite_path)
        self.dsn = (database_url or "").strip() or None
        self._init()

    def _connect(self) -> Any:      # sqlite3.Connection | psycopg.Connection
        if self.dsn:
            import psycopg
            from psycopg.rows import dict_row
            return psycopg.connect(self.dsn, row_factory=dict_row)
        conn = sqlite3.connect(self.sqlite_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        ddl_sub = ("CREATE TABLE IF NOT EXISTS subscriptions ("
                   "tenant_id TEXT PRIMARY KEY, plan_code TEXT NOT NULL, "
                   "status TEXT NOT NULL, expires_at TEXT NOT NULL, "
                   "last_charge_id TEXT DEFAULT '')")
        # Ledger DURÁVEL de eventos de webhook: idempotência/replay por
        # charge_id (chave primária) — um pagamento não credita duas vezes,
        # mesmo com reenvio da Woovi. Guarda também o valor pago (auditoria).
        amount_type = "BIGINT" if self.dsn else "INTEGER"
        ddl_evt = ("CREATE TABLE IF NOT EXISTS webhook_events ("
                   "charge_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, "
                   "plan_code TEXT NOT NULL, amount_cents " + amount_type + " NOT NULL, "
                   "processed_at TEXT NOT NULL)")
        with self._connect() as c:
            if self.dsn:
                with c.cursor() as cur:
                    cur.execute(ddl_sub)
                    cur.execute(ddl_evt)
            else:
                c.execute(ddl_sub)
                c.execute(ddl_evt)
                c.commit()

    def get(self, tenant_id: str) -> Subscription | None:
        """Lê a assinatura. Banco fora do ar => BillingUnavailable (fail-closed)."""
        try:
            with self._connect() as c:
                if self.dsn:
                    with c.cursor() as cur:
                        cur.execute(
                            "SELECT * FROM subscriptions WHERE tenant_id=%s",
                            (tenant_id,))
                        row = cur.fetchone()
                else:
                    row = c.execute(
                        "SELECT * FROM subscriptions WHERE tenant_id=?",
                        (tenant_id,)).fetchone()
        except Exception as exc:                     # noqa: BLE001 - degrada seguro
            raise BillingUnavailable(str(exc)) from exc
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

    def start_trial_for(self, tenant_id: str) -> Subscription:
        """Inicia o teste de 24h EXPLICITAMENTE, exatamente uma vez.

        Idempotente e seguro sob concorrência: um INSERT condicional
        (ON CONFLICT DO NOTHING) garante que só a PRIMEIRA chamada cria o
        trial. Se já existe QUALQUER linha (trial em andamento, expirado, pago
        ou suspenso), devolve a existente SEM renovar — restart/outage/nova
        instância não recriam trial, e TRIAL_EXPIRED nunca volta a TRIALING.
        """
        novo = start_trial(tenant_id)
        r = novo.to_row()
        try:
            with self._connect() as c:
                if self.dsn:
                    with c.cursor() as cur:
                        cur.execute(
                            "INSERT INTO subscriptions (tenant_id,plan_code,"
                            "status,expires_at,last_charge_id) "
                            "VALUES (%s,%s,%s,%s,%s) "
                            "ON CONFLICT (tenant_id) DO NOTHING",
                            (r["tenant_id"], r["plan_code"], r["status"],
                             r["expires_at"], r["last_charge_id"]))
                else:
                    c.execute(
                        "INSERT INTO subscriptions (tenant_id,plan_code,status,"
                        "expires_at,last_charge_id) VALUES (?,?,?,?,?) "
                        "ON CONFLICT(tenant_id) DO NOTHING",
                        (r["tenant_id"], r["plan_code"], r["status"],
                         r["expires_at"], r["last_charge_id"]))
                    c.commit()
        except Exception as exc:                     # noqa: BLE001
            raise BillingUnavailable(str(exc)) from exc
        # Relê a fonte canônica: se houve conflito, devolve a assinatura que já
        # existia (nunca a nova). Se criou, devolve a recém-inserida.
        atual = self.get(tenant_id)
        return atual if atual is not None else novo

    def record_paid_charge(self, charge_id: str, tenant_id: str, plan_code: str,
                           amount_cents: int,
                           now: datetime | None = None) -> tuple[bool, Subscription | None]:
        """Aplica um pagamento confirmado com IDEMPOTÊNCIA DURÁVEL, ATÔMICA.

        Numa ÚNICA transação: registra o `charge_id` no ledger (replay-safe) e
        credita a assinatura. Se o charge já foi processado (reenvio da Woovi),
        devolve (False, assinatura_atual) SEM recreditar. Se o banco falhar,
        levanta `BillingUnavailable` e NADA é commitado — o webhook responde 5xx
        e a Woovi reenvia; o reprocessamento é limpo (transação tudo-ou-nada).

        Retorna (aplicado, assinatura). `aplicado=False` == replay/idempotente.
        """
        now = now or datetime.now(timezone.utc)
        stamp = now.isoformat()
        try:
            with self._connect() as c:
                if self.dsn:
                    with c.cursor() as cur:
                        cur.execute(
                            "INSERT INTO webhook_events (charge_id,tenant_id,"
                            "plan_code,amount_cents,processed_at) "
                            "VALUES (%s,%s,%s,%s,%s) "
                            "ON CONFLICT (charge_id) DO NOTHING",
                            (charge_id, tenant_id, plan_code, amount_cents, stamp))
                        aplicado = cur.rowcount == 1
                        cur.execute("SELECT * FROM subscriptions WHERE tenant_id=%s",
                                    (tenant_id,))
                        row = cur.fetchone()
                        atual = Subscription.from_row(dict(row)) if row else None
                        if not aplicado:
                            return False, atual
                        nova = apply_paid_event(atual, tenant_id, plan_code, charge_id, now)
                        r = nova.to_row()
                        cur.execute(
                            "INSERT INTO subscriptions (tenant_id,plan_code,status,"
                            "expires_at,last_charge_id) VALUES (%s,%s,%s,%s,%s) "
                            "ON CONFLICT (tenant_id) DO UPDATE SET "
                            "plan_code=EXCLUDED.plan_code,status=EXCLUDED.status,"
                            "expires_at=EXCLUDED.expires_at,"
                            "last_charge_id=EXCLUDED.last_charge_id",
                            (r["tenant_id"], r["plan_code"], r["status"],
                             r["expires_at"], r["last_charge_id"]))
                else:
                    cur = c.execute(
                        "INSERT INTO webhook_events (charge_id,tenant_id,plan_code,"
                        "amount_cents,processed_at) VALUES (?,?,?,?,?) "
                        "ON CONFLICT(charge_id) DO NOTHING",
                        (charge_id, tenant_id, plan_code, amount_cents, stamp))
                    aplicado = cur.rowcount == 1
                    row = c.execute("SELECT * FROM subscriptions WHERE tenant_id=?",
                                    (tenant_id,)).fetchone()
                    atual = Subscription.from_row(dict(row)) if row else None
                    if not aplicado:
                        return False, atual
                    nova = apply_paid_event(atual, tenant_id, plan_code, charge_id, now)
                    r = nova.to_row()
                    c.execute(
                        "INSERT INTO subscriptions (tenant_id,plan_code,status,"
                        "expires_at,last_charge_id) VALUES (?,?,?,?,?) "
                        "ON CONFLICT(tenant_id) DO UPDATE SET plan_code=excluded.plan_code,"
                        "status=excluded.status,expires_at=excluded.expires_at,"
                        "last_charge_id=excluded.last_charge_id",
                        (r["tenant_id"], r["plan_code"], r["status"],
                         r["expires_at"], r["last_charge_id"]))
                    c.commit()
        except Exception as exc:                     # noqa: BLE001 - degrada seguro
            raise BillingUnavailable(str(exc)) from exc
        return True, nova


__all__ = ["SubscriptionStore", "BillingUnavailable"]

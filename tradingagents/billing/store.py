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


class ChargeConflict(RuntimeError):
    """Mesmo charge_id chegou com payload DIVERGENTE (tenant/plano/valor/moeda).
    NÃO é replay — é conflito. O webhook responde 409, nunca 'replay ok'."""

    def __init__(self, charge_id: str, seen: str, new: str):
        super().__init__(f"charge {charge_id} já visto com payload diferente")
        self.charge_id, self.seen, self.new = charge_id, seen, new


def _fingerprint(tenant_id: str, plan_code: str, amount_cents: int, currency: str) -> str:
    return f"{tenant_id}|{plan_code}|{amount_cents}|{currency.upper()}"


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
                   "currency TEXT NOT NULL DEFAULT 'BRL', "
                   "provider TEXT NOT NULL DEFAULT 'woovi', "
                   "event_id TEXT NOT NULL DEFAULT '', "
                   "correlation TEXT NOT NULL DEFAULT '', "
                   "payload_hash TEXT NOT NULL DEFAULT '', "
                   "fingerprint TEXT NOT NULL DEFAULT '', "
                   "processed_at TEXT NOT NULL)")
        # Conflitos DURÁVEIS (incidentes): mesmo charge_id com payload divergente.
        ddl_conf = ("CREATE TABLE IF NOT EXISTS webhook_conflicts ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, charge_id TEXT NOT NULL, "
                    "seen_fingerprint TEXT NOT NULL, new_fingerprint TEXT NOT NULL, "
                    "provider TEXT NOT NULL DEFAULT '', event_id TEXT NOT NULL DEFAULT '', "
                    "at TEXT NOT NULL)") if not self.dsn else (
            "CREATE TABLE IF NOT EXISTS webhook_conflicts ("
            "id BIGSERIAL PRIMARY KEY, charge_id TEXT NOT NULL, "
            "seen_fingerprint TEXT NOT NULL, new_fingerprint TEXT NOT NULL, "
            "provider TEXT NOT NULL DEFAULT '', event_id TEXT NOT NULL DEFAULT '', "
            "at TEXT NOT NULL)")
        with self._connect() as c:
            if self.dsn:
                with c.cursor() as cur:
                    cur.execute(ddl_sub)
                    cur.execute(ddl_evt)
                    cur.execute(ddl_conf)
            else:
                c.execute(ddl_sub)
                c.execute(ddl_evt)
                c.execute(ddl_conf)
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
                           amount_cents: int, currency: str = "BRL", *,
                           provider: str = "woovi", event_id: str = "",
                           correlation: str = "", payload_hash: str = "",
                           now: datetime | None = None) -> tuple[bool, Subscription | None]:
        """Aplica um pagamento confirmado com IDEMPOTÊNCIA DURÁVEL, ATÔMICA.

        Persiste provider, event_id, charge_id, tenant, plano, valor, moeda,
        correlation e payload_hash. Reenvio EXATO do mesmo charge => replay
        (False, assinatura_atual), sem recreditar. Mesmo `charge_id` com payload
        DIVERGENTE => grava um CONFLITO DURÁVEL (webhook_conflicts, incidente) e
        levanta `ChargeConflict` (o webhook responde 409, NENHUMA ativação).
        Banco fora do ar => `BillingUnavailable` e nada é commitado (5xx; reprocessa).
        """
        now = now or datetime.now(timezone.utc)
        stamp = now.isoformat()
        fp = _fingerprint(tenant_id, plan_code, amount_cents, currency)
        cols = ("charge_id,tenant_id,plan_code,amount_cents,currency,provider,"
                "event_id,correlation,payload_hash,fingerprint,processed_at")
        vals = (charge_id, tenant_id, plan_code, amount_cents, currency.upper(),
                provider, event_id, correlation, payload_hash, fp, stamp)
        try:
            with self._connect() as c:
                if self.dsn:
                    with c.cursor() as cur:
                        cur.execute(f"INSERT INTO webhook_events ({cols}) VALUES "
                                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                                    "ON CONFLICT (charge_id) DO NOTHING", vals)
                        aplicado = cur.rowcount == 1
                        if not aplicado:
                            cur.execute("SELECT fingerprint FROM webhook_events "
                                        "WHERE charge_id=%s", (charge_id,))
                            self._check_fp(cur.fetchone(), fp, charge_id)
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
                    q = (f"INSERT INTO webhook_events ({cols}) VALUES "
                         "(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(charge_id) DO NOTHING")
                    cur = c.execute(q, vals)
                    aplicado = cur.rowcount == 1
                    if not aplicado:
                        got = c.execute("SELECT fingerprint FROM webhook_events "
                                        "WHERE charge_id=?", (charge_id,)).fetchone()
                        self._check_fp(got, fp, charge_id)
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
        except ChargeConflict as conf:
            self._record_conflict(conf, provider, event_id, stamp)   # incidente durável
            raise
        except Exception as exc:                     # noqa: BLE001 - degrada seguro
            raise BillingUnavailable(str(exc)) from exc
        return True, nova

    @staticmethod
    def _check_fp(row: Any, esperado: str, charge_id: str) -> None:
        guardado = (dict(row).get("fingerprint") if row else None)
        if guardado is not None and guardado != esperado:
            raise ChargeConflict(charge_id, guardado, esperado)

    def _record_conflict(self, conf: ChargeConflict, provider: str, event_id: str,
                         stamp: str) -> None:
        """Grava o conflito num ledger DURÁVEL (incidente). Best-effort: uma
        falha ao registrar não deve mascarar o 409 do conflito."""
        q = ("INSERT INTO webhook_conflicts (charge_id,seen_fingerprint,"
             "new_fingerprint,provider,event_id,at) VALUES (%s,%s,%s,%s,%s,%s)")
        params = (conf.charge_id, conf.seen, conf.new, provider, event_id, stamp)
        try:
            with self._connect() as c:
                if self.dsn:
                    with c.cursor() as cur:
                        cur.execute(q, params)
                else:
                    c.execute(q.replace("%s", "?"), params)
                    c.commit()
        except Exception:                            # noqa: BLE001
            pass

    def count_conflicts(self, charge_id: str) -> int:
        with self._connect() as c:
            if self.dsn:
                with c.cursor() as cur:
                    cur.execute("SELECT COUNT(*) AS n FROM webhook_conflicts WHERE charge_id=%s",
                                (charge_id,))
                    row = cur.fetchone()
            else:
                row = c.execute("SELECT COUNT(*) AS n FROM webhook_conflicts WHERE charge_id=?",
                                (charge_id,)).fetchone()
        return int(dict(row)["n"]) if row else 0


__all__ = ["SubscriptionStore", "BillingUnavailable", "ChargeConflict"]

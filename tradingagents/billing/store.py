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

from ..dbschema import SCHEMA_LOCK_KEY, add_column_if_missing, run_migrations
from .woovi import Subscription, apply_paid_event, start_trial

# Namespace FIXO do lock por-tenant. hashtextextended(text, int8) devolve BIGINT
# (64 bits), com semente fixa: chave determinística, server-side, sem colisão de
# namespace com o lock de schema (SCHEMA_LOCK_KEY) e sem usar hash() do Python.
_TENANT_LOCK_SEED = 0x647A32335F544E54    # "dz23_TNT"


class BillingUnavailable(RuntimeError):
    """Banco de cobrança indisponível — o estado da conta é DESCONHECIDO."""


class ChargeConflict(RuntimeError):
    """Mesmo charge_id chegou com payload DIVERGENTE (tenant/plano/valor/moeda).
    NÃO é replay — é conflito. O webhook responde 409, nunca 'replay ok'."""

    def __init__(self, charge_id: str, seen: str, new: str):
        super().__init__(f"charge {charge_id} já visto com payload diferente")
        self.charge_id, self.seen, self.new = charge_id, seen, new


def _fingerprint(*, provider: str, event_id: str, charge_id: str, tenant_id: str,
                 plan_code: str, amount_cents: int, currency: str,
                 correlation: str) -> str:
    """Identidade SEMÂNTICA do pagamento (§3): só o conteúdo econômico que
    realmente distingue/contradiz um pagamento — provider, event_id, charge_id,
    tenant, plano, valor, moeda e correlação.

    NÃO inclui o `payload_hash` (hash do corpo BRUTO): reformatar o JSON (mesma
    semântica, bytes diferentes) não pode virar conflito nem replay recreditado.
    O `payload_hash` é guardado à parte, como EVIDÊNCIA do corpo recebido, e a
    verificação de assinatura sobre o corpo bruto acontece no webhook — aqui é só
    identidade de idempotência."""
    return "|".join([provider, event_id, charge_id, tenant_id, plan_code,
                     str(amount_cents), currency.upper(), correlation])


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

    def _integrity_errors(self) -> tuple[type[Exception], ...]:
        """Exceções de violação de UNICIDADE por dialeto — para converter a
        colisão de UNIQUE(provider,event_id) em ChargeConflict (não em 5xx)."""
        if self.dsn:
            import psycopg
            return (psycopg.errors.UniqueViolation,)
        return (sqlite3.IntegrityError,)

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
                    # Serializa a criação de tabelas entre processos (corrida de
                    # pg_type do CREATE TABLE IF NOT EXISTS no Postgres).
                    cur.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK_KEY,))
                    cur.execute(ddl_sub)
                    cur.execute(ddl_evt)
                    cur.execute(ddl_conf)
            else:
                c.execute(ddl_sub)
                c.execute(ddl_evt)
                c.execute(ddl_conf)
                c.commit()
        self._migrate()

    def _migrate(self) -> None:
        """§1: leva um `webhook_events` ANTIGO ao schema atual, versionado e
        verificado. CREATE IF NOT EXISTS não adiciona coluna a tabela existente."""
        dialect = "postgres" if self.dsn else "sqlite"
        colunas = [
            ("currency", "TEXT NOT NULL DEFAULT 'BRL'"),
            ("provider", "TEXT NOT NULL DEFAULT 'woovi'"),
            ("event_id", "TEXT NOT NULL DEFAULT ''"),
            ("correlation", "TEXT NOT NULL DEFAULT ''"),
            ("payload_hash", "TEXT NOT NULL DEFAULT ''"),
            ("fingerprint", "TEXT NOT NULL DEFAULT ''"),
        ]

        def _m1(c: Any, d: str) -> None:
            for nome, ddl in colunas:
                add_column_if_missing(c, d, "webhook_events", nome, ddl)

        def _m2(c: Any, d: str) -> None:
            # §3: UNICIDADE de (provider,event_id) — ÍNDICE PARCIAL que ignora o
            # event_id legado vazio ('') para não conflitar com dados antigos.
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_webhook_events_provider_event "
                      "ON webhook_events(provider, event_id) WHERE event_id <> ''")

        run_migrations(self._connect, dialect, [(1, _m1), (2, _m2)], component="billing")

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
        fp = _fingerprint(provider=provider, event_id=event_id, charge_id=charge_id,
                          tenant_id=tenant_id, plan_code=plan_code,
                          amount_cents=amount_cents, currency=currency,
                          correlation=correlation)
        cols = ("charge_id,tenant_id,plan_code,amount_cents,currency,provider,"
                "event_id,correlation,payload_hash,fingerprint,processed_at")
        vals = (charge_id, tenant_id, plan_code, amount_cents, currency.upper(),
                provider, event_id, correlation, payload_hash, fp, stamp)
        try:
            with self._connect() as c:
                if self.dsn:
                    with c.cursor() as cur:
                        # §2: trava por TENANT durante a transação — dois charges
                        # distintos do mesmo tenant se serializam (sem lost update).
                        cur.execute(
                            "SELECT pg_advisory_xact_lock(hashtextextended(%s, %s))",
                            (tenant_id, _TENANT_LOCK_SEED))
                        try:
                            cur.execute(f"INSERT INTO webhook_events ({cols}) VALUES "
                                        "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                                        "ON CONFLICT (charge_id) DO NOTHING", vals)
                        except self._integrity_errors() as exc:   # UNIQUE(provider,event_id)
                            raise ChargeConflict(charge_id, "event_id-unico", fp) from exc
                        aplicado = cur.rowcount == 1
                        if not aplicado:
                            cur.execute("SELECT fingerprint FROM webhook_events "
                                        "WHERE charge_id=%s", (charge_id,))
                            self._check_fp(cur.fetchone(), fp, charge_id)
                        if aplicado and event_id:      # §3: event_id reusado sob OUTRO charge
                            cur.execute("SELECT 1 FROM webhook_events WHERE event_id=%s "
                                        "AND charge_id<>%s LIMIT 1", (event_id, charge_id))
                            if cur.fetchone():
                                raise ChargeConflict(charge_id, "event_id-reusado", fp)
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
                    # §2: BEGIN IMMEDIATE pega o write-lock ANTES do SELECT, então
                    # um segundo pagamento concorrente espera, relê a expiração já
                    # atualizada e SOMA o período (sem lost update). SQLite é
                    # single-writer; isto serializa writers com segurança.
                    c.execute("BEGIN IMMEDIATE")
                    q = (f"INSERT INTO webhook_events ({cols}) VALUES "
                         "(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(charge_id) DO NOTHING")
                    try:
                        cur = c.execute(q, vals)
                    except self._integrity_errors() as exc:   # UNIQUE(provider,event_id)
                        raise ChargeConflict(charge_id, "event_id-unico", fp) from exc
                    aplicado = cur.rowcount == 1
                    if not aplicado:
                        got = c.execute("SELECT fingerprint FROM webhook_events "
                                        "WHERE charge_id=?", (charge_id,)).fetchone()
                        self._check_fp(got, fp, charge_id)
                    if aplicado and event_id:          # §3: event_id reusado sob OUTRO charge
                        dup = c.execute("SELECT 1 FROM webhook_events WHERE event_id=? "
                                        "AND charge_id<>? LIMIT 1", (event_id, charge_id)).fetchone()
                        if dup:
                            raise ChargeConflict(charge_id, "event_id-reusado", fp)
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
            # §3: o conflito NÃO é best-effort. Se o incidente não persistir,
            # respondemos 5xx (BillingUnavailable), nunca um 409 "durável" falso.
            try:
                self._record_conflict(conf, provider, event_id, stamp)
            except Exception as exc:                 # noqa: BLE001
                raise BillingUnavailable(
                    f"conflito não pôde ser persistido: {exc}") from exc
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
        """Grava o conflito num ledger DURÁVEL (incidente). NÃO é best-effort:
        se levantar, o chamador transforma em 5xx (nunca 409 'durável' falso)."""
        q = ("INSERT INTO webhook_conflicts (charge_id,seen_fingerprint,"
             "new_fingerprint,provider,event_id,at) VALUES (%s,%s,%s,%s,%s,%s)")
        params = (conf.charge_id, conf.seen, conf.new, provider, event_id, stamp)
        with self._connect() as c:
            if self.dsn:
                with c.cursor() as cur:
                    cur.execute(q, params)
            else:
                c.execute(q.replace("%s", "?"), params)
                c.commit()

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

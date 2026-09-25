"""Provas ESTRUTURAIS em PostgreSQL real (§1/§2/§3).

Rodam só quando DZ23_TEST_DATABASE_URL aponta para um Postgres (o CI sobe um
container). Cobrem: migração de schema antigo COM dados, concorrência de
migrações, dois pagamentos simultâneos por CONEXÕES separadas (advisory lock,
sem lost update) e unicidade de event_id sob concorrência.
"""
from __future__ import annotations

import os
import threading

import pytest

DSN = os.environ.get("DZ23_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="DZ23_TEST_DATABASE_URL não definido")


def _reset() -> None:
    import psycopg
    with psycopg.connect(DSN) as c:
        c.execute("DROP TABLE IF EXISTS webhook_conflicts, webhook_events, "
                  "subscriptions, schema_migrations CASCADE")
        c.commit()


def _store():
    from tradingagents.billing.store import SubscriptionStore
    return SubscriptionStore("unused.db", DSN)     # dsn => Postgres


@pytest.fixture()
def clean_pg():
    _reset()
    yield
    _reset()


def test_migracao_pg_schema_antigo_com_dados(clean_pg):
    import psycopg
    with psycopg.connect(DSN) as c:
        c.execute("CREATE TABLE webhook_events (charge_id TEXT PRIMARY KEY, "
                  "tenant_id TEXT NOT NULL, plan_code TEXT NOT NULL, "
                  "amount_cents BIGINT NOT NULL, processed_at TEXT NOT NULL)")
        c.execute("INSERT INTO webhook_events(charge_id,tenant_id,plan_code,amount_cents,"
                  "processed_at) VALUES('old','t','dz23_monthly',15000,'2026-01-01')")
        c.commit()
    st = _store()                                  # roda a migração no _init
    with psycopg.connect(DSN) as c:
        cols = {r[0] for r in c.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='webhook_events'")}
        n_old = c.execute("SELECT COUNT(*) FROM webhook_events WHERE charge_id='old'").fetchone()[0]
    assert {"currency", "provider", "event_id", "correlation", "payload_hash",
            "fingerprint"} <= cols
    assert n_old == 1                              # dado antigo intacto
    ok, sub = st.record_paid_charge("c1", "t2", "dz23_monthly", 15000, "BRL")
    assert ok and sub.status == "active"           # paga pós-migração


def test_migracoes_por_componente_pg(clean_pg):
    # §6: no MESMO banco Postgres, jobs/identidade/billing NÃO colidem — cada um
    # migra sob seu component. (O bug original era específico de Postgres, onde
    # os três compartilham o banco.)
    import psycopg

    from tradingagents.job_store import JobStore
    from tradingagents.tenancy import PostgresTenantStore
    with psycopg.connect(DSN) as c:
        c.execute("DROP TABLE IF EXISTS jobs, users, sessions, memberships, "
                  "step_up_tokens, login_failures, schema_versions, "
                  "schema_migrations CASCADE")
        c.commit()
    JobStore("unused.db", DSN)                       # component 'jobs'
    PostgresTenantStore(DSN, signing_key="k" * 40)   # component 'identity'
    _store()                                         # component 'billing'
    with psycopg.connect(DSN) as c:
        def cols(t: str) -> set:
            return {r[0] for r in c.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name=%s", (t,))}
        jobc, usrc, whc = cols("jobs"), cols("users"), cols("webhook_events")
        comps = {r[0] for r in c.execute("SELECT DISTINCT component FROM schema_versions")}
    assert "cancel_requested" in jobc
    assert "oauth_only" in usrc
    assert "provider" in whc            # billing NÃO falha com 'no such column: provider'
    assert {"jobs", "identity", "billing"} <= comps


def test_migracao_concorrente_pg(clean_pg):
    inicio = threading.Barrier(4)
    err: list[str] = []

    def init() -> None:
        try:
            inicio.wait()
            _store()
        except Exception as exc:                   # noqa: BLE001
            err.append(repr(exc))

    ts = [threading.Thread(target=init) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not err, err                            # nenhuma corrida de migração quebrou


def test_pagamentos_concorrentes_pg_somam(clean_pg):
    from tradingagents.billing.woovi import _add_months
    st = _store()
    st.record_paid_charge("c0", "t", "dz23_monthly", 15000, "BRL")
    base = st.get("t").expires_at
    inicio = threading.Barrier(2)
    err: list[str] = []

    def pay(cid: str) -> None:
        try:
            inicio.wait()
            _store().record_paid_charge(cid, "t", "dz23_monthly", 15000, "BRL")  # conexão própria
        except Exception as exc:                   # noqa: BLE001
            err.append(repr(exc))

    ts = [threading.Thread(target=pay, args=(x,)) for x in ("c1", "c2")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not err, err
    assert st.get("t").expires_at == _add_months(base, 2)   # somou 2 (sem lost update)


def test_advisory_lock_serializa_pg(clean_pg):
    # §2 DETERMINÍSTICO: record_paid_charge adquire o advisory lock POR TENANT
    # (hashtextextended(tenant, _TENANT_LOCK_SEED)) ANTES do efeito financeiro.
    # Seguramos o MESMO lock numa conexão externa; a cobrança tem de BLOQUEAR
    # até liberarmos. Sem o lock (mutante), ela NÃO bloqueia => este teste falha.
    import time

    import psycopg

    from tradingagents.billing.store import _TENANT_LOCK_SEED
    from tradingagents.billing.woovi import _add_months
    st = _store()
    st.record_paid_charge("c0", "t", "dz23_monthly", 15000, "BRL")   # base
    base = st.get("t").expires_at

    holder = psycopg.connect(DSN)                # segura o lock do tenant "t"
    holder.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, %s))",
                   ("t", _TENANT_LOCK_SEED))

    done: dict[str, float | None] = {"at": None}
    err: list[str] = []

    def pay() -> None:
        try:
            _store().record_paid_charge("c1", "t", "dz23_monthly", 15000, "BRL")
            done["at"] = time.monotonic()
        except Exception as exc:                 # noqa: BLE001
            err.append(repr(exc))

    th = threading.Thread(target=pay)
    th.start()
    time.sleep(1.0)
    bloqueado = done["at"] is None               # ainda presa no advisory lock externo
    holder.commit()                              # libera o lock (xact-scoped)
    th.join(timeout=15)
    holder.close()

    assert not err, err
    assert bloqueado, "record_paid_charge NÃO bloqueou no advisory lock por-tenant"
    assert done["at"] is not None, "cobrança não concluiu após liberar o lock"
    # depois de liberar, a 2ª cobrança soma o período (leu o valor já atualizado)
    assert st.get("t").expires_at == _add_months(base, 1)


def test_unique_index_provider_event_pg(clean_pg):
    # §3: prova ESTRUTURAL de que o índice (provider,event_id) é UNIQUE e parcial.
    # Mata mutante que troca CREATE UNIQUE INDEX por CREATE INDEX (não-único).
    import psycopg
    _store()                                   # roda a migração v2 (cria o índice)
    with psycopg.connect(DSN) as c:
        row = c.execute(
            "SELECT ix.indisunique, pg_get_expr(ix.indpred, ix.indrelid) AS pred "
            "FROM pg_class t JOIN pg_index ix ON ix.indrelid=t.oid "
            "JOIN pg_class i ON i.oid=ix.indexrelid "
            "WHERE t.relname='webhook_events' "
            "AND i.relname='ux_webhook_events_provider_event'").fetchone()
    assert row is not None, "índice ux_webhook_events_provider_event ausente"
    assert row[0] is True, "índice (provider,event_id) precisa ser UNIQUE"
    assert row[1] and "event_id" in row[1], "índice precisa ser PARCIAL (WHERE event_id<>'')"


def test_event_id_concorrente_pg_um_conflita(clean_pg):
    from tradingagents.billing.store import ChargeConflict
    _store()
    inicio = threading.Barrier(2)
    res: list[tuple[str, str]] = []

    def pay(cid: str) -> None:
        try:
            inicio.wait()
            _store().record_paid_charge(cid, "t", "dz23_monthly", 15000, "BRL", event_id="EV")
            res.append(("ok", cid))
        except ChargeConflict:
            res.append(("conflict", cid))
        except Exception as exc:                   # noqa: BLE001
            res.append(("err", repr(exc)))

    ts = [threading.Thread(target=pay, args=(x,)) for x in ("cA", "cB")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    oks = [r for r in res if r[0] == "ok"]
    conf = [r for r in res if r[0] == "conflict"]
    assert len(oks) == 1 and len(conf) == 1, res   # unicidade sob concorrência

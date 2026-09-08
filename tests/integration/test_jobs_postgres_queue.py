from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from tradingagents.platform.connections import CredentialKeyring
from tradingagents.platform.contracts import RunMode
from tradingagents.platform.identity import (
    IdentityPrincipal,
    IdentityService,
    MembershipRole,
    TokenHasher,
)
from tradingagents.platform.jobs import AnalysisJob, AnalysisJobCommand, JobError, JobService
from tradingagents.platform.llm_connections import LlmConnectionService
from tradingagents.platform.llm_provenance import build_llm_reference_snapshot
from tradingagents.platform.worker import ExecutionOutcome, WorkerConfig, WorkerRunner

APP_URL = os.environ.get("TRADINGAGENTS_TEST_POSTGRES_URL")
WORKER_URL = os.environ.get("TRADINGAGENTS_TEST_WORKER_POSTGRES_URL")
pytestmark = pytest.mark.integration


def _reference(**kwargs):
    kwargs.pop("tenant_id", None)
    return build_llm_reference_snapshot(base_url=None, **kwargs)


@pytest.mark.skipif(
    not APP_URL or not WORKER_URL,
    reason="PostgreSQL app and worker test URLs are not configured",
)
def test_postgres_jobs_enforce_tenants_and_claim_once_under_concurrency() -> None:
    app_engine = create_engine(APP_URL, pool_pre_ping=True)
    worker_engine = create_engine(WORKER_URL, pool_pre_ping=True)
    app_factory = sessionmaker(app_engine, expire_on_commit=False)
    worker_factory = sessionmaker(worker_engine, expire_on_commit=False)
    identity = IdentityService(
        app_factory,
        token_hasher=TokenHasher(b"jobs-postgres-token-pepper-value-32"),
    )
    suffix = uuid4().hex[:10]
    principals: list[IdentityPrincipal] = []
    for label in ("a", "b"):
        registration = identity.register_tenant(
            tenant_name=f"Jobs {label.upper()}",
            tenant_slug=f"jobs-pg-{label}-{suffix}",
            owner_email=f"jobs-pg-{label}-{suffix}@example.test",
            display_name=f"Owner {label.upper()}",
            password="correct horse battery staple",
        )
        principals.append(
            IdentityPrincipal(
                tenant_id=registration.tenant_id,
                user_id=registration.user_id,
                membership_id=registration.membership_id,
                session_id=uuid4(),
                role=MembershipRole.OWNER,
            )
        )

    references = SimpleNamespace(assert_reference=_reference)
    app_service = JobService(
        app_factory,
        idempotency_digest_key=b"a" * 32,
        llm_reference_validator=references,
    )
    connection_id = uuid4()
    expected: set = set()
    for index, principal in enumerate(principals):
        expected.add(
            app_service.enqueue(
                principal=principal,
                idempotency_key=f"postgres-job-{suffix}-{index}",
                command=AnalysisJobCommand(
                    symbol=("BTC/USDT", "ETH/USDT")[index],
                    asset_type="crypto",
                    trade_date="2024-01-02",
                    mode=RunMode.BACKTEST,
                    provider="openai",
                    model="gpt-5",
                    llm_connection_id=connection_id,
                    llm_credential_version=1,
                    selected_analysts=("market",),
                ),
            )
        )

    first, second = principals
    with app_factory() as db, db.begin():
        db.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(first.tenant_id)},
        )
        assert {job.tenant_id for job in db.scalars(select(AnalysisJob))} == {first.tenant_id}
        assert (
            db.scalar(select(AnalysisJob).where(AnalysisJob.tenant_id == second.tenant_id)) is None
        )

    def claim(worker_id: str):
        return JobService(
            worker_factory,
            idempotency_digest_key=b"a" * 32,
            llm_reference_validator=references,
        ).claim_next(worker_id=worker_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = tuple(pool.map(claim, ("worker-one", "worker-two")))
    claimed_ids = {claim.id for claim in claims if claim is not None}
    assert claimed_ids == expected
    assert len(claimed_ids) == 2
    cleanup = JobService(
        worker_factory,
        idempotency_digest_key=b"a" * 32,
        llm_reference_validator=references,
    )
    for claim in claims:
        assert claim is not None
        cleanup.start(claim)
        cleanup.complete(
            claim,
            result={"signal": "hold"},
            manifest=claim.run_context.manifest(),
        )

    app_engine.dispose()
    worker_engine.dispose()


@pytest.mark.skipif(not APP_URL, reason="PostgreSQL app test URL is not configured")
def test_postgres_tenant_admission_quota_is_atomic_under_concurrency() -> None:
    engine = create_engine(APP_URL, pool_pre_ping=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    identity = IdentityService(
        factory, token_hasher=TokenHasher(b"jobs-quota-token-pepper-value-32")
    )
    suffix = uuid4().hex[:10]
    registration = identity.register_tenant(
        tenant_name="Quota Race",
        tenant_slug=f"quota-race-{suffix}",
        owner_email=f"quota-race-{suffix}@example.test",
        display_name="Quota Owner",
        password="correct horse battery staple",
    )
    principal = IdentityPrincipal(
        tenant_id=registration.tenant_id,
        user_id=registration.user_id,
        membership_id=registration.membership_id,
        session_id=uuid4(),
        role=MembershipRole.OWNER,
    )
    barrier = threading.Barrier(2)

    def enqueue(index: int) -> str:
        service = JobService(
            factory,
            idempotency_digest_key=b"q" * 32,
            llm_reference_validator=SimpleNamespace(assert_reference=_reference),
            max_active_jobs_per_tenant=1,
        )
        barrier.wait()
        try:
            service.enqueue(
                principal=principal,
                idempotency_key=f"quota-race-{suffix}-{index}",
                command=AnalysisJobCommand(
                    symbol=("BTC/USDT", "ETH/USDT")[index],
                    asset_type="crypto",
                    trade_date="2024-01-02",
                    mode=RunMode.BACKTEST,
                    provider="openai",
                    model="gpt-5",
                    llm_connection_id=uuid4(),
                    llm_credential_version=1,
                    selected_analysts=("market",),
                ),
            )
            return "created"
        except JobError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(enqueue, (0, 1)))
    assert sorted(outcomes) == ["ACTIVE_JOB_QUOTA_EXCEEDED", "created"]
    cleanup = JobService(
        factory,
        idempotency_digest_key=b"q" * 32,
        llm_reference_validator=SimpleNamespace(assert_reference=_reference),
        max_active_jobs_per_tenant=1,
    )
    for job in cleanup.list_jobs(principal=principal):
        cleanup.cancel(principal=principal, job_id=job.id)
    engine.dispose()


@pytest.mark.skipif(
    not APP_URL or not WORKER_URL,
    reason="PostgreSQL app and worker test URLs are not configured",
)
def test_postgres_worker_resolves_only_bound_tenant_credential_and_completes() -> None:
    app_engine = create_engine(APP_URL, pool_pre_ping=True)
    worker_engine = create_engine(WORKER_URL, pool_pre_ping=True)
    app_factory = sessionmaker(app_engine, expire_on_commit=False)
    worker_factory = sessionmaker(worker_engine, expire_on_commit=False)
    keyring = CredentialKeyring(keys={"pg-worker": b"k" * 32}, active_key_id="pg-worker")
    identity = IdentityService(
        app_factory,
        token_hasher=TokenHasher(b"jobs-postgres-worker-token-pepper-32"),
    )
    suffix = uuid4().hex[:10]
    registration = identity.register_tenant(
        tenant_name="Worker Integration",
        tenant_slug=f"worker-pg-{suffix}",
        owner_email=f"worker-pg-{suffix}@example.test",
        display_name="Worker Owner",
        password="correct horse battery staple",
    )
    principal = IdentityPrincipal(
        tenant_id=registration.tenant_id,
        user_id=registration.user_id,
        membership_id=registration.membership_id,
        session_id=uuid4(),
        role=MembershipRole.OWNER,
    )
    app_llm = LlmConnectionService(
        app_factory,
        keyring=keyring,
        idempotency_digest_key=b"l" * 32,
    )
    connection_id = app_llm.upsert(
        principal=principal,
        idempotency_key=f"worker-pg-llm-{suffix}",
        label="Provider",
        provider="openai",
        api_key="postgres-worker-secret-never-persist",
    )
    app_jobs = JobService(
        app_factory,
        idempotency_digest_key=b"a" * 32,
        llm_reference_validator=app_llm,
    )
    job_id = app_jobs.enqueue(
        principal=principal,
        idempotency_key=f"worker-pg-job-{suffix}",
        command=AnalysisJobCommand(
            symbol="BTC/USDT",
            asset_type="crypto",
            trade_date="2024-01-02",
            mode=RunMode.BACKTEST,
            provider="openai",
            model="gpt-5",
            llm_connection_id=connection_id,
            llm_credential_version=1,
            selected_analysts=("market",),
        ),
    )
    worker_llm = LlmConnectionService(
        worker_factory,
        keyring=keyring,
        idempotency_digest_key=b"l" * 32,
    )
    worker_jobs = JobService(
        worker_factory,
        idempotency_digest_key=b"a" * 32,
        llm_reference_validator=worker_llm,
    )

    def execute(claim, credential, _control):
        assert credential.api_key == "postgres-worker-secret-never-persist"
        assert claim.tenant_id == principal.tenant_id
        return ExecutionOutcome(result={"signal": "hold"}, manifest=claim.run_context.manifest())

    runner = WorkerRunner(
        jobs=worker_jobs,
        credentials=worker_llm,
        executor=execute,
        config=WorkerConfig(worker_id=f"worker-{suffix}"),
    )
    assert runner.run_once() is True
    detail = app_jobs.get_job(principal=principal, job_id=job_id)
    assert detail.job.status == "succeeded"
    with app_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(principal.tenant_id)},
        )
        event_id = connection.scalar(
            text(
                "SELECT id FROM llm_credential_access_events WHERE tenant_id = :tenant_id LIMIT 1"
            ),
            {"tenant_id": str(principal.tenant_id)},
        )
    assert event_id is not None
    for statement in (
        "UPDATE llm_credential_access_events SET outcome = 'denied' WHERE id = :id",
        "DELETE FROM llm_credential_access_events WHERE id = :id",
        "INSERT INTO llm_credential_access_events "
        "SELECT gen_random_uuid(), tenant_id, connection_id, credential_version, "
        "actor_user_id, purpose, outcome, occurred_at, details "
        "FROM llm_credential_access_events WHERE id = :id",
    ):
        with pytest.raises(DBAPIError), app_engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(principal.tenant_id)},
            )
            connection.execute(text(statement), {"id": event_id})
    assert detail.result == {"signal": "hold"}
    app_engine.dispose()
    worker_engine.dispose()

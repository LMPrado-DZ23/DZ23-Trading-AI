from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tradingagents.platform.contracts import RunMode
from tradingagents.platform.identity import (
    Base,
    IdentityPrincipal,
    IdentityService,
    MembershipRole,
    TokenHasher,
)
from tradingagents.platform.jobs import AnalysisJob, AnalysisJobCommand, JobError, JobService
from tradingagents.platform.usage import JobUsage


def _fixture():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    identity = IdentityService(
        factory, token_hasher=TokenHasher(b"job-test-identity-pepper-value-32")
    )
    registration = identity.register_tenant(
        tenant_name="Job Tenant",
        tenant_slug=f"jobs-{uuid4().hex[:10]}",
        owner_email=f"jobs-{uuid4().hex[:10]}@example.test",
        display_name="Job Owner",
        password="correct horse battery staple",
    )
    principal = IdentityPrincipal(
        tenant_id=registration.tenant_id,
        user_id=registration.user_id,
        membership_id=registration.membership_id,
        session_id=uuid4(),
        role=MembershipRole.OWNER,
    )
    service = JobService(
        factory,
        idempotency_digest_key=b"j" * 32,
        llm_reference_validator=SimpleNamespace(assert_reference=lambda **_kwargs: None),
    )
    return principal, service


def _command(**overrides) -> AnalysisJobCommand:
    values = {
        "symbol": "BTC/USDT",
        "asset_type": "crypto",
        "trade_date": "2026-09-08",
        "mode": RunMode.SHADOW,
        "provider": "openai",
        "model": "gpt-5",
        "llm_connection_id": uuid4(),
        "llm_credential_version": 1,
        "selected_analysts": ("market", "news"),
        "config": {"output_language": "Português"},
    }
    values.update(overrides)
    return AnalysisJobCommand(**values)


def test_job_lifecycle_persists_immutable_context_and_manifest():
    principal, service = _fixture()
    command = _command()
    job_id = service.enqueue(
        principal=principal,
        idempotency_key="job-lifecycle-0001",
        command=command,
    )
    claim = service.claim_next(worker_id="worker-a")
    assert claim is not None and claim.id == job_id
    assert claim.run_context.tenant_id == principal.tenant_id
    assert claim.run_context.digest == claim.run_context.manifest()["run_context_digest"]
    service.start(claim)
    assert service.heartbeat(claim) > datetime.now(UTC)
    service.complete(
        claim,
        result={"signal": "hold"},
        manifest=claim.run_context.manifest(),
        usage=JobUsage(
            input_tokens=40,
            output_tokens=10,
            total_tokens=50,
            llm_requests=2,
            unmetered_llm_requests=1,
            estimated_cost_microusd=120,
            pricing_version="price-v1",
        ),
    )
    [view] = service.list_jobs(principal=principal)
    assert view.status == "succeeded"
    assert view.attempts == 1
    assert (view.input_tokens, view.output_tokens, view.total_tokens) == (40, 10, 50)
    assert view.estimated_cost_microusd == 120
    assert service.claim_next(worker_id="worker-a") is None


def test_job_idempotency_rejects_payload_mismatch_and_never_accepts_credentials():
    principal, service = _fixture()
    command = _command()
    first = service.enqueue(
        principal=principal,
        idempotency_key="job-idempotency-001",
        command=command,
    )
    assert (
        service.enqueue(
            principal=principal,
            idempotency_key="job-idempotency-001",
            command=command,
        )
        == first
    )
    with pytest.raises(JobError, match="IDEMPOTENCY_PAYLOAD_MISMATCH"):
        service.enqueue(
            principal=principal,
            idempotency_key="job-idempotency-001",
            command=_command(symbol="ETH/USDT"),
        )
    with pytest.raises(ValidationError, match="credentials"):
        _command(config={"api_key": "must-not-enter-job"})
    with pytest.raises(ValidationError, match="credentials"):
        _command(config={"provider": {"nested": [{"accessToken": "must-not-enter"}]}})
    with pytest.raises(ValidationError, match="unsupported"):
        _command(config={"results_dir": "../../outside-tenant"})
    assert _command(config={"max_tokens": 4096}).config == {"max_tokens": 4096}


def test_cancel_before_claim_and_during_run_is_fail_closed():
    principal, service = _fixture()
    first = service.enqueue(
        principal=principal,
        idempotency_key="job-cancel-queued-01",
        command=_command(),
    )
    assert service.cancel(principal=principal, job_id=first) == "cancelled"

    second = service.enqueue(
        principal=principal,
        idempotency_key="job-cancel-running-02",
        command=_command(symbol="ETH/USDT"),
    )
    claim = service.claim_next(worker_id="worker-a")
    assert claim is not None and claim.id == second
    service.start(claim)
    assert service.cancel(principal=principal, job_id=second) == "cancel_requested"
    service.complete(claim, result={"signal": "buy"}, manifest={"safe": True})
    statuses = {view.id: view.status for view in service.list_jobs(principal=principal)}
    assert statuses[second] == "cancelled"


def test_expired_lease_is_retried_then_terminally_failed():
    principal, service = _fixture()
    clock = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    service._now = lambda: clock
    service.enqueue(
        principal=principal,
        idempotency_key="job-expired-lease-01",
        command=_command(),
        max_attempts=2,
    )
    first = service.claim_next(worker_id="worker-a", lease_seconds=30)
    assert first is not None
    first_context_digest = first.run_context.digest
    first_context_created_at = first.run_context.created_at
    clock += timedelta(seconds=31)
    second = service.claim_next(worker_id="worker-b", lease_seconds=30)
    assert second is not None and second.id == first.id and second.attempts == 2
    assert second.run_context.digest == first_context_digest
    assert second.run_context.created_at == first_context_created_at
    with pytest.raises(JobError, match="JOB_LEASE_INVALID"):
        service.heartbeat(first)
    clock += timedelta(seconds=31)
    assert service.claim_next(worker_id="worker-c", lease_seconds=30) is None
    [view] = service.list_jobs(principal=principal)
    assert view.status == "failed"
    assert view.error_code == "LEASE_EXPIRED"


def test_cancel_requested_job_is_terminal_after_worker_lease_expires():
    principal, service = _fixture()
    clock = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    service._now = lambda: clock
    job_id = service.enqueue(
        principal=principal,
        idempotency_key="job-cancel-expired-0001",
        command=_command(),
    )
    claim = service.claim_next(worker_id="worker-a", lease_seconds=30)
    assert claim is not None
    service.start(claim)
    assert service.cancel(principal=principal, job_id=job_id) == "cancel_requested"
    clock += timedelta(seconds=31)
    assert service.claim_next(worker_id="worker-b", lease_seconds=30) is None
    assert service.get_job(principal=principal, job_id=job_id).job.status == "cancelled"


@pytest.mark.parametrize("tamper", ["payload", "payload_and_hash", "workflow", "context"])
def test_claim_quarantines_tampered_job_state(tamper):
    principal, service = _fixture()
    job_id = service.enqueue(
        principal=principal,
        idempotency_key=f"job-integrity-{tamper}-0001",
        command=_command(),
    )
    if tamper == "context":
        first = service.claim_next(worker_id="worker-a", lease_seconds=30)
        assert first is not None
        clock = service._now()
        service._now = lambda: clock + timedelta(seconds=31)
    with service._sessions() as db, db.begin():
        row = db.get(AnalysisJob, job_id)
        if tamper in {"payload", "payload_and_hash"}:
            row.payload = {**row.payload, "symbol": "ETH/USDT"}
            if tamper == "payload_and_hash":
                import hashlib
                import json

                canonical = json.dumps(row.payload, sort_keys=True, separators=(",", ":"))
                row.payload_digest = hashlib.sha256(canonical.encode()).hexdigest()
        elif tamper == "workflow":
            row.workflow_schema_version += 1
        else:
            row.run_context = {**row.run_context, "symbol": "ETH/USDT"}
    assert service.claim_next(worker_id="worker-b", lease_seconds=30) is None
    detail = service.get_job(principal=principal, job_id=job_id)
    assert detail.job.status == "failed"
    assert detail.job.error_code in {
        "JOB_PAYLOAD_INTEGRITY_FAILED",
        "JOB_REQUEST_AUTHENTICATION_FAILED",
        "WORKFLOW_SCHEMA_UNSUPPORTED",
        "RUN_CONTEXT_INTEGRITY_FAILED",
    }


def test_active_job_quota_is_enforced_per_tenant():
    principal, _service = _fixture()
    # Reuse the same database through the service factory but tighten only this test.
    service = JobService(
        _service._sessions,
        idempotency_digest_key=b"q" * 32,
        llm_reference_validator=SimpleNamespace(assert_reference=lambda **_kwargs: None),
        max_active_jobs_per_tenant=1,
    )
    service.enqueue(
        principal=principal,
        idempotency_key="job-quota-first-001",
        command=_command(),
    )
    with pytest.raises(JobError, match="ACTIVE_JOB_QUOTA_EXCEEDED"):
        service.enqueue(
            principal=principal,
            idempotency_key="job-quota-second-02",
            command=_command(symbol="ETH/USDT"),
        )


def test_job_command_rejects_invalid_dates_and_calendar_spoofing():
    with pytest.raises(ValidationError, match="valid market session"):
        _command(trade_date="2026-99-99")
    with pytest.raises(ValidationError, match="incompatible"):
        _command(market_calendar="BVMF")
    b3 = _command(symbol="PETR4.SA", asset_type="stock", market_calendar="BVMF")
    assert b3.market_calendar == "BVMF"

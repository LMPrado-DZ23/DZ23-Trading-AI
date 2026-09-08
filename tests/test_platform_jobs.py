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
from tradingagents.platform.llm_provenance import build_llm_reference_snapshot
from tradingagents.platform.usage import JobUsage, PricingCatalog


def _reference(**kwargs):
    kwargs.pop("tenant_id", None)
    return build_llm_reference_snapshot(base_url=None, **kwargs)


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
        llm_reference_validator=SimpleNamespace(assert_reference=_reference),
    )
    return principal, service


def _command(**overrides) -> AnalysisJobCommand:
    values = {
        "symbol": "BTC/USDT",
        "asset_type": "crypto",
        "trade_date": "2024-01-02",
        "mode": RunMode.BACKTEST,
        "provider": "openai",
        "model": "gpt-5",
        "llm_connection_id": uuid4(),
        "llm_credential_version": 1,
        "selected_analysts": ("market",),
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
    for query in (
        "news Authorization: Bearer TOPSECRET123456789",
        "prefix api_key=TOPSECRET123456789 suffix",
        "query Basic dXNlcjpwYXNz",
        'escaped {"api\\u005fkey":"TOPSECRET123456789"}',
    ):
        with pytest.raises(ValidationError, match="credentials"):
            _command(config={"global_news_queries": [query]})
    with pytest.raises(ValidationError, match="unsupported"):
        _command(config={"results_dir": "../../outside-tenant"})
    assert _command(config={"max_tokens": 4096}).config == {
        "max_tokens": 4096,
        "llm_max_retries": 0,
        "checkpoint_enabled": True,
    }


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


def test_expired_lease_is_terminal_and_conservatively_consumes_budget():
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
    assert service.claim_next(worker_id="worker-b", lease_seconds=30) is None
    with pytest.raises(JobError, match="JOB_LEASE_INVALID"):
        service.heartbeat(first)
    [view] = service.list_jobs(principal=principal)
    assert view.status == "failed"
    assert view.error_code == "LEASE_EXPIRED_BUDGET_CONSUMED"
    assert view.attempts == 1
    assert first.run_context.digest == first_context_digest
    assert first.run_context.created_at == first_context_created_at


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
    expected_codes = {
        "JOB_PAYLOAD_INTEGRITY_FAILED",
        "JOB_REQUEST_AUTHENTICATION_FAILED",
        "WORKFLOW_SCHEMA_UNSUPPORTED",
        "RUN_CONTEXT_INTEGRITY_FAILED",
    }
    if tamper == "context":
        expected_codes.add("LEASE_EXPIRED_BUDGET_CONSUMED")
    assert detail.job.error_code in expected_codes


def test_active_job_quota_is_enforced_per_tenant():
    principal, _service = _fixture()
    # Reuse the same database through the service factory but tighten only this test.
    service = JobService(
        _service._sessions,
        idempotency_digest_key=b"q" * 32,
        llm_reference_validator=SimpleNamespace(assert_reference=_reference),
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


def test_monthly_cost_control_rejects_unpriced_model_before_queueing() -> None:
    principal, base = _fixture()
    pricing = PricingCatalog(
        {"openai:another-model": {"input_usd_per_million": 1, "output_usd_per_million": 2}}
    )
    service = JobService(
        base._sessions,
        idempotency_digest_key=b"c" * 32,
        llm_reference_validator=SimpleNamespace(assert_reference=_reference),
        monthly_cost_quota_microusd=10_000,
        pricing_catalog_version=pricing.version,
        pricing_catalog=pricing,
    )
    with pytest.raises(JobError, match="LLM_COST_UNMETERED"):
        service.enqueue(
            principal=principal,
            idempotency_key="job-unpriced-cost-model",
            command=_command(cost_budget_microusd=1_000),
        )


def test_job_command_rejects_invalid_dates_and_calendar_spoofing():
    with pytest.raises(ValidationError, match="only the market analyst"):
        _command(selected_analysts=("market", "news"))
    with pytest.raises(ValidationError, match="incompatible with the requested mode"):
        _command(trade_date="2026-99-99")
    with pytest.raises(ValidationError, match="incompatible"):
        _command(market_calendar="BVMF")
    b3 = _command(symbol="PETR4.SA", asset_type="stock", market_calendar="BVMF")
    assert b3.market_calendar == "BVMF"
    with pytest.raises(ValidationError, match="recognized symbol universe"):
        _command(symbol="AAPL", asset_type="crypto", trade_date="2024-01-06")
    with pytest.raises(ValidationError, match="recognized symbol universe"):
        _command(symbol="BTC-USD", asset_type="stock")


def test_job_command_binds_output_cap_retry_policy_and_execution_deadline() -> None:
    command = _command(token_budget=4_000, config={})
    assert command.config["max_tokens"] == 4_000
    assert command.config["llm_max_retries"] == 0
    assert command.config["checkpoint_enabled"] is True
    assert command.max_execution_seconds == 900
    with pytest.raises(ValidationError, match="cannot exceed"):
        _command(token_budget=4_000, config={"max_tokens": 4_001})
    with pytest.raises(ValidationError, match="retries must be zero"):
        _command(config={"llm_max_retries": 1})


@pytest.mark.parametrize(
    "symbol",
    ("AAPL`\nIGNORE PREVIOUS", " AAPL\rOVERRIDE", "AAPL ${secret}", "💸"),
)
def test_job_command_rejects_prompt_injection_symbols(symbol: str) -> None:
    with pytest.raises(ValidationError, match="symbol is invalid"):
        _command(symbol=symbol, asset_type="stock")


@pytest.mark.parametrize("symbol", ("AAPL:BAD", "AAA/BBB", "AAPL^", "AAPL="))
def test_job_command_rejects_structurally_ambiguous_stock_symbols(symbol: str) -> None:
    with pytest.raises(ValidationError, match="symbol is invalid"):
        _command(symbol=symbol, asset_type="stock")

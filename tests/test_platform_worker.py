from __future__ import annotations

import threading
from uuid import uuid4

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tradingagents.platform.connections import CredentialKeyring
from tradingagents.platform.contracts import RunMode
from tradingagents.platform.identity import (
    Base,
    IdentityPrincipal,
    IdentityService,
    MembershipRole,
    TokenHasher,
)
from tradingagents.platform.jobs import AnalysisJob, AnalysisJobCommand, JobService
from tradingagents.platform.llm_connections import LlmConnectionService
from tradingagents.platform.usage import JobUsage
from tradingagents.platform.worker import (
    ExecutionOutcome,
    GraphExecutionFailure,
    WorkerConfig,
    WorkerRunner,
)


def _stack():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    identity = IdentityService(
        factory, token_hasher=TokenHasher(b"worker-identity-token-pepper-value-32")
    )
    registration = identity.register_tenant(
        tenant_name="Worker Tenant",
        tenant_slug=f"worker-{uuid4().hex[:10]}",
        owner_email=f"worker-{uuid4().hex[:10]}@example.test",
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
    llm = LlmConnectionService(
        factory,
        keyring=CredentialKeyring(keys={"worker-test": b"k" * 32}, active_key_id="worker-test"),
        idempotency_digest_key=b"l" * 32,
    )
    connection_id = llm.upsert(
        principal=principal,
        idempotency_key="worker-llm-connection-0001",
        label="Worker OpenAI",
        provider="openai",
        api_key="worker-provider-secret-never-persist",
    )
    jobs = JobService(
        factory,
        idempotency_digest_key=b"j" * 32,
        llm_reference_validator=llm,
    )
    return factory, principal, llm, jobs, connection_id


def test_worker_executes_with_resolved_tenant_secret_and_persists_safe_projection() -> None:
    factory, principal, llm, jobs, connection_id = _stack()
    job_id = jobs.enqueue(
        principal=principal,
        idempotency_key="worker-analysis-job-0001",
        command=AnalysisJobCommand(
            symbol="BTC/USDT",
            asset_type="crypto",
            trade_date="2026-09-08",
            mode=RunMode.SHADOW,
            provider="openai",
            model="gpt-5",
            llm_connection_id=connection_id,
            llm_credential_version=1,
            selected_analysts=("market",),
        ),
    )

    def execute(claim, credential, _control):
        assert credential.api_key == "worker-provider-secret-never-persist"
        assert claim.run_context.tenant_id == principal.tenant_id
        return ExecutionOutcome(
            result={"signal": "hold", "tenant_id": str(principal.tenant_id)},
            manifest=claim.run_context.manifest(),
            usage=JobUsage(
                input_tokens=12,
                output_tokens=3,
                total_tokens=15,
                llm_requests=1,
            ),
        )

    worker = WorkerRunner(
        jobs=jobs,
        credentials=llm,
        executor=execute,
        config=WorkerConfig(worker_id="worker-test"),
    )
    assert worker.run_once() is True
    assert worker.run_once() is False
    with factory() as db:
        row = db.get(AnalysisJob, job_id)
        assert row is not None and row.status == "succeeded"
        assert (row.input_tokens, row.output_tokens, row.total_tokens) == (12, 3, 15)
        serialized = str(row.result) + str(row.manifest) + str(row.run_context)
        assert "worker-provider-secret" not in serialized


def test_job_rejects_foreign_or_disabled_llm_reference_before_queueing() -> None:
    _factory, principal, llm, jobs, connection_id = _stack()
    llm.disable(principal=principal, connection_id=connection_id)
    command = AnalysisJobCommand(
        symbol="ETH/USDT",
        asset_type="crypto",
        trade_date="2026-09-08",
        mode=RunMode.SHADOW,
        provider="openai",
        model="gpt-5",
        llm_connection_id=connection_id,
        llm_credential_version=1,
        selected_analysts=("market",),
    )
    from tradingagents.platform.jobs import JobError

    try:
        jobs.enqueue(
            principal=principal,
            idempotency_key="worker-disabled-llm-job",
            command=command,
        )
    except JobError as exc:
        assert str(exc) == "LLM_CONNECTION_INVALID"
    else:
        raise AssertionError("disabled LLM connection was accepted")


def test_worker_heartbeats_before_potentially_slow_credential_resolution() -> None:
    _factory, principal, llm, jobs, connection_id = _stack()
    jobs.enqueue(
        principal=principal,
        idempotency_key="worker-heartbeat-before-credential",
        command=AnalysisJobCommand(
            symbol="BTC/USDT",
            asset_type="crypto",
            trade_date="2026-09-08",
            mode=RunMode.SHADOW,
            provider="openai",
            model="gpt-5",
            llm_connection_id=connection_id,
            llm_credential_version=1,
            selected_analysts=("market",),
        ),
    )
    heartbeat_started = threading.Event()

    class ProbeRunner(WorkerRunner):
        def _heartbeat(self, claim, stop, lease_lost):
            heartbeat_started.set()
            stop.wait()

    class ProbeCredentials:
        def resolve_for_worker(self, **kwargs):
            assert heartbeat_started.wait(1)
            return llm.resolve_for_worker(**kwargs)

    worker = ProbeRunner(
        jobs=jobs,
        credentials=ProbeCredentials(),
        executor=lambda claim, _credential, _control: ExecutionOutcome(
            result={"signal": "hold"}, manifest=claim.run_context.manifest()
        ),
        config=WorkerConfig(worker_id="worker-heartbeat-probe"),
    )
    assert worker.run_once() is True


def test_worker_persists_partial_usage_before_retrying_failed_graph() -> None:
    _factory, principal, llm, jobs, connection_id = _stack()
    job_id = jobs.enqueue(
        principal=principal,
        idempotency_key="worker-partial-usage-failure",
        command=AnalysisJobCommand(
            symbol="BTC/USDT",
            asset_type="crypto",
            trade_date="2026-09-08",
            mode=RunMode.SHADOW,
            provider="openai",
            model="gpt-5",
            llm_connection_id=connection_id,
            llm_credential_version=1,
            selected_analysts=("market",),
        ),
    )

    def fail_after_billed_call(_claim, _credential, _control):
        raise GraphExecutionFailure(
            JobUsage(
                input_tokens=9,
                output_tokens=1,
                total_tokens=10,
                llm_requests=1,
            )
        )

    worker = WorkerRunner(
        jobs=jobs,
        credentials=llm,
        executor=fail_after_billed_call,
        config=WorkerConfig(worker_id="worker-partial-usage"),
    )
    assert worker.run_once() is True
    detail = jobs.get_job(principal=principal, job_id=job_id)
    assert detail.job.status == "queued"
    assert detail.job.total_tokens == 10
    assert detail.job.llm_requests == 1


def test_worker_persists_usage_when_result_is_too_large() -> None:
    _factory, principal, llm, jobs, connection_id = _stack()
    job_id = jobs.enqueue(
        principal=principal,
        idempotency_key="worker-oversized-result-usage",
        command=AnalysisJobCommand(
            symbol="BTC/USDT",
            asset_type="crypto",
            trade_date="2026-09-08",
            mode=RunMode.SHADOW,
            provider="openai",
            model="gpt-5",
            llm_connection_id=connection_id,
            llm_credential_version=1,
            selected_analysts=("market",),
        ),
    )
    usage = JobUsage(input_tokens=10, output_tokens=5, total_tokens=15, llm_requests=1)
    worker = WorkerRunner(
        jobs=jobs,
        credentials=llm,
        executor=lambda claim, _credential, _control: ExecutionOutcome(
            result={"large": "x" * 2_000_001},
            manifest=claim.run_context.manifest(),
            usage=usage,
        ),
        config=WorkerConfig(worker_id="worker-oversized-result"),
    )
    assert worker.run_once() is True
    detail = jobs.get_job(principal=principal, job_id=job_id)
    assert detail.job.status == "queued"
    assert detail.job.total_tokens == 15


def test_isolated_graph_construction_never_writes_tenant_secret_to_global_config(
    monkeypatch,
) -> None:
    from tradingagents.dataflows.config import get_config, reset_config
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    reset_config()
    observed = []

    def initialize(self, _analysts):
        observed.append(get_config().get("llm_api_key"))

    monkeypatch.setattr(TradingAgentsGraph, "_initialize", initialize)
    TradingAgentsGraph(config={"llm_api_key": "TENANT_A_SECRET"}, isolated_config=True)
    assert observed == ["TENANT_A_SECRET"]
    assert get_config().get("llm_api_key") != "TENANT_A_SECRET"


def test_worker_preserves_billed_usage_when_lease_is_lost_after_executor_returns() -> None:
    _factory, principal, llm, jobs, connection_id = _stack()
    job_id = jobs.enqueue(
        principal=principal,
        idempotency_key="worker-post-execution-lease-loss",
        command=AnalysisJobCommand(
            symbol="BTC/USDT",
            asset_type="crypto",
            trade_date="2026-09-08",
            mode=RunMode.SHADOW,
            provider="openai",
            model="gpt-5",
            llm_connection_id=connection_id,
            llm_credential_version=1,
            selected_analysts=("market",),
        ),
    )
    usage = JobUsage(input_tokens=7, output_tokens=2, total_tokens=9, llm_requests=1)

    def execute(claim, _credential, control):
        control.lease_lost.set()
        return ExecutionOutcome(
            result={"signal": "hold"}, manifest=claim.run_context.manifest(), usage=usage
        )

    runner = WorkerRunner(
        jobs=jobs,
        credentials=llm,
        executor=execute,
        config=WorkerConfig(worker_id="worker-post-execution-loss"),
    )
    assert runner.run_once() is True
    detail = jobs.get_job(principal=principal, job_id=job_id)
    assert detail.job.total_tokens == 9

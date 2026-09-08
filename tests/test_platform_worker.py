from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
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
    ExecutionControl,
    ExecutionOutcome,
    GraphExecutionFailure,
    ProductionGraphExecutor,
    WorkerConfig,
    WorkerRunner,
    _protocol_failure,
    _supervise_attempt_process,
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
            trade_date="2024-01-02",
            mode=RunMode.BACKTEST,
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
        trade_date="2024-01-02",
        mode=RunMode.BACKTEST,
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
            trade_date="2024-01-02",
            mode=RunMode.BACKTEST,
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


def test_worker_persists_partial_usage_and_does_not_retry_failed_graph() -> None:
    _factory, principal, llm, jobs, connection_id = _stack()
    job_id = jobs.enqueue(
        principal=principal,
        idempotency_key="worker-partial-usage-failure",
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
    assert detail.job.status == "failed"
    assert detail.job.attempts == 1
    assert detail.job.total_tokens == 10
    assert detail.job.llm_requests == 1


def test_worker_rejects_job_created_by_a_different_application_build(monkeypatch) -> None:
    _factory, principal, llm, jobs, connection_id = _stack()
    job_id = jobs.enqueue(
        principal=principal,
        idempotency_key="worker-build-provenance-mismatch",
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
    monkeypatch.setattr(
        "tradingagents.platform.llm_provenance.application_build_digest",
        lambda: "b" * 64,
    )
    called = []
    worker = WorkerRunner(
        jobs=jobs,
        credentials=llm,
        executor=lambda *_args: called.append(True),
        config=WorkerConfig(worker_id="worker-build-mismatch"),
    )
    assert worker.run_once() is True
    detail = jobs.get_job(principal=principal, job_id=job_id)
    assert detail.job.status == "failed"
    assert detail.job.error_code == "RUN_CONTEXT_BUILD_MISMATCH"
    assert called == []


def test_worker_rejects_job_after_decision_critical_sdk_version_changes(
    monkeypatch,
) -> None:
    from tradingagents.platform import llm_provenance

    llm_provenance.application_build_digest.cache_clear()
    _factory, principal, llm, jobs, connection_id = _stack()
    job_id = jobs.enqueue(
        principal=principal,
        idempotency_key="worker-sdk-provenance-mismatch",
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
    real_version = llm_provenance.version

    def upgraded(distribution: str) -> str:
        if distribution == "httpx":
            return "999.0.0-transport-change"
        return real_version(distribution)

    monkeypatch.setattr(llm_provenance, "version", upgraded)
    llm_provenance.application_build_digest.cache_clear()
    called = []
    worker = WorkerRunner(
        jobs=jobs,
        credentials=llm,
        executor=lambda *_args: called.append(True),
        config=WorkerConfig(worker_id="worker-sdk-mismatch"),
    )
    assert worker.run_once() is True
    detail = jobs.get_job(principal=principal, job_id=job_id)
    assert detail.job.error_code == "RUN_CONTEXT_BUILD_MISMATCH"
    assert called == []
    llm_provenance.application_build_digest.cache_clear()


def test_worker_never_retries_when_result_commit_fails_after_usage_wal(monkeypatch) -> None:
    _factory, principal, llm, jobs, connection_id = _stack()
    job_id = jobs.enqueue(
        principal=principal,
        idempotency_key="worker-result-commit-failure",
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
    usage = JobUsage(input_tokens=7, output_tokens=2, total_tokens=9, llm_requests=1)

    def fail_result_commit(*_args, **_kwargs):
        raise RuntimeError("simulated result storage outage")

    monkeypatch.setattr(jobs, "complete", fail_result_commit)
    worker = WorkerRunner(
        jobs=jobs,
        credentials=llm,
        executor=lambda claim, _credential, _control: ExecutionOutcome(
            result={"signal": "hold"},
            manifest=claim.run_context.manifest(),
            usage=usage,
        ),
        config=WorkerConfig(worker_id="worker-result-commit-failure"),
    )
    assert worker.run_once() is True
    detail = jobs.get_job(principal=principal, job_id=job_id)
    assert detail.job.status == "failed"
    assert detail.job.attempts == 1
    assert detail.job.total_tokens == 9
    assert detail.job.llm_requests == 1


def test_worker_persists_usage_when_result_is_too_large() -> None:
    _factory, principal, llm, jobs, connection_id = _stack()
    job_id = jobs.enqueue(
        principal=principal,
        idempotency_key="worker-oversized-result-usage",
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
    assert detail.job.status == "failed"
    assert detail.job.error_code == "WORKER_RESULT_TOO_LARGE"
    assert detail.job.attempts == 1
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
            trade_date="2024-01-02",
            mode=RunMode.BACKTEST,
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


def test_retry_attempts_use_distinct_checkpoint_roots(monkeypatch, tmp_path) -> None:
    _factory, principal, llm, jobs, connection_id = _stack()
    jobs.enqueue(
        principal=principal,
        idempotency_key="worker-distinct-attempt-checkpoints",
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
    claim = jobs.claim_next(worker_id="checkpoint-worker")
    assert claim is not None
    credential = llm.resolve_for_worker(
        tenant_id=claim.tenant_id,
        connection_id=connection_id,
        credential_version=1,
        expected_provider="openai",
        actor_user_id=claim.requested_by_user_id,
    )
    roots = []

    class FakeGraph:
        def __init__(self, *, config, **_kwargs):
            roots.append(config["results_dir"])

        def propagate(self, *_args, **_kwargs):
            return {"company_of_interest": "BTC/USDT"}, "HOLD"

    monkeypatch.setattr("tradingagents.platform.worker.TradingAgentsGraph", FakeGraph)
    executor = ProductionGraphExecutor(
        runtime_root=tmp_path / "worker-runs", isolate_process=False
    )
    control = ExecutionControl(threading.Event(), time.monotonic() + 60)
    executor(claim, credential, control)
    executor(
        replace(claim, attempts=2, lease_id=uuid4()),
        credential,
        control,
    )
    assert roots[0] != roots[1]
    assert "attempt-1-" in roots[0]
    assert "attempt-2-" in roots[1]


def test_failed_graph_retains_partial_evidence_manifest(monkeypatch, tmp_path) -> None:
    from tradingagents.platform.evidence_ledger import record_evidence

    _factory, _principal, llm, jobs, connection_id = _stack()
    jobs.enqueue(
        principal=_principal,
        idempotency_key="worker-partial-evidence-manifest",
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
    claim = jobs.claim_next(worker_id="partial-evidence-worker")
    assert claim is not None
    credential = llm.resolve_for_worker(
        tenant_id=claim.tenant_id,
        connection_id=connection_id,
        credential_version=1,
        expected_provider="openai",
        actor_user_id=claim.requested_by_user_id,
    )

    class FailingGraph:
        def __init__(self, **_kwargs):
            pass

        def propagate(self, *_args, **_kwargs):
            record_evidence(
                method="get_stock_data",
                vendor="yfinance",
                outcome="delivered",
                args=("BTC/USDT",),
                kwargs={},
                decision_at=claim.run_context.decision_at.isoformat(),
                available_at=claim.run_context.decision_at.isoformat(),
                availability_basis="test_verified_bar",
                content="safe market row",
            )
            raise RuntimeError("provider exploded with data already consumed")

    monkeypatch.setattr("tradingagents.platform.worker.TradingAgentsGraph", FailingGraph)
    executor = ProductionGraphExecutor(
        runtime_root=tmp_path / "partial-evidence-runs", isolate_process=False
    )
    with pytest.raises(GraphExecutionFailure) as captured:
        executor(
            claim,
            credential,
            ExecutionControl(threading.Event(), time.monotonic() + 60),
        )
    manifest = captured.value.partial_manifest
    assert manifest is not None
    assert manifest["schema"] == "analysis-attempt-manifest/1"
    assert manifest["evidence_ledger"][0]["method"] == "get_stock_data"
    assert manifest["evidence_ledger"][0]["outcome"] == "delivered"


def test_attempt_supervisor_physically_terminates_a_blocked_process_tree(tmp_path) -> None:
    pid_file = tmp_path / "descendant.pid"
    script = (
        "import pathlib,subprocess,sys,time;"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid));"
        "time.sleep(60)"
    )
    creation_flags = 0
    if os.name == "nt":
        creation_flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        creationflags=creation_flags,
        start_new_session=os.name != "nt",
    )
    deadline = time.monotonic() + 3
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert pid_file.exists()
    descendant_pid = int(pid_file.read_text())
    started = time.monotonic()
    code = _supervise_attempt_process(
        process,
        ExecutionControl(threading.Event(), time.monotonic() + 0.2),
    )
    assert code == "WORKER_DEADLINE_EXCEEDED"
    assert process.poll() is not None
    assert time.monotonic() - started < 3
    if os.name == "nt":
        listing = subprocess.run(
            ["tasklist", "/FI", f"PID eq {descendant_pid}", "/FO", "CSV", "/NH"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        assert str(descendant_pid) not in listing
    else:
        with pytest.raises(ProcessLookupError):
            os.kill(descendant_pid, 0)


@pytest.mark.skipif(os.name != "nt", reason="Windows taskkill failure path")
def test_taskkill_timeout_is_terminal_and_never_escapes_supervisor(monkeypatch) -> None:
    class Process:
        pid = 424242

        def __init__(self) -> None:
            self.returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            if self.returncode is None:
                raise subprocess.TimeoutExpired("attempt", timeout)
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(
        "tradingagents.platform.worker.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("taskkill", 5)
        ),
    )
    code = _supervise_attempt_process(
        Process(),
        ExecutionControl(threading.Event(), time.monotonic() - 1),
    )
    assert code == "WORKER_TERMINATION_UNCONFIRMED"


def test_unexpected_terminator_error_never_enters_retry_path(monkeypatch) -> None:
    class Process:
        def poll(self):
            return None

    monkeypatch.setattr(
        "tradingagents.platform.worker._terminate_process_tree",
        lambda _process: (_ for _ in ()).throw(RuntimeError("os containment failure")),
    )
    code = _supervise_attempt_process(
        Process(),
        ExecutionControl(threading.Event(), time.monotonic() - 1),
    )
    assert code == "WORKER_TERMINATION_UNCONFIRMED"


def test_runtime_root_canonical_alias_cannot_select_the_working_tree() -> None:
    alias = Path.cwd() / ".." / Path.cwd().name
    with pytest.raises(RuntimeError, match="dedicated subdirectory"):
        ProductionGraphExecutor(runtime_root=alias)


def test_attempt_supervisor_fails_closed_when_storage_quota_is_crossed(tmp_path) -> None:
    attempt_root = tmp_path / "tenant" / "run" / "attempt"
    attempt_root.mkdir(parents=True)
    payload = attempt_root / "provider-output.bin"
    script = (
        "import pathlib,time;"
        f"pathlib.Path({str(payload)!r}).write_bytes(b'x'*65536);"
        "time.sleep(60)"
    )
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        start_new_session=os.name != "nt",
    )
    code = _supervise_attempt_process(
        process,
        ExecutionControl(threading.Event(), time.monotonic() + 5),
        attempt_root=attempt_root,
        tenant_root=tmp_path / "tenant",
        max_attempt_bytes=1024,
        max_tenant_bytes=1024 * 1024,
    )
    assert code == "WORKER_ATTEMPT_STORAGE_EXCEEDED"
    assert process.poll() is not None


def test_protocol_failure_charges_remaining_budget_and_retains_durable_evidence(
    tmp_path,
) -> None:
    _factory, principal, _llm, jobs, connection_id = _stack()
    jobs.enqueue(
        principal=principal,
        idempotency_key="worker-untrusted-protocol-accounting",
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
            token_budget=4321,
            cost_budget_microusd=7654,
        ),
    )
    claim = jobs.claim_next(worker_id="protocol-accounting-worker")
    assert claim is not None
    journal = tmp_path / "attempt-evidence.jsonl"
    journal.write_text(
        '{"method":"get_stock_data","outcome":"delivered","secret":"api_key=hidden123456"}\n',
        encoding="utf-8",
    )
    failure = _protocol_failure(claim, journal)
    assert failure.retryable is False
    assert failure.usage.total_tokens == 4321
    assert failure.usage.estimated_cost_microusd == 7654
    assert failure.partial_manifest is not None
    serialized = str(failure.partial_manifest)
    assert "get_stock_data" in serialized
    assert "hidden123456" not in serialized


def test_attempt_cleanup_removes_only_the_exact_fenced_attempt(tmp_path) -> None:
    _factory, principal, _llm, jobs, connection_id = _stack()
    jobs.enqueue(
        principal=principal,
        idempotency_key="worker-exact-attempt-cleanup",
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
    claim = jobs.claim_next(worker_id="attempt-cleanup-worker")
    assert claim is not None
    root = tmp_path / "worker-runs"
    executor = ProductionGraphExecutor(runtime_root=root)
    attempt = (
        root
        / str(claim.tenant_id)
        / str(claim.run_id)
        / f"attempt-{claim.attempts}-{claim.lease_id.hex}"
    )
    sibling = attempt.parent / "must-survive"
    attempt.mkdir(parents=True)
    sibling.mkdir()
    (attempt / "private.bin").write_bytes(b"secret")
    executor.cleanup_attempt(claim)
    assert not attempt.exists()
    assert sibling.is_dir()


def test_real_attempt_subprocess_starts_with_runtime_root_outside_its_cwd(tmp_path) -> None:
    _factory, principal, llm, jobs, connection_id = _stack()
    jobs.enqueue(
        principal=principal,
        idempotency_key="worker-real-child-construction",
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
    claim = jobs.claim_next(worker_id="real-child-worker")
    assert claim is not None
    credential = llm.resolve_for_worker(
        tenant_id=claim.tenant_id,
        connection_id=connection_id,
        credential_version=1,
        expected_provider="openai",
        actor_user_id=claim.requested_by_user_id,
    )
    executor = ProductionGraphExecutor(runtime_root=tmp_path / "isolated-runs")
    with pytest.raises(GraphExecutionFailure) as captured:
        executor(
            claim,
            credential,
            ExecutionControl(threading.Event(), time.monotonic() + 1.5),
        )
    assert captured.value.code in {
        "WORKER_DEADLINE_EXCEEDED",
        "WORKER_DEADLINE_WINDOW_INSUFFICIENT",
    }

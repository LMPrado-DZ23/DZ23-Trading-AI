import inspect
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tradingagents.service import (
    IdempotencyConflict,
    JobStatus,
    JobSubmission,
    JobWorker,
    PostgresJobStore,
    SQLiteJobStore,
    create_app,
)
from tradingagents.service.entrypoints import configured_store
from tradingagents.service.store import LeaseConflict

TOKEN = "test-token-with-at-least-thirty-two-characters"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _payload(ticker="AAPL"):
    return JobSubmission(
        kind="analysis",
        ticker=ticker,
        trade_date="2024-01-02",
    ).canonical_payload()


def test_submission_schema_forbids_config_credentials_and_invalid_modes():
    with pytest.raises(ValidationError, match="Extra inputs"):
        JobSubmission(
            kind="analysis",
            ticker="AAPL",
            trade_date="2024-01-02",
            api_key="secret",
        )
    with pytest.raises(ValidationError, match="fundamentals analyst"):
        JobSubmission(
            kind="analysis",
            ticker="BTC-USD",
            asset_type="crypto",
            trade_date="2024-01-02",
        )
    with pytest.raises(ValidationError, match="requires start_date"):
        JobSubmission(kind="backtest", ticker="AAPL", trade_date="2024-01-02")


def test_sqlite_idempotency_and_conflict(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.sqlite")
    store.initialize()

    first, created = store.enqueue(_payload(), "idempotency-key-0001")
    second, created_again = store.enqueue(_payload(), "idempotency-key-0001")

    assert created is True
    assert created_again is False
    assert second.job_id == first.job_id
    with pytest.raises(IdempotencyConflict):
        store.enqueue(_payload("MSFT"), "idempotency-key-0001")


def test_concurrent_workers_claim_a_job_only_once(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.sqlite")
    store.initialize()
    store.enqueue(_payload(), "idempotency-key-0002")
    claimed = []
    barrier = threading.Barrier(8)

    def claim(index):
        barrier.wait(timeout=3)
        record = store.claim(f"worker-{index}")
        if record:
            claimed.append(record)

    threads = [threading.Thread(target=claim, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(claimed) == 1
    assert claimed[0].status is JobStatus.RUNNING
    assert claimed[0].attempts == 1


def test_lease_expiry_retry_budget_and_stale_completion(tmp_path):
    now = [datetime(2024, 1, 1, tzinfo=timezone.utc)]
    store = SQLiteJobStore(tmp_path / "jobs.sqlite", clock=lambda: now[0])
    store.initialize()
    job, _ = store.enqueue(_payload(), "idempotency-key-0003")
    first = store.claim("worker-a", lease_seconds=10, max_attempts=2)
    now[0] += timedelta(seconds=11)
    second = store.claim("worker-b", lease_seconds=10, max_attempts=2)

    assert second.job_id == job.job_id
    assert second.attempts == 2
    with pytest.raises(LeaseConflict):
        store.complete(job.job_id, first.lease_token, {"stale": True})
    now[0] += timedelta(seconds=11)
    assert store.claim("worker-c", lease_seconds=10, max_attempts=2) is None
    exhausted = store.get(job.job_id)
    assert exhausted.status is JobStatus.FAILED
    assert exhausted.error_code == "max_attempts"


def test_cancel_invalidates_running_lease(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.sqlite")
    store.initialize()
    job, _ = store.enqueue(_payload(), "idempotency-key-0004")
    claimed = store.claim("worker")

    cancelled = store.cancel(job.job_id)

    assert cancelled.status is JobStatus.CANCELLED
    with pytest.raises(LeaseConflict):
        store.complete(job.job_id, claimed.lease_token, {"should": "not persist"})


def test_lease_renewal_extends_ownership_and_rejects_after_cancel(tmp_path):
    now = [datetime(2024, 1, 1, tzinfo=timezone.utc)]
    store = SQLiteJobStore(tmp_path / "jobs.sqlite", clock=lambda: now[0])
    store.initialize()
    job, _ = store.enqueue(_payload(), "idempotency-key-renew-0001")
    claimed = store.claim("worker", lease_seconds=10)
    original_until = claimed.lease_until

    now[0] += timedelta(seconds=5)
    assert store.renew(job.job_id, claimed.lease_token, lease_seconds=20) is True
    assert store.get(job.job_id).lease_until > original_until
    store.cancel(job.job_id)
    assert store.renew(job.job_id, claimed.lease_token, lease_seconds=20) is False


def test_worker_renews_lease_while_handler_is_running(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.sqlite")
    store.initialize()
    store.enqueue(_payload(), "idempotency-key-renew-0002")
    renewals = []
    original_renew = store.renew

    def renew(*args, **kwargs):
        renewals.append(args[0])
        return original_renew(*args, **kwargs)

    store.renew = renew

    def slow_handler(record):
        time.sleep(0.15)
        return {"signal": "Hold"}

    result = JobWorker(
        store,
        slow_handler,
        lease_seconds=1,
        heartbeat_interval=0.03,
    ).run_once()

    assert result.status is JobStatus.SUCCEEDED
    assert renewals


def test_worker_completes_and_redacts_result(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.sqlite")
    store.initialize()
    job, _ = store.enqueue(_payload(), "idempotency-key-0005")
    worker = JobWorker(
        store,
        lambda record: {"signal": "Hold", "authorization": "Bearer very-secret-token"},
    )

    result = worker.run_once()

    assert result.job_id == job.job_id
    assert result.status is JobStatus.SUCCEEDED
    assert "very-secret-token" not in str(result.result)


def test_worker_failure_is_redacted_and_persisted(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.sqlite")
    store.initialize()
    store.enqueue(_payload(), "idempotency-key-0006")

    def fail(record):
        raise RuntimeError("api_key=extremely-secret-value")

    result = JobWorker(store, fail).run_once()

    assert result.status is JobStatus.FAILED
    assert result.error_code == "RuntimeError"
    assert "extremely-secret-value" not in result.error_message


def test_authenticated_api_submission_query_cancel_and_replay(tmp_path):
    store = SQLiteJobStore(tmp_path / "jobs.sqlite")
    client = TestClient(create_app(store, api_token=TOKEN))
    body = _payload()
    headers = {**AUTH, "Idempotency-Key": "idempotency-key-0007"}

    assert client.get("/healthz").status_code == 200
    assert client.post("/v1/jobs", json=body).status_code == 401
    created = client.post("/v1/jobs", json=body, headers=headers)
    replay = client.post("/v1/jobs", json=body, headers=headers)
    queried = client.get(f"/v1/jobs/{created.json()['job_id']}", headers=AUTH)
    cancelled = client.post(
        f"/v1/jobs/{created.json()['job_id']}/cancel", headers=AUTH
    )

    assert created.status_code == 202
    assert replay.status_code == 200
    assert replay.json()["job_id"] == created.json()["job_id"]
    assert queried.json()["status"] == "queued"
    assert cancelled.json()["status"] == "cancelled"
    assert "lease_token" not in queried.json()
    assert client.get("/openapi.json").status_code == 404


def test_api_rejects_short_token_and_idempotency_conflict(tmp_path):
    with pytest.raises(ValueError, match="at least 32"):
        create_app(SQLiteJobStore(tmp_path / "bad.sqlite"), api_token="short")
    store = SQLiteJobStore(tmp_path / "jobs.sqlite")
    client = TestClient(create_app(store, api_token=TOKEN))
    headers = {**AUTH, "Idempotency-Key": "idempotency-key-0008"}
    assert client.post("/v1/jobs", json=_payload(), headers=headers).status_code == 202
    assert client.post("/v1/jobs", json=_payload("MSFT"), headers=headers).status_code == 409


def test_postgres_claim_contract_uses_skip_locked():
    source = inspect.getsource(PostgresJobStore.claim)
    assert "FOR UPDATE SKIP LOCKED" in source


def test_production_store_requires_postgres(monkeypatch):
    monkeypatch.delenv("TRADINGAGENTS_DATABASE_URL", raising=False)
    monkeypatch.delenv("TRADINGAGENTS_ENV", raising=False)

    with pytest.raises(RuntimeError, match="required"):
        configured_store()


def test_sqlite_store_requires_explicit_development(monkeypatch, tmp_path):
    path = tmp_path / "local.sqlite"
    monkeypatch.setenv("TRADINGAGENTS_ENV", "development")
    monkeypatch.setenv("TRADINGAGENTS_SQLITE_PATH", str(path))
    monkeypatch.delenv("TRADINGAGENTS_DATABASE_URL", raising=False)

    store = configured_store()

    assert isinstance(store, SQLiteJobStore)
    assert store.path == path

"""Lease-based job worker. One graph instance is created per job by the handler."""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable
from typing import Any

from tradingagents.dataflows.redact import redact_secrets

from .jobs import JobRecord, JobStatus
from .store import JobStore, LeaseConflict


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {str(key): _redact_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_secrets(str(value))


class JobWorker:
    def __init__(
        self,
        store: JobStore,
        handler: Callable[[JobRecord], dict[str, Any]],
        *,
        worker_id: str | None = None,
        lease_seconds: int = 300,
        max_attempts: int = 3,
        max_result_bytes: int = 64_000,
        heartbeat_interval: float | None = None,
    ) -> None:
        if lease_seconds <= 0 or max_attempts <= 0 or max_result_bytes <= 0:
            raise ValueError("worker limits must be positive")
        self.store = store
        self.handler = handler
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex}"
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.max_result_bytes = max_result_bytes
        self.heartbeat_interval = heartbeat_interval or max(0.01, lease_seconds / 3)
        if self.heartbeat_interval >= lease_seconds:
            raise ValueError("heartbeat interval must be shorter than the lease")

    def run_once(self) -> JobRecord | None:
        job = self.store.claim(
            self.worker_id,
            lease_seconds=self.lease_seconds,
            max_attempts=self.max_attempts,
        )
        if job is None:
            return None
        stop_heartbeat = threading.Event()
        lease_lost = threading.Event()

        def renew_lease() -> None:
            while not stop_heartbeat.wait(self.heartbeat_interval):
                try:
                    if not self.store.renew(
                        job.job_id,
                        job.lease_token,
                        lease_seconds=self.lease_seconds,
                    ):
                        lease_lost.set()
                        return
                except Exception:
                    lease_lost.set()
                    return

        heartbeat = threading.Thread(
            target=renew_lease,
            name=f"lease-heartbeat-{job.job_id}",
            daemon=True,
        )
        heartbeat.start()
        try:
            result = _redact_value(self.handler(job))
            if not isinstance(result, dict):
                raise TypeError("job handler must return a dictionary")
            encoded = json.dumps(result, sort_keys=True, allow_nan=False).encode("utf-8")
            if len(encoded) > self.max_result_bytes:
                raise ValueError("job result exceeds the configured size limit")
            if lease_lost.is_set():
                return self.store.get(job.job_id)
            current = self.store.get(job.job_id)
            if current is not None and current.status is JobStatus.CANCELLED:
                return current
            return self.store.complete(job.job_id, job.lease_token, result)
        except Exception as exc:
            current = self.store.get(job.job_id)
            if current is not None and current.status is JobStatus.CANCELLED:
                return current
            message = redact_secrets(str(exc))[:1000]
            try:
                return self.store.fail(
                    job.job_id,
                    job.lease_token,
                    type(exc).__name__,
                    message,
                )
            except LeaseConflict:
                return self.store.get(job.job_id)
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=max(1.0, self.heartbeat_interval * 2))

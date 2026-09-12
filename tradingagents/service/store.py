"""SQLite development queue and PostgreSQL production queue."""

from __future__ import annotations

import json
import sqlite3
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .jobs import JOB_SCHEMA_VERSION, JobRecord, JobStatus, request_hash


class IdempotencyConflict(ValueError):
    pass


class LeaseConflict(RuntimeError):
    pass


class JobStore(ABC):
    @abstractmethod
    def initialize(self) -> None: ...

    @abstractmethod
    def enqueue(self, payload: dict[str, Any], idempotency_key: str) -> tuple[JobRecord, bool]: ...

    @abstractmethod
    def get(self, job_id: str) -> JobRecord | None: ...

    @abstractmethod
    def claim(self, worker_id: str, *, lease_seconds: int = 300, max_attempts: int = 3) -> JobRecord | None: ...

    @abstractmethod
    def complete(self, job_id: str, lease_token: str, result: dict[str, Any]) -> JobRecord: ...

    @abstractmethod
    def renew(self, job_id: str, lease_token: str, *, lease_seconds: int) -> bool: ...

    @abstractmethod
    def fail(self, job_id: str, lease_token: str, code: str, message: str) -> JobRecord: ...

    @abstractmethod
    def cancel(self, job_id: str) -> JobRecord | None: ...

    @abstractmethod
    def list_recent(self, limit: int = 50) -> list[JobRecord]: ...


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class SQLiteJobStore(JobStore):
    """Single-host development backend; PostgreSQL is required for production."""

    def __init__(self, path: str | Path, *, clock: Callable[[], datetime] = _utcnow) -> None:
        self.path = Path(path)
        self.clock = clock

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_sha256 TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    lease_until TEXT,
                    worker_id TEXT,
                    lease_token TEXT,
                    result_json TEXT,
                    error_code TEXT,
                    error_message TEXT
                )
            """)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS jobs_claim_idx ON jobs(status, lease_until, created_at)"
            )

    def enqueue(self, payload: dict[str, Any], idempotency_key: str) -> tuple[JobRecord, bool]:
        if not 16 <= len(idempotency_key) <= 128:
            raise ValueError("idempotency key must be 16-128 characters")
        digest = request_hash(payload)
        now = self.clock()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM jobs WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing:
                record = self._row(existing)
                if record.request_sha256 != digest:
                    raise IdempotencyConflict("idempotency key was already used for another request")
                return record, False
            job_id = uuid.uuid4().hex
            connection.execute(
                """INSERT INTO jobs (
                    job_id, schema_version, idempotency_key, request_sha256,
                    payload_json, status, attempts, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                (
                    job_id, JOB_SCHEMA_VERSION, idempotency_key, digest,
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                    JobStatus.QUEUED.value, _iso(now), _iso(now),
                ),
            )
            created = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            return self._row(created), True

    def get(self, job_id: str) -> JobRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._row(row) if row else None

    def claim(self, worker_id: str, *, lease_seconds: int = 300, max_attempts: int = 3) -> JobRecord | None:
        if lease_seconds <= 0 or max_attempts <= 0:
            raise ValueError("lease_seconds and max_attempts must be positive")
        now = self.clock()
        lease_until = now + timedelta(seconds=lease_seconds)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE jobs SET status = ?, error_code = ?, error_message = ?, updated_at = ?
                   WHERE status = ? AND lease_until <= ? AND attempts >= ?""",
                (
                    JobStatus.FAILED.value, "max_attempts", "retry budget exhausted",
                    _iso(now), JobStatus.RUNNING.value, _iso(now), max_attempts,
                ),
            )
            row = connection.execute(
                """SELECT * FROM jobs
                   WHERE (status = ? OR (status = ? AND lease_until <= ?)) AND attempts < ?
                   ORDER BY created_at, job_id LIMIT 1""",
                (JobStatus.QUEUED.value, JobStatus.RUNNING.value, _iso(now), max_attempts),
            ).fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            connection.execute(
                """UPDATE jobs SET status = ?, attempts = attempts + 1, updated_at = ?,
                   lease_until = ?, worker_id = ?, lease_token = ?, error_code = NULL,
                   error_message = NULL WHERE job_id = ?""",
                (
                    JobStatus.RUNNING.value, _iso(now), _iso(lease_until), worker_id,
                    token, row["job_id"],
                ),
            )
            updated = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (row["job_id"],)
            ).fetchone()
            return self._row(updated)

    def complete(self, job_id: str, lease_token: str, result: dict[str, Any]) -> JobRecord:
        return self._finish(job_id, lease_token, JobStatus.SUCCEEDED, result=result)

    def renew(self, job_id: str, lease_token: str, *, lease_seconds: int) -> bool:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now = self.clock()
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE jobs SET lease_until = ?, updated_at = ?
                   WHERE job_id = ? AND status = ? AND lease_token = ?""",
                (
                    _iso(now + timedelta(seconds=lease_seconds)), _iso(now), job_id,
                    JobStatus.RUNNING.value, lease_token,
                ),
            )
            return cursor.rowcount == 1

    def fail(self, job_id: str, lease_token: str, code: str, message: str) -> JobRecord:
        return self._finish(
            job_id, lease_token, JobStatus.FAILED, error_code=code, error_message=message
        )

    def _finish(
        self,
        job_id: str,
        lease_token: str,
        status: JobStatus,
        *,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> JobRecord:
        now = self.clock()
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE jobs SET status = ?, updated_at = ?, lease_until = NULL,
                   worker_id = NULL, lease_token = NULL, result_json = ?,
                   error_code = ?, error_message = ?
                   WHERE job_id = ? AND status = ? AND lease_token = ?""",
                (
                    status.value, _iso(now),
                    json.dumps(result, sort_keys=True) if result is not None else None,
                    error_code, error_message, job_id, JobStatus.RUNNING.value, lease_token,
                ),
            )
            if cursor.rowcount != 1:
                raise LeaseConflict("job lease is no longer valid")
        return self.get(job_id)

    def cancel(self, job_id: str) -> JobRecord | None:
        now = self.clock()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            if row["status"] in (JobStatus.QUEUED.value, JobStatus.RUNNING.value):
                connection.execute(
                    """UPDATE jobs SET status = ?, updated_at = ?, lease_until = NULL,
                       worker_id = NULL, lease_token = NULL WHERE job_id = ?""",
                    (JobStatus.CANCELLED.value, _iso(now), job_id),
                )
        return self.get(job_id)

    def list_recent(self, limit: int = 50) -> list[JobRecord]:
        limit = max(1, min(int(limit), 200))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(row: Any) -> JobRecord:
        if row["schema_version"] != JOB_SCHEMA_VERSION:
            raise ValueError("job schema is incompatible")
        return JobRecord(
            job_id=row["job_id"],
            schema=row["schema_version"],
            idempotency_key=row["idempotency_key"],
            request_sha256=row["request_sha256"],
            payload=json.loads(row["payload_json"]),
            status=JobStatus(row["status"]),
            attempts=int(row["attempts"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            lease_until=datetime.fromisoformat(row["lease_until"]) if row["lease_until"] else None,
            worker_id=row["worker_id"],
            lease_token=row["lease_token"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            error_code=row["error_code"],
            error_message=row["error_message"],
        )


class PostgresJobStore(JobStore):
    """Production queue using row locks and SKIP LOCKED for worker concurrency."""

    def __init__(self, dsn: str, *, clock: Callable[[], datetime] = _utcnow) -> None:
        if not dsn:
            raise ValueError("PostgreSQL DSN must not be empty")
        self.dsn = dsn
        self.clock = clock

    def _connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("install tradingagents[api] for PostgreSQL support") from exc
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def initialize(self) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tradingagents_jobs (
                    job_id TEXT PRIMARY KEY, schema_version TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE, request_sha256 TEXT NOT NULL,
                    payload_json JSONB NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
                    lease_until TIMESTAMPTZ, worker_id TEXT, lease_token TEXT,
                    result_json JSONB, error_code TEXT, error_message TEXT
                )
            """)
            cursor.execute("""CREATE INDEX IF NOT EXISTS tradingagents_jobs_claim_idx
                ON tradingagents_jobs(status, lease_until, created_at)""")

    def enqueue(self, payload: dict[str, Any], idempotency_key: str) -> tuple[JobRecord, bool]:
        from psycopg.types.json import Jsonb

        if not 16 <= len(idempotency_key) <= 128:
            raise ValueError("idempotency key must be 16-128 characters")
        digest = request_hash(payload)
        now = self.clock()
        with self._connect() as connection, connection.cursor() as cursor:
            job_id = uuid.uuid4().hex
            cursor.execute(
                """INSERT INTO tradingagents_jobs (
                    job_id, schema_version, idempotency_key, request_sha256, payload_json,
                    status, attempts, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s)
                ON CONFLICT (idempotency_key) DO NOTHING RETURNING *""",
                (
                    job_id, JOB_SCHEMA_VERSION, idempotency_key, digest, Jsonb(payload),
                    JobStatus.QUEUED.value, now, now,
                ),
            )
            created = cursor.fetchone()
            if created:
                return self._row(created), True
            cursor.execute(
                "SELECT * FROM tradingagents_jobs WHERE idempotency_key = %s FOR UPDATE",
                (idempotency_key,),
            )
            record = self._row(cursor.fetchone())
            if record.request_sha256 != digest:
                raise IdempotencyConflict("idempotency key was already used for another request")
            return record, False

    def get(self, job_id: str) -> JobRecord | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM tradingagents_jobs WHERE job_id = %s", (job_id,))
            row = cursor.fetchone()
        return self._row(row) if row else None

    def claim(self, worker_id: str, *, lease_seconds: int = 300, max_attempts: int = 3) -> JobRecord | None:
        if lease_seconds <= 0 or max_attempts <= 0:
            raise ValueError("lease_seconds and max_attempts must be positive")
        now = self.clock()
        lease_until = now + timedelta(seconds=lease_seconds)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE tradingagents_jobs SET status = %s, error_code = %s,
                   error_message = %s, updated_at = %s WHERE status = %s
                   AND lease_until <= %s AND attempts >= %s""",
                (
                    JobStatus.FAILED.value, "max_attempts", "retry budget exhausted", now,
                    JobStatus.RUNNING.value, now, max_attempts,
                ),
            )
            token = uuid.uuid4().hex
            cursor.execute(
                """WITH candidate AS (
                    SELECT job_id FROM tradingagents_jobs
                    WHERE (status = %s OR (status = %s AND lease_until <= %s))
                      AND attempts < %s
                    ORDER BY created_at, job_id FOR UPDATE SKIP LOCKED LIMIT 1
                )
                UPDATE tradingagents_jobs AS jobs SET status = %s, attempts = attempts + 1,
                    updated_at = %s, lease_until = %s, worker_id = %s, lease_token = %s,
                    error_code = NULL, error_message = NULL
                FROM candidate WHERE jobs.job_id = candidate.job_id RETURNING jobs.*""",
                (
                    JobStatus.QUEUED.value, JobStatus.RUNNING.value, now, max_attempts,
                    JobStatus.RUNNING.value, now, lease_until, worker_id, token,
                ),
            )
            row = cursor.fetchone()
            return self._row(row) if row else None

    def complete(self, job_id: str, lease_token: str, result: dict[str, Any]) -> JobRecord:
        from psycopg.types.json import Jsonb
        return self._finish(job_id, lease_token, JobStatus.SUCCEEDED, result=Jsonb(result))

    def renew(self, job_id: str, lease_token: str, *, lease_seconds: int) -> bool:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now = self.clock()
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE tradingagents_jobs SET lease_until = %s, updated_at = %s
                   WHERE job_id = %s AND status = %s AND lease_token = %s""",
                (
                    now + timedelta(seconds=lease_seconds), now, job_id,
                    JobStatus.RUNNING.value, lease_token,
                ),
            )
            return cursor.rowcount == 1

    def fail(self, job_id: str, lease_token: str, code: str, message: str) -> JobRecord:
        return self._finish(
            job_id, lease_token, JobStatus.FAILED, error_code=code, error_message=message
        )

    def _finish(self, job_id, lease_token, status, *, result=None, error_code=None, error_message=None):
        now = self.clock()
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE tradingagents_jobs SET status = %s, updated_at = %s,
                   lease_until = NULL, worker_id = NULL, lease_token = NULL,
                   result_json = %s, error_code = %s, error_message = %s
                   WHERE job_id = %s AND status = %s AND lease_token = %s RETURNING *""",
                (
                    status.value, now, result, error_code, error_message, job_id,
                    JobStatus.RUNNING.value, lease_token,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                raise LeaseConflict("job lease is no longer valid")
            return self._row(row)

    def cancel(self, job_id: str) -> JobRecord | None:
        now = self.clock()
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE tradingagents_jobs SET status = %s, updated_at = %s,
                   lease_until = NULL, worker_id = NULL, lease_token = NULL
                   WHERE job_id = %s AND status IN (%s, %s) RETURNING *""",
                (
                    JobStatus.CANCELLED.value, now, job_id,
                    JobStatus.QUEUED.value, JobStatus.RUNNING.value,
                ),
            )
            row = cursor.fetchone()
        return self._row(row) if row else self.get(job_id)

    def list_recent(self, limit: int = 50) -> list[JobRecord]:
        limit = max(1, min(int(limit), 200))
        with self._connect() as connection:
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT %s", (limit,)
                )
                rows = cur.fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(row: dict[str, Any]) -> JobRecord:
        if row["schema_version"] != JOB_SCHEMA_VERSION:
            raise ValueError("job schema is incompatible")
        return JobRecord(
            job_id=row["job_id"], schema=row["schema_version"],
            idempotency_key=row["idempotency_key"], request_sha256=row["request_sha256"],
            payload=dict(row["payload_json"]), status=JobStatus(row["status"]),
            attempts=row["attempts"], created_at=row["created_at"], updated_at=row["updated_at"],
            lease_until=row["lease_until"], worker_id=row["worker_id"], lease_token=row["lease_token"],
            result=dict(row["result_json"]) if row["result_json"] else None,
            error_code=row["error_code"], error_message=row["error_message"],
        )

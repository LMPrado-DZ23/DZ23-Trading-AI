"""Fail-closed process entry points for the API and durable worker."""

from __future__ import annotations

import os
import time
from pathlib import Path

from .api import create_app
from .executor import TradingJobHandler
from .store import JobStore, PostgresJobStore, SQLiteJobStore
from .worker import JobWorker


def configured_store() -> JobStore:
    environment = os.environ.get("TRADINGAGENTS_ENV", "production").strip().lower()
    dsn = os.environ.get("TRADINGAGENTS_DATABASE_URL", "").strip()
    if dsn:
        return PostgresJobStore(dsn)
    if environment != "development":
        raise RuntimeError(
            "TRADINGAGENTS_DATABASE_URL is required outside explicit development mode"
        )
    path = Path(
        os.environ.get(
            "TRADINGAGENTS_SQLITE_PATH",
            ".tradingagents-runtime/jobs.sqlite",
        )
    )
    return SQLiteJobStore(path)


def api_main() -> None:
    import uvicorn

    host = os.environ.get("TRADINGAGENTS_API_HOST", "127.0.0.1")
    port = int(os.environ.get("TRADINGAGENTS_API_PORT", "8000"))
    uvicorn.run(create_app(configured_store()), host=host, port=port, access_log=False)


def worker_main() -> None:
    store = configured_store()
    store.initialize()
    worker = JobWorker(
        store,
        TradingJobHandler(),
        lease_seconds=int(os.environ.get("TRADINGAGENTS_JOB_LEASE_SECONDS", "300")),
        max_attempts=int(os.environ.get("TRADINGAGENTS_JOB_MAX_ATTEMPTS", "3")),
    )
    poll_seconds = float(os.environ.get("TRADINGAGENTS_JOB_POLL_SECONDS", "1"))
    if poll_seconds <= 0:
        raise ValueError("TRADINGAGENTS_JOB_POLL_SECONDS must be positive")
    try:
        while True:
            if worker.run_once() is None:
                time.sleep(poll_seconds)
    except KeyboardInterrupt:
        return

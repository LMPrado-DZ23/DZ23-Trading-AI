"""Single-use subprocess entrypoint for one bounded analysis attempt."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from uuid import UUID

from tradingagents.platform.jobs import AnalysisJobCommand, ClaimedJob
from tradingagents.platform.llm_connections import ResolvedLlmCredential
from tradingagents.platform.run_context import AnalysisRunContext
from tradingagents.platform.usage import JobUsage, PricingCatalog
from tradingagents.platform.worker import (
    ExecutionControl,
    GraphExecutionFailure,
    ProductionGraphExecutor,
)


def _claim(payload: dict) -> ClaimedJob:
    return ClaimedJob(
        id=UUID(payload["id"]),
        run_id=UUID(payload["run_id"]),
        tenant_id=UUID(payload["tenant_id"]),
        requested_by_user_id=UUID(payload["requested_by_user_id"]),
        lease_id=UUID(payload["lease_id"]),
        lease_owner=payload["lease_owner"],
        lease_expires_at=datetime.fromisoformat(payload["lease_expires_at"]),
        command=AnalysisJobCommand.model_validate(payload["command"]),
        run_context=AnalysisRunContext.model_validate(payload["run_context"]),
        attempts=payload["attempts"],
        prior_usage=JobUsage(**payload["prior_usage"]),
        claimed_at=datetime.fromisoformat(payload["claimed_at"]),
    )


def main() -> int:
    payload: dict = {}
    try:
        payload = json.loads(sys.stdin.buffer.read())
        claim = _claim(payload["claim"])
        raw_credential = payload["credential"]
        credential = ResolvedLlmCredential(
            connection_id=UUID(raw_credential["connection_id"]),
            provider=raw_credential["provider"],
            base_url=raw_credential["base_url"],
            version=raw_credential["version"],
            api_key=raw_credential["api_key"],
        )
        executor = ProductionGraphExecutor(
            runtime_root=Path(payload["runtime_root"]),
            pricing=PricingCatalog(payload["pricing"]),
            request_timeout_seconds=payload["request_timeout_seconds"],
            isolate_process=False,
        )
        control = ExecutionControl(
            lease_lost=threading.Event(),
            deadline_monotonic=time.monotonic() + payload["remaining_seconds"],
        )
        outcome = executor(claim, credential, control)
        result = {
            "kind": "success",
            "result": outcome.result,
            "manifest": outcome.manifest,
            "usage": asdict(outcome.usage),
        }
    except GraphExecutionFailure as exc:
        result = {
            "kind": "failure",
            "code": exc.code,
            "retryable": exc.retryable,
            "usage": asdict(exc.usage),
            "partial_manifest": exc.partial_manifest,
        }
    except BaseException:
        result = {
            "kind": "failure",
            "code": "WORKER_EXECUTION_FAILED",
            "retryable": False,
            "usage": asdict(JobUsage()),
            "partial_manifest": None,
        }
    result_path = Path(payload.get("result_path", ""))
    if not result_path.is_absolute():
        return 2
    temporary = result_path.with_name(result_path.name + ".tmp")
    encoded = json.dumps(result, separators=(",", ":"), default=str).encode()
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    os.replace(temporary, result_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

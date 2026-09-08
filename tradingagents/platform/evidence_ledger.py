"""Per-run, append-only provenance ledger for external data tool calls."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tradingagents.dataflows.evidence import UntrustedEvidence
from tradingagents.dataflows.redact import redact_secrets


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    sequence: int
    method: str
    vendor: str
    outcome: str
    request: dict[str, Any]
    request_digest: str
    decision_at: str
    fetched_at: str
    available_at: str | None
    availability_basis: str | None
    content_digest: str | None
    exclusion_reason: str | None


class EvidenceLedger:
    def __init__(self, journal_path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._records: list[EvidenceRecord] = []
        self._journal_path = journal_path

    def append(self, **fields: Any) -> None:
        with self._lock:
            record = EvidenceRecord(sequence=len(self._records) + 1, **fields)
            self._records.append(record)
            if self._journal_path is not None:
                self._journal_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                encoded = (
                    json.dumps(asdict(record), sort_keys=True, separators=(",", ":"))
                    + "\n"
                ).encode()
                descriptor = os.open(
                    self._journal_path,
                    os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                    0o600,
                )
                try:
                    with os.fdopen(descriptor, "ab", closefd=False) as handle:
                        handle.write(encoded)
                        handle.flush()
                        os.fsync(handle.fileno())
                finally:
                    os.close(descriptor)

    def snapshot(self) -> tuple[EvidenceRecord, ...]:
        with self._lock:
            return tuple(self._records)


_active_ledger: ContextVar[EvidenceLedger | None] = ContextVar(
    "tradingagents_evidence_ledger",
    default=None,
)


@contextmanager
def capture_evidence(journal_path: Path | None = None) -> Iterator[EvidenceLedger]:
    ledger = EvidenceLedger(journal_path=journal_path)
    token = _active_ledger.set(ledger)
    try:
        yield ledger
    finally:
        _active_ledger.reset(token)


def record_evidence(
    *,
    method: str,
    vendor: str,
    outcome: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    decision_at: str,
    available_at: str | None,
    availability_basis: str | None = None,
    content: Any = None,
    exclusion_reason: str | None = None,
) -> None:
    ledger = _active_ledger.get()
    if ledger is None:
        return
    request = {
        "args": [_safe_value(value) for value in args],
        "kwargs": {str(key): _safe_value(value) for key, value in sorted(kwargs.items())},
    }
    canonical_request = json.dumps(request, sort_keys=True, separators=(",", ":"))
    fetched_at = datetime.now(UTC).isoformat()
    if isinstance(content, UntrustedEvidence):
        fetched_at = content.fetched_at
        available_at = content.available_at
        content_digest = content.digest
    elif content is None:
        content_digest = None
    else:
        content_digest = hashlib.sha256(str(content).encode("utf-8")).hexdigest()
    ledger.append(
        method=method,
        vendor=vendor,
        outcome=outcome,
        request=request,
        request_digest=hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        decision_at=decision_at,
        fetched_at=fetched_at,
        available_at=available_at,
        availability_basis=availability_basis,
        content_digest=content_digest,
        exclusion_reason=exclusion_reason,
    )


def serialize_evidence(ledger: EvidenceLedger) -> list[dict[str, Any]]:
    return [asdict(record) for record in ledger.snapshot()]


def record_untrusted_evidence(
    evidence: UntrustedEvidence,
    *,
    method: str,
    outcome: str,
    args: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
    availability_basis: str | None = None,
    exclusion_reason: str | None = None,
) -> None:
    """Append the exact evidence object that crossed a prompt boundary.

    Direct prefetchers do not pass through ``route_to_vendor``.  Recording at
    this boundary makes the manifest complete for both routed and direct data
    paths without inventing an availability timestamp.
    """
    from tradingagents.platform.run_context import current_run_context

    context = current_run_context()
    if context is None:
        return
    record_evidence(
        method=method,
        vendor=evidence.source,
        outcome=outcome,
        args=args,
        kwargs=kwargs or {},
        decision_at=context.decision_at.isoformat(),
        available_at=evidence.available_at,
        availability_basis=availability_basis,
        content=evidence,
        exclusion_reason=exclusion_reason,
    )


def _safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact_secrets(value)[:512]
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value[:50]]
    if isinstance(value, dict):
        return {
            str(key)[:80]: _safe_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))[:50]
        }
    return f"<{type(value).__name__}>"

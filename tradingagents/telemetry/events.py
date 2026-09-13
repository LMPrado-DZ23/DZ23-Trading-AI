from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: str
    event_type: str
    schema_version: str
    sequence: int
    aggregate_type: str
    aggregate_id: str
    run_id: str
    occurred_at: str
    actor: str
    payload: dict[str, Any]
    payload_hash: str
    previous_event_hash: str

    @classmethod
    def create(cls, *, event_id: str, event_type: str, sequence: int,
               aggregate_type: str, aggregate_id: str, run_id: str,
               actor: str, payload: dict[str, Any], previous_event_hash: str = "",
               schema_version: str = "1.0", occurred_at: datetime | None = None) -> AuditEvent:
        stamp = (occurred_at or datetime.now(timezone.utc)).isoformat()
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        return cls(event_id, event_type, schema_version, sequence, aggregate_type,
                   aggregate_id, run_id, stamp, actor, payload, digest, previous_event_hash)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def event_hash(self) -> str:
        canonical = json.dumps(self.as_dict(), sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def verify(self) -> None:
        canonical = json.dumps(self.payload, sort_keys=True, ensure_ascii=False, default=str)
        expected = hashlib.sha256(canonical.encode()).hexdigest()
        if expected != self.payload_hash:
            raise ValueError(f"payload_hash inválido no evento {self.event_id}")

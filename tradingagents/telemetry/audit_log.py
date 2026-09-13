from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from .events import AuditEvent


class AuditLog:
    """Journal local append-only para desenvolvimento e Paper Trading."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: AuditEvent) -> AuditEvent:
        event.verify()
        existing = {item.event_id for item in self.read()}
        if event.event_id in existing:
            return event
        previous = ""
        for item in self.read():
            previous = item.event_hash()
        if event.previous_event_hash and event.previous_event_hash != previous:
            raise ValueError("quebra no hash encadeado do journal")
        if not event.previous_event_hash and previous:
            event = AuditEvent(**{**event.as_dict(), "previous_event_hash": previous})
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.as_dict(), ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
        return event

    def read(self) -> list[AuditEvent]:
        if not self.path.exists():
            return []
        events: list[AuditEvent] = []
        with self.path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    event = AuditEvent(**json.loads(line))
                    event.verify()
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ValueError(f"evento inválido na linha {line_number}") from exc
                events.append(event)
        return events

    def by_run(self, run_id: str) -> Iterable[AuditEvent]:
        return (event for event in self.read() if event.run_id == run_id)

    def verify_chain(self) -> None:
        previous = ""
        for event in self.read():
            if event.previous_event_hash != previous:
                raise ValueError(f"hash anterior inválido no evento {event.event_id}")
            previous = event.event_hash()

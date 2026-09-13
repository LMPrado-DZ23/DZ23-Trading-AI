from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock

from .events import AuditEvent


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    sequence: int
    event: AuditEvent


class EventBus:
    """Barramento local para desenvolvimento; persistência deve usar AuditLog."""

    def __init__(self):
        self._lock = Lock()
        self._sequence = 0
        self._events: list[EventEnvelope] = []
        self._seen: set[str] = set()
        self._subscribers: dict[str, list[Callable[[EventEnvelope], None]]] = defaultdict(list)

    def publish(self, event: AuditEvent) -> EventEnvelope:
        with self._lock:
            if event.event_id in self._seen:
                return next(item for item in self._events if item.event.event_id == event.event_id)
            self._sequence += 1
            envelope = EventEnvelope(self._sequence, event)
            self._events.append(envelope)
            self._seen.add(event.event_id)
            callbacks = list(self._subscribers.get(event.event_type, ())) + list(self._subscribers.get("*", ()))
        for callback in callbacks:
            callback(envelope)
        return envelope

    def subscribe(self, event_type: str, callback: Callable[[EventEnvelope], None]) -> None:
        self._subscribers[event_type].append(callback)

    def replay(self, after_sequence: int = 0) -> list[EventEnvelope]:
        with self._lock:
            return [item for item in self._events if item.sequence > after_sequence]

    @property
    def latest_sequence(self) -> int:
        return self._sequence

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event, Thread
from time import monotonic


@dataclass(slots=True)
class WorkerState:
    name: str
    status: str = "STOPPED"
    heartbeat_at: datetime | None = None
    failures: int = 0
    last_error: str = ""


class WorkerSupervisor:
    """Supervisor simples para execução contínua; produção deve usar um process manager."""

    def __init__(self):
        self._stop = Event()
        self._threads: list[Thread] = []
        self.states: dict[str, WorkerState] = {}

    def add(self, name: str, target: Callable[[], None], *, interval_seconds: float = 5.0) -> WorkerState:
        if name in self.states:
            raise ValueError(f"worker duplicado: {name}")
        state = WorkerState(name)
        self.states[name] = state

        def loop() -> None:
            state.status = "RUNNING"
            delay = interval_seconds
            while not self._stop.is_set():
                started = monotonic()
                try:
                    target()
                    state.failures = 0
                    state.last_error = ""
                    state.status = "RUNNING"
                    delay = interval_seconds
                except Exception as exc:  # worker não derruba o supervisor
                    state.failures += 1
                    state.status = "DEGRADED"
                    state.last_error = f"{type(exc).__name__}: {exc}"[:500]
                    delay = min(delay * 2, 300)
                state.heartbeat_at = datetime.now(timezone.utc)
                self._stop.wait(max(delay - (monotonic() - started), 0.01))
            state.status = "STOPPED"

        self._threads.append(Thread(target=loop, name=f"dz23-{name}", daemon=True))
        return state

    def start(self) -> None:
        for thread in self._threads:
            if not thread.is_alive():
                thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=timeout)

    def health(self) -> dict[str, dict]:
        return {name: {"name": state.name, "status": state.status, "heartbeat_at": state.heartbeat_at.isoformat() if state.heartbeat_at else None, "failures": state.failures, "last_error": state.last_error} for name, state in self.states.items()}

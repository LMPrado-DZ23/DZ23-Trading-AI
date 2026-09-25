from __future__ import annotations

from datetime import datetime, timezone
from threading import Event, Lock, Thread, current_thread
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tradingagents.agency_runtime import AgencyRuntime


class AgencyWorker:
    """Loop durável e explicitamente controlado para um tenant.

    O worker não cria credenciais, não habilita Live e não inicia sozinho.
    Cada ciclo delega ao mesmo executor governado usado pela API manual.
    """

    def __init__(self, runtime: AgencyRuntime):
        self.runtime = runtime
        self._lock = Lock()
        self._stop = Event()
        self._thread: Thread | None = None
        self._tenant_id = ""
        self._poll_interval = 1.0
        self._cycle_timeout = 30.0
        self._started_at: datetime | None = None
        self._last_cycle_at: datetime | None = None
        self._last_error = ""
        self._cycles = 0
        self._error_count = 0
        self._last_result: dict[str, Any] = {}

    def start(self, tenant_id: str, *, poll_interval: float = 1.0, cycle_timeout: float = 30.0) -> dict[str, Any]:
        if not tenant_id.strip():
            raise ValueError("tenant_id obrigatório")
        if not 0.1 <= poll_interval <= 60:
            raise ValueError("poll_interval deve estar entre 0.1 e 60 segundos")
        if not 0.1 <= cycle_timeout <= 120:
            raise ValueError("cycle_timeout deve estar entre 0.1 e 120 segundos")
        with self._lock:
            if self._thread and self._thread.is_alive():
                if self._tenant_id != tenant_id:
                    raise RuntimeError("worker já está ligado para outro tenant")
                already_running = True
            else:
                already_running = False
            if not already_running:
                self._tenant_id = tenant_id
                self._poll_interval = poll_interval
                self._cycle_timeout = cycle_timeout
                self._stop.clear()
                self._started_at = datetime.now(timezone.utc)
                self._last_error = ""
                self._thread = Thread(target=self._loop, name=f"dz23-agency-{tenant_id}", daemon=True)
                self._thread.start()
        return self.status()

    def stop(self, *, timeout: float = 5.0) -> dict[str, Any]:
        if timeout <= 0:
            raise ValueError("timeout deve ser positivo")
        with self._lock:
            thread = self._thread
            self._stop.set()
        if thread and thread is not current_thread():
            thread.join(timeout=timeout)
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            alive = bool(self._thread and self._thread.is_alive())
            return {
                "health": "degraded" if self._last_error else "ready",
                "running": alive,
                "tenant_id": self._tenant_id,
                "poll_interval": self._poll_interval,
                "cycle_timeout": self._cycle_timeout,
                "started_at": self._started_at.isoformat() if self._started_at else None,
                "last_cycle_at": self._last_cycle_at.isoformat() if self._last_cycle_at else None,
                "last_error": self._last_error,
                "error_count": self._error_count,
                "cycles": self._cycles,
                "last_result": self._last_result,
            }

    def _loop(self) -> None:
        delay = self._poll_interval
        while not self._stop.is_set():
            try:
                result = self.runtime.run_tasks(self._tenant_id, timeout=self._cycle_timeout)
                with self._lock:
                    self._last_result = {
                        "task_count": len(result.get("tasks", [])),
                        "workers": result.get("workers", 0),
                    }
                    self._last_error = ""
                    self._last_cycle_at = datetime.now(timezone.utc)
                    self._cycles += 1
                    delay = self._poll_interval
            except Exception as exc:  # o worker permanece disponível para o próximo ciclo
                with self._lock:
                    self._last_error = type(exc).__name__
                    self._error_count += 1
                    self._last_cycle_at = datetime.now(timezone.utc)
                    self._cycles += 1
                delay = min(delay * 2, 30.0)
            self._stop.wait(delay)

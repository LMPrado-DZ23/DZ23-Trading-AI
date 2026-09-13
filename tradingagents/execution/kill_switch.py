from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock


@dataclass(frozen=True, slots=True)
class KillSwitchState:
    enabled: bool
    reason: str
    changed_at: datetime
    actor: str


class KillSwitch:
    """Bloqueio independente do frontend e dos agentes; inicia bloqueado somente quando acionado."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._state = KillSwitchState(False, "", datetime.now(timezone.utc), "system")

    def engage(self, reason: str, *, actor: str = "system") -> KillSwitchState:
        if not reason.strip():
            raise ValueError("motivo do kill switch é obrigatório")
        with self._lock:
            self._state = KillSwitchState(True, reason.strip(), datetime.now(timezone.utc), actor)
            return self._state

    def reset(self, *, actor: str, reconciliation_ok: bool) -> KillSwitchState:
        if not actor.strip():
            raise ValueError("ator obrigatório")
        if not reconciliation_ok:
            raise PermissionError("não é possível desbloquear sem reconciliação consistente")
        with self._lock:
            self._state = KillSwitchState(False, "", datetime.now(timezone.utc), actor)
            return self._state

    def assert_allowed(self) -> None:
        with self._lock:
            if self._state.enabled:
                raise PermissionError(f"execução bloqueada pelo kill switch: {self._state.reason}")

    @property
    def state(self) -> KillSwitchState:
        with self._lock:
            return self._state

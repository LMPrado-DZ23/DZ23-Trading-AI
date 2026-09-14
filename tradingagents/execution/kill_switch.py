from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock


@dataclass(frozen=True, slots=True)
class KillSwitchState:
    enabled: bool
    reason: str
    changed_at: datetime
    actor: str


class KillSwitch:
    """Bloqueio persistente e independente do frontend/agentes."""

    def __init__(self, state_path: str | Path | None = None) -> None:
        self._lock = Lock()
        self._path = Path(state_path) if state_path else None
        self._state = self._load()

    def _load(self) -> KillSwitchState:
        if not self._path or not self._path.exists():
            return KillSwitchState(False, "", datetime.now(timezone.utc), "system")
        try:
            body = json.loads(self._path.read_text(encoding="utf-8"))
            return KillSwitchState(bool(body["enabled"]), str(body.get("reason", "")), datetime.fromisoformat(body["changed_at"]), str(body.get("actor", "system")))
        except (OSError, ValueError, KeyError, TypeError):
            return KillSwitchState(True, "estado do kill switch inválido", datetime.now(timezone.utc), "system")

    def _save(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(json.dumps({"enabled": self._state.enabled, "reason": self._state.reason, "changed_at": self._state.changed_at.isoformat(), "actor": self._state.actor}), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self._path)

    def engage(self, reason: str, *, actor: str = "system") -> KillSwitchState:
        if not reason.strip():
            raise ValueError("motivo do kill switch é obrigatório")
        with self._lock:
            self._state = KillSwitchState(True, reason.strip(), datetime.now(timezone.utc), actor)
            self._save()
            return self._state

    def reset(self, *, actor: str, reconciliation_ok: bool) -> KillSwitchState:
        if not actor.strip():
            raise ValueError("ator obrigatório")
        if not reconciliation_ok:
            raise PermissionError("não é possível desbloquear sem reconciliação consistente")
        with self._lock:
            self._state = KillSwitchState(False, "", datetime.now(timezone.utc), actor)
            self._save()
            return self._state

    def assert_allowed(self) -> None:
        with self._lock:
            if self._state.enabled:
                raise PermissionError(f"execução bloqueada pelo kill switch: {self._state.reason}")

    @property
    def state(self) -> KillSwitchState:
        with self._lock:
            return self._state

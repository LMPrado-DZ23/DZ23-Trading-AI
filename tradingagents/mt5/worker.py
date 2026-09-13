from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .adapter import MT5Adapter


@dataclass(slots=True)
class MT5Worker:
    adapter: MT5Adapter
    last_heartbeat: datetime | None = None
    state: str = "STARTING"

    def start(self, **kwargs) -> bool:
        self.state = "READY" if self.adapter.initialize(**kwargs) else "DISCONNECTED"
        self.heartbeat()
        return self.state == "READY"

    def heartbeat(self) -> dict:
        self.last_heartbeat = datetime.now(timezone.utc)
        health = self.adapter.healthcheck()
        self.state = health["status"]
        return {**health, "heartbeat": self.last_heartbeat.isoformat()}

    def stop(self) -> None:
        self.adapter.shutdown()
        self.state = "STOPPING"

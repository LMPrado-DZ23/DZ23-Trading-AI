from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from tradingagents.telemetry import AuditEvent, AuditLog
from tradingagents.telemetry.event_bus import EventBus


@dataclass(slots=True)
class Meeting:
    meeting_id: str
    meeting_type: str
    desk_id: str
    participants: tuple[str, ...]
    quorum: int
    status: str = "SCHEDULED"
    votes: list[dict[str, Any]] = field(default_factory=list)

    def record_vote(self, agent_id: str, action: str, confidence: float, evidence_ids: list[str]) -> None:
        if self.status not in {"SCHEDULED", "RUNNING"}:
            raise ValueError("reunião não está recebendo votos")
        if agent_id not in self.participants:
            raise ValueError("agente não participa desta reunião")
        if any(v["agent_id"] == agent_id for v in self.votes):
            raise ValueError("agente já votou")
        if not 0 <= confidence <= 1:
            raise ValueError("confidence deve estar entre 0 e 1")
        self.status = "RUNNING"
        self.votes.append({"agent_id": agent_id, "action": action, "confidence": confidence, "evidence_ids": evidence_ids})
        if len(self.votes) >= self.quorum:
            self.status = "QUORUM_REACHED"


class AgencyRuntime:
    def __init__(self, audit_log: AuditLog | None = None, event_bus: EventBus | None = None):
        self.audit_log = audit_log
        self.event_bus = event_bus or EventBus()
        self.meetings: dict[str, Meeting] = {}
        self.agents: dict[str, dict[str, Any]] = {}
        self.desks: dict[str, dict[str, Any]] = {}

    def register_agent(self, agent_id: str, desk_id: str, role: str) -> None:
        if agent_id in self.agents:
            raise ValueError(f"agente já registrado: {agent_id}")
        self.agents[agent_id] = {"agent_id": agent_id, "desk_id": desk_id, "role": role, "status": "WAITING_SETUP"}
        desk = self.desks.setdefault(desk_id, {"desk_id": desk_id, "status": "WAITING_SETUP", "agents": []})
        desk["agents"].append(agent_id)

    def set_agent_status(self, agent_id: str, status: str) -> AuditEvent:
        if agent_id not in self.agents:
            raise KeyError(agent_id)
        self.agents[agent_id]["status"] = status
        event = AuditEvent.create(event_id=f"agent-status-{agent_id}-{status}", event_type="agent.status.changed", sequence=self.event_bus.latest_sequence + 1, aggregate_type="agent", aggregate_id=agent_id, run_id="agency", actor="runtime", payload=self.agents[agent_id])
        self._emit(event)
        return event

    def start_meeting(self, meeting_id: str, meeting_type: str, desk_id: str, participants: tuple[str, ...], quorum: int) -> Meeting:
        if meeting_id in self.meetings:
            raise ValueError(f"reunião já existe: {meeting_id}")
        if not participants or quorum <= 0 or quorum > len(participants):
            raise ValueError("quorum inválido")
        meeting = Meeting(meeting_id, meeting_type, desk_id, participants, quorum)
        self.meetings[meeting_id] = meeting
        return meeting

    def snapshot(self) -> dict[str, Any]:
        return {"agents": list(self.agents.values()), "desks": list(self.desks.values()), "meetings": [asdict(meeting) for meeting in self.meetings.values()], "sequence": self.event_bus.latest_sequence}

    def _emit(self, event: AuditEvent) -> None:
        if self.audit_log:
            self.audit_log.append(event)
        self.event_bus.publish(event)

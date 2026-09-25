from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4

from tradingagents.agency import AgencyExecutor, AgencyTask
from tradingagents.agency.store import AgencyTaskStore
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
    def __init__(self, audit_log: AuditLog | None = None, event_bus: EventBus | None = None,
                 store: AgencyTaskStore | None = None):
        self.audit_log = audit_log
        self.event_bus = event_bus or EventBus()
        self.executor = AgencyExecutor(event_bus=self.event_bus, store=store)
        self.store = store
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
        return {"agents": list(self.agents.values()), "desks": list(self.desks.values()), "meetings": [asdict(meeting) for meeting in self.meetings.values()], "tasks": self.executor.snapshot(), "sequence": self.event_bus.latest_sequence}

    def register_task_handler(self, agent_id: str, handler) -> None:
        """Registra execução real; ausência de handler não vira sucesso falso."""
        if agent_id not in self.agents:
            raise KeyError(agent_id)
        self.executor.register_handler(agent_id, handler)

    def submit_task(self, task: AgencyTask) -> AgencyTask:
        if task.agent_id not in self.agents:
            raise KeyError(task.agent_id)
        return self.executor.submit(task)

    def create_mission(self, tenant_id: str, objective: str, agent_ids: tuple[str, ...], *, requires_approval: bool = False) -> dict[str, Any]:
        objective = objective.strip()
        if not objective:
            raise ValueError("objective obrigatório")
        if not agent_ids:
            raise ValueError("ao menos um agente é obrigatório")
        unknown = [agent_id for agent_id in agent_ids if agent_id not in self.agents]
        if unknown:
            raise KeyError(unknown[0])
        mission_id = f"mission-{uuid4().hex[:16]}"
        tasks = []
        for agent_id in agent_ids:
            task_id = f"{mission_id}-{agent_id}"
            tasks.append(self.submit_task(AgencyTask(
                task_id, mission_id, tenant_id, agent_id, "mission",
                {"objective": objective, "remember": True},
                f"{tenant_id}:{mission_id}:{agent_id}",
                requires_approval=requires_approval)))
        return {"mission_id": mission_id, "objective": objective,
                "tasks": [self.executor._serialize(task) for task in tasks]}

    def missions_for_tenant(self, tenant_id: str) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for item in self.tasks_for_tenant(tenant_id):
            mission = grouped.setdefault(item["mission_id"], {"mission_id": item["mission_id"], "tasks": []})
            mission["tasks"].append(item)
        return list(grouped.values())

    def tasks_for_tenant(self, tenant_id: str) -> list[dict[str, Any]]:
        if not tenant_id.strip():
            raise ValueError("tenant_id obrigatório")
        return [item for item in self.executor.snapshot()["tasks"]
                if item["tenant_id"] == tenant_id]

    def approve_task(self, task_id: str, tenant_id: str) -> AgencyTask:
        task = self.executor.tasks.get(task_id)
        if task is None or task.tenant_id != tenant_id:
            raise KeyError(task_id)
        return self.executor.approve(task_id)

    def run_tasks(self, tenant_id: str, *, timeout: float = 30.0) -> dict[str, Any]:
        if not tenant_id.strip():
            raise ValueError("tenant_id obrigatório")
        self.executor.run_until_idle(timeout=timeout)
        return {"tasks": self.tasks_for_tenant(tenant_id),
                "workers": self.executor.max_workers}

    def recall_memory(self, tenant_id: str, *, query: str = "", limit: int = 20) -> list[dict[str, Any]]:
        if not self.store:
            return []
        return self.store.recall(tenant_id, query=query, limit=limit)

    def _emit(self, event: AuditEvent) -> None:
        if self.audit_log:
            self.audit_log.append(event)
        self.event_bus.publish(event)

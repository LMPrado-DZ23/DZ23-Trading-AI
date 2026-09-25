from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class TaskStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_DEPENDENCY = "WAITING_DEPENDENCY"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    RETRYING = "RETRYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class AgentSpec:
    agent_id: str
    module: str
    role: str
    version: str = "1.0"
    capabilities: tuple[str, ...] = ()
    max_concurrency: int = 1
    enabled: bool = True

    def __post_init__(self) -> None:
        for name in ("agent_id", "module", "role", "version"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} obrigatório")
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency deve ser positivo")


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    evidence_id: str
    source: str
    content_hash: str
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.evidence_id.strip() or not self.source.strip() or not self.content_hash.strip():
            raise ValueError("evidência exige id, fonte e hash")


@dataclass(slots=True)
class AgencyTask:
    task_id: str
    mission_id: str
    tenant_id: str
    agent_id: str
    kind: str
    payload: dict[str, Any]
    idempotency_key: str
    dependencies: tuple[str, ...] = ()
    requires_approval: bool = False
    status: TaskStatus = TaskStatus.QUEUED
    attempts: int = 0
    max_attempts: int = 2
    error: str = ""
    result: TaskResult | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        for name in ("task_id", "mission_id", "tenant_id", "agent_id", "kind", "idempotency_key"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} obrigatório")
        if self.max_attempts < 1:
            raise ValueError("max_attempts deve ser positivo")
        if self.task_id in self.dependencies:
            raise ValueError("tarefa não pode depender de si mesma")


@dataclass(frozen=True, slots=True)
class TaskResult:
    task_id: str
    status: TaskStatus
    output: dict[str, Any] = field(default_factory=dict)
    evidence: tuple[EvidenceRef, ...] = ()
    error: str = ""
    finished_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

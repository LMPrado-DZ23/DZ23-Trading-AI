from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .contracts import AgencyTask, EvidenceRef, TaskResult, TaskStatus


class AgencyTaskStore:
    """Persistência local durável do control plane da agência.

    A API é deliberadamente pequena para permitir um backend PostgreSQL futuro
    sem acoplar o executor a SQL. Todas as consultas são tenant-scoped.
    """

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""CREATE TABLE IF NOT EXISTS agency_tasks(
                task_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, mission_id TEXT NOT NULL,
                agent_id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
                idem TEXT NOT NULL, dependencies TEXT NOT NULL, requires_approval INTEGER NOT NULL,
                status TEXT NOT NULL, attempts INTEGER NOT NULL, max_attempts INTEGER NOT NULL,
                error TEXT NOT NULL, result TEXT, created REAL NOT NULL, updated REAL NOT NULL,
                lease_until REAL, UNIQUE(tenant_id, idem))""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_agency_tasks_ready ON agency_tasks(tenant_id, status, updated)")
            conn.execute("""CREATE TABLE IF NOT EXISTS agency_memory(
                memory_id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL,
                mission_id TEXT NOT NULL, kind TEXT NOT NULL, content TEXT NOT NULL,
                evidence TEXT NOT NULL, created REAL NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_agency_memory_tenant ON agency_memory(tenant_id, created DESC)")

    def insert(self, task: AgencyTask) -> None:
        now = task.updated_at.timestamp()
        with self._connect() as conn:
            conn.execute("""INSERT INTO agency_tasks(
                task_id,tenant_id,mission_id,agent_id,kind,payload,idem,dependencies,
                requires_approval,status,attempts,max_attempts,error,result,created,updated,lease_until)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)""", (
                task.task_id, task.tenant_id, task.mission_id, task.agent_id, task.kind,
                json.dumps(task.payload, ensure_ascii=False, default=str), task.idempotency_key,
                json.dumps(task.dependencies), int(task.requires_approval), task.status.value,
                task.attempts, task.max_attempts, task.error, None, task.created_at.timestamp(), now))

    def update(self, task: AgencyTask, *, lease_seconds: float | None = None) -> None:
        lease_until = time.time() + lease_seconds if lease_seconds else None
        result = None
        if task.result:
            result = json.dumps({
                "task_id": task.result.task_id, "status": task.result.status.value,
                "output": task.result.output,
                "evidence": [e.__dict__ if hasattr(e, "__dict__") else {
                    "evidence_id": e.evidence_id, "source": e.source,
                    "content_hash": e.content_hash, "observed_at": e.observed_at.isoformat()
                } for e in task.result.evidence], "error": task.result.error,
                "finished_at": task.result.finished_at.isoformat()}, ensure_ascii=False, default=str)
        with self._connect() as conn:
            conn.execute("""UPDATE agency_tasks SET status=?,attempts=?,error=?,result=?,updated=?,lease_until=?
                WHERE task_id=? AND tenant_id=?""", (
                task.status.value, task.attempts, task.error, result, time.time(), lease_until,
                task.task_id, task.tenant_id))

    def claim(self, task_id: str, *, lease_seconds: float) -> bool:
        """Obtém lease apenas se a tarefa ainda estiver pronta para execução."""
        with self._connect() as conn:
            cur = conn.execute("""UPDATE agency_tasks SET status='RUNNING', lease_until=?, updated=?
                WHERE task_id=? AND status IN ('QUEUED','WAITING_DEPENDENCY')""",
                (time.time() + lease_seconds, time.time(), task_id))
            return cur.rowcount == 1

    def load(self) -> list[AgencyTask]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM agency_tasks ORDER BY created").fetchall()
        return [self._task_from_row(row) for row in rows]

    def recover_expired(self) -> int:
        with self._connect() as conn:
            cur = conn.execute("""UPDATE agency_tasks SET status='QUEUED', lease_until=NULL, updated=?
                WHERE status='RUNNING' AND lease_until IS NOT NULL AND lease_until < ?""", (time.time(), time.time()))
            return cur.rowcount

    def remember(self, *, tenant_id: str, mission_id: str, kind: str, content: dict[str, Any], evidence: tuple[EvidenceRef, ...] = ()) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO agency_memory(tenant_id,mission_id,kind,content,evidence,created) VALUES(?,?,?,?,?,?)", (
                tenant_id, mission_id, kind, json.dumps(content, ensure_ascii=False, default=str),
                json.dumps([{"evidence_id": e.evidence_id, "source": e.source, "content_hash": e.content_hash, "observed_at": e.observed_at.isoformat()} for e in evidence]), time.time()))

    def recall(self, tenant_id: str, *, query: str = "", limit: int = 20) -> list[dict[str, Any]]:
        limit = min(max(limit, 1), 100)
        with self._connect() as conn:
            if query:
                rows = conn.execute("SELECT * FROM agency_memory WHERE tenant_id=? AND content LIKE ? ORDER BY created DESC LIMIT ?", (tenant_id, f"%{query}%", limit)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM agency_memory WHERE tenant_id=? ORDER BY created DESC LIMIT ?", (tenant_id, limit)).fetchall()
        return [{"memory_id": row["memory_id"], "mission_id": row["mission_id"], "kind": row["kind"], "content": json.loads(row["content"]), "evidence": json.loads(row["evidence"]), "created": row["created"]} for row in rows]

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> AgencyTask:
        result = None
        if row["result"]:
            raw = json.loads(row["result"])
            from datetime import datetime, timezone
            evidence = tuple(EvidenceRef(e["evidence_id"], e["source"], e["content_hash"], datetime.fromisoformat(e["observed_at"]) if e.get("observed_at") else datetime.now(timezone.utc)) for e in raw.get("evidence", []))
            result = TaskResult(row["task_id"], TaskStatus(raw["status"]), raw.get("output", {}), evidence, raw.get("error", ""), datetime.fromisoformat(raw["finished_at"]) if raw.get("finished_at") else datetime.now(timezone.utc))
        from datetime import datetime, timezone
        created = datetime.fromtimestamp(row["created"], timezone.utc)
        updated = datetime.fromtimestamp(row["updated"], timezone.utc)
        return AgencyTask(row["task_id"], row["mission_id"], row["tenant_id"], row["agent_id"], row["kind"], json.loads(row["payload"]), row["idem"], tuple(json.loads(row["dependencies"])), bool(row["requires_approval"]), TaskStatus(row["status"]), row["attempts"], row["max_attempts"], row["error"], result, created, updated)

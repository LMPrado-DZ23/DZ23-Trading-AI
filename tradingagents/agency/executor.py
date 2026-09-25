from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict
from threading import Lock
from typing import Any

from tradingagents.telemetry import AuditEvent, EventBus

from .contracts import AgencyTask, TaskResult, TaskStatus
from .store import AgencyTaskStore

TaskHandler = Callable[[AgencyTask], dict[str, Any] | TaskResult]
_SENSITIVE_TASK_KINDS = frozenset({"publish", "send_message", "payment", "trade_live", "credential_change", "external_write"})


class AgencyExecutor:
    """Executa tarefas independentes em paralelo, sem ultrapassar limites.

    O executor não conhece LLM, corretora nem ferramentas externas. Handlers
    são registrados pelo control plane e recebem apenas uma tarefa validada.
    Ações sensíveis ficam em REVIEW_REQUIRED até aprovação explícita.
    """

    def __init__(self, *, event_bus: EventBus | None = None, max_workers: int = 4,
                 store: AgencyTaskStore | None = None):
        if max_workers < 1:
            raise ValueError("max_workers deve ser positivo")
        self.event_bus = event_bus or EventBus()
        self.store = store
        self.max_workers = max_workers
        self.tasks: dict[str, AgencyTask] = {}
        self.handlers: dict[str, TaskHandler] = {}
        self._idempotency: dict[tuple[str, str], str] = {}
        self._lock = Lock()
        self._run_lock = Lock()
        self._stop = False
        if self.store:
            self.store.recover_expired()
            for task in self.store.load():
                self.tasks[task.task_id] = task
                self._idempotency[(task.tenant_id, task.idempotency_key)] = task.task_id

    def register_handler(self, agent_id: str, handler: TaskHandler) -> None:
        if not agent_id.strip():
            raise ValueError("agent_id obrigatório")
        with self._lock:
            if agent_id in self.handlers:
                raise ValueError(f"handler já registrado: {agent_id}")
            self.handlers[agent_id] = handler

    def submit(self, task: AgencyTask) -> AgencyTask:
        key = (task.tenant_id, task.idempotency_key)
        with self._lock:
            existing_id = self._idempotency.get(key)
            if existing_id:
                return self.tasks[existing_id]
            if task.task_id in self.tasks:
                raise ValueError(f"task_id já existe: {task.task_id}")
            missing = [dep for dep in task.dependencies if dep not in self.tasks]
            if missing:
                raise ValueError(f"dependência inexistente: {missing[0]}")
            foreign = [dep for dep in task.dependencies if self.tasks[dep].tenant_id != task.tenant_id]
            if foreign:
                raise ValueError(f"dependência pertence a outro tenant: {foreign[0]}")
            if task.requires_approval or task.kind in _SENSITIVE_TASK_KINDS or task.payload.get("external_action") is True:
                task.requires_approval = True
                task.status = TaskStatus.REVIEW_REQUIRED
            self.tasks[task.task_id] = task
            self._idempotency[key] = task.task_id
            if self.store:
                self.store.insert(task)
            self._emit("agency.task.submitted", task, {"status": task.status.value})
            return task

    def approve(self, task_id: str) -> AgencyTask:
        with self._lock:
            task = self._task(task_id)
            if task.status is not TaskStatus.REVIEW_REQUIRED:
                raise ValueError("tarefa não está aguardando aprovação")
            task.status = TaskStatus.QUEUED
            task.updated_at = self._now()
            self._persist(task)
            self._emit("agency.task.approved", task, {"status": task.status.value})
            return task

    def cancel(self, task_id: str) -> AgencyTask:
        with self._lock:
            task = self._task(task_id)
            if task.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                return task
            task.status = TaskStatus.CANCELLED
            task.updated_at = self._now()
            self._persist(task)
            self._emit("agency.task.cancelled", task, {"status": task.status.value})
            return task

    def run_until_idle(self, *, timeout: float = 30.0, tenant_id: str | None = None) -> dict[str, Any]:
        with self._run_lock:
            return self._run_until_idle(timeout=timeout, tenant_id=tenant_id)

    def _run_until_idle(self, *, timeout: float, tenant_id: str | None) -> dict[str, Any]:
        """Drena tarefas executáveis; retorna quando não há trabalho ativo."""
        futures: dict[Future, str] = {}
        with ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="dz23-agent") as pool:
            deadline = self._monotonic() + timeout
            while self._monotonic() < deadline and not self._stop:
                self._mark_blocked_dependencies(tenant_id)
                for task in self._ready_tasks(futures, tenant_id):
                    if self.store and not self.store.claim(task.task_id, tenant_id=tenant_id, lease_seconds=max(timeout, 30)):
                        continue
                    task.status = TaskStatus.RUNNING
                    task.attempts += 1
                    task.updated_at = self._now()
                    self._persist(task, lease_seconds=max(timeout, 30))
                    self._emit("agency.task.started", task, {"attempt": task.attempts})
                    futures[pool.submit(self._run_handler, task)] = task.task_id
                if not futures:
                    if not self._has_runnable_work(tenant_id):
                        break
                    self._sleep(0.01)
                    continue
                done, _ = wait(futures, timeout=max(deadline - self._monotonic(), 0), return_when=FIRST_COMPLETED)
                for future in done:
                    task_id = futures.pop(future)
                    self._finish(task_id, future)
            if futures:
                # Timeout is a soft scheduling deadline: drain handlers before
                # returning so no local side effect remains RUNNING and can be
                # duplicated by recovery while this process is still alive.
                done, _ = wait(futures)
                for future in done:
                    task_id = futures.pop(future)
                    self._finish(task_id, future)
        return self.snapshot()

    def stop(self) -> None:
        self._stop = True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"tasks": [self._serialize(task) for task in self.tasks.values()],
                    "workers": self.max_workers,
                    "pending": sum(task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING_DEPENDENCY} for task in self.tasks.values())}

    def _run_handler(self, task: AgencyTask) -> dict[str, Any] | TaskResult:
        handler = self.handlers.get(task.agent_id)
        if handler is None:
            raise LookupError(f"agente sem handler: {task.agent_id}")
        return handler(task)

    def _finish(self, task_id: str, future: Future) -> None:
        with self._lock:
            task = self._task(task_id)
            try:
                raw = future.result()
                result = raw if isinstance(raw, TaskResult) else TaskResult(task.task_id, TaskStatus.SUCCEEDED, dict(raw))
                if result.status is not TaskStatus.SUCCEEDED:
                    raise ValueError(result.error or "handler não concluiu com sucesso")
                task.result = result
                task.status = TaskStatus.SUCCEEDED
                task.error = ""
                self._persist(task)
                if self.store and result.output.get("remember"):
                    self.store.remember(tenant_id=task.tenant_id, mission_id=task.mission_id,
                                        kind=task.kind, content=result.output,
                                        evidence=result.evidence)
                self._emit("agency.task.succeeded", task, {"status": task.status.value, "output": result.output})
            except Exception as exc:
                task.error = f"{type(exc).__name__}: {exc}"[:500]
                if task.attempts < task.max_attempts:
                    task.status = TaskStatus.RETRYING
                    task.status = TaskStatus.QUEUED
                else:
                    task.status = TaskStatus.FAILED
                task.updated_at = self._now()
                self._persist(task)
                self._emit("agency.task.failed", task, {"status": task.status.value, "error": task.error})

    def _ready_tasks(self, futures: dict[Future, str], tenant_id: str | None = None) -> list[AgencyTask]:
        active_agents = {self.tasks[task_id].agent_id for task_id in futures.values()}
        ready = []
        for task in self.tasks.values():
            if tenant_id is not None and task.tenant_id != tenant_id:
                continue
            if task.status not in {TaskStatus.QUEUED, TaskStatus.RETRYING, TaskStatus.WAITING_DEPENDENCY}:
                continue
            if task.agent_id in active_agents:
                continue
            if all(self.tasks[dep].status is TaskStatus.SUCCEEDED for dep in task.dependencies):
                ready.append(task)
                active_agents.add(task.agent_id)
        return ready

    def _mark_blocked_dependencies(self, tenant_id: str | None = None) -> None:
        for task in self.tasks.values():
            if tenant_id is not None and task.tenant_id != tenant_id:
                continue
            if task.status not in {TaskStatus.QUEUED, TaskStatus.RETRYING, TaskStatus.WAITING_DEPENDENCY}:
                continue
            dependency_states = [self.tasks[dep].status for dep in task.dependencies]
            if any(state in {TaskStatus.FAILED, TaskStatus.BLOCKED, TaskStatus.CANCELLED} for state in dependency_states):
                task.status = TaskStatus.BLOCKED
                task.error = "dependência não concluída"
                self._persist(task)
                self._emit("agency.task.blocked", task, {"status": task.status.value})
            elif not all(state is TaskStatus.SUCCEEDED for state in dependency_states):
                task.status = TaskStatus.WAITING_DEPENDENCY
                self._persist(task)

    def _has_runnable_work(self, tenant_id: str | None = None) -> bool:
        return any(task.status in {TaskStatus.QUEUED, TaskStatus.RETRYING} and
                   (tenant_id is None or task.tenant_id == tenant_id)
                   for task in self.tasks.values())

    def _task(self, task_id: str) -> AgencyTask:
        try:
            return self.tasks[task_id]
        except KeyError:
            raise KeyError(f"tarefa não encontrada: {task_id}") from None

    def _emit(self, event_type: str, task: AgencyTask, payload: dict[str, Any]) -> None:
        event = AuditEvent.create(event_id=f"{event_type}:{task.task_id}:{task.attempts}:{task.updated_at.isoformat()}", event_type=event_type, sequence=self.event_bus.latest_sequence + 1, aggregate_type="agency_task", aggregate_id=task.task_id, run_id=task.mission_id, actor=task.agent_id, payload={"tenant_id": task.tenant_id, "task_id": task.task_id, **payload})
        self.event_bus.publish(event)

    def _persist(self, task: AgencyTask, *, lease_seconds: float | None = None) -> None:
        if self.store:
            self.store.update(task, lease_seconds=lease_seconds)

    @staticmethod
    def _serialize(task: AgencyTask) -> dict[str, Any]:
        data = asdict(task)
        data["status"] = task.status.value
        if task.result:
            data["result"]["status"] = task.result.status.value
        return data

    @staticmethod
    def _now():
        from datetime import datetime, timezone
        return datetime.now(timezone.utc)

    @staticmethod
    def _monotonic() -> float:
        from time import monotonic
        return monotonic()

    @staticmethod
    def _sleep(seconds: float) -> None:
        from time import sleep
        sleep(seconds)

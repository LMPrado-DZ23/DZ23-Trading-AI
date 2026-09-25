from __future__ import annotations

from threading import Barrier, Lock

from tradingagents.agency import AgencyExecutor, AgencyTask, TaskStatus


def task(task_id: str, agent_id: str, *, deps=(), approval=False) -> AgencyTask:
    return AgencyTask(task_id, "mission-1", "tenant-1", agent_id, "research", {},
                      f"idem-{task_id}", tuple(deps), approval)


def test_independent_tasks_run_concurrently_and_dependents_wait():
    executor = AgencyExecutor(max_workers=2)
    barrier = Barrier(2)
    lock = Lock()
    started: list[str] = []

    def worker(current):
        with lock:
            started.append(current.task_id)
        barrier.wait(timeout=2)
        return {"task": current.task_id}

    executor.register_handler("research-a", worker)
    executor.register_handler("research-b", worker)
    executor.register_handler("synth", lambda current: {"inputs": ["a", "b"]})
    executor.submit(task("a", "research-a"))
    executor.submit(task("b", "research-b"))
    executor.submit(task("s", "synth", deps=("a", "b")))

    snapshot = executor.run_until_idle(timeout=5)
    states = {item["task_id"]: item["status"] for item in snapshot["tasks"]}
    assert set(started) == {"a", "b"}
    assert states == {"a": "SUCCEEDED", "b": "SUCCEEDED", "s": "SUCCEEDED"}


def test_idempotency_returns_original_task():
    executor = AgencyExecutor()
    original = task("a", "research")
    executor.submit(original)
    duplicate = AgencyTask("different-id", "mission-1", "tenant-1", "research", "research", {}, "idem-a")
    assert executor.submit(duplicate) is original
    assert len(executor.tasks) == 1


def test_sensitive_task_requires_explicit_approval():
    executor = AgencyExecutor()
    executor.register_handler("publisher", lambda current: {"published": True})
    sensitive = task("publish", "publisher", approval=True)
    executor.submit(sensitive)
    before = executor.run_until_idle()
    assert before["tasks"][0]["status"] == TaskStatus.REVIEW_REQUIRED
    executor.approve("publish")
    after = executor.run_until_idle()
    assert after["tasks"][0]["status"] == TaskStatus.SUCCEEDED


def test_failed_dependency_blocks_downstream_task():
    executor = AgencyExecutor()
    executor.register_handler("broken", lambda current: (_ for _ in ()).throw(RuntimeError("falhou")))
    executor.register_handler("synth", lambda current: {"unexpected": True})
    executor.submit(task("broken", "broken"))
    executor.submit(task("synth", "synth", deps=("broken",)))
    snapshot = executor.run_until_idle()
    states = {item["task_id"]: item["status"] for item in snapshot["tasks"]}
    assert states["broken"] == "FAILED"
    assert states["synth"] == "BLOCKED"


def test_event_stream_contains_task_lifecycle():
    executor = AgencyExecutor()
    executor.register_handler("research", lambda current: {"ok": True})
    executor.submit(task("a", "research"))
    executor.run_until_idle()
    event_types = [item.event.event_type for item in executor.event_bus.replay()]
    assert event_types == ["agency.task.submitted", "agency.task.started", "agency.task.succeeded"]

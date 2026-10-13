from __future__ import annotations

from pathlib import Path
from time import monotonic, sleep

from tradingagents.agency import AgencyTask, TaskStatus
from tradingagents.agency.store import AgencyTaskStore
from tradingagents.agency_runtime import AgencyRuntime


def test_worker_processes_durable_tasks_and_stops_idempotently(tmp_path: Path):
    runtime = AgencyRuntime(store=AgencyTaskStore(tmp_path / "agency.db"))
    runtime.register_agent("research", "research", "Research")
    runtime.register_task_handler("research", lambda task: {"remember": True, "answer": "done"})
    runtime.submit_task(AgencyTask("t1", "m1", "tenant-1", "research", "research", {}, "once"))

    first = runtime.start_worker("tenant-1", poll_interval=0.1, cycle_timeout=1)
    second = runtime.start_worker("tenant-1", poll_interval=0.1, cycle_timeout=1)
    assert first["running"] is True and second["running"] is True

    deadline = monotonic() + 3
    while monotonic() < deadline and (runtime.executor.tasks["t1"].status is not TaskStatus.SUCCEEDED or runtime.worker_status("tenant-1")["cycles"] < 1):
        sleep(0.05)
    assert runtime.executor.tasks["t1"].status is TaskStatus.SUCCEEDED
    assert runtime.worker_status("tenant-1")["cycles"] >= 1

    stopped = runtime.stop_worker("tenant-1")
    assert stopped["running"] is False
    assert runtime.stop_worker("tenant-1")["running"] is False


def test_worker_does_not_cross_tenant_boundary(tmp_path: Path):
    runtime = AgencyRuntime(store=AgencyTaskStore(tmp_path / "agency.db"))
    runtime.start_worker("tenant-a", poll_interval=0.1, cycle_timeout=1)
    try:
        try:
            runtime.worker_status("tenant-b")
        except KeyError:
            pass
        else:
            raise AssertionError("tenant boundary not enforced")
    finally:
        runtime.stop_worker("tenant-a")

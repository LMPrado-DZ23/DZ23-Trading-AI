from __future__ import annotations

from time import monotonic, sleep

from tradingagents.agency import AgencyExecutor, AgencyTask


def test_executor_handles_bounded_burst_without_losing_tasks():
    executor = AgencyExecutor(max_workers=8)
    executor.register_handler("worker", lambda task: {"task": task.task_id})
    for index in range(200):
        executor.submit(AgencyTask(f"t-{index}", "load", "tenant-load", "worker", "noop", {}, f"idem-{index}"))
    started = monotonic()
    snapshot = executor.run_until_idle(timeout=10)
    elapsed = monotonic() - started
    assert snapshot["pending"] == 0
    assert all(item["status"] == "SUCCEEDED" for item in snapshot["tasks"])
    assert elapsed < 10


def test_executor_continuous_cycles_keep_progress_after_transient_failures():
    executor = AgencyExecutor(max_workers=2)
    attempts = {"count": 0}

    def flaky(task):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("transient")
        return {"ok": True}

    executor.register_handler("worker", flaky)
    executor.submit(AgencyTask("t1", "continuous", "tenant-load", "worker", "noop", {}, "once", max_attempts=2))
    assert executor.run_until_idle(timeout=5)["tasks"][0]["status"] == "SUCCEEDED"
    sleep(0.001)
    assert executor.run_until_idle(timeout=1)["pending"] == 0

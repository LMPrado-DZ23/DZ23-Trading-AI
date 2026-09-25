from __future__ import annotations

from pathlib import Path

from tradingagents.agency import AgencyExecutor, AgencyTask, EvidenceRef, TaskResult, TaskStatus
from tradingagents.agency.store import AgencyTaskStore


def make_task(task_id: str, tenant: str = "tenant-1") -> AgencyTask:
    return AgencyTask(task_id, "mission-1", tenant, "research", "brief", {"topic": "x"}, f"idem-{task_id}")


def test_task_survives_executor_restart_and_runs(tmp_path: Path):
    store = AgencyTaskStore(tmp_path / "agency.db")
    first = AgencyExecutor(store=store)
    first.submit(make_task("t1"))
    second = AgencyExecutor(store=store)
    second.register_handler("research", lambda task: {"remember": True, "text": "persisted"})
    second.run_until_idle()
    assert second.tasks["t1"].status is TaskStatus.SUCCEEDED
    third = AgencyExecutor(store=store)
    assert third.tasks["t1"].status is TaskStatus.SUCCEEDED
    assert store.recall("tenant-1")[0]["content"]["text"] == "persisted"


def test_memory_isolated_by_tenant(tmp_path: Path):
    store = AgencyTaskStore(tmp_path / "agency.db")
    store.remember(tenant_id="a", mission_id="m", kind="research", content={"secret": "a"})
    store.remember(tenant_id="b", mission_id="m", kind="research", content={"secret": "b"})
    assert [item["content"]["secret"] for item in store.recall("a")] == ["a"]
    assert store.recall("a", query="b") == []


def test_approval_state_survives_restart_and_sensitive_kind_is_forced(tmp_path: Path):
    store = AgencyTaskStore(tmp_path / "agency.db")
    first = AgencyExecutor(store=store)
    task = AgencyTask("publish", "m", "tenant-1", "publisher", "publish", {}, "publish-once")
    first.submit(task)
    assert task.status is TaskStatus.REVIEW_REQUIRED
    second = AgencyExecutor(store=store)
    assert second.tasks["publish"].status is TaskStatus.REVIEW_REQUIRED
    assert second.run_until_idle()["tasks"][0]["status"] == "REVIEW_REQUIRED"


def test_result_provenance_timestamps_survive_restart(tmp_path: Path):
    store = AgencyTaskStore(tmp_path / "agency.db")
    executor = AgencyExecutor(store=store)
    executor.register_handler("research", lambda task: TaskResult(task.task_id, TaskStatus.SUCCEEDED, {"ok": True}, (EvidenceRef("e1", "source", "hash"),)))
    executor.submit(make_task("timestamps"))
    executor.run_until_idle()
    before = executor.tasks["timestamps"].result
    restarted = AgencyExecutor(store=store)
    after = restarted.tasks["timestamps"].result
    assert before and after and before.finished_at == after.finished_at
    assert before.evidence[0].observed_at == after.evidence[0].observed_at

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tradingagents.agency import AgencyTask, LLMTaskHandler, LLMTaskPolicy, TaskStatus


def make_task() -> AgencyTask:
    return AgencyTask("t1", "m1", "tenant-1", "research-agent", "research", {"topic": "market"}, "once")


def test_llm_handler_returns_audit_evidence_and_never_marks_facts_verified():
    client = SimpleNamespace(model="test-model", invoke=lambda prompt: SimpleNamespace(content="resultado com incerteza"))
    result = LLMTaskHandler(client, LLMTaskPolicy("research"))(make_task())
    assert result.status is TaskStatus.SUCCEEDED
    assert result.output["verified"] is False
    assert result.evidence[0].source == "llm:research:test-model"
    assert result.evidence[0].content_hash


def test_llm_handler_rejects_empty_output():
    client = SimpleNamespace(model="test-model", invoke=lambda prompt: SimpleNamespace(content="  "))
    with pytest.raises(ValueError, match="vazio"):
        LLMTaskHandler(client, LLMTaskPolicy("research"))(make_task())


def test_llm_handler_truncates_output_to_policy():
    client = SimpleNamespace(model="test-model", invoke=lambda prompt: SimpleNamespace(content="x" * 400))
    result = LLMTaskHandler(client, LLMTaskPolicy("research", max_output_chars=256))(make_task())
    assert len(result.output["text"]) == 256

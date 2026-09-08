import json
import threading
import uuid
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from tradingagents.observability import (
    TELEMETRY_SCHEMA_VERSION,
    TelemetryCallbackHandler,
)
from tradingagents.runtime import RunContext, build_runtime_config


def _context(tmp_path: Path) -> RunContext:
    config = build_runtime_config({"results_dir": str(tmp_path)})
    return RunContext.create("AAPL", "2024-01-02", "stock", config)


def _response(model: str = "priced-model") -> LLMResult:
    message = AIMessage(
        content="model-secret-must-not-be-recorded",
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
        },
        response_metadata={"model_name": model},
    )
    return LLMResult(generations=[[ChatGeneration(message=message)]])


def _read_events(summary: dict) -> list[dict]:
    return [
        json.loads(line)
        for line in Path(summary["events_path"]).read_text(encoding="utf-8").splitlines()
    ]


def test_records_metadata_tokens_latency_and_configured_cost_only(tmp_path):
    telemetry = TelemetryCallbackHandler({
        "priced-model": {
            "input_per_million": 2.0,
            "output_per_million": 4.0,
        }
    })
    context = _context(tmp_path)
    run_id = telemetry.begin_run(context)
    llm_id = uuid.uuid4()
    tool_id = uuid.uuid4()

    telemetry.on_chat_model_start(
        {"name": "chat-model"},
        [["prompt-secret-must-not-be-recorded"]],
        run_id=llm_id,
        metadata={"langgraph_node": "market_analyst"},
    )
    telemetry.on_llm_end(_response(), run_id=llm_id)
    telemetry.on_tool_start(
        {"name": "market-data"},
        "tool-input-secret-must-not-be-recorded",
        run_id=tool_id,
    )
    telemetry.on_tool_end("tool-output-secret-must-not-be-recorded", run_id=tool_id)
    summary = telemetry.finish_run("succeeded", tmp_path)

    assert summary["schema"] == TELEMETRY_SCHEMA_VERSION
    assert summary["run_id"] == run_id
    assert summary["tokens"] == {"input": 100, "output": 50, "total": 150}
    assert summary["estimated_cost_usd"] == pytest.approx(0.0004)
    assert summary["priced_cost_usd"] == pytest.approx(0.0004)
    assert summary["unpriced_llm_calls"] == 0
    assert summary["by_node"]["market_analyst"] == {
        "llm_calls": 1,
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
        "priced_cost_usd": pytest.approx(0.0004),
        "unpriced_llm_calls": 0,
    }
    events = _read_events(summary)
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert all(event.get("duration_ms", 0) >= 0 for event in events)
    persisted = Path(summary["events_path"]).read_text(encoding="utf-8")
    assert "prompt-secret" not in persisted
    assert "model-secret" not in persisted
    assert "tool-input-secret" not in persisted
    assert "tool-output-secret" not in persisted


def test_unpriced_calls_are_not_reported_as_zero_cost(tmp_path):
    telemetry = TelemetryCallbackHandler()
    telemetry.begin_run(_context(tmp_path))
    operation_id = uuid.uuid4()
    telemetry.on_llm_start({"name": "unknown"}, ["private"], run_id=operation_id)
    telemetry.on_llm_end(_response("unknown-model"), run_id=operation_id)

    summary = telemetry.finish_run("succeeded", tmp_path)

    assert summary["estimated_cost_usd"] is None
    assert summary["priced_cost_usd"] == 0
    assert summary["unpriced_llm_calls"] == 1


def test_errors_are_redacted_and_failure_is_persisted(tmp_path):
    telemetry = TelemetryCallbackHandler()
    telemetry.begin_run(_context(tmp_path))
    operation_id = uuid.uuid4()
    telemetry.on_tool_start({"name": "fetch"}, "private", run_id=operation_id)
    telemetry.on_tool_error(
        RuntimeError("authorization: Bearer super-secret-token-value"),
        run_id=operation_id,
    )
    summary = telemetry.finish_run(
        "failed",
        tmp_path,
        RuntimeError("api_key=another-super-secret-value"),
    )

    persisted = (
        Path(summary["events_path"]).read_text(encoding="utf-8")
        + Path(summary["summary_path"]).read_text(encoding="utf-8")
    )
    assert "super-secret-token-value" not in persisted
    assert "another-super-secret-value" not in persisted
    assert "[REDACTED]" in persisted
    assert summary["status"] == "failed"


def test_concurrent_callbacks_are_ordered_and_not_duplicated(tmp_path):
    telemetry = TelemetryCallbackHandler()
    telemetry.begin_run(_context(tmp_path))
    operation_ids = [uuid.uuid4() for _ in range(40)]

    def invoke(operation_id):
        telemetry.on_tool_start({"name": "parallel"}, "private", run_id=operation_id)
        telemetry.on_tool_start({"name": "parallel"}, "private", run_id=operation_id)
        telemetry.on_tool_end("private", run_id=operation_id)
        telemetry.on_tool_end("private", run_id=operation_id)

    threads = [threading.Thread(target=invoke, args=(operation_id,)) for operation_id in operation_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    summary = telemetry.finish_run("succeeded", tmp_path)
    events = _read_events(summary)

    assert summary["counts"]["tool.started"] == len(operation_ids)
    assert summary["counts"]["tool.completed"] == len(operation_ids)
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))


def test_same_logical_run_gets_distinct_invocation_files(tmp_path):
    context = _context(tmp_path)
    first = TelemetryCallbackHandler()
    first_id = first.begin_run(context)
    first_summary = first.finish_run("succeeded", tmp_path)
    second = TelemetryCallbackHandler()
    second_id = second.begin_run(context)
    second_summary = second.finish_run("succeeded", tmp_path)

    assert first_id != second_id
    assert first_summary["run_key"] == second_summary["run_key"]
    assert first_summary["events_path"] != second_summary["events_path"]
    assert Path(first_summary["events_path"]).exists()
    assert Path(second_summary["events_path"]).exists()
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize(
    "pricing",
    [
        {"m": {"input_per_million": 1}},
        {"m": {"input_per_million": -1, "output_per_million": 1}},
        {"m": {"input_per_million": float("inf"), "output_per_million": 1}},
    ],
)
def test_invalid_pricing_fails_closed(pricing):
    with pytest.raises(ValueError):
        TelemetryCallbackHandler(pricing)


def test_lifecycle_misuse_fails_loudly(tmp_path):
    telemetry = TelemetryCallbackHandler()
    with pytest.raises(RuntimeError):
        telemetry.finish_run("succeeded", tmp_path)
    telemetry.begin_run(_context(tmp_path))
    with pytest.raises(RuntimeError):
        telemetry.begin_run(_context(tmp_path))
    telemetry.finish_run("cancelled", tmp_path)

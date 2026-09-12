"""Structured, secret-safe telemetry for LLM and tool execution."""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult

from tradingagents.dataflows.redact import redact_secrets
from tradingagents.runtime import RunContext

TELEMETRY_SCHEMA_VERSION = "tradingagents/telemetry/1"
_MAX_ERROR_CHARS = 2_000


def _safe_text(value: Any, limit: int = 256) -> str:
    return redact_secrets(str(value or ""))[:limit]


def _non_negative_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(parsed, 0)


class TelemetryCallbackHandler(BaseCallbackHandler):
    """Capture metadata only: never prompts, tool inputs, outputs or model text."""

    def __init__(self, pricing: Mapping[str, Mapping[str, float]] | None = None) -> None:
        super().__init__()
        self._lock = threading.RLock()
        self._pricing = self._validate_pricing(pricing or {})
        self._active: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._events: list[dict[str, Any]] = []
        self._sequence = 0
        self._run_id: str | None = None
        self._run_key: str | None = None
        self._run_context: RunContext | None = None
        self._started_at: str | None = None
        self._started_monotonic: float | None = None
        self._open = False
        self.last_summary: dict[str, Any] | None = None

    @staticmethod
    def _validate_pricing(
        pricing: Mapping[str, Mapping[str, float]],
    ) -> dict[str, dict[str, float]]:
        validated: dict[str, dict[str, float]] = {}
        allowed = {"input_per_million", "output_per_million"}
        for model, prices in pricing.items():
            if not isinstance(prices, Mapping) or set(prices) != allowed:
                raise ValueError(
                    f"pricing for {model!r} must define exactly {sorted(allowed)!r}"
                )
            row = {key: float(prices[key]) for key in allowed}
            if any(value < 0 or not math.isfinite(value) for value in row.values()):
                raise ValueError(f"pricing for {model!r} must be finite and non-negative")
            validated[str(model)] = row
        return validated

    def begin_run(self, context: RunContext) -> str:
        canonical = json.dumps(context.to_dict(), sort_keys=True, separators=(",", ":"))
        run_key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        run_id = uuid.uuid4().hex
        with self._lock:
            if self._open:
                raise RuntimeError("telemetry run is already active")
            self._active.clear()
            self._events.clear()
            self._sequence = 0
            self._run_id = run_id
            self._run_key = run_key
            self._run_context = context
            self._started_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            self._started_monotonic = time.perf_counter()
            self._open = True
            self.last_summary = None
            self._event("run.started", {"run_key": run_key, "run_mode": context.run_mode})
        return run_id

    def _event(self, event: str, fields: Mapping[str, Any] | None = None) -> None:
        self._sequence += 1
        payload = {
            "schema": TELEMETRY_SCHEMA_VERSION,
            "sequence": self._sequence,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "run_id": self._run_id,
            "event": event,
        }
        payload.update(fields or {})
        self._events.append(payload)

    @staticmethod
    def _operation_id(run_id: Any) -> str:
        return _safe_text(run_id, 64)

    def _start(self, kind: str, serialized: Mapping[str, Any], run_id: Any, **kwargs: Any) -> None:
        operation_id = self._operation_id(run_id)
        serialized = serialized if isinstance(serialized, Mapping) else {}
        name = serialized.get("name") or serialized.get("id") or "unknown"
        metadata = kwargs.get("metadata")
        node = None
        if isinstance(metadata, Mapping):
            node = metadata.get("tradingagents_analyst") or metadata.get("langgraph_node")
        fields = {
            "kind": kind,
            "operation_id": operation_id,
            "name": _safe_text(name),
            "node": _safe_text(node, 128) if node else None,
            "tags": [
                _safe_text(tag, 128) for tag in list(kwargs.get("tags") or [])[:20]
            ],
        }
        with self._lock:
            if not self._open:
                return
            key = (kind, operation_id)
            if key in self._active:
                return
            self._active[key] = (time.perf_counter(), fields)
            self._event(f"{kind}.started", fields)

    def _finish(
        self,
        kind: str,
        run_id: Any,
        *,
        error: BaseException | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        operation_id = self._operation_id(run_id)
        with self._lock:
            if not self._open:
                return
            active = self._active.pop((kind, operation_id), None)
            if active is None:
                return
            started, fields = active
            event_fields = {
                **fields,
                "duration_ms": round((time.perf_counter() - started) * 1_000, 3),
                **(extra or {}),
            }
            if error is not None:
                event_fields.update({
                    "error_type": type(error).__name__,
                    "error": _safe_text(error, _MAX_ERROR_CHARS),
                })
                self._event(f"{kind}.failed", event_fields)
            else:
                self._event(f"{kind}.completed", event_fields)

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        self._start("llm", serialized, run_id, **kwargs)

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self._start("llm", serialized, run_id, **kwargs)

    def on_llm_end(self, response: LLMResult, *, run_id, **kwargs):
        usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        model = None
        try:
            message = response.generations[0][0].message
        except (AttributeError, IndexError, TypeError):
            message = None
        if isinstance(message, AIMessage):
            metadata = message.usage_metadata or {}
            usage["input_tokens"] = _non_negative_int(metadata.get("input_tokens"))
            usage["output_tokens"] = _non_negative_int(metadata.get("output_tokens"))
            usage["total_tokens"] = _non_negative_int(
                metadata.get("total_tokens")
                or usage["input_tokens"] + usage["output_tokens"]
            )
            response_metadata = message.response_metadata or {}
            model = response_metadata.get("model_name") or response_metadata.get("model")
        llm_output = response.llm_output if isinstance(response.llm_output, Mapping) else {}
        token_usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
        token_usage = token_usage if isinstance(token_usage, Mapping) else {}
        if not any(usage.values()):
            usage["input_tokens"] = _non_negative_int(
                token_usage.get("prompt_tokens") or token_usage.get("input_tokens") or 0
            )
            usage["output_tokens"] = _non_negative_int(
                token_usage.get("completion_tokens") or token_usage.get("output_tokens") or 0
            )
            usage["total_tokens"] = _non_negative_int(
                token_usage.get("total_tokens")
                or usage["input_tokens"] + usage["output_tokens"]
            )
        model = model or llm_output.get("model_name") or llm_output.get("model")
        cost = self._estimate_cost(model, usage)
        self._finish(
            "llm", run_id,
            extra={
                **usage,
                "model": _safe_text(model) if model else None,
                "estimated_cost_usd": cost,
                "cost_basis": "configured" if cost is not None else "unavailable",
            },
        )

    def on_llm_error(self, error: BaseException, *, run_id, **kwargs):
        self._finish("llm", run_id, error=error)

    def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        self._start("tool", serialized, run_id, **kwargs)

    def on_tool_end(self, output, *, run_id, **kwargs):
        self._finish("tool", run_id)

    def on_tool_error(self, error: BaseException, *, run_id, **kwargs):
        self._finish("tool", run_id, error=error)

    def _estimate_cost(self, model: Any, usage: Mapping[str, int]) -> float | None:
        prices = self._pricing.get(str(model)) if model is not None else None
        if not prices:
            return None
        input_price = prices.get("input_per_million")
        output_price = prices.get("output_per_million")
        if input_price is None or output_price is None:
            return None
        amount = (
            usage["input_tokens"] * input_price
            + usage["output_tokens"] * output_price
        ) / 1_000_000
        return round(amount, 10)

    def finish_run(
        self,
        status: str,
        output_dir: str | Path,
        error: BaseException | None = None,
    ) -> dict[str, Any]:
        if status not in {"succeeded", "failed", "cancelled"}:
            raise ValueError(f"invalid telemetry status {status!r}")
        with self._lock:
            if not self._open or self._run_id is None or self._run_context is None:
                raise RuntimeError("begin_run must be called before finish_run")
            if error is not None:
                self._event("run.error", {
                    "error_type": type(error).__name__,
                    "error": _safe_text(error, _MAX_ERROR_CHARS),
                })
            self._event("run.finished", {"status": status})
            counts: dict[str, int] = defaultdict(int)
            for event in self._events:
                counts[event["event"]] += 1
            llm_events = [event for event in self._events if event["event"] == "llm.completed"]
            priced_cost = round(sum(
                event["estimated_cost_usd"]
                for event in llm_events
                if event["estimated_cost_usd"] is not None
            ), 10)
            unpriced_calls = sum(
                event["estimated_cost_usd"] is None for event in llm_events
            )
            by_node: dict[str, dict[str, Any]] = {}
            for event in llm_events:
                node = event.get("node") or "unattributed"
                row = by_node.setdefault(node, {
                    "llm_calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "priced_cost_usd": 0.0,
                    "unpriced_llm_calls": 0,
                })
                row["llm_calls"] += 1
                row["input_tokens"] += event["input_tokens"]
                row["output_tokens"] += event["output_tokens"]
                row["total_tokens"] += event["total_tokens"]
                if event["estimated_cost_usd"] is None:
                    row["unpriced_llm_calls"] += 1
                else:
                    row["priced_cost_usd"] = round(
                        row["priced_cost_usd"] + event["estimated_cost_usd"], 10
                    )
            summary = {
                "schema": TELEMETRY_SCHEMA_VERSION,
                "run_id": self._run_id,
                "run_key": self._run_key,
                "status": status,
                "started_at": self._started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "duration_ms": round(
                    (time.perf_counter() - (self._started_monotonic or time.perf_counter())) * 1_000,
                    3,
                ),
                "run_context": self._run_context.to_dict(),
                "counts": dict(sorted(counts.items())),
                "tokens": {
                    "input": sum(event["input_tokens"] for event in llm_events),
                    "output": sum(event["output_tokens"] for event in llm_events),
                    "total": sum(event["total_tokens"] for event in llm_events),
                },
                "estimated_cost_usd": None if unpriced_calls else priced_cost,
                "priced_cost_usd": priced_cost,
                "unpriced_llm_calls": unpriced_calls,
                "by_node": dict(sorted(by_node.items())),
            }
            destination = Path(output_dir)
            destination.mkdir(parents=True, exist_ok=True)
            events_path = destination / f"{self._run_id}.events.jsonl"
            summary_path = destination / f"{self._run_id}.summary.json"
            self._atomic_write(
                events_path,
                "".join(json.dumps(event, sort_keys=True) + "\n" for event in self._events),
            )
            self._atomic_write(
                summary_path, json.dumps(summary, sort_keys=True, indent=2) + "\n"
            )
            summary["events_path"] = str(events_path)
            summary["summary_path"] = str(summary_path)
            self.last_summary = summary
            self._open = False
            self._active.clear()
            return dict(summary)

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(content, encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

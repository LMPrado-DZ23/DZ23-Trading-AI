"""Provider-neutral LLM usage accounting for one immutable analysis run."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler


class UsagePolicyError(RuntimeError):
    """Deterministic, non-retryable failure of the local usage policy."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class JobUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    llm_requests: int = 0
    unmetered_llm_requests: int = 0
    estimated_cost_microusd: int | None = None
    pricing_version: str | None = None

    def __post_init__(self) -> None:
        numeric = (
            self.input_tokens,
            self.output_tokens,
            self.total_tokens,
            self.llm_requests,
            self.unmetered_llm_requests,
        )
        if any(isinstance(value, bool) or value < 0 for value in numeric):
            raise ValueError("usage counters must be non-negative integers")
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input_tokens plus output_tokens")
        if self.unmetered_llm_requests > self.llm_requests:
            raise ValueError("unmetered requests cannot exceed total requests")
        if self.estimated_cost_microusd is not None and self.estimated_cost_microusd < 0:
            raise ValueError("estimated cost cannot be negative")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "llm-usage/1",
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "llm_requests": self.llm_requests,
            "unmetered_llm_requests": self.unmetered_llm_requests,
            "estimated_cost_microusd": self.estimated_cost_microusd,
            "currency": "USD",
            "pricing_version": self.pricing_version,
        }


class PricingCatalog:
    """Exact provider/model prices supplied by the operator, never guessed."""

    def __init__(self, entries: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        normalized: dict[str, tuple[Decimal, Decimal]] = {}
        for raw_key, raw_rates in (entries or {}).items():
            key = str(raw_key).strip().casefold()
            if (
                not key
                or ":" not in key
                or len(key) > 240
                or not isinstance(raw_rates, Mapping)
                or set(raw_rates) != {"input_usd_per_million", "output_usd_per_million"}
            ):
                raise ValueError("invalid LLM pricing entry")
            try:
                input_rate = Decimal(str(raw_rates["input_usd_per_million"]))
                output_rate = Decimal(str(raw_rates["output_usd_per_million"]))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError("invalid LLM pricing rate") from exc
            if (
                not input_rate.is_finite()
                or not output_rate.is_finite()
                or input_rate < 0
                or output_rate < 0
                or input_rate > 100_000
                or output_rate > 100_000
            ):
                raise ValueError("LLM pricing rate is outside the supported range")
            normalized[key] = (input_rate, output_rate)
        self._entries = normalized
        canonical = json.dumps(
            {key: [str(rates[0]), str(rates[1])] for key, rates in sorted(normalized.items())},
            sort_keys=True,
            separators=(",", ":"),
        )
        self.version = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    def estimate_microusd(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> int | None:
        rates = self._entries.get(f"{provider}:{model}".casefold())
        if rates is None:
            return None
        # One USD per million tokens equals one micro-USD per token.
        amount = Decimal(input_tokens) * rates[0] + Decimal(output_tokens) * rates[1]
        return int(amount.quantize(Decimal("1"), rounding=ROUND_CEILING))

    def has_price(self, *, provider: str, model: str) -> bool:
        """Return whether this exact provider/model has an operator-supplied price."""

        return f"{provider}:{model}".casefold() in self._entries

    def export(self) -> dict[str, dict[str, str]]:
        """Return the normalized, secret-free catalog for an isolated attempt."""

        return {
            key: {
                "input_usd_per_million": str(rates[0]),
                "output_usd_per_million": str(rates[1]),
            }
            for key, rates in sorted(self._entries.items())
        }


class LlmUsageCallback(BaseCallbackHandler):
    """Thread-safe callback that records successful and unmetered LLM calls."""

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        pricing: PricingCatalog,
        abort_event: threading.Event | None = None,
        max_total_tokens: int | None = None,
        max_cost_microusd: int | None = None,
        max_output_tokens_per_request: int | None = None,
        prior_usage: JobUsage | None = None,
        deadline_monotonic: float | None = None,
        minimum_request_window_seconds: float | None = None,
    ) -> None:
        super().__init__()
        self._provider = provider
        self._model = model
        self._pricing = pricing
        self._abort_event = abort_event
        self._max_total_tokens = max_total_tokens
        self._max_cost_microusd = max_cost_microusd
        self._max_output_tokens_per_request = max_output_tokens_per_request
        self._prior_usage = prior_usage or JobUsage()
        self._deadline_monotonic = deadline_monotonic
        self._minimum_request_window_seconds = minimum_request_window_seconds
        if minimum_request_window_seconds is not None and minimum_request_window_seconds <= 0:
            raise ValueError("minimum request window must be positive")
        if self._prior_usage.unmetered_llm_requests and (
            max_total_tokens is not None or max_cost_microusd is not None
        ):
            raise UsagePolicyError("LLM_USAGE_UNMETERED")
        if max_cost_microusd is not None and not pricing.has_price(
            provider=provider,
            model=model,
        ):
            raise UsagePolicyError("LLM_COST_UNMETERED")
        self.raise_error = True
        self._lock = threading.Lock()
        self._input_tokens = 0
        self._output_tokens = 0
        self._requests = 0
        self._unmetered = 0
        # Per-call conservative upper bounds.  A provider error may happen
        # after the provider accepted/billed a request but before usage
        # metadata reaches us.  In that ambiguous case we charge the reserved
        # input/output ceiling instead of creating an unresolved monthly
        # "unmetered" poison pill that can lock a tenant for the rest of the
        # month.
        self._reservations: dict[str, tuple[int, int, int | None]] = {}

    def on_llm_start(self, serialized: Any, prompts: Any, **kwargs: Any) -> None:
        self._reserve_request(
            {
                "serialized": serialized,
                "prompts": prompts,
                "invocation_params": kwargs.get("invocation_params"),
            },
            run_id=kwargs.get("run_id"),
        )

    def on_chat_model_start(self, serialized: Any, messages: Any, **kwargs: Any) -> None:
        self._reserve_request(
            {
                "serialized": serialized,
                "messages": messages,
                "invocation_params": kwargs.get("invocation_params"),
            },
            run_id=kwargs.get("run_id"),
        )

    def on_tool_start(self, *_args: Any, **_kwargs: Any) -> None:
        self._assert_active()

    def on_chain_start(self, *_args: Any, **_kwargs: Any) -> None:
        self._assert_active()

    def on_llm_end(self, response, **_kwargs: Any) -> None:
        usage = _extract_usage(response)
        with self._lock:
            self._release_reservation_locked(_kwargs.get("run_id"))
            self._requests += 1
            if usage is None:
                self._unmetered += 1
            else:
                self._input_tokens += usage[0]
                self._output_tokens += usage[1]
        self._assert_active()
        self._assert_budget()

    def on_llm_error(self, _error: BaseException, **_kwargs: Any) -> None:
        with self._lock:
            reservation = self._release_reservation_locked(_kwargs.get("run_id"))
            self._requests += 1
            if reservation is None:
                self._unmetered += 1
            else:
                # Worst-case accounting is deterministic and auditable.  It
                # intentionally overcharges the tenant quota on ambiguous
                # failures rather than undercounting provider cost.
                self._input_tokens += reservation[0]
                self._output_tokens += reservation[1]
        self._assert_budget()

    def snapshot(self) -> JobUsage:
        with self._lock:
            input_tokens = self._input_tokens
            output_tokens = self._output_tokens
            requests = self._requests
            unmetered = self._unmetered
        estimated = self._pricing.estimate_microusd(
            provider=self._provider,
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        return JobUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            llm_requests=requests,
            unmetered_llm_requests=unmetered,
            estimated_cost_microusd=estimated,
            pricing_version=self._pricing.version if estimated is not None else None,
        )

    def _assert_active(self) -> None:
        if self._abort_event is not None and self._abort_event.is_set():
            raise UsagePolicyError("WORKER_LEASE_LOST")
        if self._deadline_monotonic is not None and time.monotonic() >= self._deadline_monotonic:
            raise UsagePolicyError("WORKER_DEADLINE_EXCEEDED")
        if (
            self._deadline_monotonic is not None
            and self._minimum_request_window_seconds is not None
            and self._deadline_monotonic - time.monotonic()
            < self._minimum_request_window_seconds
        ):
            raise UsagePolicyError("WORKER_DEADLINE_WINDOW_INSUFFICIENT")

    def _assert_budget(self) -> None:
        usage = self.snapshot()
        if usage.unmetered_llm_requests and (
            self._max_total_tokens is not None or self._max_cost_microusd is not None
        ):
            raise UsagePolicyError("LLM_USAGE_UNMETERED")
        if self._max_total_tokens is not None and usage.total_tokens > self._max_total_tokens:
            raise UsagePolicyError("LLM_TOKEN_BUDGET_EXCEEDED")
        if self._max_cost_microusd is not None:
            if usage.estimated_cost_microusd is None:
                raise UsagePolicyError("LLM_COST_UNMETERED")
            if usage.estimated_cost_microusd > self._max_cost_microusd:
                raise UsagePolicyError("LLM_COST_BUDGET_EXCEEDED")

    def _reserve_request(self, payload: Any, *, run_id: Any) -> None:
        self._assert_active()
        if self._max_total_tokens is None and self._max_cost_microusd is None:
            return
        if run_id is None:
            raise UsagePolicyError("LLM_BUDGET_RUN_ID_MISSING")
        if self._max_output_tokens_per_request is None:
            raise UsagePolicyError("LLM_OUTPUT_CAP_MISSING")
        input_upper_bound = _input_token_upper_bound(payload)
        reserved_tokens = input_upper_bound + self._max_output_tokens_per_request
        reserved_cost = self._pricing.estimate_microusd(
            provider=self._provider,
            model=self._model,
            input_tokens=input_upper_bound,
            output_tokens=self._max_output_tokens_per_request,
        )
        key = str(run_id)
        with self._lock:
            if key in self._reservations:
                raise UsagePolicyError("LLM_DUPLICATE_RUN_START")
            actual_tokens = (
                self._prior_usage.total_tokens + self._input_tokens + self._output_tokens
            )
            pending_tokens = sum(item[0] + item[1] for item in self._reservations.values())
            if (
                self._max_total_tokens is not None
                and actual_tokens + pending_tokens + reserved_tokens > self._max_total_tokens
            ):
                raise UsagePolicyError("LLM_TOKEN_BUDGET_PRECHECK_FAILED")
            if self._max_cost_microusd is not None:
                actual_cost = self._pricing.estimate_microusd(
                    provider=self._provider,
                    model=self._model,
                    input_tokens=self._prior_usage.input_tokens + self._input_tokens,
                    output_tokens=self._prior_usage.output_tokens + self._output_tokens,
                )
                pending_costs = tuple(item[2] for item in self._reservations.values())
                if actual_cost is None or reserved_cost is None or any(
                    item is None for item in pending_costs
                ):
                    raise UsagePolicyError("LLM_COST_UNMETERED")
                projected_cost = (
                    actual_cost
                    + sum(item for item in pending_costs if item is not None)
                    + reserved_cost
                )
                if projected_cost > self._max_cost_microusd:
                    raise UsagePolicyError("LLM_COST_BUDGET_PRECHECK_FAILED")
            self._reservations[key] = (
                input_upper_bound,
                self._max_output_tokens_per_request,
                reserved_cost,
            )

    def _release_reservation_locked(
        self, run_id: Any
    ) -> tuple[int, int, int | None] | None:
        if run_id is not None:
            return self._reservations.pop(str(run_id), None)
        return None


def _extract_usage(response: Any) -> tuple[int, int] | None:
    metadata: Mapping[str, Any] | None = None
    try:
        message = response.generations[0][0].message
        candidate = getattr(message, "usage_metadata", None)
        if isinstance(candidate, Mapping):
            metadata = candidate
    except (AttributeError, IndexError, TypeError):
        pass
    if metadata is None:
        output = getattr(response, "llm_output", None)
        candidate = output.get("token_usage") if isinstance(output, Mapping) else None
        if isinstance(candidate, Mapping):
            metadata = candidate
    if metadata is None:
        return None
    input_value = metadata.get("input_tokens", metadata.get("prompt_tokens"))
    output_value = metadata.get("output_tokens", metadata.get("completion_tokens"))
    if not _valid_counter(input_value) or not _valid_counter(output_value):
        return None
    return int(input_value), int(output_value)


def _valid_counter(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _input_token_upper_bound(payload: Any) -> int:
    """Conservatively bound tokenizer input without provider-specific tokenizers.

    Modern provider tokenizers cannot emit more tokens than the UTF-8 bytes they
    consume. Counting bytes therefore over-reserves safely and never relies on a
    remote tokenizer before the budget gate.
    """

    pending: list[Any] = [payload]
    total = 0
    nodes = 0
    while pending:
        current = pending.pop()
        nodes += 1
        if nodes > 100_000:
            raise UsagePolicyError("LLM_INPUT_TOO_COMPLEX")
        # Reserve structural bytes (JSON delimiters, field separators, roles and
        # serializer framing) for every traversed node. This deliberately
        # overestimates provider payloads.
        total += 32
        if current is None:
            continue
        if isinstance(current, str):
            total += len(current.encode("utf-8"))
        elif isinstance(current, bytes):
            total += len(current)
        elif isinstance(current, Mapping):
            pending.extend(current.keys())
            pending.extend(current.values())
        elif isinstance(current, (list, tuple)):
            pending.extend(current)
        elif callable(getattr(current, "model_dump", None)):
            pending.append(current.model_dump(mode="json"))
        elif hasattr(current, "content"):
            pending.append(current.content)
        else:
            total += len(str(current).encode("utf-8"))
        if total > 10_000_000:
            raise UsagePolicyError("LLM_INPUT_TOO_LARGE")
    return max(1, total)

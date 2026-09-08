from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from tradingagents.platform.usage import JobUsage, LlmUsageCallback, PricingCatalog


def test_usage_accounting_basis_is_explicit_and_fail_closed() -> None:
    assert JobUsage().as_dict()["accounting_basis"] == "measured"
    assert (
        JobUsage(accounting_basis="conservative_reservation").as_dict()[
            "accounting_basis"
        ]
        == "conservative_reservation"
    )
    with pytest.raises(ValueError, match="accounting basis"):
        JobUsage(accounting_basis="unknown")


def _response(*, usage=None, fallback=None):
    return LLMResult(
        generations=[[ChatGeneration(message=AIMessage(content="ok", usage_metadata=usage))]],
        llm_output={"token_usage": fallback} if fallback is not None else None,
    )


def test_usage_callback_prefers_normalized_metadata_and_prices_exact_model() -> None:
    pricing = PricingCatalog(
        {
            "openai:gpt-test": {
                "input_usd_per_million": "2.50",
                "output_usd_per_million": "10",
            }
        }
    )
    callback = LlmUsageCallback(provider="openai", model="gpt-test", pricing=pricing)
    callback.on_llm_end(
        _response(
            usage={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
            fallback={"prompt_tokens": 999, "completion_tokens": 999},
        )
    )
    usage = callback.snapshot()
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (100, 20, 120)
    assert usage.estimated_cost_microusd == 450
    assert usage.pricing_version == pricing.version


def test_usage_callback_falls_back_and_marks_unknown_or_failed_calls_unmetered() -> None:
    callback = LlmUsageCallback(provider="anthropic", model="unknown", pricing=PricingCatalog())
    callback.on_llm_end(_response(fallback={"prompt_tokens": 12, "completion_tokens": 3}))
    callback.on_llm_end(SimpleNamespace(generations=[], llm_output={}))
    callback.on_llm_error(RuntimeError("provider error with secret"))
    usage = callback.snapshot()
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (12, 3, 15)
    assert usage.llm_requests == 3
    assert usage.unmetered_llm_requests == 2
    assert usage.estimated_cost_microusd is None
    assert usage.pricing_version is None


def test_provider_error_consumes_reserved_upper_bound_without_month_long_poison() -> None:
    pricing = PricingCatalog(
        {"openai:m": {"input_usd_per_million": 1, "output_usd_per_million": 2}}
    )
    callback = LlmUsageCallback(
        provider="openai",
        model="m",
        pricing=pricing,
        max_total_tokens=10_000,
        max_output_tokens_per_request=7,
    )
    run_id = uuid4()
    callback.on_llm_start(None, ["hello"], run_id=run_id)
    callback.on_llm_error(TimeoutError("ambiguous provider timeout"), run_id=run_id)

    usage = callback.snapshot()
    assert usage.llm_requests == 1
    assert usage.unmetered_llm_requests == 0
    assert usage.input_tokens > 0
    assert usage.output_tokens == 7
    assert usage.estimated_cost_microusd is not None


@pytest.mark.parametrize(
    "catalog",
    [
        {"missing-separator": {"input_usd_per_million": 1, "output_usd_per_million": 1}},
        {"openai:m": {"input_usd_per_million": -1, "output_usd_per_million": 1}},
        {"openai:m": {"input_usd_per_million": "NaN", "output_usd_per_million": 1}},
        {"openai:m": {"input_usd_per_million": 1}},
    ],
)
def test_pricing_catalog_rejects_ambiguous_or_unsafe_entries(catalog) -> None:
    with pytest.raises(ValueError, match="pricing"):
        PricingCatalog(catalog)


def test_usage_callback_fails_closed_when_token_or_cost_budget_cannot_be_proved() -> None:
    pricing = PricingCatalog(
        {"openai:m": {"input_usd_per_million": 10, "output_usd_per_million": 20}}
    )
    tokens = LlmUsageCallback(
        provider="openai", model="m", pricing=pricing, max_total_tokens=10
    )
    with pytest.raises(RuntimeError, match="TOKEN_BUDGET"):
            tokens.on_llm_end(
                _response(usage={"input_tokens": 8, "output_tokens": 3, "total_tokens": 11})
            )

    with pytest.raises(RuntimeError, match="COST_UNMETERED"):
        LlmUsageCallback(
            provider="openai",
            model="unknown",
            pricing=pricing,
            max_cost_microusd=100,
        )

    unmetered = LlmUsageCallback(
        provider="openai", model="m", pricing=pricing, max_total_tokens=10
    )
    with pytest.raises(RuntimeError, match="USAGE_UNMETERED"):
        unmetered.on_llm_end(SimpleNamespace(generations=[], llm_output={}))


def test_preflight_reserves_prompt_and_output_before_any_provider_call() -> None:
    pricing = PricingCatalog(
        {"openai:m": {"input_usd_per_million": 1, "output_usd_per_million": 1}}
    )
    callback = LlmUsageCallback(
        provider="openai",
        model="m",
        pricing=pricing,
        max_total_tokens=10,
        max_output_tokens_per_request=3,
    )
    with pytest.raises(RuntimeError, match="TOKEN_BUDGET_PRECHECK_FAILED"):
        callback.on_llm_start(None, ["prompt-is-already-too-large"], run_id=uuid4())
    assert callback.snapshot().llm_requests == 0


def test_preflight_counts_prior_attempt_usage_and_concurrent_reservations() -> None:
    pricing = PricingCatalog(
        {"openai:m": {"input_usd_per_million": 1, "output_usd_per_million": 1}}
    )
    exhausted = LlmUsageCallback(
        provider="openai",
        model="m",
        pricing=pricing,
        max_total_tokens=10,
        max_output_tokens_per_request=2,
        prior_usage=JobUsage(
            input_tokens=6,
            output_tokens=3,
            total_tokens=9,
            llm_requests=1,
        ),
    )
    with pytest.raises(RuntimeError, match="TOKEN_BUDGET_PRECHECK_FAILED"):
        exhausted.on_llm_start(None, ["x"], run_id=uuid4())

    concurrent = LlmUsageCallback(
        provider="openai",
        model="m",
        pricing=pricing,
        max_total_tokens=600,
        max_output_tokens_per_request=3,
    )
    concurrent.on_llm_start(None, ["x"], run_id=uuid4())
    concurrent.on_llm_start(None, ["y"], run_id=uuid4())
    with pytest.raises(RuntimeError, match="TOKEN_BUDGET_PRECHECK_FAILED"):
        concurrent.on_llm_start(None, ["z"], run_id=uuid4())


def test_preflight_counts_tool_schema_even_when_prompt_is_tiny() -> None:
    pricing = PricingCatalog(
        {"openai:m": {"input_usd_per_million": 1, "output_usd_per_million": 1}}
    )
    callback = LlmUsageCallback(
        provider="openai",
        model="m",
        pricing=pricing,
        max_total_tokens=150,
        max_output_tokens_per_request=3,
    )
    serialized = {
        "tools": [
            {
                "name": "large-tool",
                "description": "schema-bytes-" * 20,
                "parameters": {"type": "object", "properties": {}},
            }
        ]
    }
    with pytest.raises(RuntimeError, match="TOKEN_BUDGET_PRECHECK_FAILED"):
        callback.on_chat_model_start(serialized, [["x"]], run_id=uuid4())


def test_cost_preflight_rejects_unpriced_or_over_budget_without_request() -> None:
    pricing = PricingCatalog(
        {"openai:m": {"input_usd_per_million": 1, "output_usd_per_million": 1}}
    )
    callback = LlmUsageCallback(
        provider="openai",
        model="m",
        pricing=pricing,
        max_cost_microusd=9,
        max_output_tokens_per_request=5,
    )
    with pytest.raises(RuntimeError, match="COST_BUDGET_PRECHECK_FAILED"):
        callback.on_llm_start(None, ["12345"], run_id=uuid4())
    assert callback.snapshot().llm_requests == 0


def test_each_llm_call_requires_enough_remaining_deadline_window(monkeypatch) -> None:
    import tradingagents.platform.usage as usage_module

    clock = {"now": 90.0}
    monkeypatch.setattr(usage_module.time, "monotonic", lambda: clock["now"])
    callback = LlmUsageCallback(
        provider="openai",
        model="m",
        pricing=PricingCatalog(),
        deadline_monotonic=100.0,
        minimum_request_window_seconds=5.0,
    )
    callback.on_llm_start(None, ["first"], run_id=uuid4())
    clock["now"] = 96.0
    with pytest.raises(RuntimeError, match="DEADLINE_WINDOW_INSUFFICIENT"):
        callback.on_llm_start(None, ["blocked"], run_id=uuid4())

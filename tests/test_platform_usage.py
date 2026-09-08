from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from tradingagents.platform.usage import LlmUsageCallback, PricingCatalog


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

    unpriced = LlmUsageCallback(
        provider="openai", model="unknown", pricing=pricing, max_cost_microusd=100
    )
    with pytest.raises(RuntimeError, match="COST_UNMETERED"):
        unpriced.on_llm_end(
            _response(usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})
        )

    unmetered = LlmUsageCallback(
        provider="openai", model="m", pricing=pricing, max_total_tokens=10
    )
    with pytest.raises(RuntimeError, match="USAGE_UNMETERED"):
        unmetered.on_llm_end(SimpleNamespace(generations=[], llm_output={}))

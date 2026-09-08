"""Bound every graph LLM request so unattended runs cannot wait indefinitely."""

from __future__ import annotations

import importlib

import pytest

import tradingagents.default_config as default_config_module
from tradingagents.graph.trading_graph import (
    TradingAgentsGraph,
    _coerce_timeout_seconds,
)
from tradingagents.llm_clients.openai_client import OpenAIClient


def _bare_graph(config):
    graph = object.__new__(TradingAgentsGraph)
    graph.config = config
    return graph


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "expected"),
    [(1, 1.0), (30.5, 30.5), ("120", 120.0), ("0.25", 0.25)],
)
def test_timeout_accepts_positive_finite_numbers(value, expected):
    assert _coerce_timeout_seconds(value) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad",
    [0, -1, "0", "-0.5", True, False, "invalid", "nan", "inf", None],
)
def test_timeout_rejects_unbounded_or_invalid_values(bad):
    with pytest.raises(ValueError, match="llm_timeout_seconds"):
        _coerce_timeout_seconds(bad)


@pytest.mark.unit
@pytest.mark.parametrize(
    "provider",
    [
        "openai",
        "anthropic",
        "google",
        "deepseek",
        "ollama",
        "openai_compatible",
        "bedrock",
    ],
)
def test_timeout_is_forwarded_for_every_graph_provider(provider):
    kwargs = _bare_graph(
        {"llm_provider": provider, "llm_timeout_seconds": "45.5"}
    )._get_provider_kwargs()
    assert kwargs["timeout"] == 45.5


@pytest.mark.unit
def test_timeout_can_be_explicitly_omitted_for_legacy_embedding():
    kwargs = _bare_graph(
        {"llm_provider": "openai", "llm_timeout_seconds": None}
    )._get_provider_kwargs()
    assert "timeout" not in kwargs


@pytest.mark.unit
def test_openai_compatible_client_receives_request_timeout():
    llm = OpenAIClient(
        "qwen2.5:0.5b", provider="ollama", timeout=7.5, max_retries=0
    ).get_llm()
    assert llm.request_timeout == 7.5


@pytest.mark.unit
def test_bedrock_receives_bounded_connect_and_read_transport(monkeypatch):
    import tradingagents.llm_clients.bedrock_client as bedrock_client

    captured = {}

    class FakeChat:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(bedrock_client, "_bedrock_class", lambda: FakeChat)
    monkeypatch.setattr(
        bedrock_client,
        "_bedrock_transport_config",
        lambda seconds: {
            "connect_timeout": seconds,
            "read_timeout": seconds,
        },
    )

    client = bedrock_client.BedrockClient("model", timeout=9.5)
    client.get_llm()

    assert captured["config"] == {
        "connect_timeout": 9.5,
        "read_timeout": 9.5,
    }


def _reload_with_env(monkeypatch, **overrides):
    for key in list(default_config_module._ENV_OVERRIDES):
        monkeypatch.delenv(key, raising=False)
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)
    return importlib.reload(default_config_module)


@pytest.mark.unit
def test_timeout_has_safe_default(monkeypatch):
    config = _reload_with_env(monkeypatch).DEFAULT_CONFIG
    assert config["llm_timeout_seconds"] == 120.0


@pytest.mark.unit
def test_timeout_environment_override_is_coerced_at_graph_boundary(monkeypatch):
    config = _reload_with_env(
        monkeypatch, TRADINGAGENTS_LLM_TIMEOUT_SECONDS="12.5"
    ).DEFAULT_CONFIG
    assert config["llm_timeout_seconds"] == 12.5
    assert _coerce_timeout_seconds(config["llm_timeout_seconds"]) == 12.5

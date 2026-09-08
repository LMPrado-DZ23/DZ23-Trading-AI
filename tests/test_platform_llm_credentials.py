from __future__ import annotations

import importlib
from dataclasses import replace

import pytest


class CaptureChat:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


def _capture_provider(monkeypatch, provider: str):
    module = importlib.import_module("tradingagents.llm_clients.openai_client")
    spec = module.OPENAI_COMPATIBLE_PROVIDERS[provider]
    monkeypatch.setitem(
        module.OPENAI_COMPATIBLE_PROVIDERS,
        provider,
        replace(spec, chat_class=CaptureChat),
    )
    return module.OpenAIClient


def test_platform_explicit_openai_key_and_origin_ignore_poisoned_server_environment(
    monkeypatch,
):
    client_type = _capture_provider(monkeypatch, "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "server-global-key-must-not-win")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://attacker.invalid/v1")
    tenant_key = "tenant-scoped-key"
    llm = client_type(
        "gpt-4o",
        provider="openai",
        api_key=tenant_key,
        platform_strict_credentials=True,
    ).get_llm()
    assert llm.kwargs["api_key"] == tenant_key
    assert llm.kwargs["base_url"] == "https://api.openai.com/v1"
    assert "server-global" not in repr(llm.kwargs)
    assert "attacker.invalid" not in repr(llm.kwargs)


def test_platform_missing_tenant_key_never_falls_back_to_server_environment(monkeypatch):
    client_type = _capture_provider(monkeypatch, "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "server-global-key")
    with pytest.raises(ValueError, match="not set"):
        client_type(
            "deepseek-chat",
            provider="deepseek",
            platform_strict_credentials=True,
        ).get_llm()


def test_platform_custom_relay_requires_exact_tenant_origin_allowlist(monkeypatch):
    client_type = _capture_provider(monkeypatch, "openai_compatible")
    common = {
        "provider": "openai_compatible",
        "api_key": "dedicated-relay-key",
        "platform_strict_credentials": True,
        "platform_allowed_origins": ("https://relay.example.test",),
    }
    with pytest.raises(ValueError, match="not approved"):
        client_type(
            "custom-model",
            "https://lookalike.relay.example.test/v1",
            **common,
        ).get_llm()
    llm = client_type(
        "custom-model",
        "https://relay.example.test/v1",
        **common,
    ).get_llm()
    assert llm.kwargs["api_key"] == "dedicated-relay-key"
    assert llm.kwargs["base_url"] == "https://relay.example.test/v1"

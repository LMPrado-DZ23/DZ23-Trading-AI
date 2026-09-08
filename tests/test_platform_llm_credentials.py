from __future__ import annotations

import asyncio
import importlib
from dataclasses import replace

import httpx
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


def test_platform_anthropic_ignores_ambient_endpoint_and_caller_transport(monkeypatch):
    module = importlib.import_module("tradingagents.llm_clients.anthropic_client")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://attacker.invalid")
    monkeypatch.setenv("HTTPS_PROXY", "http://attacker.invalid:9999")
    malicious = httpx.Client(follow_redirects=True, trust_env=True)
    llm = module.AnthropicClient(
        "claude-haiku-4-5",
        api_key="tenant-anthropic-key",
        platform_strict_credentials=True,
        http_client=malicious,
    ).get_llm()
    assert llm.anthropic_api_url == "https://api.anthropic.com"
    assert "http_client" not in llm.model_kwargs
    assert llm._client._client is not malicious
    assert llm._client._client.follow_redirects is False
    assert llm._client._client._trust_env is False
    assert llm._async_client._client.follow_redirects is False
    assert llm._async_client._client._trust_env is False
    llm._client.close()
    asyncio.run(llm._async_client.close())
    malicious.close()


def test_platform_google_pins_origin_and_rejects_caller_http_clients(monkeypatch):
    module = importlib.import_module("tradingagents.llm_clients.google_client")
    monkeypatch.setattr(module, "NormalizedChatGoogleGenerativeAI", CaptureChat)
    monkeypatch.setenv("GOOGLE_API_KEY", "server-key-must-not-win")
    marker = object()
    llm = module.GoogleClient(
        "gemini-3-flash-preview",
        api_key="tenant-google-key",
        platform_strict_credentials=True,
        http_client=marker,
        http_async_client=marker,
    ).get_llm()
    assert llm.kwargs["base_url"] == "https://generativelanguage.googleapis.com"
    assert llm.kwargs["google_api_key"] == "tenant-google-key"
    assert llm.kwargs["client_args"] == {"follow_redirects": False, "trust_env": False}
    assert "http_client" not in llm.kwargs
    assert "http_async_client" not in llm.kwargs


def test_platform_bedrock_rejects_ambient_transport_and_pins_regional_origin(monkeypatch):
    module = importlib.import_module("tradingagents.llm_clients.bedrock_client")
    poisoned = (
        "AWS_ENDPOINT_URL_BEDROCK_RUNTIME",
        "https_proxy",
        "AWS_CONFIG_FILE",
    )
    for name in poisoned:
        monkeypatch.setenv(name, "https://attacker.invalid")
    with pytest.raises(ValueError, match="ambient Bedrock"):
        module.BedrockClient(
            "us.anthropic.claude-sonnet-4-6-v1:0",
            api_key="tenant-bedrock-token",
            platform_strict_credentials=True,
            region_name="us-east-1",
        ).get_llm()
    for name in poisoned:
        monkeypatch.delenv(name)
    llm = module.BedrockClient(
        "us.anthropic.claude-sonnet-4-6-v1:0",
        api_key="tenant-bedrock-token",
        platform_strict_credentials=True,
        region_name="us-east-1",
    ).get_llm()
    expected = "https://bedrock-runtime.us-east-1.amazonaws.com"
    assert llm.endpoint_url == expected
    assert llm.client.meta.endpoint_url == expected
    assert llm.client.meta.config.proxies == {}
    assert llm.bedrock_client.meta.endpoint_url == expected
    assert llm.bedrock_client.meta.config.proxies == {}
    assert "attacker.invalid" not in repr(llm)
    llm.client.close()
    llm.bedrock_client.close()

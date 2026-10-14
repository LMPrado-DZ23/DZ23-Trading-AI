import os
from typing import Any

from langchain_openai import AzureChatOpenAI

from .base_client import BaseLLMClient, normalize_content

_PASSTHROUGH_KWARGS = (
    "timeout", "max_retries", "api_key", "reasoning_effort", "temperature",
    "callbacks", "http_client", "http_async_client",
)


class NormalizedAzureChatOpenAI(AzureChatOpenAI):
    """AzureChatOpenAI with normalized content output."""

    def invoke(self, input, config=None, **kwargs):
        return normalize_content(super().invoke(input, config, **kwargs))


class AzureOpenAIClient(BaseLLMClient):
    """Client for Azure OpenAI deployments.

    Requires environment variables:
        AZURE_OPENAI_API_KEY: API key
        AZURE_OPENAI_ENDPOINT: Endpoint URL (e.g. https://<resource>.openai.azure.com/)
        AZURE_OPENAI_DEPLOYMENT_NAME: Deployment name
        OPENAI_API_VERSION: API version (e.g. 2025-03-01-preview)
    """

    def __init__(self, model: str, base_url: str | None = None, **kwargs):
        super().__init__(model, base_url, **kwargs)

    def get_llm(self) -> Any:
        """Return configured AzureChatOpenAI instance."""
        self.warn_if_unknown_model()

        # Rodada-6 finding: without passing the endpoint explicitly, the SDK
        # resolves AZURE_OPENAI_ENDPOINT on its own and the policy never sees
        # the effective origin. Resolve, validate, pass explicitly.
        from .backend_policy import validate_backend_url

        azure_endpoint = self.base_url or os.environ.get("AZURE_OPENAI_ENDPOINT")
        if not azure_endpoint:
            raise ValueError(
                "Azure requires an endpoint: set AZURE_OPENAI_ENDPOINT or pass "
                "backend_url (https://<resource>.openai.azure.com)."
            )
        validate_backend_url("azure", azure_endpoint)

        from .transport import (
            LLM_HTTP_TIMEOUT,
            build_hardened_async_http_client,
            build_hardened_http_client,
        )

        llm_kwargs = {
            "model": self.model,
            "azure_endpoint": azure_endpoint,
            "azure_deployment": os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", self.model),
            # Achado r7: sem isto, o SDK Azure constrói httpx próprio com
            # follow_redirects=True e trust_env=True. Mesmo transporte
            # endurecido da família OpenAI; passthrough do usuário prevalece.
            "http_client": build_hardened_http_client(),
            "http_async_client": build_hardened_async_http_client(),
        }

        # Rodada-8 (auditoria): timeout finito por requisição — a langchain
        # passa timeout=None ao SDK, que o carimba em cada chamada e anula o
        # default do httpx.Client. Passthrough do usuário (abaixo) prevalece.
        llm_kwargs.setdefault("timeout", LLM_HTTP_TIMEOUT)

        for key in _PASSTHROUGH_KWARGS:
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]

        return NormalizedAzureChatOpenAI(**llm_kwargs)

    def validate_model(self) -> bool:
        """Azure accepts any deployed model name."""
        return True

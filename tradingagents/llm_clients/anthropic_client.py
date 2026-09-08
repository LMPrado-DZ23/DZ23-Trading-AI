import re
from functools import cached_property
from typing import Any

import anthropic
import httpx2
from langchain_anthropic import ChatAnthropic

from .base_client import BaseLLMClient, normalize_content
from .validators import validate_model

_PASSTHROUGH_KWARGS = (
    "timeout", "max_retries", "api_key", "max_tokens", "temperature",
    "callbacks", "effort",
)

# Anthropic's extended-thinking ``effort`` parameter is accepted by Opus 4.5+,
# Sonnet 4.6+, and the Claude 5 family (Sonnet 5, Fable 5). Sonnet 4.5 and any
# Haiku version 400 with ``"This model does not support the effort parameter"``
# (#831). Versions may be dotted (``opus-4-8``) or single-number (``sonnet-5``,
# ``fable-5``); the per-family minimum below is forward-compatible.
_EFFORT_EXACT = {
    "claude-mythos-preview",  # non-standard preview name; effort-capable
    "claude-mythos-5",        # Fable 5 twin (Project Glasswing); effort-capable
}
_EFFORT_MODEL = re.compile(r"^claude-(opus|sonnet|fable)-(\d+)(?:-(\d+))?$")
_EFFORT_MIN_VERSION = {"opus": (4, 5), "sonnet": (4, 6), "fable": (5, 0)}


def _supports_effort(model: str) -> bool:
    """Whether Anthropic accepts the ``effort`` parameter for this model."""
    model_lc = model.lower()
    if model_lc in _EFFORT_EXACT:
        return True
    match = _EFFORT_MODEL.match(model_lc)
    if not match:
        return False
    family = match.group(1)
    major = int(match.group(2))
    minor = int(match.group(3)) if match.group(3) else 0
    return (major, minor) >= _EFFORT_MIN_VERSION[family]


class NormalizedChatAnthropic(ChatAnthropic):
    """ChatAnthropic with normalized content output.

    Claude models with extended thinking or tool use return content as a
    list of typed blocks. This normalizes to string for consistent
    downstream handling.
    """

    def invoke(self, input, config=None, **kwargs):
        return normalize_content(super().invoke(input, config, **kwargs))


class HardenedChatAnthropic(NormalizedChatAnthropic):
    """ChatAnthropic whose native SDK transport ignores ambient networking.

    The installed ``langchain-anthropic`` version does not expose an injected
    httpx client as a model field.  Passing one as a constructor keyword would
    silently move it to ``model_kwargs`` and send it in the API request body.
    Override the two cached SDK clients instead so the transport actually used
    by Anthropic has redirects and environment proxy/CA discovery disabled.
    """

    @cached_property
    def _client(self) -> anthropic.Client:
        timeout = self._client_params.get("timeout", 120.0)
        return anthropic.Client(
            **self._client_params,
            http_client=httpx2.Client(
                follow_redirects=False,
                trust_env=False,
                timeout=timeout,
            ),
        )

    @cached_property
    def _async_client(self) -> anthropic.AsyncClient:
        timeout = self._client_params.get("timeout", 120.0)
        return anthropic.AsyncClient(
            **self._client_params,
            http_client=httpx2.AsyncClient(
                follow_redirects=False,
                trust_env=False,
                timeout=timeout,
            ),
        )


class AnthropicClient(BaseLLMClient):
    """Client for Anthropic Claude models."""

    def __init__(self, model: str, base_url: str | None = None, **kwargs):
        super().__init__(model, base_url, **kwargs)

    def get_llm(self) -> Any:
        """Return configured ChatAnthropic instance."""
        self.warn_if_unknown_model()
        llm_kwargs = {"model": self.model}
        strict = bool(self.kwargs.get("platform_strict_credentials"))
        if strict and not self.kwargs.get("api_key"):
            raise ValueError("API key for provider 'anthropic' is not set.")
        if strict and self.base_url:
            raise ValueError("Custom Anthropic endpoints are forbidden in platform mode.")

        if strict:
            llm_kwargs["anthropic_api_url"] = "https://api.anthropic.com"
        elif self.base_url:
            llm_kwargs["anthropic_api_url"] = self.base_url

        for key in _PASSTHROUGH_KWARGS:
            if key not in self.kwargs:
                continue
            if key == "effort" and not _supports_effort(self.model):
                continue
            llm_kwargs[key] = self.kwargs[key]

        llm_class = HardenedChatAnthropic if strict else NormalizedChatAnthropic
        return llm_class(**llm_kwargs)

    def validate_model(self) -> bool:
        """Validate model for Anthropic."""
        return validate_model("anthropic", self.model)

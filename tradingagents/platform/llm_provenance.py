"""Immutable, secret-free provenance for one resolved LLM route."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

TRANSPORT_POLICY_VERSION = "llm-transport-policy/3"

# These packages can change transport, parsing, temporal cut-offs, numerical
# indicators, provider request semantics or persistence without changing this
# repository's Python sources.  API and worker images must therefore agree on
# every value.  Optional adapters remain represented explicitly as ``missing``
# so installing one is itself a build identity change.
DECISION_CRITICAL_DISTRIBUTIONS = (
    "alembic",
    "anthropic",
    "boto3",
    "botocore",
    "certifi",
    "cryptography",
    "exchange-calendars",
    "google-genai",
    "httpcore",
    "httpx",
    "langchain-anthropic",
    "langchain-aws",
    "langchain-core",
    "langchain-google-genai",
    "langchain-openai",
    "langgraph",
    "numpy",
    "openai",
    "pandas",
    "psycopg",
    "pydantic",
    "python-dateutil",
    "pytz",
    "redis",
    "requests",
    "sqlalchemy",
    "stockstats",
    "tenacity",
    "tiktoken",
    "tzdata",
    "urllib3",
    "yfinance",
)


def dependency_environment_manifest() -> dict[str, str]:
    """Return the deterministic decision/runtime dependency identity."""

    manifest = {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "python_cache_tag": sys.implementation.cache_tag or "missing",
    }
    for distribution in DECISION_CRITICAL_DISTRIBUTIONS:
        try:
            manifest[distribution] = version(distribution)
        except PackageNotFoundError:
            manifest[distribution] = "missing"
    return manifest


@lru_cache(maxsize=1)
def application_build_digest() -> str:
    """Hash executable application sources and decision-critical libraries."""

    package_root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(package_root.rglob("*.py"), key=lambda item: item.as_posix()):
        digest.update(path.relative_to(package_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    for name, installed in sorted(dependency_environment_manifest().items()):
        digest.update(f"{name}={installed}".encode())
        digest.update(b"\0")
    return digest.hexdigest()


def calendar_engine_version() -> str:
    try:
        return version("exchange-calendars")
    except PackageNotFoundError:
        return "missing"


def llm_adapter_build_digest() -> str:
    """Hash the actual provider/policy source shipped to this process."""

    root = Path(__file__).resolve().parents[1] / "llm_clients"
    digest = hashlib.sha256()
    for path in sorted(root.glob("*.py"), key=lambda item: item.name):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


class LlmReferenceSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    provider: str = Field(pattern=r"^[a-z0-9_-]+$")
    model: str = Field(min_length=1, max_length=160)
    connection_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    credential_version: int = Field(ge=1)
    endpoint: str = Field(min_length=1, max_length=2048)
    allowed_origins: tuple[str, ...] = Field(max_length=16)
    transport_policy_version: str = TRANSPORT_POLICY_VERSION
    adapter_build_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    application_build_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    calendar_engine_version: str = Field(min_length=1, max_length=80)

    @property
    def digest(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_llm_reference_snapshot(
    *,
    provider: str,
    model: str,
    connection_id: object,
    credential_version: int,
    base_url: str | None,
) -> LlmReferenceSnapshot:
    provider_key = provider.strip().casefold()
    if base_url:
        parsed = urlparse(base_url)
        port = f":{parsed.port}" if parsed.port not in {None, 443} else ""
        origin = f"{parsed.scheme}://{parsed.hostname}{port}"
        endpoint = base_url.rstrip("/")
        origins = (origin,)
    elif provider_key == "openai":
        endpoint = "https://api.openai.com/v1"
        origins = ("https://api.openai.com",)
    elif provider_key == "anthropic":
        endpoint = "https://api.anthropic.com"
        origins = (endpoint,)
    elif provider_key == "google":
        endpoint = "https://generativelanguage.googleapis.com"
        origins = (endpoint,)
    elif provider_key in {
        "xai",
        "deepseek",
        "qwen",
        "qwen-cn",
        "glm",
        "glm-cn",
        "minimax",
        "minimax-cn",
        "openrouter",
        "mistral",
        "kimi",
        "groq",
        "nvidia",
    }:
        from tradingagents.llm_clients.openai_client import OPENAI_COMPATIBLE_PROVIDERS

        endpoint = OPENAI_COMPATIBLE_PROVIDERS[provider_key].base_url or ""
        if not endpoint:
            raise ValueError("provider registry has no deterministic endpoint")
        parsed = urlparse(endpoint)
        port = f":{parsed.port}" if parsed.port not in {None, 443} else ""
        origins = (f"{parsed.scheme}://{parsed.hostname}{port}",)
    elif provider_key == "azure":
        raise ValueError("Azure provenance requires its resolved endpoint")
    elif provider_key == "bedrock":
        raise ValueError("Bedrock platform jobs require a persisted resolved region")
    else:
        origins = (f"provider-sdk:{provider_key}",)
        endpoint = f"provider-sdk:{provider_key}"
    return LlmReferenceSnapshot(
        provider=provider_key,
        model=model,
        connection_id=str(connection_id),
        credential_version=credential_version,
        endpoint=endpoint,
        allowed_origins=origins,
        adapter_build_digest=llm_adapter_build_digest(),
        application_build_digest=application_build_digest(),
        calendar_engine_version=calendar_engine_version(),
    )

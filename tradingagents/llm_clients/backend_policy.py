"""Trusted-origin policy for LLM backend URLs (#security-fase0, rodadas 3–5).

Threat: ``backend_url`` (config or ``TRADINGAGENTS_LLM_BACKEND_URL``) points a
provider client at an arbitrary endpoint while the client authenticates with
that provider's REAL API key — exfiltrating the credential. The ``.env``
auto-discovery and the project's own ``docker-compose.yml`` (``env_file:
.env``) mean the URL can be attacker-supplied, and rodada 3 established that
no environment-provenance heuristic ("was it set before import?") can prove a
trustworthy origin for an opt-in — Compose, IDEs, CI and launchers all
materialize workspace files into the process environment before Python starts.

Closure therefore uses CREDENTIAL SEPARATION, not opt-ins:

1. An OFFICIAL provider's credential is only ever sent to that provider's own
   exact HTTPS origins, or to loopback. No environment variable, flag or
   config overrides this. Fail closed.
2. Custom/relay endpoints (LiteLLM, corporate gateways, self-hosted servers)
   must use the ``openai_compatible`` provider, whose credential is
   ``OPENAI_COMPATIBLE_API_KEY`` — distinct by construction, so a redirected
   URL can only ever capture the relay's own key, never an official one.
3. Local runtimes (``ollama``) hold no credential; loopback/HTTPS rules apply.

Additional hard failures (rodada-2/3 items): userinfo in the URL, non-HTTPS
outside loopback, and lookalike hosts (exact-match only — no suffix logic).
Transport-level protections (redirect revalidation, DNS resolution to
forbidden networks) belong to the HTTP client layer and are wired in the
factory step; this module is the static gate called before any client is
constructed and before any credential is read or bound.

Pure stdlib; unit-testable in isolation.
"""

from urllib.parse import urlparse

# Exact official API hosts per provider key (as registered in
# api_key_env.PROVIDER_API_KEY_ENV). Matched case-insensitively and exactly —
# "api.openai.com.evil.tld" never passes. Regional variants of one vendor are
# grouped per provider key; every listed host is vendor-controlled.
KNOWN_ORIGINS: dict[str, frozenset[str]] = {
    "openai": frozenset({"api.openai.com"}),
    "anthropic": frozenset({"api.anthropic.com"}),
    "google": frozenset({"generativelanguage.googleapis.com"}),
    "xai": frozenset({"api.x.ai"}),
    "openrouter": frozenset({"openrouter.ai"}),
    "deepseek": frozenset({"api.deepseek.com"}),
    "qwen": frozenset({"dashscope-intl.aliyuncs.com", "dashscope.aliyuncs.com"}),
    "qwen-cn": frozenset({"dashscope.aliyuncs.com"}),
    "glm": frozenset({"api.z.ai", "open.bigmodel.cn"}),
    "glm-cn": frozenset({"open.bigmodel.cn"}),
    "minimax": frozenset({"api.minimax.io", "api.minimaxi.com", "api.minimax.chat"}),
    "minimax-cn": frozenset({"api.minimaxi.com", "api.minimax.io", "api.minimax.chat"}),
    "kimi": frozenset({"api.moonshot.ai", "api.moonshot.cn"}),
    "groq": frozenset({"api.groq.com"}),
    "mistral": frozenset({"api.mistral.ai"}),
    "nvidia": frozenset({"integrate.api.nvidia.com"}),
}

# Providers whose hosted endpoints are per-tenant subdomains of a fixed,
# vendor-controlled parent domain: exact parent or exactly one label beneath
# it (resource.openai.azure.com — never deeper chains).
_PARENT_DOMAIN_PROVIDERS: dict[str, str] = {
    "azure": "openai.azure.com",
}

# Providers whose credential is distinct-by-construction (or absent), so any
# HTTPS origin — plus loopback — is acceptable: a redirect can only capture
# the relay's own key. Official-provider keys never travel through these.
_CUSTOM_ENDPOINT_PROVIDERS = frozenset({"openai_compatible", "ollama"})

# Providers that carry NO real credential (a fixed placeholder only).
# Rodada-5 ruling: "no key to steal" covers only credentials — prompts,
# tickers, strategy, evidence, memory and the final recommendation still
# transit in cleartext, and an HTTP response can be MODIFIED in flight,
# altering a financial decision. So plain HTTP for these providers is:
#   - automatic on loopback only;
#   - on any other host, allowed solely under the EXPLICIT insecure
#     development mode (TRADINGAGENTS_INSECURE_TRANSPORT=true), which is a
#     development posture, never a production control. Outputs produced
#     under it are untrusted for any future PAPER/capital-facing gate —
#     that gate must consult is_insecure_transport() and refuse.
_PLACEHOLDER_ONLY_PROVIDERS = frozenset({"ollama"})

_INSECURE_TRANSPORT_ENV = "TRADINGAGENTS_INSECURE_TRANSPORT"


def _insecure_dev_mode_enabled() -> bool:
    import os

    raw = (os.environ.get(_INSECURE_TRANSPORT_ENV) or "").strip().lower()
    return raw in ("true", "1", "yes", "on")


def is_insecure_transport(provider: str, backend_url: str | None) -> bool:
    """True when this provider+URL combination runs over cleartext to a
    non-loopback host. Future PAPER/capital-facing gates MUST refuse
    decisions produced under insecure transport."""
    if backend_url is None or str(backend_url).strip() == "":
        return False
    parsed, host = _parse(backend_url)
    return parsed.scheme != "https" and host not in _LOOPBACK_HOSTS

# Bedrock authenticates via the AWS SigV4 chain and takes no backend_url in
# this codebase; an unexpected URL fails closed.
_NO_BACKEND_URL_PROVIDERS = frozenset({"bedrock"})

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class UntrustedBackendError(ValueError):
    """backend_url would send a credential to an unapproved origin."""


def _parse(backend_url: str):
    parsed = urlparse(str(backend_url).strip())
    host = (parsed.hostname or "").lower()
    if not host:
        # Do not echo the raw URL: a malformed value can embed credentials.
        raise UntrustedBackendError("backend_url has no parseable host")
    if parsed.username or parsed.password:
        raise UntrustedBackendError(
            "backend_url must not embed userinfo (user:pass@host)"
        )
    return parsed, host


def validate_backend_url(provider: str, backend_url: str | None) -> None:
    """Static gate: raise UntrustedBackendError unless this provider's
    credential may be sent to this URL. Call before constructing any client
    and before reading/binding any credential.

    No environment variable or config flag can widen this policy — custom
    endpoints must use the ``openai_compatible`` provider with its own
    ``OPENAI_COMPATIBLE_API_KEY`` (credential separation, rodada 3).
    """
    if backend_url is None or str(backend_url).strip() == "":
        return

    provider_key = (provider or "").strip().lower()
    parsed, host = _parse(backend_url)

    # Loopback relays run on the operator's own machine: allowed for every
    # provider, any scheme (Ollama/LiteLLM local defaults are plain HTTP).
    if host in _LOOPBACK_HOSTS:
        return

    if provider_key in _NO_BACKEND_URL_PROVIDERS:
        raise UntrustedBackendError(
            f"provider {provider_key!r} does not accept a backend_url; "
            "its SDK manages endpoints internally"
        )

    if provider_key in _CUSTOM_ENDPOINT_PROVIDERS:
        if parsed.scheme != "https":
            if (
                provider_key in _PLACEHOLDER_ONLY_PROVIDERS
                and _insecure_dev_mode_enabled()
            ):
                return  # explicit insecure DEVELOPMENT mode (rodada 5)
            raise UntrustedBackendError(
                "custom backend_url must use https outside loopback, got "
                f"{parsed.scheme!r} for {host!r}. For a keyless local-network "
                f"server in development, set {_INSECURE_TRANSPORT_ENV}=true "
                "(cleartext transport; outputs are untrusted for any "
                "capital-facing use)."
            )
        return

    is_known = host in KNOWN_ORIGINS.get(provider_key, frozenset())
    if not is_known:
        parent = _PARENT_DOMAIN_PROVIDERS.get(provider_key)
        if parent and (
            host == parent
            or (
                host.endswith("." + parent)
                and host[: -len(parent) - 1].count(".") == 0
            )
        ):
            is_known = True

    if is_known:
        if parsed.scheme != "https":
            raise UntrustedBackendError(
                f"backend_url for provider {provider_key!r} must use https, "
                f"got {parsed.scheme!r}"
            )
        return

    raise UntrustedBackendError(
        f"backend_url host {host!r} is not an official origin for provider "
        f"{provider_key!r}, and this provider's API key is never sent to "
        "custom origins. For relays/gateways, use provider "
        "'openai_compatible' with its own OPENAI_COMPATIBLE_API_KEY "
        "(credential separation)."
    )

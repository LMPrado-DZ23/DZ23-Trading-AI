"""Hardened HTTP transport for LLM clients (#security-fase0, rodada 5).

Rodada-5 correction (Codex, verified against the installed SDK): the OpenAI
python SDK explicitly sets ``follow_redirects=True`` on the httpx client it
builds (``kwargs.setdefault("follow_redirects", True)`` in
``openai/_base_client.py``, openai 3.8.0 here) — so a compromised or
misconfigured endpoint could 30x-redirect a request, credentials attached,
to another origin. Relying on transitive defaults is not a control.

The explicit control: every client in the OpenAI-compatible family gets an
httpx client with ``follow_redirects=False``. The OpenAI SDK uses a supplied
``http_client`` as-is, so its own True-default never applies. A redirect
response then surfaces as the 3xx itself and the SDK raises — no silent
cross-origin hop, no credential forwarding, sync or async.

Native SDK clients (anthropic/google) are pinned by the backend policy to
their official origins only, so a cross-origin redirect there requires the
official API itself to be hostile — a categorically weaker threat. Their
transport hardening is tracked as follow-up in RUN-INFO, not silently
assumed.
"""

import httpx


def build_hardened_http_client() -> httpx.Client:
    """Sync httpx client: no redirects, no ambient proxy/CA (rodada 6).

    ``trust_env=False`` — HTTP(S)_PROXY, ALL_PROXY, SSL_CERT_FILE and
    NO_PROXY from the environment are IGNORED: an ambient proxy would be an
    unvalidated man-in-the-middle outside the backend policy. Corporate
    proxy/CA support must arrive as explicit, validated configuration
    recorded in run provenance — not inherited from the environment."""
    return httpx.Client(follow_redirects=False, trust_env=False)


def build_hardened_async_http_client() -> httpx.AsyncClient:
    """Async variant of build_hardened_http_client (same posture)."""
    return httpx.AsyncClient(follow_redirects=False, trust_env=False)

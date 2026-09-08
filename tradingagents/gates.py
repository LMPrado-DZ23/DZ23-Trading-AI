"""Capital-facing security gates (#security-fase0, rodada 6).

Rodada-6 requirement: insecure transport must leave a PERSISTENT mark on the
run's result — not merely a queryable function — and the future PAPER/
capital-facing gate must FAIL CLOSED on that mark.

The mark
--------
``transport_security_mark(config)`` computes, from the run's effective
configuration, whether any LLM traffic can travel over cleartext to a
non-loopback host (the explicit ``TRADINGAGENTS_INSECURE_TRANSPORT`` dev
mode). ``TradingAgentsGraph._run_graph`` stamps the mark into the returned
``final_state["transport_security"]`` and it is persisted with the state log
on disk, so the classification survives the run.

The gate
--------
``assert_secure_for_capital(final_state)`` is the contract every future
PAPER/live consumer must call before acting on a decision. Fail-closed
semantics, exactly as ruled:

- mark ABSENT  -> refuse (an unmarked state proves nothing);
- mark insecure -> refuse;
- mark secure  -> pass.
"""

import os
from urllib.parse import urlparse

from tradingagents.llm_clients.backend_policy import is_insecure_transport

MARK_KEY = "transport_security"
MARK_SCHEMA = "transport_security/1"


class InsecureTransportError(RuntimeError):
    """Decision produced under (or without proof of) secure transport."""


def _effective_urls(config: dict) -> list[tuple[str, str | None]]:
    """(provider, effective backend_url) pairs this run can consume.

    Mirrors client precedence: explicit backend_url, plus the provider env
    override for providers that have one (OLLAMA_BASE_URL today).
    """
    provider = (config.get("llm_provider") or "").lower()
    urls: list[tuple[str, str | None]] = [(provider, config.get("backend_url"))]
    if provider == "ollama":
        env_url = os.environ.get("OLLAMA_BASE_URL")
        if env_url:
            urls.append((provider, env_url))
    return urls


def transport_security_mark(config: dict, run_ticker: str | None = None,
                            run_trade_date: str | None = None) -> dict:
    """Persistent, JSON-serializable transport classification for a run."""
    insecure_targets = []
    for provider, url in _effective_urls(config):
        if url and is_insecure_transport(provider, url):
            host = urlparse(str(url)).hostname or "?"
            insecure_targets.append({"provider": provider, "host": host})
    return {
        "schema": MARK_SCHEMA,
        "insecure": bool(insecure_targets),
        "insecure_targets": insecure_targets,
        "policy": "backend_policy/rodada5",
        "run": {
            "ticker": run_ticker,
            "trade_date": run_trade_date,
        },
    }


def assert_secure_for_capital(final_state: dict) -> None:
    """FAIL-CLOSED gate for any PAPER/capital-facing consumer.

    Raises InsecureTransportError unless the state carries an explicit
    secure transport mark. Absence of the mark is refusal, not a pass.
    """
    mark = (final_state or {}).get(MARK_KEY)
    if not isinstance(mark, dict):
        raise InsecureTransportError(
            "final_state carries no transport_security mark; capital-facing "
            "consumers fail closed on unproven transport."
        )
    # Closed, versioned schema (rodada-6 finding: an empty/partial dict must
    # never pass). Every check is an explicit requirement, not truthiness.
    if mark.get("schema") != MARK_SCHEMA:
        raise InsecureTransportError(
            f"transport_security mark has schema {mark.get('schema')!r}, "
            f"expected {MARK_SCHEMA!r}; refusing unrecognized provenance."
        )
    if mark.get("insecure") is not False:
        # True OR absent OR any non-False value refuses — fail closed.
        if mark.get("insecure") is True:
            targets = ", ".join(
                f"{t.get('provider')}→{t.get('host')}"
                for t in mark.get("insecure_targets", [])
            ) or "unknown target"
            raise InsecureTransportError(
                "decision was produced under explicit insecure transport "
                f"({targets}); refusing capital-facing use."
            )
        raise InsecureTransportError(
            "transport_security mark lacks an explicit insecure=False; "
            "refusing incomplete provenance."
        )
    if not isinstance(mark.get("insecure_targets"), list) or mark["insecure_targets"]:
        raise InsecureTransportError(
            "secure mark must carry an empty insecure_targets list."
        )
    run = mark.get("run")
    if not isinstance(run, dict) or not run.get("ticker") or not run.get("trade_date"):
        raise InsecureTransportError(
            "transport_security mark is not bound to a run (ticker/trade_date)."
        )
    # Achado r7: identidade AUSENTE no estado permitia pular a comparação.
    # Fail-closed: o estado precisa declarar sua própria identidade E ela
    # precisa bater com a do mark — um estado anônimo não prova nada.
    ticker = (final_state or {}).get("company_of_interest")
    trade_date = (final_state or {}).get("trade_date")
    if not ticker or not trade_date:
        raise InsecureTransportError(
            "final_state lacks company_of_interest/trade_date; an anonymous "
            "state cannot be bound to its transport mark — refusing."
        )
    if run["ticker"] != ticker:
        raise InsecureTransportError("mark run.ticker does not match state.")
    if run["trade_date"] != trade_date:
        raise InsecureTransportError("mark run.trade_date does not match state.")

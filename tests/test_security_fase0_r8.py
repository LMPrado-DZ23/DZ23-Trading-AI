"""Rodada-8: proof tests for the four reproduced findings of the r7 re-audit.

F1 Azure clients carry the hardened transport (no redirects, no trust_env).
F2 Gate refuses an anonymous final_state (identity required, not optional).
F3 Debate envelopes never persist into state history (no degradation).
F4 Redaction covers JSON-quoted keys and Authorization: Basic.
"""

import os
from unittest import mock

import pytest

# --------------------------------------------------------------- F1 ----

class TestAzureHardenedTransport:
    def test_azure_llm_does_not_follow_redirects_and_ignores_env(self):
        from tradingagents.llm_clients.azure_client import AzureOpenAIClient

        env = {"AZURE_OPENAI_ENDPOINT": "https://myres.openai.azure.com",
               "AZURE_OPENAI_API_KEY": "azkey-123456789",
               "OPENAI_API_VERSION": "2025-03-01-preview",
               "HTTPS_PROXY": "http://mitm.example.com:8080"}
        with mock.patch.dict(os.environ, env):
            llm = AzureOpenAIClient("gpt-5.6").get_llm()
        sync_httpx = llm.root_client._client
        async_httpx = llm.root_async_client._client
        assert sync_httpx.follow_redirects is False
        assert async_httpx.follow_redirects is False
        assert getattr(sync_httpx, "_trust_env", True) is False
        assert getattr(async_httpx, "_trust_env", True) is False


# --------------------------------------------------------------- F2 ----

class TestGateRequiresStateIdentity:
    def _mark(self):
        from tradingagents.gates import transport_security_mark

        return transport_security_mark(
            {"llm_provider": "openai", "backend_url": None},
            run_ticker="NVDA", run_trade_date="2026-09-05",
        )

    def test_anonymous_state_refused_even_with_valid_mark(self):
        from tradingagents.gates import (
            MARK_KEY,
            InsecureTransportError,
            assert_secure_for_capital,
        )

        mark = self._mark()
        # The r7 finding: no identity in state → comparison skipped → passed.
        with pytest.raises(InsecureTransportError, match="anonymous"):
            assert_secure_for_capital({MARK_KEY: mark})
        with pytest.raises(InsecureTransportError, match="anonymous"):
            assert_secure_for_capital({MARK_KEY: mark, "company_of_interest": "NVDA"})
        with pytest.raises(InsecureTransportError, match="anonymous"):
            assert_secure_for_capital({MARK_KEY: mark, "trade_date": "2026-09-05"})

    def test_full_identity_match_passes(self):
        from tradingagents.gates import MARK_KEY, assert_secure_for_capital

        assert_secure_for_capital({
            MARK_KEY: self._mark(),
            "company_of_interest": "NVDA",
            "trade_date": "2026-09-05",
        })


# --------------------------------------------------------------- F3 ----

class _FakeLLM:
    def __init__(self, content):
        self.content = content
        self.seen = []

    def invoke(self, p):
        self.seen.append(str(p))

        class R:
            pass

        r = R()
        r.content = self.content
        return r


def _invest_state(history, current_response):
    return {
        "investment_debate_state": {
            "history": history, "bull_history": "", "bear_history": "",
            "current_response": current_response, "count": 1},
        "market_report": "", "sentiment_report": "", "news_report": "",
        "fundamentals_report": "", "company_of_interest": "NVDA",
        "trade_date": "2026-09-05", "asset_type": "stock",
    }


class TestDebateEnvelopeNeverPersists:
    def test_bull_history_update_is_envelope_free(self):
        from tradingagents.agents.researchers.bull_researcher import (
            create_bull_researcher,
        )

        state = _invest_state("Bear Analyst: doom.", "Bear Analyst: sell now.")
        upd = create_bull_researcher(_FakeLLM("Bull: growth."))(state)
        new = upd["investment_debate_state"]
        # Prompt got envelopes; PERSISTED history did not.
        assert "[UNTRUSTED EXTERNAL DATA" not in new["history"]
        assert "[END UNTRUSTED DATA" not in new["history"]
        assert new["history"].startswith("Bear Analyst: doom.")
        assert "Bull: growth." in new["history"]

    def test_two_rounds_do_not_accumulate_envelopes(self):
        from tradingagents.agents.researchers.bear_researcher import (
            create_bear_researcher,
        )
        from tradingagents.agents.researchers.bull_researcher import (
            create_bull_researcher,
        )

        state = _invest_state("", "")
        upd = create_bull_researcher(_FakeLLM("Bull r1."))(state)
        state["investment_debate_state"] = upd["investment_debate_state"]
        upd = create_bear_researcher(_FakeLLM("Bear r1."))(state)
        state["investment_debate_state"] = upd["investment_debate_state"]
        upd = create_bull_researcher(_FakeLLM("Bull r2."))(state)
        history = upd["investment_debate_state"]["history"]
        assert "[UNTRUSTED EXTERNAL DATA" not in history
        for turn in ("Bull r1.", "Bear r1.", "Bull r2."):
            assert turn in history

    def test_risk_history_update_is_envelope_free(self):
        from tradingagents.agents.risk_mgmt.aggressive_debator import (
            create_aggressive_debator,
        )

        state = {
            "risk_debate_state": {"history": "Neutral: hmm.",
                                  "aggressive_history": "", "conservative_history": "",
                                  "neutral_history": "", "latest_speaker": "",
                                  "current_aggressive_response": "",
                                  "current_conservative_response": "Conservative: cut.",
                                  "current_neutral_response": "",
                                  "judge_decision": "", "count": 2},
            "market_report": "", "sentiment_report": "", "news_report": "",
            "fundamentals_report": "", "trader_investment_plan": "BUY",
            "company_of_interest": "NVDA", "trade_date": "2026-09-05",
            "asset_type": "stock",
        }
        upd = create_aggressive_debator(_FakeLLM("Risky: go."))(state)
        new = upd["risk_debate_state"]
        assert "[UNTRUSTED EXTERNAL DATA" not in new["history"]
        assert new["history"].startswith("Neutral: hmm.")


# --------------------------------------------------------------- F4 ----

class TestRedactionJsonAndBasic:
    def test_json_quoted_key_is_redacted(self):
        from tradingagents.dataflows.redact import redact_secrets

        out = redact_secrets(
            '{"api_key": "sk-veryreal-12345678", "other": "keep"}', env={}
        )
        assert "sk-veryreal-12345678" not in out
        assert '"api_key": "[REDACTED]"' in out
        assert '"other": "keep"' in out

    def test_authorization_basic_is_redacted(self):
        from tradingagents.dataflows.redact import redact_secrets

        out = redact_secrets("Authorization: Basic dXNlcjpwYXNzd29yZDEyMw==", env={})
        assert "dXNlcjpwYXNzd29yZDEyMw==" not in out
        assert "Basic [REDACTED]" in out

    def test_json_token_and_password_variants(self):
        from tradingagents.dataflows.redact import redact_secrets

        out = redact_secrets(
            '{"token": "tok_abcdef123456", "password": "hunter2hunter2"}', env={}
        )
        assert "tok_abcdef123456" not in out and "hunter2hunter2" not in out

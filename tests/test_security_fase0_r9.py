"""Rodada-9 (estritamente focal): adversarial matrix for the two redactor
bypasses reproduced in the r8 audit.

A1 Basic short/valid tokens (4, 8, longer chars) redacted; malformed
   negatives untouched.
A2 JSON escape-aware: escaped quotes, backslashes, unicode escapes, nested
   objects — full value removed, NO suffix leak, output stays valid JSON;
   invalid JSON fragments handled without corruption of the control.
"""

import json

import pytest

from tradingagents.dataflows.redact import redact_secrets


class TestBasicShortTokens:
    """Achado r8-1: 'Basic YTpi' (a:b) must be redacted — Basic validity has
    no minimum length."""

    @pytest.mark.parametrize("token", [
        "YTpi",                # 4 chars — base64('a:b'), the audit's PoC
        "YWI6Y2Q=",            # 8 chars
        "dXNlcjpwYXNzd29yZDEyMw==",  # long
    ])
    def test_valid_basic_tokens_redacted(self, token):
        out = redact_secrets(f"Authorization: Basic {token}", env={})
        assert token not in out
        assert "Basic [REDACTED]" in out

    def test_bearer_floor_unchanged(self):
        out = redact_secrets("Authorization: Bearer abcdefgh12345678", env={})
        assert "abcdefgh12345678" not in out

    def test_malformed_negatives_untouched(self):
        # No token / non-token garbage: nothing to redact, nothing corrupted.
        assert redact_secrets("Authorization: Basic", env={}) == "Authorization: Basic"
        out = redact_secrets("Basic !!!", env={})
        assert out == "Basic !!!"

    def test_prose_word_after_basic_is_conservatively_redacted(self):
        # Deliberate trade-off, documented: a token-shaped word after
        # "Basic" is treated as credential. Fail-closed for a redactor.
        out = redact_secrets("the Basic plan", env={})
        assert "Basic [REDACTED]" in out


class TestJsonEscapeAware:
    """Achado r8-2: escaped quote must not split the value."""

    def test_escaped_quote_no_suffix_leak_and_valid_json(self):
        raw = '{"password": "abc\\"defghijk"}'
        out = redact_secrets(raw, env={})
        assert "defghijk" not in out          # the audit's leaked suffix
        parsed = json.loads(out)              # output remains valid JSON
        assert parsed["password"] == "[REDACTED]"

    def test_backslashes_inside_value(self):
        raw = '{"api_key": "a\\\\b\\"c-secret-tail"}'
        out = redact_secrets(raw, env={})
        assert "secret-tail" not in out
        assert json.loads(out)["api_key"] == "[REDACTED]"

    def test_unicode_escape_inside_value(self):
        raw = '{"token": "\\u0041bcdefgh-tail"}'
        out = redact_secrets(raw, env={})
        assert "tail" not in out
        assert json.loads(out)["token"] == "[REDACTED]"

    def test_nested_objects_inner_keys_redacted(self):
        raw = '{"outer": {"api_key": "x\\"y-inner-secret"}, "keep": "ok"}'
        out = redact_secrets(raw, env={})
        assert "inner-secret" not in out
        parsed = json.loads(out)
        assert parsed["outer"]["api_key"] == "[REDACTED]"
        assert parsed["keep"] == "ok"

    def test_invalid_json_fragment_does_not_break_control(self):
        # Truncated body: regex still consumes conservatively, no exception.
        raw = '... "password": "abc\\"defghijk'  # no closing quote at all
        out = redact_secrets(raw, env={})
        # Without a closing quote the JSON pattern cannot bound the value;
        # the value-based and param passes remain the guards here. The
        # control must simply not corrupt or crash.
        assert isinstance(out, str)

    def test_multiple_keys_one_line(self):
        raw = '{"token": "t\\"1-sec", "password": "p\\"2-sec", "other": "keep"}'
        out = redact_secrets(raw, env={})
        assert "1-sec" not in out and "2-sec" not in out
        parsed = json.loads(out)
        assert parsed["other"] == "keep"

    def test_idempotent_after_fix(self):
        raw = '{"password": "abc\\"defghijk"} Basic YTpi'
        once = redact_secrets(raw, env={})
        assert redact_secrets(once, env={}) == once

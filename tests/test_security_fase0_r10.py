"""Rodada-10 (focal): finding sensitive-data.redaction-json-bypass
(csf_cf7913e270a1fa5d1ab125fc). The two audit PoCs plus the full matrix the
report demands: every truncation point, escaped key, secret entirely absent,
valid JSON, nested/multiple keys, Basic/Bearer, params, configured env
values, idempotency. Confidentiality is asserted (secret NOT in output) —
never just the output type."""

import json

import pytest

from tradingagents.dataflows.redact import redact_secrets


SECRET = "SUPERSECRETVAL123"


class TestAuditPoCs:
    def test_poc_truncated_json_removes_secret(self):
        out = redact_secrets('truncated_json={"password": "abc\\"defghijk', env={})
        assert "defghijk" not in out
        assert 'abc\\"' not in out
        assert "[REDACTED]" in out

    def test_poc_escaped_key_is_semantically_matched(self):
        out = redact_secrets('{"api\\u005fkey": "unknown-secret-tail"}', env={})
        assert "unknown-secret-tail" not in out
        parsed = json.loads(out)
        assert parsed["api_key"] == "[REDACTED]"

    def test_poc_valid_escaped_value_still_redacted(self):
        out = redact_secrets('{"password": "abc\\"defghijk"}', env={})
        assert "defghijk" not in out
        assert json.loads(out)["password"] == "[REDACTED]"

    def test_poc_short_basic_still_redacted(self):
        out = redact_secrets("Authorization: Basic YTpi", env={})
        assert "YTpi" not in out and "Basic [REDACTED]" in out


class TestEveryTruncationPoint:
    """Cut the input at EVERY index after the sensitive value begins; the
    secret (or any suffix of it that entered the input) must never survive."""

    FULL = '{"password": "' + SECRET + '"} tail-text'

    def test_all_cut_points(self):
        value_start = self.FULL.index(SECRET)
        for cut in range(value_start + 1, len(self.FULL) + 1):
            fragment = self.FULL[:cut]
            out = redact_secrets(fragment, env={})
            entered = fragment[value_start:cut]
            # whatever portion of the secret entered the input is gone
            if entered and not entered.startswith('"'):
                secret_part = entered.split('"')[0]
                if secret_part:
                    assert secret_part not in out, (cut, out)

    def test_truncated_escaped_key_also_covered(self):
        out = redact_secrets('{"api\\u005fkey": "' + SECRET, env={})
        assert SECRET not in out


class TestAbsenceAndInnocentInputs:
    def test_secret_entirely_absent_text_unchanged(self):
        text = "ordinary error body with no credentials at all"
        assert redact_secrets(text, env={}) == text

    def test_innocent_valid_json_stays_byte_identical(self):
        text = '{"status": "ok", "count": 3}'
        assert redact_secrets(text, env={}) == text

    def test_innocent_truncated_json_untouched(self):
        text = '{"status": "ok", "cou'
        assert redact_secrets(text, env={}) == text


class TestValidJsonMatrix:
    def test_nested_and_multiple_keys(self):
        raw = json.dumps({
            "outer": {"api_key": "x\"y-inner", "keep": "ok"},
            "list": [{"token": "tok-1"}, {"password": "pw-2"}],
            "other": 1,
        })
        out = redact_secrets(raw, env={})
        parsed = json.loads(out)
        assert parsed["outer"]["api_key"] == "[REDACTED]"
        assert parsed["list"][0]["token"] == "[REDACTED]"
        assert parsed["list"][1]["password"] == "[REDACTED]"
        assert parsed["outer"]["keep"] == "ok" and parsed["other"] == 1
        assert "tok-1" not in out and "pw-2" not in out

    def test_non_string_sensitive_value_in_whole_json(self):
        out = redact_secrets('{"token": 1234567890}', env={})
        assert "1234567890" not in out
        assert json.loads(out)["token"] == "[REDACTED]"


class TestRegressionOtherPasses:
    def test_bearer_and_basic(self):
        out = redact_secrets(
            "Authorization: Bearer abcdefgh12345678 and Basic dXNlcjpwdw==", env={}
        )
        assert "abcdefgh12345678" not in out and "dXNlcjpwdw==" not in out

    def test_plain_params(self):
        out = redact_secrets("GET /v1?api_key=plainkey12345&x=1", env={})
        assert "plainkey12345" not in out

    def test_configured_env_value_redacted_anywhere(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"FRED_API_KEY": SECRET}):
            out = redact_secrets(f"body echoes {SECRET} verbatim")
        assert SECRET not in out


class TestIdempotency:
    @pytest.mark.parametrize("raw", [
        '{"password": "abc\\"defghijk"}',
        'truncated={"password": "abc\\"defghijk',
        '{"api\\u005fkey": "unknown-secret-tail"}',
        "Authorization: Basic YTpi",
        '{"status": "ok"}',
    ])
    def test_double_application_stable(self, raw):
        once = redact_secrets(raw, env={})
        assert redact_secrets(once, env={}) == once

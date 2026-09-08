"""Rodada-12 (focal): finding F1-r11 (csf_6c9d2403836fc9d0095728a4, scan
7672b597) — mismatched container closer terminated the sensitive value early.

Acceptance criteria from the report: both mismatch orders ({...] and [...}),
literal and escaped keys, direct and via UntrustedEvidence, valid mixed
nested containers as control, byte-absence assertions, idempotency, and full
r8–r11 regression (run as suite)."""

import pytest

from tradingagents.dataflows.evidence import UntrustedEvidence
from tradingagents.dataflows.redact import redact_secrets


def _ev(content: str) -> str:
    return UntrustedEvidence(
        source="t", kind="social", content=content,
        fetched_at="2026-09-06T00:00:00Z",
    ).content


class TestAuditEvidenceVerbatim:
    CASES = [
        ('{"secret": {"part": ] "MISMATCHSECRET"}', "MISMATCHSECRET"),
        ('{"token": ["head" } "MISMATCHTOKEN"]', "MISMATCHTOKEN"),
        ('{"api\\u005fkey": {] "ESCAPEDMISMATCH"}', "ESCAPEDMISMATCH"),
    ]

    @pytest.mark.parametrize("raw,leak", CASES)
    def test_secret_absent_direct_and_via_evidence(self, raw, leak):
        assert leak not in redact_secrets(raw, env={})
        assert leak not in _ev(raw)


class TestBothMismatchOrders:
    @pytest.mark.parametrize("key", ['"password"', '"api\\u005fkey"'])
    @pytest.mark.parametrize("opener,wrong", [("{", "]"), ("[", "}")])
    def test_mismatch_never_terminates(self, key, opener, wrong):
        raw = "{" + key + ": " + opener + '"a" ' + wrong + ' "TAILSECRET"'
        out = redact_secrets(raw, env={})
        assert "TAILSECRET" not in out
        assert "TAILSECRET" not in _ev(raw)

    @pytest.mark.parametrize("opener,wrong", [("{", "]"), ("[", "}")])
    def test_mismatch_deep_inside_also_fails_closed(self, opener, wrong):
        raw = ('{"secret": ' + opener + ' {"x": [1, 2' + wrong
               + ' "DEEPTAIL"')
        out = redact_secrets(raw, env={})
        assert "DEEPTAIL" not in out


class TestValidMixedNestedControls:
    def test_valid_mixed_nesting_terminates_exactly(self):
        raw = '{"secret": {"a": [1, {"b": [2]}]} , "keep": "SAFE-TAIL"'
        out = redact_secrets(raw, env={})
        assert "SAFE-TAIL" in out           # o que vem DEPOIS sobrevive
        assert '"b"' not in out              # o valor inteiro foi consumido
        assert "[REDACTED]" in out

    def test_valid_whole_json_mixed_still_parses(self):
        import json

        raw = '{"outer": {"secret": [{"k": [1, 2]}], "keep": 3}}'
        out = redact_secrets(raw, env={})
        parsed = json.loads(out)
        assert parsed["outer"]["secret"] == "[REDACTED]"
        assert parsed["outer"]["keep"] == 3


class TestIdempotencyAndInnocent:
    @pytest.mark.parametrize("raw", [
        '{"secret": {"part": ] "MISMATCHSECRET"}',
        '{"token": ["head" } "MISMATCHTOKEN"]',
        '{"secret": {"a": [1, {"b": [2]}]} , "keep": "ok"',
    ])
    def test_idempotent(self, raw):
        once = redact_secrets(raw, env={})
        assert redact_secrets(once, env={}) == once

    def test_innocent_mismatched_json_untouched(self):
        # sem chave sensível, delimitador trocado não é problema do redator
        raw = '{"status": {"x": ] "ok"}'
        assert redact_secrets(raw, env={}) == raw

"""Rodada-11 (focal): findings F1/F2 do scan 04b32298 (r10).

F1: valores NÃO-STRING sob chave sensível em JSON malformado/truncado —
    número, booleano, null, objeto e lista — nunca sobrevivem, com chave
    literal E escapada, em TODOS os pontos de truncamento, direto e através
    de UntrustedEvidence.
F2: profundidade imediatamente abaixo do limite processa; acima do limite
    produz saída segura SEM RecursionError, direto e via UntrustedEvidence.
Regressões preservadas: PoCs r8–r10, JSON inocente byte-idêntico,
Basic/Bearer/params/env, idempotência.
"""

import json

import pytest

from tradingagents.dataflows.evidence import UntrustedEvidence
from tradingagents.dataflows.redact import (
    _MAX_JSON_DEPTH,
    redact_secrets,
)


def _ev(content: str) -> str:
    return UntrustedEvidence(
        source="t", kind="social", content=content,
        fetched_at="2026-09-06T00:00:00Z",
    ).content


# Valores por tipo JSON, com os bytes-sentinela que NÃO podem sobreviver.
TYPED_VALUES = [
    ('"STRSECRET-abc\\"def"', ["STRSECRET", "def"]),
    ("1234567890", ["1234567890"]),
    ("true", ["true"]),
    ("false", ["false"]),
    ("null", ["null"]),
    ('{"part": "OBJECTSECRET", "n": 7}', ["OBJECTSECRET"]),
    ('["ARRAYSECRET", 42]', ["ARRAYSECRET", "42"]),
]
KEYS = ['"password"', '"api\\u005fkey"']  # literal e semanticamente escapada


class TestF1AllTypesAllTruncationPoints:
    @pytest.mark.parametrize("key", KEYS)
    @pytest.mark.parametrize("value,leaks", TYPED_VALUES)
    def test_every_truncation_point_direct_and_via_evidence(self, key, value, leaks):
        full = "{" + key + ": " + value + "} tail"
        value_start = full.index(": ") + 2
        for cut in range(value_start + 1, len(full) + 1):
            frag = full[:cut]
            out = redact_secrets(frag, env={})
            entered = frag[value_start:cut]
            for leak in leaks:
                # o prefixo do sentinela que ENTROU no fragmento não sobrevive
                for L in range(3, len(leak) + 1):
                    prefix = leak[:L]
                    if prefix in entered:
                        assert prefix not in out, (key, value, cut, out)
        # fronteira de produção nos 3 cortes representativos
        for cut in (value_start + 2, max(value_start + 2, len(full) - 6), len(full)):
            frag = full[:cut]
            ev_out = _ev(frag)
            for leak in leaks:
                if leak in frag:
                    assert leak not in ev_out, (key, value, cut, ev_out)

    def test_audit_evidence_cases_verbatim(self):
        cases = [
            ('{"token": 1234567890', "1234567890"),
            ('{"password": true', "true"),
            ('{"secret": {"part": "OBJECTSECRET"}', "OBJECTSECRET"),
            ('{"authorization": ["ARRAYSECRET"]', "ARRAYSECRET"),
            ('{"api\\u005fkey": 9876543210', "9876543210"),
        ]
        for raw, leak in cases:
            assert leak not in redact_secrets(raw, env={})
            assert leak not in _ev(raw)


class TestF2DepthBudget:
    def test_just_below_limit_processes_and_redacts(self):
        depth = _MAX_JSON_DEPTH - 4
        raw = ('{"a":' * depth) + '{"token": "DEEPSECRET"}' + ("}" * depth)
        out = redact_secrets(raw, env={})
        assert "DEEPSECRET" not in out

    @pytest.mark.parametrize("depth", [_MAX_JSON_DEPTH + 1, 200, 1000, 1500])
    def test_above_limit_no_recursion_error(self, depth):
        raw = ("[" * depth) + "1" + ("]" * depth)
        out = redact_secrets(raw, env={})     # nenhuma exceção
        assert isinstance(out, str)
        _ev(raw)                              # fronteira real também

    def test_deep_value_under_sensitive_key_in_invalid_json(self):
        # profundo E truncado E sensível: consumidor de container iterativo
        raw = '{"secret": ' + ("[" * 900) + '"DEEPLEAK"'
        out = redact_secrets(raw, env={})
        assert "DEEPLEAK" not in out
        assert "DEEPLEAK" not in _ev(raw)

    def test_deep_dict_via_evidence_no_exception(self):
        raw = ('{"a":' * 1000) + "1" + ("}" * 1000)
        assert isinstance(_ev(raw), str)


class TestRegressionsPreserved:
    def test_r9_r10_pocs(self):
        out = redact_secrets('truncated={"password": "abc\\"defghijk', env={})
        assert "defghijk" not in out
        out = redact_secrets('{"api\\u005fkey": "unknown-secret-tail"}', env={})
        assert "unknown-secret-tail" not in out
        out = redact_secrets("Authorization: Basic YTpi", env={})
        assert "YTpi" not in out

    def test_innocent_json_byte_identical(self):
        for text in ('{"status": "ok", "count": 3}',
                     '{"status": "ok", "cou',
                     "texto comum sem credenciais"):
            assert redact_secrets(text, env={}) == text

    def test_valid_json_still_parses_after_redaction(self):
        out = redact_secrets(json.dumps({"outer": {"api_key": "x", "keep": 1}}), env={})
        parsed = json.loads(out)
        assert parsed["outer"]["api_key"] == "[REDACTED]"
        assert parsed["outer"]["keep"] == 1

    @pytest.mark.parametrize("raw", [
        '{"token": 1234567890',
        '{"secret": {"part": "OBJECTSECRET"}',
        '{"password": "abc\\"defghijk"}',
        "Authorization: Basic YTpi and Bearer abcdefgh12345678",
        '{"status": "ok"}',
        ("[" * 300) + "1" + ("]" * 300),
    ])
    def test_idempotency(self, raw):
        once = redact_secrets(raw, env={})
        assert redact_secrets(once, env={}) == once

    def test_env_value_pass_unaffected(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"X_API_KEY": "ENVSECRET123XYZ"}):
            assert "ENVSECRET123XYZ" not in redact_secrets("echo ENVSECRET123XYZ")

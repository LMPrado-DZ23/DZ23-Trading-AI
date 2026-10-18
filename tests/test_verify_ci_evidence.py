"""Regressão para as evidências de CI citadas nos documentos de estado."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path("scripts/verify_ci_evidence.py")


def _module():
    spec = importlib.util.spec_from_file_location("verify_ci_evidence", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_accepts_a_successful_run_for_the_documented_commit():
    module = _module()
    evidence = {
        "schema": "dz23.ci-evidence/1",
        "claims": [{"run_id": 123, "head_sha": "a" * 40}],
    }

    errors = module.validate_claims(
        evidence, "LMPrado-DZ23/DZ23-Trading-AI", "token", opener=lambda _: {
            "conclusion": "success", "head_sha": "a" * 40,
        })

    assert errors == []


def test_rejects_a_claim_when_the_real_run_failed_or_targets_another_commit():
    module = _module()
    evidence = {
        "schema": "dz23.ci-evidence/1",
        "claims": [{"run_id": 123, "head_sha": "a" * 40}],
    }

    failed = module.validate_claims(
        evidence, "LMPrado-DZ23/DZ23-Trading-AI", "token", opener=lambda _: {
            "conclusion": "failure", "head_sha": "a" * 40,
        })
    wrong_commit = module.validate_claims(
        evidence, "LMPrado-DZ23/DZ23-Trading-AI", "token", opener=lambda _: {
            "conclusion": "success", "head_sha": "b" * 40,
        })

    assert "conclusion=failure" in failed[0]
    assert "SHA diferente" in wrong_commit[0]


def test_rejects_malformed_evidence_before_any_network_request(tmp_path):
    module = _module()
    path = tmp_path / "ci-evidence.json"
    path.write_text(json.dumps({"schema": "outra", "claims": []}), encoding="utf-8")

    try:
        module.load_evidence(path)
    except ValueError as exc:
        assert "schema" in str(exc)
    else:
        raise AssertionError("evidência inválida foi aceita")

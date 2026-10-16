from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_every_alert_has_an_existing_runbook_and_bounded_window():
    policy = (ROOT / "ops" / "alerts.yml").read_text(encoding="utf-8")
    runbooks = re.findall(r"^\s+runbook: (.+)$", policy, flags=re.MULTILINE)
    alert_ids = re.findall(r"^\s+- id: (.+)$", policy, flags=re.MULTILINE)

    assert len(alert_ids) == len(runbooks) == 5
    assert "bearer_from_secret_store" in policy
    assert "evaluation:\n  interval_seconds: 30" in policy
    for relative_path in runbooks:
        path = ROOT / relative_path
        assert path.is_file(), f"runbook ausente para alerta: {relative_path}"
        content = path.read_text(encoding="utf-8")
        assert content.startswith("# Runbook: DZ23")
        assert len(content) > 250


def test_observability_contract_forbids_sensitive_high_cardinality_labels():
    contract = (ROOT / "docs" / "OBSERVABILITY.md").read_text(encoding="utf-8")
    assert "Uma coleta única ou um teste local não constitui prova de SLO" in contract
    assert "request ID como rótulo" in contract
    assert "conta de corretora" in contract
    assert "token" in contract

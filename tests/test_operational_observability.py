"""Prova contínua de SLO e entrega externa, sem autorizar LIVE."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tradingagents.desk import operational_observability as observability


def _samples(*, count=121, interval=30, instance="instance-1", errors=0,
             p95_limit=1000.0, dropped=0):
    start = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
    result = []
    for index in range(count):
        requests = index * 3
        result.append({
            "observed_at": (start + timedelta(seconds=index * interval)).isoformat(),
            "health_http_status": 200,
            "readiness_status": "ready",
            "kill_switch": False,
            "process_instance_id": instance,
            "requests": requests,
            "errors_5xx": min(errors, index),
            "histogram_count": requests,
            "duration_buckets": {
                format(p95_limit, "g"): requests,
                "2000": requests,
                "5000": requests,
            },
            "telemetry_series_dropped": dropped,
        })
    return result


def _delivery(samples, channel="telegram"):
    return {"ok": True, "canal": channel, "em": samples[-1]["observed_at"],
            "latencia_ms": 12}


def test_writes_and_revalidates_continuous_sanitized_proof(tmp_path):
    policy = tmp_path / "alerts.yml"
    policy.write_text("schema: test\n", encoding="utf-8")
    samples = _samples()

    proof = observability.write_observability_proof(
        tmp_path, samples, _delivery(samples), policy_path=policy
    )
    status = observability.validate_observability_proof(
        tmp_path,
        now=datetime(2026, 10, 16, 13, 1, tzinfo=timezone.utc),
        policy_path=policy,
        expected_instance_id="instance-1",
    )

    assert proof["envio_de_ordens"] is False
    assert status["valida"] is True
    assert status["amostras"] == 121
    serialized = json.dumps(proof)
    assert "Authorization" not in serialized
    assert "Bearer" not in serialized

    restarted = observability.validate_observability_proof(
        tmp_path,
        now=datetime(2026, 10, 16, 13, 1, tzinfo=timezone.utc),
        policy_path=policy,
        expected_instance_id="instance-2",
    )
    assert restarted["valida"] is False
    assert "reiniciou" in restarted["motivo"]


def test_writer_discards_unrecognized_sample_fields(tmp_path):
    policy = tmp_path / "alerts.yml"
    policy.write_text("schema: test\n", encoding="utf-8")
    samples = _samples()
    samples[0]["authorization"] = "Bearer must-not-be-persisted"

    proof = observability.write_observability_proof(
        tmp_path, samples, _delivery(samples), policy_path=policy
    )

    assert "must-not-be-persisted" not in json.dumps(proof)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda rows: rows.__setitem__(60, {**rows[60], "health_http_status": 503}),
         "indisponibilidade"),
        (lambda rows: rows.__setitem__(60, {**rows[60], "kill_switch": True}),
         "parada"),
        (lambda rows: rows.__setitem__(60, {**rows[60], "process_instance_id": "restarted"}),
         "reiniciou"),
    ],
)
def test_refuses_unhealthy_blocked_or_restarted_window(mutate, message):
    samples = _samples()
    mutate(samples)
    with pytest.raises(observability.ObservabilityProofError, match=message):
        observability.evaluate_evidence(samples, _delivery(samples))


def test_refuses_short_sparse_slow_or_non_external_evidence():
    short = _samples(count=120)
    with pytest.raises(observability.ObservabilityProofError, match="1 hora"):
        observability.evaluate_evidence(short, _delivery(short))

    sparse = _samples(interval=61)
    with pytest.raises(observability.ObservabilityProofError, match="60 segundos"):
        observability.evaluate_evidence(sparse, _delivery(sparse))

    slow = _samples(p95_limit=2500.0)
    for item in slow:
        item["duration_buckets"] = {"2500": item["requests"], "5000": item["requests"]}
    with pytest.raises(observability.ObservabilityProofError, match="p95"):
        observability.evaluate_evidence(slow, _delivery(slow))

    good = _samples()
    with pytest.raises(observability.ObservabilityProofError, match="externo"):
        observability.evaluate_evidence(good, _delivery(good, "email"))


def test_validation_rejects_policy_drift_tampering_and_expiry(tmp_path):
    policy = tmp_path / "alerts.yml"
    policy.write_text("schema: first\n", encoding="utf-8")
    samples = _samples()
    observability.write_observability_proof(
        tmp_path, samples, _delivery(samples), policy_path=policy
    )

    policy.write_text("schema: changed\n", encoding="utf-8")
    assert "política" in observability.validate_observability_proof(
        tmp_path, now=datetime(2026, 10, 16, 13, tzinfo=timezone.utc), policy_path=policy
    )["motivo"]

    policy.write_text("schema: first\n", encoding="utf-8")
    proof_path = tmp_path / observability.PROOF_NAME
    payload = json.loads(proof_path.read_text(encoding="utf-8"))
    payload["summary"]["requisicoes"] += 1
    proof_path.write_text(json.dumps(payload), encoding="utf-8")
    assert "resumo" in observability.validate_observability_proof(
        tmp_path, now=datetime(2026, 10, 16, 13, tzinfo=timezone.utc), policy_path=policy
    )["motivo"]

    observability.write_observability_proof(
        tmp_path, samples, _delivery(samples), policy_path=policy
    )
    expired = observability.validate_observability_proof(
        tmp_path, now=datetime(2026, 10, 18, tzinfo=timezone.utc), policy_path=policy
    )
    assert expired["valida"] is False
    assert "expirou" in expired["motivo"]

"""Evidência temporal, sanitizada e fail-closed de observabilidade operacional."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tradingagents.desk.storage import escrever_json_atomico

SCHEMA = "dz23.observability-proof/2"
PROOF_NAME = "observability-proof.json"
MIN_DURATION = timedelta(hours=1)
MAX_AGE = timedelta(hours=24)
MAX_GAP_SECONDS = 60
MIN_SAMPLES = 100
MIN_REQUESTS = 20
MAX_ERROR_RATIO = 0.01
MAX_P95_MS = 2000.0
MAX_FUTURE_SKEW = timedelta(minutes=5)
EXTERNAL_CHANNELS = frozenset({"telegram", "discord", "whatsapp", "webpush"})


class ObservabilityProofError(RuntimeError):
    """A evidência não demonstra operação monitorada dentro do contrato."""


def _parse_time(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ObservabilityProofError("horário de observabilidade inválido") from exc
    if parsed.tzinfo is None:
        raise ObservabilityProofError("horário de observabilidade não possui fuso")
    return parsed.astimezone(timezone.utc)


def default_policy_path() -> Path:
    return Path(__file__).resolve().parents[2] / "ops" / "alerts.yml"


def policy_sha256(path: str | Path | None = None) -> str:
    policy = Path(path) if path is not None else default_policy_path()
    if not policy.is_file() or policy.is_symlink():
        raise ObservabilityProofError("política de alertas não está disponível")
    return hashlib.sha256(policy.read_bytes()).hexdigest()


def sanitize_sample(observed_at: str, health_http_status: int,
                    health: dict, readiness: dict, metrics: dict) -> dict:
    """Retém apenas agregados operacionais; descarta rótulos e dados de negócio."""
    counters = {"requests": 0, "errors_5xx": 0}
    for series in metrics.get("counter_series") or ():
        if not isinstance(series, dict) or series.get("name") != "http.requests":
            continue
        value = int(series.get("value") or 0)
        counters["requests"] += value
        if (series.get("labels") or {}).get("status_class") == "5xx":
            counters["errors_5xx"] += value

    bucket_totals: dict[str, int] = {}
    histogram_count = 0
    for series in metrics.get("histogram_series") or ():
        if not isinstance(series, dict) or series.get("name") != "http.request.duration_ms":
            continue
        histogram_count += int(series.get("count") or 0)
        for bucket in series.get("buckets") or ():
            limit = float(bucket.get("le"))
            key = format(limit, "g")
            bucket_totals[key] = bucket_totals.get(key, 0) + int(bucket.get("count") or 0)

    telemetry_dropped = int(
        (metrics.get("counters") or {}).get("telemetry.series_dropped") or 0
    )
    return {
        "observed_at": _parse_time(observed_at).isoformat(),
        "health_http_status": int(health_http_status),
        "health_status": str(health.get("status") or ""),
        "readiness_status": str(readiness.get("status") or ""),
        "paper_status": str((readiness.get("paper") or {}).get("status") or ""),
        "kill_switch": readiness.get("kill_switch") is True,
        "process_instance_id": str(metrics.get("process_instance_id") or ""),
        "requests": counters["requests"],
        "errors_5xx": counters["errors_5xx"],
        "histogram_count": histogram_count,
        "duration_buckets": bucket_totals,
        "telemetry_series_dropped": telemetry_dropped,
    }


def _delta(last: dict, first: dict, key: str) -> int:
    value = int(last.get(key) or 0) - int(first.get(key) or 0)
    if value < 0:
        raise ObservabilityProofError(f"contador {key} regrediu durante a janela")
    return value


def _canonical_sample(sample: dict) -> dict:
    buckets = sample.get("duration_buckets") or {}
    if not isinstance(buckets, dict):
        raise ObservabilityProofError("buckets de duração inválidos")
    return {
        "observed_at": _parse_time(sample.get("observed_at")).isoformat(),
        "health_http_status": int(sample.get("health_http_status") or 0),
        "health_status": str(sample.get("health_status") or ""),
        "readiness_status": str(sample.get("readiness_status") or ""),
        "paper_status": str(sample.get("paper_status") or ""),
        "kill_switch": sample.get("kill_switch") is True,
        "process_instance_id": str(sample.get("process_instance_id") or ""),
        "requests": int(sample.get("requests") or 0),
        "errors_5xx": int(sample.get("errors_5xx") or 0),
        "histogram_count": int(sample.get("histogram_count") or 0),
        "duration_buckets": {
            format(float(limit), "g"): int(count)
            for limit, count in buckets.items()
        },
        "telemetry_series_dropped": int(
            sample.get("telemetry_series_dropped") or 0
        ),
    }


def evaluate_evidence(samples: list[dict], delivery: dict) -> dict:
    if len(samples) < MIN_SAMPLES:
        raise ObservabilityProofError(
            f"janela precisa de ao menos {MIN_SAMPLES} amostras")
    ordered = sorted(samples, key=lambda item: _parse_time(item.get("observed_at")))
    times = [_parse_time(item.get("observed_at")) for item in ordered]
    if any(right <= left for left, right in zip(times, times[1:], strict=False)):
        raise ObservabilityProofError("amostras precisam de horários distintos")
    duration = (times[-1] - times[0]).total_seconds()
    if duration < MIN_DURATION.total_seconds():
        raise ObservabilityProofError("janela contínua menor que 1 hora")
    gaps = [
        (right - left).total_seconds()
        for left, right in zip(times, times[1:], strict=False)
    ]
    max_gap = max(gaps, default=0.0)
    if max_gap > MAX_GAP_SECONDS:
        raise ObservabilityProofError("lacuna de coleta excedeu 60 segundos")
    if any(int(item.get("health_http_status") or 0) != 200 for item in ordered):
        raise ObservabilityProofError("health apresentou indisponibilidade")
    if any(item.get("health_status") != "ok" for item in ordered):
        raise ObservabilityProofError("health apresentou estado degradado")
    if any(item.get("readiness_status") != "ready" or item.get("kill_switch") is True
           for item in ordered):
        raise ObservabilityProofError("readiness ou parada de emergência bloqueou a janela")
    if any(item.get("paper_status") != "READY" for item in ordered):
        raise ObservabilityProofError("Paper Adapter não permaneceu saudável")

    instances = {str(item.get("process_instance_id") or "") for item in ordered}
    if "" in instances or len(instances) != 1:
        raise ObservabilityProofError("processo reiniciou ou não publicou identidade")

    first, last = ordered[0], ordered[-1]
    requests = _delta(last, first, "requests")
    errors = _delta(last, first, "errors_5xx")
    histogram_count = _delta(last, first, "histogram_count")
    dropped = _delta(last, first, "telemetry_series_dropped")
    if requests < MIN_REQUESTS or histogram_count < MIN_REQUESTS:
        raise ObservabilityProofError("tráfego insuficiente para validar RED")
    error_ratio = errors / requests
    if error_ratio > MAX_ERROR_RATIO:
        raise ObservabilityProofError("taxa de respostas 5xx excedeu 1%")
    if dropped != 0:
        raise ObservabilityProofError("telemetria descartou séries durante a janela")

    first_buckets = first.get("duration_buckets") or {}
    last_buckets = last.get("duration_buckets") or {}
    deltas: list[tuple[float, int]] = []
    for raw_limit, raw_count in last_buckets.items():
        limit = float(raw_limit)
        count = int(raw_count) - int(first_buckets.get(raw_limit) or 0)
        if count < 0:
            raise ObservabilityProofError("histograma regrediu durante a janela")
        deltas.append((limit, count))
    deltas.sort()
    target = max(1, math.ceil(histogram_count * 0.95))
    p95 = next((limit for limit, count in deltas if count >= target), None)
    if p95 is None or p95 > MAX_P95_MS:
        raise ObservabilityProofError("latência p95 excedeu 2 segundos")

    channel = str(delivery.get("canal") or "").lower()
    if delivery.get("ok") is not True or channel not in EXTERNAL_CHANNELS:
        raise ObservabilityProofError("entrega de alerta externo não foi comprovada")
    delivered_at = _parse_time(delivery.get("em"))
    if delivered_at < times[0] or delivered_at > times[-1] + MAX_FUTURE_SKEW:
        raise ObservabilityProofError("entrega de alerta está fora da janela")

    return {
        "janela_inicio": times[0].isoformat(),
        "janela_fim": times[-1].isoformat(),
        "duracao_segundos": round(duration, 3),
        "amostras": len(ordered),
        "maior_lacuna_segundos": round(max_gap, 3),
        "process_instance_id": next(iter(instances)),
        "requisicoes": requests,
        "erros_5xx": errors,
        "taxa_5xx": round(error_ratio, 8),
        "p95_superior_ms": p95,
        "series_descartadas": dropped,
        "canal_alerta": channel,
        "alerta_entregue_em": delivered_at.isoformat(),
    }


def write_observability_proof(data_dir: str | Path, samples: list[dict],
                              delivery: dict, *, policy_path: str | Path | None = None) -> dict:
    canonical_samples = [_canonical_sample(sample) for sample in samples]
    summary = evaluate_evidence(canonical_samples, delivery)
    proof = {
        "schema": SCHEMA,
        "resultado": "janela_e_alerta_verificados",
        "policy_sha256": policy_sha256(policy_path),
        "envio_de_ordens": False,
        "samples": canonical_samples,
        "delivery": {
            "ok": True,
            "canal": summary["canal_alerta"],
            "em": summary["alerta_entregue_em"],
            "latencia_ms": float(delivery.get("latencia_ms") or 0),
        },
        "summary": summary,
    }
    escrever_json_atomico(Path(data_dir) / PROOF_NAME, proof)
    return proof


def validate_observability_proof(data_dir: str | Path, *, now: datetime | None = None,
                                 policy_path: str | Path | None = None,
                                 expected_instance_id: str | None = None) -> dict:
    base = {"valida": False, "verificado_em": "", "idade_maxima_horas": 24}
    path = Path(data_dir) / PROOF_NAME
    if not path.is_file() or path.is_symlink():
        return {**base, "motivo": "janela contínua de observabilidade ainda não registrada"}
    try:
        proof = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(proof, dict) or proof.get("schema") != SCHEMA:
            raise ObservabilityProofError("schema da prova de observabilidade incompatível")
        if proof.get("resultado") != "janela_e_alerta_verificados":
            raise ObservabilityProofError("observabilidade não foi aprovada")
        if proof.get("envio_de_ordens") is not False:
            raise ObservabilityProofError("prova não garante ausência de ordens")
        if proof.get("policy_sha256") != policy_sha256(policy_path):
            raise ObservabilityProofError("política de alertas mudou; refaça a janela")
        summary = evaluate_evidence(proof.get("samples") or [], proof.get("delivery") or {})
        if proof.get("summary") != summary:
            raise ObservabilityProofError("resumo da prova de observabilidade divergiu")
        if (expected_instance_id is not None
                and summary["process_instance_id"] != expected_instance_id):
            raise ObservabilityProofError(
                "serviço reiniciou depois da prova; refaça a janela")
        verified = _parse_time(summary["janela_fim"])
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if verified > current + MAX_FUTURE_SKEW:
            raise ObservabilityProofError("prova de observabilidade possui horário futuro")
        if current - verified > MAX_AGE:
            raise ObservabilityProofError("prova de observabilidade expirou")
    except (OSError, json.JSONDecodeError, ObservabilityProofError, TypeError, ValueError) as exc:
        return {**base, "motivo": str(exc)}
    return {
        **base,
        "valida": True,
        "verificado_em": verified.isoformat(),
        "amostras": summary["amostras"],
        "duracao_segundos": summary["duracao_segundos"],
        "canal_alerta": summary["canal_alerta"],
        "motivo": "janela RED contínua e entrega externa de alerta comprovadas",
    }


__all__ = [
    "EXTERNAL_CHANNELS", "PROOF_NAME", "SCHEMA", "ObservabilityProofError",
    "evaluate_evidence", "sanitize_sample", "validate_observability_proof",
    "write_observability_proof",
]

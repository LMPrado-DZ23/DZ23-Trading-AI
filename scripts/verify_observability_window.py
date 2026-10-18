"""Coleta uma janela operacional real sem receber URL ou segredos na CLI."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

from tradingagents.desk.operational_observability import (
    MIN_DURATION,
    sanitize_sample,
    write_observability_proof,
)


def _json(response: requests.Response) -> dict:
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("endpoint não retornou objeto JSON")
    return payload


def _validated_base_url(value: str) -> str:
    """Aceita somente a origem HTTPS, sem credenciais, query ou subcaminho."""
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ValueError(
            "DZ23_OBSERVABILITY_BASE_URL deve ser uma origem HTTPS sem "
            "credenciais, query, fragmento ou subcaminho"
        )
    return f"https://{parsed.netloc}".rstrip("/")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Coleta health/readiness/RED e comprova entrega de alerta externo"
    )
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--duration-seconds", type=int, default=3600)
    parser.add_argument("--interval-seconds", type=int, default=30)
    parser.add_argument("--timeout-seconds", type=int, default=15)
    args = parser.parse_args(argv)
    if args.duration_seconds < int(MIN_DURATION.total_seconds()):
        parser.error("--duration-seconds deve ser no mínimo 3600")
    if not 10 <= args.interval_seconds <= 60:
        parser.error("--interval-seconds deve ficar entre 10 e 60")

    raw_base_url = os.environ.get("DZ23_OBSERVABILITY_BASE_URL", "")
    token = os.environ.get("DZ23_OBSERVABILITY_TOKEN", "")
    channel = os.environ.get("DZ23_OBSERVABILITY_ALERT_CHANNEL", "").strip().lower()
    if not raw_base_url or not token or not channel:
        parser.error(
            "defina DZ23_OBSERVABILITY_BASE_URL, DZ23_OBSERVABILITY_TOKEN e "
            "DZ23_OBSERVABILITY_ALERT_CHANNEL no ambiente"
        )
    try:
        base_url = _validated_base_url(raw_base_url)
    except ValueError as exc:
        parser.error(str(exc))

    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})
    samples: list[dict] = []
    started = time.monotonic()
    first_observed_at: datetime | None = None
    next_sample_at = started
    while True:
        observed = datetime.now(timezone.utc)
        first_observed_at = first_observed_at or observed
        observed_at = observed.isoformat()
        health = session.get(base_url + "/healthz", timeout=args.timeout_seconds)
        health_payload = _json(health)
        readiness = _json(session.get(base_url + "/readyz", timeout=args.timeout_seconds))
        metrics = _json(session.get(base_url + "/metrics", timeout=args.timeout_seconds))
        samples.append(sanitize_sample(
            observed_at, health.status_code, health_payload, readiness, metrics
        ))
        if (observed - first_observed_at).total_seconds() >= args.duration_seconds:
            break
        next_sample_at += args.interval_seconds
        delay = next_sample_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    delivery = _json(session.post(
        base_url + "/v1/notificacoes/testar",
        json={"canal": channel},
        timeout=args.timeout_seconds,
    ))
    proof = write_observability_proof(args.data_dir, samples, delivery)
    print(json.dumps({
        "schema": proof["schema"],
        "resultado": proof["resultado"],
        "summary": proof["summary"],
        "envio_de_ordens": False,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

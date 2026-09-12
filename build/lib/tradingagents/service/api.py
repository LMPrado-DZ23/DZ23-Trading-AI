"""Authenticated FastAPI surface for durable analysis/backtest jobs."""

from __future__ import annotations

import os
import secrets
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status

from .jobs import JOB_SCHEMA_VERSION, JobSubmission
from .store import IdempotencyConflict, JobStore


def create_app(store: JobStore, *, api_token: str | None = None) -> FastAPI:
    token = api_token or os.environ.get("TRADINGAGENTS_API_TOKEN")
    if not token or len(token) < 32:
        raise ValueError("TRADINGAGENTS_API_TOKEN must contain at least 32 characters")
    store.initialize()
    app = FastAPI(
        title="TradingAgents Job API",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def authorize(authorization: Annotated[str | None, Header()] = None) -> None:
        prefix = "Bearer "
        candidate = authorization[len(prefix):] if authorization and authorization.startswith(prefix) else ""
        if not candidate or not secrets.compare_digest(candidate, token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok", "schema": JOB_SCHEMA_VERSION}

    @app.post("/v1/jobs", dependencies=[Depends(authorize)])
    def submit_job(
        submission: JobSubmission,
        response: Response,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict:
        if idempotency_key is None or not 16 <= len(idempotency_key) <= 128:
            raise HTTPException(status_code=400, detail="Idempotency-Key must be 16-128 characters")
        try:
            record, created = store.enqueue(submission.canonical_payload(), idempotency_key)
        except IdempotencyConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        response.status_code = 202 if created else 200
        return record.public_dict()

    @app.get("/v1/jobs/{job_id}", dependencies=[Depends(authorize)])
    def get_job(job_id: str) -> dict:
        record = store.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="job not found")
        return record.public_dict()

    @app.post("/v1/jobs/{job_id}/cancel", dependencies=[Depends(authorize)])
    def cancel_job(job_id: str) -> dict:
        record = store.cancel(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="job not found")
        return record.public_dict()

    return app

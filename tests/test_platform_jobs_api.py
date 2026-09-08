from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tradingagents.platform.api import ApiSecuritySettings, SqlAlchemyIdentityAdapter
from tradingagents.platform.api.app import create_platform_app
from tradingagents.platform.api.jobs_adapter import SqlAlchemyAnalysisJobAdapter
from tradingagents.platform.api.security import InMemorySlidingWindowRateLimiter
from tradingagents.platform.identity import Base, IdentityService, TokenHasher
from tradingagents.platform.jobs import JobService


class Mailbox:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, **message) -> None:
        self.messages.append(message)


def _stack() -> tuple[TestClient, Mailbox]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    identity = IdentityService(
        factory,
        token_hasher=TokenHasher(b"jobs-api-identity-token-pepper-value"),
    )
    mailbox = Mailbox()
    app = create_platform_app(
        identity_service=SqlAlchemyIdentityAdapter(
            service=identity,
            recovery_delivery=Mailbox(),
            verification_delivery=mailbox,
        ),
        analysis_job_service=SqlAlchemyAnalysisJobAdapter(
            identity=identity,
            jobs=JobService(
                factory,
                idempotency_digest_key=b"j" * 32,
                llm_reference_validator=SimpleNamespace(assert_reference=lambda **_kwargs: None),
            ),
        ),
        security=ApiSecuritySettings(csrf_secret=b"jobs-api-csrf-secret-value-32-bytes"),
        rate_limiter=InMemorySlidingWindowRateLimiter(),
    )
    return TestClient(app, base_url="https://testserver"), mailbox


def _csrf(client: TestClient) -> str:
    response = client.get("/v1/auth/csrf")
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _register_and_verify(client: TestClient, mailbox: Mailbox) -> None:
    response = client.post(
        "/v1/auth/register",
        headers={"x-csrf-token": _csrf(client)},
        json={
            "email": "jobs-owner@example.test",
            "password": "correct horse battery staple",
            "display_name": "Jobs Owner",
            "organization_name": "Jobs Org",
            "organization_slug": "jobs-org",
        },
    )
    assert response.status_code == 201
    response = client.post(
        "/v1/auth/email-verification/confirm",
        headers={"x-csrf-token": _csrf(client)},
        json={"verification_token": mailbox.messages[0]["token"]},
    )
    assert response.status_code == 200


def test_analysis_jobs_api_enqueue_replay_list_and_cancel() -> None:
    client, mailbox = _stack()
    _register_and_verify(client, mailbox)
    payload = {
        "symbol": "BTC/USDT",
        "asset_type": "crypto",
        "trade_date": "2026-09-08",
        "mode": "shadow",
        "provider": "openai",
        "model": "gpt-5",
        "llm_connection_id": str(uuid4()),
        "llm_credential_version": 1,
        "selected_analysts": ["market", "news"],
        "config": {"output_language": "Português"},
    }
    headers = {
        "x-csrf-token": _csrf(client),
        "idempotency-key": "analysis-job-api-0001",
    }
    created = client.post("/v1/jobs", headers=headers, json=payload)
    assert created.status_code == 202
    job_id = created.json()["id"]
    assert client.post("/v1/jobs", headers=headers, json=payload).json()["id"] == job_id
    listed = client.get("/v1/jobs")
    assert listed.status_code == 200
    assert listed.json()[0]["status"] == "queued"
    detail = client.get(f"/v1/jobs/{job_id}")
    assert detail.status_code == 200
    assert detail.json()["result"] is None
    cancelled = client.post(
        f"/v1/jobs/{job_id}/cancel",
        headers={"x-csrf-token": _csrf(client)},
    )
    assert cancelled.status_code == 200
    assert client.get("/v1/jobs").json()[0]["status"] == "cancelled"


def test_analysis_jobs_api_rejects_live_and_nested_credentials() -> None:
    client, mailbox = _stack()
    _register_and_verify(client, mailbox)
    payload = {
        "symbol": "BTC/USDT",
        "asset_type": "crypto",
        "trade_date": "2026-09-08",
        "mode": "live_automated",
        "provider": "openai",
        "model": "gpt-5",
        "llm_connection_id": str(uuid4()),
        "llm_credential_version": 1,
        "selected_analysts": ["market"],
        "config": {},
    }
    response = client.post(
        "/v1/jobs",
        headers={
            "x-csrf-token": _csrf(client),
            "idempotency-key": "analysis-job-api-live",
        },
        json=payload,
    )
    assert response.status_code == 422

    payload["mode"] = "shadow"
    payload["config"] = {"provider": {"accessToken": "never-store-me"}}
    response = client.post(
        "/v1/jobs",
        headers={
            "x-csrf-token": _csrf(client),
            "idempotency-key": "analysis-job-api-secret",
        },
        json=payload,
    )
    assert response.status_code == 400
    assert "never-store-me" not in response.text

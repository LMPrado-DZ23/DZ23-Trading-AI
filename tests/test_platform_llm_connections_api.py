from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tradingagents.platform.api import ApiSecuritySettings, SqlAlchemyIdentityAdapter
from tradingagents.platform.api.app import create_platform_app
from tradingagents.platform.api.compliance_adapter import SqlAlchemyComplianceAdapter
from tradingagents.platform.api.llm_connections_adapter import (
    SqlAlchemyLlmConnectionAdapter,
)
from tradingagents.platform.api.security import InMemorySlidingWindowRateLimiter
from tradingagents.platform.compliance import (
    AesGcmSecretCrypto,
    ComplianceService,
    MfaService,
    RecoveryCodeHasher,
    TotpEngine,
)
from tradingagents.platform.connections import CredentialKeyring
from tradingagents.platform.identity import Base, IdentityService, TokenHasher
from tradingagents.platform.llm_connections import LlmConnectionService, LlmCredential


class Mailbox:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, **message) -> None:
        self.messages.append(message)


def _stack():
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
        factory, token_hasher=TokenHasher(b"llm-api-identity-token-pepper-value")
    )
    mfa = MfaService(
        factory,
        crypto=AesGcmSecretCrypto(key=b"m" * 32, key_id="llm-api-mfa"),
        recovery_hasher=RecoveryCodeHasher(b"llm-api-recovery-pepper-value-32"),
    )
    compliance = ComplianceService(factory)
    service = LlmConnectionService(
        factory,
        keyring=CredentialKeyring(keys={"test": b"k" * 32}, active_key_id="test"),
        idempotency_digest_key=b"i" * 32,
    )
    mailbox = Mailbox()
    app = create_platform_app(
        identity_service=SqlAlchemyIdentityAdapter(
            service=identity,
            recovery_delivery=Mailbox(),
            verification_delivery=mailbox,
        ),
        llm_connection_service=SqlAlchemyLlmConnectionAdapter(
            identity=identity, mfa=mfa, connections=service
        ),
        compliance_service=SqlAlchemyComplianceAdapter(
            identity=identity,
            compliance=compliance,
            mfa=mfa,
        ),
        security=ApiSecuritySettings(csrf_secret=b"llm-api-csrf-secret-value-32-bytes"),
        rate_limiter=InMemorySlidingWindowRateLimiter(),
    )
    return TestClient(app, base_url="https://testserver"), mailbox, factory


def _csrf(client: TestClient) -> str:
    response = client.get("/v1/auth/csrf")
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _onboard(client: TestClient, mailbox: Mailbox) -> None:
    assert (
        client.post(
            "/v1/auth/register",
            headers={"x-csrf-token": _csrf(client)},
            json={
                "email": "llm-owner@example.test",
                "password": "correct horse battery staple",
                "display_name": "LLM Owner",
                "organization_name": "LLM Org",
                "organization_slug": "llm-org",
            },
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/v1/auth/email-verification/confirm",
            headers={"x-csrf-token": _csrf(client)},
            json={"verification_token": mailbox.messages[0]["token"]},
        ).status_code
        == 200
    )


def _step_up(client: TestClient) -> None:
    enrollment = client.post(
        "/v1/mfa/enroll",
        headers={"x-csrf-token": _csrf(client)},
        json={"account_label": "llm-owner@example.test"},
    )
    assert enrollment.status_code == 200
    activation = client.post(
        "/v1/mfa/activate",
        headers={"x-csrf-token": _csrf(client)},
        json={"code": TotpEngine().code_at(enrollment.json()["secret"], datetime.now(UTC))},
    )
    assert activation.status_code == 200
    assert (
        client.post(
            "/v1/mfa/verify",
            headers={"x-csrf-token": _csrf(client)},
            json={"recovery_code": activation.json()["recovery_codes"][0]},
        ).status_code
        == 200
    )


def test_llm_connection_api_requires_step_up_and_never_returns_secret() -> None:
    client, mailbox, factory = _stack()
    _onboard(client, mailbox)
    assert client.get("/v1/llm/providers").status_code == 200
    secret = "tenant-llm-secret-never-returned"
    payload = {"label": "Minha IA", "provider": "openai", "api_key": secret}
    denied = client.post(
        "/v1/llm/connections",
        headers={
            "x-csrf-token": _csrf(client),
            "idempotency-key": "llm-api-connection-0001",
        },
        json=payload,
    )
    assert denied.status_code == 403
    _step_up(client)
    saved = client.post(
        "/v1/llm/connections",
        headers={
            "x-csrf-token": _csrf(client),
            "idempotency-key": "llm-api-connection-0001",
        },
        json=payload,
    )
    assert saved.status_code == 201
    assert secret not in saved.text
    listed = client.get("/v1/llm/connections")
    assert listed.status_code == 200
    assert listed.json()[0]["provider"] == "openai"
    assert secret not in listed.text
    with factory() as db:
        credential = db.scalar(select(LlmCredential))
        assert credential is not None and secret.encode() not in credential.ciphertext


def test_llm_connection_api_rejects_custom_endpoint_for_official_key() -> None:
    client, mailbox, _factory = _stack()
    _onboard(client, mailbox)
    _step_up(client)
    response = client.post(
        "/v1/llm/connections",
        headers={
            "x-csrf-token": _csrf(client),
            "idempotency-key": "llm-api-redirect-bad",
        },
        json={
            "label": "Falsa OpenAI",
            "provider": "openai",
            "api_key": "tenant-llm-secret",
            "base_url": "https://evil.example/v1",
        },
    )
    assert response.status_code == 400
    assert "tenant-llm-secret" not in response.text

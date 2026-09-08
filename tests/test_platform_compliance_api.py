from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tradingagents.platform.api import ApiSecuritySettings, SqlAlchemyIdentityAdapter
from tradingagents.platform.api.app import create_platform_app
from tradingagents.platform.api.compliance_adapter import SqlAlchemyComplianceAdapter
from tradingagents.platform.api.security import InMemorySlidingWindowRateLimiter
from tradingagents.platform.compliance import (
    AesGcmSecretCrypto,
    ComplianceService,
    MfaService,
    RecoveryCodeHasher,
    TotpEngine,
)
from tradingagents.platform.identity import Base, IdentityService, TokenHasher

PASSWORD = "correct horse battery staple"


class Mailbox:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, **message) -> None:
        self.messages.append(message)


def _stack() -> tuple[TestClient, ComplianceService, Mailbox]:
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
        token_hasher=TokenHasher(b"compliance-api-identity-pepper-32"),
    )
    compliance = ComplianceService(factory)
    mfa = MfaService(
        factory,
        crypto=AesGcmSecretCrypto(key=b"m" * 32, key_id="test-key-v1"),
        recovery_hasher=RecoveryCodeHasher(b"compliance-api-recovery-pepper-32"),
    )
    recovery_mailbox = Mailbox()
    verification_mailbox = Mailbox()
    identity_adapter = SqlAlchemyIdentityAdapter(
        service=identity,
        recovery_delivery=recovery_mailbox,
        verification_delivery=verification_mailbox,
    )
    app = create_platform_app(
        identity_service=identity_adapter,
        compliance_service=SqlAlchemyComplianceAdapter(
            identity=identity,
            compliance=compliance,
            mfa=mfa,
        ),
        security=ApiSecuritySettings(csrf_secret=b"compliance-api-csrf-secret-32bytes"),
        rate_limiter=InMemorySlidingWindowRateLimiter(),
    )
    return TestClient(app, base_url="https://testserver"), compliance, verification_mailbox


def _csrf(client: TestClient) -> str:
    response = client.get("/v1/auth/csrf")
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _register_and_verify(client: TestClient, mailbox: Mailbox) -> dict:
    registered = client.post(
        "/v1/auth/register",
        headers={"x-csrf-token": _csrf(client)},
        json={
            "email": "owner@example.test",
            "password": PASSWORD,
            "display_name": "Owner",
            "organization_name": "Empresa",
            "organization_slug": "empresa",
        },
    )
    assert registered.status_code == 201
    confirmation = client.post(
        "/v1/auth/email-verification/confirm",
        headers={"x-csrf-token": _csrf(client)},
        json={"verification_token": mailbox.messages[0]["token"]},
    )
    assert confirmation.status_code == 200
    return client.get("/v1/auth/me").json()


def test_verified_user_completes_terms_privacy_and_mfa_journey() -> None:
    client, compliance, mailbox = _stack()
    principal = _register_and_verify(client, mailbox)
    assert principal["email_verified"] is True

    terms_id = compliance.publish_terms(
        document_type="terms-of-service",
        version="2026-09",
        content_sha256="a" * 64,
        publication_uri="https://trading.example.test/legal/terms/2026-09",
        published_at=datetime.now(UTC),
    )
    terms = client.get("/v1/legal/terms/current")
    assert terms.status_code == 200
    assert terms.json()["id"] == str(terms_id)

    consent = client.post(
        "/v1/legal/consents",
        headers={"x-csrf-token": _csrf(client)},
        json={"terms_document_id": str(terms_id), "decision": "accepted"},
    )
    assert consent.status_code == 201
    assert client.get("/v1/legal/consents").json()[0]["decision"] == "accepted"

    privacy = client.post(
        "/v1/privacy/requests",
        headers={"x-csrf-token": _csrf(client)},
        json={"kind": "export"},
    )
    assert privacy.status_code == 201
    assert privacy.json()["status"] == "requested"
    assert client.get(f"/v1/privacy/requests/{privacy.json()['id']}").status_code == 200

    enrollment = client.post(
        "/v1/mfa/enroll",
        headers={"x-csrf-token": _csrf(client)},
        json={"account_label": "owner@example.test"},
    )
    assert enrollment.status_code == 200
    secret = enrollment.json()["secret"]
    activation = client.post(
        "/v1/mfa/activate",
        headers={"x-csrf-token": _csrf(client)},
        json={"code": TotpEngine().code_at(secret, datetime.now(UTC))},
    )
    assert activation.status_code == 200
    recovery_code = activation.json()["recovery_codes"][0]
    verification = client.post(
        "/v1/mfa/verify",
        headers={"x-csrf-token": _csrf(client)},
        json={"recovery_code": recovery_code},
    )
    assert verification.status_code == 200
    assert verification.json()["method"] == "recovery_code"


def test_unverified_user_cannot_accept_terms_or_enroll_mfa() -> None:
    client, compliance, _mailbox = _stack()
    response = client.post(
        "/v1/auth/register",
        headers={"x-csrf-token": _csrf(client)},
        json={
            "email": "owner@example.test",
            "password": PASSWORD,
            "display_name": "Owner",
            "organization_name": "Empresa",
            "organization_slug": "empresa",
        },
    )
    assert response.status_code == 201
    terms_id = compliance.publish_terms(
        document_type="terms-of-service",
        version="2026-09",
        content_sha256="a" * 64,
        publication_uri="https://trading.example.test/legal/terms/2026-09",
        published_at=datetime.now(UTC),
    )
    blocked = client.post(
        "/v1/legal/consents",
        headers={"x-csrf-token": _csrf(client)},
        json={"terms_document_id": str(terms_id), "decision": "accepted"},
    )
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "EMAIL_VERIFICATION_REQUIRED"

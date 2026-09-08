from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tradingagents.platform.api import ApiSecuritySettings, SqlAlchemyIdentityAdapter
from tradingagents.platform.api.app import create_platform_app
from tradingagents.platform.api.compliance_adapter import SqlAlchemyComplianceAdapter
from tradingagents.platform.api.connections_adapter import (
    SqlAlchemyBrokerConnectionAdapter,
)
from tradingagents.platform.api.security import InMemorySlidingWindowRateLimiter
from tradingagents.platform.compliance import (
    AesGcmSecretCrypto,
    ComplianceService,
    MfaService,
    RecoveryCodeHasher,
    TotpEngine,
)
from tradingagents.platform.connections import (
    BrokerCredential,
    ConnectionService,
    CredentialKeyring,
    DiagnosticResult,
)
from tradingagents.platform.identity import Base, IdentityService, TokenHasher

PASSWORD = "correct horse battery staple"


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
        factory, token_hasher=TokenHasher(b"connections-api-identity-pepper-32")
    )
    compliance = ComplianceService(factory)
    mfa = MfaService(
        factory,
        crypto=AesGcmSecretCrypto(key=b"m" * 32, key_id="mfa-test"),
        recovery_hasher=RecoveryCodeHasher(b"connections-api-recovery-pepper-32"),
    )
    connections = ConnectionService(
        factory,
        keyring=CredentialKeyring(keys={"broker-test": b"k" * 32}, active_key_id="broker-test"),
        diagnostics=SimpleNamespace(
            run=lambda **_kwargs: DiagnosticResult(
                True,
                "DIAGNOSTIC_OK",
                market_count=3,
                permission_status="not_supported",
                adapter_version="test-ccxt",
            )
        ),
    )
    recovery_mailbox = Mailbox()
    verification_mailbox = Mailbox()
    app = create_platform_app(
        identity_service=SqlAlchemyIdentityAdapter(
            service=identity,
            recovery_delivery=recovery_mailbox,
            verification_delivery=verification_mailbox,
        ),
        compliance_service=SqlAlchemyComplianceAdapter(
            identity=identity, compliance=compliance, mfa=mfa
        ),
        broker_connection_service=SqlAlchemyBrokerConnectionAdapter(
            identity=identity,
            mfa=mfa,
            connections=connections,
        ),
        security=ApiSecuritySettings(csrf_secret=b"connections-api-csrf-secret-value"),
        rate_limiter=InMemorySlidingWindowRateLimiter(),
    )
    return (
        TestClient(app, base_url="https://testserver"),
        verification_mailbox,
        factory,
    )


def _csrf(client: TestClient) -> str:
    response = client.get("/v1/auth/csrf")
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _register_verify_and_step_up(client: TestClient, mailbox: Mailbox) -> None:
    response = client.post(
        "/v1/auth/register",
        headers={"x-csrf-token": _csrf(client)},
        json={
            "email": "broker-owner@example.test",
            "password": PASSWORD,
            "display_name": "Broker Owner",
            "organization_name": "Broker Org",
            "organization_slug": "broker-org",
        },
    )
    assert response.status_code == 201
    response = client.post(
        "/v1/auth/email-verification/confirm",
        headers={"x-csrf-token": _csrf(client)},
        json={"verification_token": mailbox.messages[0]["token"]},
    )
    assert response.status_code == 200
    enrollment = client.post(
        "/v1/mfa/enroll",
        headers={"x-csrf-token": _csrf(client)},
        json={"account_label": "broker-owner@example.test"},
    )
    assert enrollment.status_code == 200
    secret = enrollment.json()["secret"]
    activation = client.post(
        "/v1/mfa/activate",
        headers={"x-csrf-token": _csrf(client)},
        json={"code": TotpEngine().code_at(secret, datetime.now(UTC))},
    )
    assert activation.status_code == 200
    step_up = client.post(
        "/v1/mfa/verify",
        headers={"x-csrf-token": _csrf(client)},
        json={"recovery_code": activation.json()["recovery_codes"][0]},
    )
    assert step_up.status_code == 200


def test_connection_api_requires_mfa_never_echoes_secrets_and_supports_lifecycle():
    client, mailbox, factory = _stack()
    _register_verify_and_step_up(client, mailbox)
    secret = "provider-secret-never-return"
    payload = {
        "label": "Minha Binance",
        "exchange_slug": "binance",
        "environment": "testnet",
        "api_key": "provider-api-key",
        "secret": secret,
    }
    response = client.post(
        "/v1/connections",
        headers={
            "x-csrf-token": _csrf(client),
            "idempotency-key": "create-binance-connection-01",
        },
        json=payload,
    )
    assert response.status_code == 201
    assert secret not in response.text
    listed = client.get("/v1/connections")
    assert listed.status_code == 200
    assert secret not in listed.text
    connection = listed.json()[0]
    assert connection["label"] == "Minha Binance"
    assert connection["status"] == "pending_diagnostic"

    diagnostic = client.post(
        f"/v1/connections/{connection['id']}/diagnose",
        headers={"x-csrf-token": _csrf(client)},
    )
    assert diagnostic.status_code == 200
    assert diagnostic.json()["code"] == "DIAGNOSTIC_OK"
    assert secret not in diagnostic.text

    with factory() as db:
        credential = db.scalar(select(BrokerCredential))
        assert credential is not None
        assert secret.encode() not in credential.ciphertext
        assert secret.encode() not in credential.wrapped_key

    removed = client.delete(
        f"/v1/connections/{connection['id']}",
        headers={"x-csrf-token": _csrf(client)},
    )
    assert removed.status_code == 200
    assert client.get("/v1/connections").json()[0]["status"] == "disabled"


def test_connection_api_fails_without_idempotency_key_and_never_downgrades_testnet():
    client, mailbox, _factory = _stack()
    _register_verify_and_step_up(client, mailbox)
    payload = {
        "label": "Minha Kraken",
        "exchange_slug": "kraken",
        "environment": "testnet",
        "api_key": "provider-api-key",
        "secret": "provider-secret",
    }
    assert (
        client.post(
            "/v1/connections",
            headers={"x-csrf-token": _csrf(client)},
            json=payload,
        ).status_code
        == 422
    )
    response = client.post(
        "/v1/connections",
        headers={
            "x-csrf-token": _csrf(client),
            "idempotency-key": "create-kraken-testnet-01",
        },
        json=payload,
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "TESTNET_UNAVAILABLE"

from __future__ import annotations

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from tradingagents.platform.connections import (
    BrokerConnection,
    BrokerCredentialInput,
    BrokerEnvironment,
    ConnectionService,
    CredentialKeyring,
    DiagnosticResult,
    capability_digest,
    exchange_capability,
)
from tradingagents.platform.identity import (
    IdentityPrincipal,
    IdentityService,
    MembershipRole,
    TokenHasher,
)

POSTGRES_URL = os.environ.get("TRADINGAGENTS_TEST_POSTGRES_URL")
pytestmark = pytest.mark.integration


@pytest.mark.skipif(not POSTGRES_URL, reason="PostgreSQL RLS test URL not configured")
def test_broker_connection_rls_rejects_cross_tenant_access() -> None:
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    identity = IdentityService(
        factory,
        token_hasher=TokenHasher(b"connection-postgres-token-pepper"),
    )
    service = ConnectionService(
        factory,
        keyring=CredentialKeyring(keys={"test": b"k" * 32}, active_key_id="test"),
        diagnostics=SimpleNamespace(
            run=lambda **_kwargs: DiagnosticResult(True, "DIAGNOSTIC_OK")
        ),
    )
    principals: list[IdentityPrincipal] = []
    suffix = uuid4().hex[:10]
    for label in ("a", "b"):
        registration = identity.register_tenant(
            tenant_name=f"Broker {label.upper()}",
            tenant_slug=f"broker-{label}-{suffix}",
            owner_email=f"broker-{label}-{suffix}@example.test",
            display_name=f"Owner {label.upper()}",
            password="correct horse battery staple",
        )
        principals.append(
            IdentityPrincipal(
                tenant_id=registration.tenant_id,
                user_id=registration.user_id,
                membership_id=registration.membership_id,
                session_id=uuid4(),
                role=MembershipRole.OWNER,
            )
        )

    first, second = principals
    first_id = service.upsert_connection(
        principal=first,
        idempotency_key=f"broker-first-{suffix}",
        label="Principal",
        exchange_slug="binance",
        environment=BrokerEnvironment.TESTNET,
        credentials=BrokerCredentialInput(api_key="test-key-a", secret="test-secret-a"),
    )
    second_id = service.upsert_connection(
        principal=second,
        idempotency_key=f"broker-second-{suffix}",
        label="Principal",
        exchange_slug="binance",
        environment=BrokerEnvironment.TESTNET,
        credentials=BrokerCredentialInput(api_key="test-key-b", secret="test-secret-b"),
    )

    assert {row.id for row in service.list_connections(principal=first)} == {first_id}
    assert {row.id for row in service.list_connections(principal=second)} == {second_id}

    with factory() as db, db.begin():
        assert tuple(db.scalars(select(BrokerConnection))) == ()

    with factory() as db, db.begin():
        db.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(first.tenant_id)},
        )
        assert db.get(BrokerConnection, second_id) is None

    with pytest.raises(DBAPIError), factory() as db, db.begin():
        db.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(first.tenant_id)},
        )
        db.add(
            BrokerConnection(
                tenant_id=second.tenant_id,
                created_by_user_id=second.user_id,
                label="Cross tenant",
                label_normalized="cross tenant",
                exchange_slug="binance",
                environment="testnet",
                status="pending_diagnostic",
                capability_digest=capability_digest(exchange_capability("binance")),
                permission_status="unverifiable",
                active_credential_version=1,
            )
        )
        db.flush()

    engine.dispose()

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tradingagents.platform.connections import CredentialKeyring
from tradingagents.platform.identity import (
    Base,
    IdentityPrincipal,
    IdentityService,
    MembershipRole,
    TokenHasher,
)
from tradingagents.platform.llm_connections import (
    LlmConnectionError,
    LlmConnectionService,
    LlmCredential,
    LlmCredentialAccessEvent,
)


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
        factory,
        token_hasher=TokenHasher(b"llm-connections-test-token-pepper-32"),
    )
    registration = identity.register_tenant(
        tenant_name="LLM Tenant",
        tenant_slug=f"llm-{uuid4().hex[:10]}",
        owner_email=f"llm-{uuid4().hex[:10]}@example.test",
        display_name="LLM Owner",
        password="correct horse battery staple",
    )
    principal = IdentityPrincipal(
        tenant_id=registration.tenant_id,
        user_id=registration.user_id,
        membership_id=registration.membership_id,
        session_id=uuid4(),
        role=MembershipRole.OWNER,
    )
    service = LlmConnectionService(
        factory,
        keyring=CredentialKeyring(keys={"llm-test": b"k" * 32}, active_key_id="llm-test"),
        idempotency_digest_key=b"i" * 32,
    )
    return factory, principal, service


def test_llm_credential_is_encrypted_versioned_resolved_and_audited() -> None:
    factory, principal, service = _stack()
    secret = "provider-secret-never-plaintext"
    connection_id = service.upsert(
        principal=principal,
        idempotency_key="llm-connection-create-0001",
        label="Minha OpenAI",
        provider="openai",
        api_key=secret,
    )
    assert (
        service.upsert(
            principal=principal,
            idempotency_key="llm-connection-create-0001",
            label="Minha OpenAI",
            provider="openai",
            api_key=secret,
        )
        == connection_id
    )
    [view] = service.list_connections(principal=principal)
    assert view.provider == "openai" and view.credential_version == 1
    resolved = service.resolve_for_worker(
        tenant_id=principal.tenant_id,
        connection_id=connection_id,
        credential_version=1,
        expected_provider="openai",
        actor_user_id=principal.user_id,
    )
    assert resolved.api_key == secret
    assert "provider-secret" not in repr(resolved)
    with factory() as db:
        credential = db.scalar(select(LlmCredential))
        assert credential is not None
        assert secret.encode() not in credential.ciphertext
        assert secret.encode() not in credential.wrapped_key
        assert db.scalar(select(LlmCredentialAccessEvent)) is not None


def test_llm_connection_rejects_credential_redirects_and_cross_tenant_resolution() -> None:
    _factory, principal, service = _stack()
    with pytest.raises(LlmConnectionError, match="LLM_ENDPOINT_FORBIDDEN"):
        service.upsert(
            principal=principal,
            idempotency_key="llm-endpoint-redirect-001",
            label="OpenAI falsa",
            provider="openai",
            api_key="valid-enough-secret",
            base_url="https://evil.example/v1",
        )
    with pytest.raises(LlmConnectionError, match="LLM_PROVIDER_UNSUPPORTED"):
        service.upsert(
            principal=principal,
            idempotency_key="llm-endpoint-http-0001",
            label="Relay inseguro",
            provider="openai_compatible",
            api_key="valid-enough-secret",
            base_url="http://relay.example/v1",
        )
    connection_id = service.upsert(
        principal=principal,
        idempotency_key="llm-endpoint-azure-0001",
        label="Azure",
        provider="azure",
        api_key="valid-enough-secret",
        base_url="https://tenant.openai.azure.com",
    )
    with pytest.raises(LlmConnectionError, match="LLM_CREDENTIAL_NOT_FOUND"):
        service.resolve_for_worker(
            tenant_id=uuid4(),
            connection_id=connection_id,
            credential_version=1,
            expected_provider="azure",
            actor_user_id=principal.user_id,
        )


def test_llm_connection_identity_is_immutable_and_secret_rotation_is_versioned() -> None:
    _factory, principal, service = _stack()
    connection_id = service.upsert(
        principal=principal,
        idempotency_key="llm-rotate-version-one",
        label="Meu provedor",
        provider="deepseek",
        api_key="first-provider-secret",
    )
    assert (
        service.upsert(
            principal=principal,
            idempotency_key="llm-rotate-version-two",
            label="Meu provedor",
            provider="deepseek",
            api_key="second-provider-secret",
        )
        == connection_id
    )
    [view] = service.list_connections(principal=principal)
    assert view.credential_version == 2
    with pytest.raises(LlmConnectionError, match="LLM_CREDENTIAL_NOT_FOUND"):
        service.resolve_for_worker(
            tenant_id=principal.tenant_id,
            connection_id=connection_id,
            credential_version=1,
            expected_provider="deepseek",
            actor_user_id=principal.user_id,
        )
    with pytest.raises(LlmConnectionError, match="LLM_CREDENTIAL_NOT_FOUND"):
        service.assert_reference(
            tenant_id=principal.tenant_id,
            connection_id=connection_id,
            credential_version=1,
            provider="deepseek",
        )
    second = service.resolve_for_worker(
        tenant_id=principal.tenant_id,
        connection_id=connection_id,
        credential_version=2,
        expected_provider="deepseek",
        actor_user_id=principal.user_id,
    )
    assert second.api_key == "second-provider-secret"
    with pytest.raises(LlmConnectionError, match="IDENTITY_IMMUTABLE"):
        service.upsert(
            principal=principal,
            idempotency_key="llm-change-provider-bad",
            label="Meu provedor",
            provider="openai",
            api_key="third-provider-secret",
        )

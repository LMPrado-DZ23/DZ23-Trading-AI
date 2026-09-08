from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tradingagents.platform.api import (
    ApiSecuritySettings,
    SqlAlchemyIdentityAdapter,
    create_platform_app,
)
from tradingagents.platform.api.security import InMemorySlidingWindowRateLimiter
from tradingagents.platform.identity import Base, IdentityService, TokenHasher
from tradingagents.platform.supabase_auth import SupabaseAuthClaims

PASSWORD = "correct horse battery staple"


class TokenMailbox:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, **message) -> None:
        self.messages.append(message)


def _stack(verifier=None) -> tuple[TestClient, TokenMailbox, TokenMailbox]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    service = IdentityService(
        sessionmaker(engine, expire_on_commit=False),
        token_hasher=TokenHasher(b"identity-adapter-test-pepper-32b"),
    )
    recovery_mailbox = TokenMailbox()
    verification_mailbox = TokenMailbox()
    adapter = SqlAlchemyIdentityAdapter(
        service=service,
        recovery_delivery=recovery_mailbox,
        verification_delivery=verification_mailbox,
        supabase_verifier=verifier,
        session_ttl=timedelta(hours=1),
    )
    app = create_platform_app(
        identity_service=adapter,
        security=ApiSecuritySettings(csrf_secret=b"identity-adapter-csrf-secret-32b"),
        rate_limiter=InMemorySlidingWindowRateLimiter(),
    )
    return (
        TestClient(app, base_url="https://testserver"),
        recovery_mailbox,
        verification_mailbox,
    )


def _csrf(client: TestClient) -> str:
    response = client.get("/v1/auth/csrf")
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _register(client: TestClient, *, slug: str, email: str) -> dict:
    response = client.post(
        "/v1/auth/register",
        headers={"x-csrf-token": _csrf(client)},
        json={
            "email": email,
            "password": PASSWORD,
            "display_name": "Pessoa Teste",
            "organization_name": f"Organização {slug}",
            "organization_slug": slug,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_real_adapter_registration_login_session_and_logout() -> None:
    client, _recovery_mailbox, verification_mailbox = _stack()
    principal = _register(client, slug="empresa-a", email="owner-a@example.com")
    assert principal["roles"] == ["owner"]
    assert principal["email_verified"] is False
    assert len(verification_mailbox.messages) == 1
    assert client.get("/v1/auth/me").status_code == 200

    logout = client.post("/v1/auth/logout", headers={"x-csrf-token": _csrf(client)})
    assert logout.status_code == 200
    assert client.get("/v1/auth/me").status_code == 401

    login = client.post(
        "/v1/auth/login",
        headers={"x-csrf-token": _csrf(client)},
        json={
            "email": "OWNER-A@example.com",
            "password": PASSWORD,
            "organization_slug": "empresa-a",
        },
    )
    assert login.status_code == 200
    assert login.json()["tenant_id"] == principal["tenant_id"]


def test_wrong_organization_and_cross_tenant_header_fail_closed() -> None:
    client, _recovery_mailbox, _verification_mailbox = _stack()
    first = _register(client, slug="empresa-a", email="owner-a@example.com")
    client.cookies.clear()
    second = _register(client, slug="empresa-b", email="owner-b@example.com")
    assert first["tenant_id"] != second["tenant_id"]

    wrong_org = client.post(
        "/v1/auth/login",
        headers={"x-csrf-token": _csrf(client)},
        json={
            "email": "owner-a@example.com",
            "password": PASSWORD,
            "organization_slug": "empresa-b",
        },
    )
    assert wrong_org.status_code == 401

    forbidden = client.get("/v1/auth/me", headers={"x-tenant-id": first["tenant_id"]})
    assert forbidden.status_code == 403


def test_same_email_can_create_separate_tenants_without_cross_tenant_access() -> None:
    client, _recovery_mailbox, _verification_mailbox = _stack()
    first = _register(client, slug="empresa-a", email="shared@example.com")
    client.cookies.clear()
    second = _register(client, slug="empresa-b", email="shared@example.com")

    assert first["user_id"] != second["user_id"]
    assert first["tenant_id"] != second["tenant_id"]
    forbidden = client.get("/v1/auth/me", headers={"x-tenant-id": first["tenant_id"]})
    assert forbidden.status_code == 403


def test_recovery_is_one_time_and_revokes_existing_session() -> None:
    client, mailbox, _verification_mailbox = _stack()
    _register(client, slug="empresa-a", email="owner-a@example.com")
    response = client.post(
        "/v1/auth/recovery/request",
        headers={"x-csrf-token": _csrf(client)},
        json={"email": "owner-a@example.com", "organization_slug": "empresa-a"},
    )
    assert response.status_code == 200
    assert len(mailbox.messages) == 1
    token = mailbox.messages[0]["token"]
    replacement = "replacement horse battery staple"
    reset = client.post(
        "/v1/auth/recovery/reset",
        headers={"x-csrf-token": _csrf(client)},
        json={"recovery_token": token, "new_password": replacement},
    )
    assert reset.status_code == 200
    assert client.get("/v1/auth/me").status_code == 401

    replay = client.post(
        "/v1/auth/recovery/reset",
        headers={"x-csrf-token": _csrf(client)},
        json={"recovery_token": token, "new_password": PASSWORD},
    )
    assert replay.status_code == 400

    login = client.post(
        "/v1/auth/login",
        headers={"x-csrf-token": _csrf(client)},
        json={
            "email": "owner-a@example.com",
            "password": replacement,
            "organization_slug": "empresa-a",
        },
    )
    assert login.status_code == 200


def test_unknown_recovery_target_does_not_enqueue_message() -> None:
    client, mailbox, _verification_mailbox = _stack()
    response = client.post(
        "/v1/auth/recovery/request",
        headers={"x-csrf-token": _csrf(client)},
        json={"email": "missing@example.com", "organization_slug": "unknown-org"},
    )
    assert response.status_code == 200
    assert mailbox.messages == []


def test_email_verification_is_one_time_and_refreshes_session_profile() -> None:
    client, _recovery_mailbox, verification_mailbox = _stack()
    principal = _register(client, slug="empresa-a", email="owner-a@example.com")
    assert principal["email_verified"] is False
    token = verification_mailbox.messages[0]["token"]

    confirmed = client.post(
        "/v1/auth/email-verification/confirm",
        headers={"x-csrf-token": _csrf(client)},
        json={"verification_token": token},
    )
    assert confirmed.status_code == 200
    assert client.get("/v1/auth/me").json()["email_verified"] is True

    replay = client.post(
        "/v1/auth/email-verification/confirm",
        headers={"x-csrf-token": _csrf(client)},
        json={"verification_token": token},
    )
    assert replay.status_code == 400


def test_authenticated_user_can_request_a_fresh_verification_link() -> None:
    client, _recovery_mailbox, verification_mailbox = _stack()
    _register(client, slug="empresa-a", email="owner-a@example.com")
    response = client.post(
        "/v1/auth/email-verification/request",
        headers={"x-csrf-token": _csrf(client)},
    )
    assert response.status_code == 200
    assert len(verification_mailbox.messages) == 2


class StubSupabaseVerifier:
    async def verify(self, token: str) -> SupabaseAuthClaims:
        assert token == "verified-supabase-token-" + "x" * 40
        return SupabaseAuthClaims(
            subject=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            email="social@example.com",
            email_verified=True,
            provider="google",
            role="authenticated",
            is_anonymous=False,
            issuer="https://project.supabase.co/auth/v1",
            audience=("authenticated",),
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
            issued_at=datetime.now(UTC),
        )


def test_supabase_exchange_provisions_once_then_authenticates_existing_tenant() -> None:
    client, _recovery_mailbox, verification_mailbox = _stack(StubSupabaseVerifier())
    token = "verified-supabase-token-" + "x" * 40
    first = client.post(
        "/v1/auth/supabase/exchange",
        headers={
            "x-csrf-token": _csrf(client),
            "authorization": f"Bearer {token}",
        },
        json={
            "organization_slug": "social-org",
            "organization_name": "Social Organization",
            "display_name": "Social User",
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["email_verified"] is True
    assert verification_mailbox.messages == []

    client.cookies.clear()
    second = client.post(
        "/v1/auth/supabase/exchange",
        headers={
            "x-csrf-token": _csrf(client),
            "authorization": f"Bearer {token}",
        },
        json={"organization_slug": "social-org"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["user_id"] == first.json()["user_id"]


def test_local_unverified_email_cannot_block_distinct_verified_social_identity() -> None:
    client, _recovery_mailbox, _verification_mailbox = _stack(StubSupabaseVerifier())
    local = _register(client, slug="local-org", email="social@example.com")
    client.cookies.clear()
    token = "verified-supabase-token-" + "x" * 40

    social = client.post(
        "/v1/auth/supabase/exchange",
        headers={
            "x-csrf-token": _csrf(client),
            "authorization": f"Bearer {token}",
        },
        json={
            "organization_slug": "social-org",
            "organization_name": "Social Organization",
            "display_name": "Social User",
        },
    )

    assert social.status_code == 200, social.text
    assert social.json()["user_id"] != local["user_id"]


def test_social_session_never_outlives_supabase_access_token() -> None:
    class ShortVerifier(StubSupabaseVerifier):
        async def verify(self, token: str) -> SupabaseAuthClaims:
            original = await super().verify(token)
            return SupabaseAuthClaims(
                subject=original.subject,
                email=original.email,
                email_verified=True,
                provider=original.provider,
                role=original.role,
                is_anonymous=False,
                issuer=original.issuer,
                audience=original.audience,
                expires_at=datetime.now(UTC) + timedelta(seconds=90),
                issued_at=original.issued_at,
            )

    client, _recovery_mailbox, _verification_mailbox = _stack(ShortVerifier())
    token = "verified-supabase-token-" + "x" * 40
    response = client.post(
        "/v1/auth/supabase/exchange",
        headers={
            "x-csrf-token": _csrf(client),
            "authorization": f"Bearer {token}",
        },
        json={
            "organization_slug": "short-session",
            "organization_name": "Short Session",
            "display_name": "Social User",
        },
    )
    assert response.status_code == 200, response.text
    cookie = response.headers["set-cookie"]
    max_age = int(cookie.split("Max-Age=", 1)[1].split(";", 1)[0])
    assert 1 <= max_age <= 90

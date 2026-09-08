"""Adversarial contract tests for the tenant-aware identity API boundary."""

from __future__ import annotations

import ipaddress
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import Request
from fastapi.testclient import TestClient

from tradingagents.platform.api import (
    ApiSecuritySettings,
    AuthenticatedSession,
    IdentityError,
    IdentityPrincipal,
    create_platform_app,
)
from tradingagents.platform.api.security import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    InMemorySlidingWindowRateLimiter,
    RateLimitUnavailable,
    client_ip,
)

TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_TENANT_ID = UUID("22222222-2222-4222-8222-222222222222")
USER_ID = UUID("33333333-3333-4333-8333-333333333333")


class FakeIdentityService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.is_ready = True
        self.session = AuthenticatedSession(
            token="opaque-session-token-with-at-least-32-characters",
            principal=IdentityPrincipal(
                user_id=USER_ID,
                tenant_id=TENANT_ID,
                email="pessoa@example.com",
                display_name="Pessoa",
                roles=("owner",),
                email_verified=False,
                session_id=uuid4(),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            ),
        )

    async def register(self, **kwargs) -> AuthenticatedSession:
        self.calls.append(("register", kwargs))
        if kwargs["email"] == "existe@example.com":
            raise IdentityError("EMAIL_EXISTS")
        return self.session

    async def authenticate(self, **kwargs) -> AuthenticatedSession:
        self.calls.append(("authenticate", kwargs))
        if kwargs["password"] != "correct horse battery staple":
            raise IdentityError("INVALID_CREDENTIALS")
        return self.session

    async def exchange_supabase_token(self, **kwargs) -> AuthenticatedSession:
        self.calls.append(("exchange_supabase_token", kwargs))
        if kwargs["access_token"].startswith("invalid"):
            raise IdentityError("SOCIAL_AUTH_INVALID")
        return self.session

    async def resolve_session(self, **kwargs) -> IdentityPrincipal:
        self.calls.append(("resolve_session", kwargs))
        if kwargs["token"] != self.session.token:
            raise IdentityError("SESSION_INVALID")
        return self.session.principal

    async def revoke_session(self, **kwargs) -> None:
        self.calls.append(("revoke_session", kwargs))

    async def request_password_reset(self, **kwargs) -> None:
        self.calls.append(("request_password_reset", kwargs))

    async def reset_password(self, **kwargs) -> None:
        self.calls.append(("reset_password", kwargs))
        if kwargs["recovery_token"].startswith("invalid"):
            raise IdentityError("RECOVERY_INVALID")

    async def request_email_verification(self, **kwargs) -> None:
        self.calls.append(("request_email_verification", kwargs))

    async def verify_email(self, **kwargs) -> None:
        self.calls.append(("verify_email", kwargs))
        if kwargs["verification_token"].startswith("invalid"):
            raise IdentityError("VERIFICATION_INVALID")

    async def ready(self) -> bool:
        return self.is_ready


def _client(
    *,
    trusted_proxies: tuple[str, ...] = (),
    allowed_origins: tuple[str, ...] = (),
) -> tuple[TestClient, FakeIdentityService]:
    identity = FakeIdentityService()
    app = create_platform_app(
        identity_service=identity,
        security=ApiSecuritySettings(
            csrf_secret=b"a5-test-secret-with-at-least-32-bytes",
            trusted_proxy_cidrs=trusted_proxies,
            allowed_origins=allowed_origins,
        ),
        rate_limiter=InMemorySlidingWindowRateLimiter(),
    )
    return TestClient(app, base_url="https://testserver"), identity


def _csrf(client: TestClient) -> str:
    response = client.get("/v1/auth/csrf")
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _login(client: TestClient) -> str:
    csrf = _csrf(client)
    response = client.post(
        "/v1/auth/login",
        headers={"x-csrf-token": csrf},
        json={
            "email": "Pessoa@Example.com",
            "password": "correct horse battery staple",
            "organization_slug": "acme",
        },
    )
    assert response.status_code == 200
    return response.headers["x-csrf-token"]


def test_health_liveness_and_fail_closed_readiness() -> None:
    client, identity = _client()
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {"status": "ready"}
    identity.is_ready = False
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "SERVICE_NOT_READY"


def test_security_headers_are_present_on_public_responses() -> None:
    client, _identity = _client()
    response = client.get("/health/live")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


def test_csrf_is_required_even_for_login_and_signature_is_verified() -> None:
    client, identity = _client()
    payload = {
        "email": "pessoa@example.com",
        "password": "correct horse battery staple",
        "organization_slug": "acme",
    }
    assert client.post("/v1/auth/login", json=payload).status_code == 403
    client.cookies.set(CSRF_COOKIE, "forged.forged")
    response = client.post(
        "/v1/auth/login", headers={"x-csrf-token": "forged.forged"}, json=payload
    )
    assert response.status_code == 403
    assert not [call for call in identity.calls if call[0] == "authenticate"]


def test_login_sets_hardened_opaque_session_and_rotates_csrf() -> None:
    client, _identity = _client()
    csrf = _csrf(client)
    response = client.post(
        "/v1/auth/login",
        headers={"x-csrf-token": csrf},
        json={
            "email": "PESSOA@example.com",
            "password": "correct horse battery staple",
            "organization_slug": "acme",
        },
    )
    assert response.status_code == 200
    set_cookie = response.headers.get_list("set-cookie")
    session_cookie = next(value for value in set_cookie if SESSION_COOKIE in value)
    csrf_cookie = next(value for value in set_cookie if CSRF_COOKIE in value)
    assert "HttpOnly" in session_cookie
    assert "Secure" in session_cookie
    assert "SameSite=strict" in session_cookie
    assert "HttpOnly" not in csrf_cookie
    assert "Secure" in csrf_cookie
    assert response.headers["x-csrf-token"] != csrf
    assert response.json()["tenant_id"] == str(TENANT_ID)


def test_supabase_exchange_requires_strict_bearer_and_never_echoes_token() -> None:
    client, identity = _client()
    csrf = _csrf(client)
    payload = {"organization_slug": "acme"}
    assert (
        client.post(
            "/v1/auth/supabase/exchange",
            headers={"x-csrf-token": csrf},
            json=payload,
        ).status_code
        == 401
    )
    token = "verified.supabase.access.token." + "x" * 64
    response = client.post(
        "/v1/auth/supabase/exchange",
        headers={"x-csrf-token": csrf, "authorization": f"Bearer {token}"},
        json=payload,
    )
    assert response.status_code == 200
    assert token not in response.text
    call = next(item for item in identity.calls if item[0] == "exchange_supabase_token")
    assert call[1]["access_token"] == token


def test_social_login_cors_preflight_allows_authorization_from_listed_origin() -> None:
    client, _identity = _client(allowed_origins=("https://app.example.test",))
    response = client.options(
        "/v1/auth/supabase/exchange",
        headers={
            "origin": "https://app.example.test",
            "access-control-request-method": "POST",
            "access-control-request-headers": "authorization,content-type,x-csrf-token",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://app.example.test"
    allowed = response.headers["access-control-allow-headers"].casefold()
    assert "authorization" in allowed


def test_request_metadata_is_server_controlled_and_bounded() -> None:
    client, identity = _client()
    csrf = _csrf(client)
    response = client.post(
        "/v1/auth/login",
        headers={
            "x-csrf-token": csrf,
            "x-request-id": "attacker\r\ninjected",
            "user-agent": "a" * 800,
        },
        json={
            "email": "pessoa@example.com",
            "password": "correct horse battery staple",
            "organization_slug": "acme",
        },
    )
    assert response.status_code == 200
    call = next(call for call in identity.calls if call[0] == "authenticate")
    context = call[1]["context"]
    assert context.request_id != "attacker\r\ninjected"
    assert len(context.request_id) == 32
    assert context.user_agent == "a" * 512


def test_session_is_server_authoritative_and_rejects_tenant_switch() -> None:
    client, _identity = _client()
    _login(client)
    assert client.get("/v1/auth/me").status_code == 200
    response = client.get("/v1/auth/me", headers={"x-tenant-id": str(OTHER_TENANT_ID)})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "TENANT_FORBIDDEN"


def test_logout_requires_csrf_and_revokes_server_session() -> None:
    client, identity = _client()
    csrf = _login(client)
    assert client.post("/v1/auth/logout").status_code == 403
    response = client.post("/v1/auth/logout", headers={"x-csrf-token": csrf})
    assert response.status_code == 200
    assert any(call[0] == "revoke_session" for call in identity.calls)
    session_cookie = next(
        value for value in response.headers.get_list("set-cookie") if SESSION_COOKIE in value
    )
    assert "Max-Age=0" in session_cookie


def test_recovery_does_not_disclose_account_existence() -> None:
    client, identity = _client()
    csrf = _csrf(client)
    response = client.post(
        "/v1/auth/recovery/request",
        headers={"x-csrf-token": csrf},
        json={"email": "nao-existe@example.com", "organization_slug": "acme"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "message": "Se a conta existir, enviaremos as instruções de recuperação."
    }
    assert any(call[0] == "request_password_reset" for call in identity.calls)


def test_untrusted_direct_client_cannot_spoof_ip_to_bypass_rate_limit() -> None:
    client, identity = _client()
    csrf = _csrf(client)
    payload = {
        "email": "pessoa@example.com",
        "password": "wrong-password",
        "organization_slug": "acme",
    }
    statuses = []
    for number in range(6):
        response = client.post(
            "/v1/auth/login",
            headers={"x-csrf-token": csrf, "x-forwarded-for": f"198.51.100.{number}"},
            json=payload,
        )
        statuses.append(response.status_code)
    assert statuses == [401, 401, 401, 401, 401, 429]
    assert len([call for call in identity.calls if call[0] == "authenticate"]) == 5


def test_rate_limit_backend_outage_is_reported_as_unavailable_not_exhausted() -> None:
    class BrokenLimiter:
        async def allow(self, **_kwargs) -> bool:
            raise RateLimitUnavailable("redis down")

    identity = FakeIdentityService()
    app = create_platform_app(
        identity_service=identity,
        security=ApiSecuritySettings(
            csrf_secret=b"a5-test-secret-with-at-least-32-bytes"
        ),
        rate_limiter=BrokenLimiter(),
    )
    client = TestClient(app, base_url="https://testserver")
    response = client.post(
        "/v1/auth/login",
        headers={"x-csrf-token": _csrf(client)},
        json={
            "email": "pessoa@example.com",
            "password": "correct horse battery staple",
            "organization_slug": "acme",
        },
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "RATE_LIMIT_UNAVAILABLE"


def test_trusted_proxy_uses_nearest_untrusted_hop_not_spoofed_leftmost() -> None:
    request = Request(
        {
            "type": "http",
            "headers": [(b"x-forwarded-for", b"198.51.100.99, 203.0.113.8")],
            "client": ("10.0.0.5", 12345),
        }
    )
    networks = (ipaddress.ip_network("10.0.0.0/8"),)
    assert client_ip(request, networks) == "203.0.113.8"


def test_validation_errors_are_legible_and_do_not_echo_secret() -> None:
    client, _identity = _client()
    csrf = _csrf(client)
    secret = "short-secret"
    response = client.post(
        "/v1/auth/register",
        headers={"x-csrf-token": csrf},
        json={
            "email": "not-an-email",
            "password": secret,
            "display_name": "P",
            "organization_name": "X",
            "admin": True,
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_REQUEST"
    assert secret not in response.text


def test_oversized_body_is_rejected_before_parsing_without_echo() -> None:
    client, identity = _client()
    secret = "private-value-" * 6000
    response = client.post(
        "/v1/auth/login",
        headers={"content-type": "application/json"},
        content='{"email":"pessoa@example.com","password":"' + secret + '"}',
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "REQUEST_TOO_LARGE"
    assert secret not in response.text
    assert not identity.calls

from __future__ import annotations

import asyncio
import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

from tradingagents.platform.supabase_auth import (
    SupabaseAuthConfig,
    SupabaseAuthError,
    SupabaseJwtVerifier,
    SupabaseUserVerifier,
)

PROJECT_REF = "fkswahrxvmueowcjisnp"
PROJECT_URL = f"https://{PROJECT_REF}.supabase.co"
ISSUER = f"{PROJECT_URL}/auth/v1"


def _jwk(private_key: object, kid: str, algorithm: str) -> dict[str, object]:
    public_key = private_key.public_key()  # type: ignore[attr-defined]
    if algorithm == "RS256":
        result = json.loads(RSAAlgorithm.to_jwk(public_key))
    else:
        result = json.loads(ECAlgorithm.to_jwk(public_key))
    result.update({"kid": kid, "alg": algorithm, "use": "sig"})
    return result


def _token(
    private_key: object,
    kid: str,
    algorithm: str,
    **overrides: object,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, object] = {
        "iss": ISSUER,
        "aud": "authenticated",
        "exp": now + timedelta(minutes=10),
        "iat": now - timedelta(seconds=1),
        "sub": str(uuid4()),
        "email": "Pessoa@Example.com",
        "role": "authenticated",
        "is_anonymous": False,
        "app_metadata": {"provider": "google", "providers": ["email", "google"]},
    }
    payload.update(overrides)
    return jwt.encode(payload, private_key, algorithm=algorithm, headers={"kid": kid, "typ": "JWT"})


def _client(document: object, calls: list[httpx.Request] | None = None, **headers: str) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        return httpx.Response(
            200,
            json=document,
            headers={"content-type": "application/json", **headers},
            request=request,
        )

    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )


def _verify(verifier: SupabaseJwtVerifier, token: str):
    return asyncio.run(verifier.verify(token))


@pytest.mark.parametrize(
    ("private_key", "algorithm", "kid"),
    [
        (rsa.generate_private_key(public_exponent=65537, key_size=2048), "RS256", "rsa-key"),
        (ec.generate_private_key(ec.SECP256R1()), "ES256", "ec-key"),
    ],
)
def test_verifies_pinned_asymmetric_supabase_tokens(private_key, algorithm, kid) -> None:
    calls: list[httpx.Request] = []
    client = _client({"keys": [_jwk(private_key, kid, algorithm)]}, calls)
    verifier = SupabaseJwtVerifier(SupabaseAuthConfig(PROJECT_REF, PROJECT_URL), client)

    claims = _verify(verifier, _token(private_key, kid, algorithm))

    assert claims.email == "Pessoa@example.com"
    assert claims.email_verified is False
    assert claims.provider == "google"
    assert claims.role == "authenticated"
    assert claims.is_anonymous is False
    assert claims.issuer == ISSUER
    assert claims.audience == ("authenticated",)
    assert len(calls) == 1
    assert calls[0].url == f"{ISSUER}/.well-known/jwks.json"
    with pytest.raises(FrozenInstanceError):
        claims.email = "changed@example.com"  # type: ignore[misc]
    asyncio.run(client.aclose())


@pytest.mark.parametrize(
    "project_url",
    [
        "http://fkswahrxvmueowcjisnp.supabase.co",
        "https://evil.supabase.co",
        "https://fkswahrxvmueowcjisnp.supabase.co.evil.example",
        "https://user@fkswahrxvmueowcjisnp.supabase.co",
        "https://fkswahrxvmueowcjisnp.supabase.co:443",
        "https://fkswahrxvmueowcjisnp.supabase.co/auth/v1",
    ],
)
def test_rejects_non_exact_project_origins(project_url: str) -> None:
    with pytest.raises(ValueError, match="origem HTTPS exata"):
        SupabaseAuthConfig(PROJECT_REF, project_url)


def test_rejects_unsafe_injected_http_clients() -> None:
    config = SupabaseAuthConfig(PROJECT_REF, PROJECT_URL)
    redirecting = httpx.AsyncClient(follow_redirects=True, trust_env=False)
    trusting_env = httpx.AsyncClient(follow_redirects=False, trust_env=True)
    with pytest.raises(ValueError, match="follow_redirects"):
        SupabaseJwtVerifier(config, redirecting)
    with pytest.raises(ValueError, match="trust_env"):
        SupabaseJwtVerifier(config, trusting_env)
    asyncio.run(redirecting.aclose())
    asyncio.run(trusting_env.aclose())


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"iss": "https://evil.supabase.co/auth/v1"}, "TOKEN_CLAIMS"),
        ({"aud": "anon"}, "TOKEN_CLAIMS"),
        ({"exp": datetime.now(timezone.utc) - timedelta(seconds=31)}, "TOKEN_EXPIRED"),
        ({"sub": "not-a-uuid"}, "TOKEN_SUBJECT"),
        ({"email": "invalid"}, "TOKEN_EMAIL"),
        ({"role": "service_role"}, "TOKEN_ROLE"),
        ({"is_anonymous": True}, "TOKEN_ANONYMOUS"),
        ({"app_metadata": {"provider": "../../admin"}}, "TOKEN_PROVIDER"),
        ({"app_metadata": {"provider": "google", "providers": ["email"]}}, "TOKEN_PROVIDER"),
    ],
)
def test_rejects_invalid_required_claims(override: dict[str, object], code: str) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    client = _client({"keys": [_jwk(key, "key-1", "RS256")]})
    verifier = SupabaseJwtVerifier(SupabaseAuthConfig(PROJECT_REF, PROJECT_URL), client)
    with pytest.raises(SupabaseAuthError) as caught:
        _verify(verifier, _token(key, "key-1", "RS256", **override))
    assert caught.value.code == code
    assert "eyJ" not in str(caught.value)
    asyncio.run(client.aclose())


def test_rejects_symmetric_none_unknown_kid_and_wrong_signature() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    client = _client({"keys": [_jwk(key, "known", "RS256")]})
    verifier = SupabaseJwtVerifier(SupabaseAuthConfig(PROJECT_REF, PROJECT_URL), client)

    unknown = _token(key, "unknown", "RS256")
    wrong_signature = _token(other, "known", "RS256")
    symmetric = jwt.encode(
        {
            "iss": ISSUER,
            "aud": "authenticated",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=1),
            "sub": str(uuid4()),
            "email": "person@example.com",
            "email_verified": True,
            "role": "authenticated",
            "is_anonymous": False,
        },
        "not-a-real-secret-but-long-enough",
        algorithm="HS256",
        headers={"kid": "known"},
    )
    for token, expected in [
        (unknown, "TOKEN_KID_UNKNOWN"),
        (wrong_signature, "TOKEN_CLAIMS"),
        (symmetric, "TOKEN_ALGORITHM"),
    ]:
        with pytest.raises(SupabaseAuthError) as caught:
            _verify(verifier, token)
        assert caught.value.code == expected
    asyncio.run(client.aclose())


def test_caches_keys_and_refreshes_once_for_a_new_kid() -> None:
    first = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    second = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        keys = [_jwk(first, "first", "RS256")]
        if len(calls) >= 2:
            keys.append(_jwk(second, "second", "RS256"))
        return httpx.Response(200, json={"keys": keys}, headers={"content-type": "application/json"})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False, trust_env=False
    )
    clock = [0.0]
    verifier = SupabaseJwtVerifier(
        SupabaseAuthConfig(PROJECT_REF, PROJECT_URL),
        client,
        monotonic=lambda: clock[0],
    )
    _verify(verifier, _token(first, "first", "RS256"))
    _verify(verifier, _token(first, "first", "RS256"))
    clock[0] = 16.0
    _verify(verifier, _token(second, "second", "RS256"))
    assert len(calls) == 2
    asyncio.run(client.aclose())


def test_unknown_kids_cannot_amplify_jwks_requests_inside_cooldown() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    calls: list[httpx.Request] = []
    client = _client({"keys": [_jwk(key, "known", "RS256")]}, calls)
    verifier = SupabaseJwtVerifier(SupabaseAuthConfig(PROJECT_REF, PROJECT_URL), client)
    _verify(verifier, _token(key, "known", "RS256"))

    for index in range(50):
        with pytest.raises(SupabaseAuthError) as caught:
            _verify(verifier, _token(key, f"unknown-{index}", "RS256"))
        assert caught.value.code == "TOKEN_KID_UNKNOWN"

    assert len(calls) == 1
    asyncio.run(client.aclose())


@pytest.mark.parametrize(
    ("document", "headers", "code"),
    [
        ({"keys": []}, {}, "JWKS_INVALID"),
        ({"keys": [{"kid": "x", "alg": "HS256", "kty": "oct", "k": "eA"}]}, {}, "JWKS_INVALID"),
        ({"keys": [{"kid": "same"}, {"kid": "same"}]}, {}, "JWKS_INVALID"),
        ({"keys": []}, {"content-type": "text/html"}, "JWKS_CONTENT_TYPE"),
        ({"keys": []}, {"content-length": "999999"}, "JWKS_TOO_LARGE"),
    ],
)
def test_rejects_malformed_or_unbounded_jwks(document, headers, code) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    client = _client(document, **headers)
    verifier = SupabaseJwtVerifier(SupabaseAuthConfig(PROJECT_REF, PROJECT_URL), client)
    with pytest.raises(SupabaseAuthError) as caught:
        _verify(verifier, _token(key, "missing", "RS256"))
    assert caught.value.code == code
    asyncio.run(client.aclose())


def test_never_follows_jwks_redirects() -> None:
    contacted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        contacted.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://evil.example/jwks"})

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False, trust_env=False
    )
    verifier = SupabaseJwtVerifier(SupabaseAuthConfig(PROJECT_REF, PROJECT_URL), client)
    with pytest.raises(SupabaseAuthError) as caught:
        _verify(verifier, _token(key, "missing", "RS256"))
    assert caught.value.code == "JWKS_UNAVAILABLE"
    assert contacted == [f"{ISSUER}/.well-known/jwks.json"]
    asyncio.run(client.aclose())


def test_user_verifier_confirms_email_against_official_user_endpoint() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = uuid4()
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/.well-known/jwks.json"):
            return httpx.Response(
                200,
                json={"keys": [_jwk(key, "key-1", "RS256")]},
                headers={"content-type": "application/json"},
            )
        assert request.url == f"{ISSUER}/user"
        return httpx.Response(
            200,
            json={
                "id": str(subject),
                "email": "Pessoa@example.com",
                "email_confirmed_at": "2026-09-08T10:15:30Z",
                "role": "authenticated",
                "is_anonymous": False,
                "user_metadata": {"email_verified": False},
            },
            headers={"content-type": "application/json"},
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False, trust_env=False
    )
    config = SupabaseAuthConfig(PROJECT_REF, PROJECT_URL)
    jwt_verifier = SupabaseJwtVerifier(config, client)
    verifier = SupabaseUserVerifier(
        config,
        "sb_publishable_test-value",
        jwt_verifier,
        client,
    )
    token = _token(key, "key-1", "RS256", sub=str(subject))

    claims = _verify(verifier, token)

    assert claims.email_verified is True
    assert len(calls) == 2
    assert calls[1].headers["authorization"] == f"Bearer {token}"
    assert calls[1].headers["apikey"] == "sb_publishable_test-value"
    asyncio.run(client.aclose())


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"email_confirmed_at": None}, "USER_EMAIL_UNVERIFIED"),
        ({"email_confirmed_at": "not-a-date"}, "USER_EMAIL_UNVERIFIED"),
        ({"role": "service_role"}, "USER_ROLE"),
        ({"is_anonymous": True}, "USER_ANONYMOUS"),
        ({"email": "other@example.com"}, "USER_EMAIL_MISMATCH"),
        ({"id": str(uuid4())}, "USER_SUBJECT_MISMATCH"),
    ],
)
def test_user_verifier_rejects_unconfirmed_or_mismatched_records(override, code) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = uuid4()
    user = {
        "id": str(subject),
        "email": "Pessoa@example.com",
        "email_confirmed_at": "2026-09-08T10:15:30+00:00",
        "role": "authenticated",
        "is_anonymous": False,
    }
    user.update(override)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/jwks.json"):
            return httpx.Response(
                200,
                json={"keys": [_jwk(key, "key-1", "RS256")]},
                headers={"content-type": "application/json"},
            )
        return httpx.Response(
            200, json=user, headers={"content-type": "application/json"}
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False, trust_env=False
    )
    config = SupabaseAuthConfig(PROJECT_REF, PROJECT_URL)
    verifier = SupabaseUserVerifier(
        config,
        "sb_publishable_test-value",
        SupabaseJwtVerifier(config, client),
        client,
    )
    with pytest.raises(SupabaseAuthError) as caught:
        _verify(verifier, _token(key, "key-1", "RS256", sub=str(subject)))
    assert caught.value.code == code
    asyncio.run(client.aclose())


def test_user_verifier_rejects_redirects_and_unsafe_clients() -> None:
    config = SupabaseAuthConfig(PROJECT_REF, PROJECT_URL)
    safe = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                302, headers={"location": "https://evil.example/user"}, request=request
            )
        ),
        follow_redirects=False,
        trust_env=False,
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwt_verifier = SupabaseJwtVerifier(config, safe)
    verifier = SupabaseUserVerifier(
        config, "sb_publishable_test-value", jwt_verifier, safe
    )
    with pytest.raises(SupabaseAuthError) as caught:
        _verify(verifier, _token(key, "key-1", "RS256"))
    assert caught.value.code == "JWKS_UNAVAILABLE"

    unsafe = httpx.AsyncClient(follow_redirects=True, trust_env=False)
    with pytest.raises(ValueError, match="follow_redirects"):
        SupabaseUserVerifier(config, "sb_publishable_test-value", jwt_verifier, unsafe)
    asyncio.run(safe.aclose())
    asyncio.run(unsafe.aclose())

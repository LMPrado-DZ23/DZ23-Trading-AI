"""Fail-closed verification of Supabase Auth access tokens.

The browser-facing publishable key is not an authentication secret.  This
module verifies the asymmetric access token signature against the project's
JWKS and returns only the claims the platform needs.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import jwt
from email_validator import EmailNotValidError, validate_email
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

_PROJECT_REF_RE = re.compile(r"^[a-z0-9]{10,64}$")
_KID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,31}$")
_ALLOWED_ALGORITHMS = frozenset({"RS256", "ES256"})
_JWKS_CONTENT_TYPES = frozenset({"application/json", "application/jwk-set+json"})
_USER_CONTENT_TYPES = frozenset({"application/json"})
_PUBLISHABLE_KEY_RE = re.compile(r"^sb_publishable_[A-Za-z0-9_-]{8,512}$")


class SupabaseAuthError(ValueError):
    """A public, token-safe authentication failure."""

    def __init__(self, code: str, message: str = "Token de acesso inválido.") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SupabaseAuthConfig:
    """Pinned Supabase project and bounded JWKS retrieval policy."""

    project_ref: str
    project_url: str
    audience: str = "authenticated"
    jwks_cache_ttl_seconds: float = 300.0
    request_timeout_seconds: float = 5.0
    max_jwks_bytes: int = 65_536
    max_jwks_keys: int = 32
    max_token_bytes: int = 16_384
    max_user_response_bytes: int = 65_536
    unknown_kid_refresh_cooldown_seconds: float = 15.0
    clock_skew_seconds: float = 30.0

    def __post_init__(self) -> None:
        ref = self.project_ref.strip().lower()
        if not _PROJECT_REF_RE.fullmatch(ref):
            raise ValueError("project_ref inválido")

        parsed = urlsplit(self.project_url)
        expected_host = f"{ref}.supabase.co"
        if (
            parsed.scheme != "https"
            or parsed.hostname != expected_host
            or parsed.netloc != expected_host
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
        ):
            raise ValueError("project_url deve ser a origem HTTPS exata do projeto Supabase")
        if self.audience != "authenticated":
            raise ValueError("audience Supabase deve ser 'authenticated'")
        if not 1.0 <= self.jwks_cache_ttl_seconds <= 3_600.0:
            raise ValueError("jwks_cache_ttl_seconds fora do limite")
        if not 0.25 <= self.request_timeout_seconds <= 10.0:
            raise ValueError("request_timeout_seconds fora do limite")
        if not 1_024 <= self.max_jwks_bytes <= 1_048_576:
            raise ValueError("max_jwks_bytes fora do limite")
        if not 1 <= self.max_jwks_keys <= 128:
            raise ValueError("max_jwks_keys fora do limite")
        if not 1_024 <= self.max_token_bytes <= 65_536:
            raise ValueError("max_token_bytes fora do limite")
        if not 1_024 <= self.max_user_response_bytes <= 1_048_576:
            raise ValueError("max_user_response_bytes fora do limite")
        if not 5.0 <= self.unknown_kid_refresh_cooldown_seconds <= 300.0:
            raise ValueError("unknown_kid_refresh_cooldown_seconds fora do limite")
        if not 0.0 <= self.clock_skew_seconds <= 120.0:
            raise ValueError("clock_skew_seconds fora do limite")

        object.__setattr__(self, "project_ref", ref)
        object.__setattr__(self, "project_url", f"https://{expected_host}")

    @property
    def issuer(self) -> str:
        return f"{self.project_url}/auth/v1"

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"

    @property
    def user_url(self) -> str:
        return f"{self.issuer}/user"


@dataclass(frozen=True, slots=True)
class SupabaseAuthClaims:
    """Small immutable identity projection from a verified access token."""

    subject: UUID
    email: str
    email_verified: bool
    provider: str | None
    role: str
    is_anonymous: bool
    issuer: str
    audience: tuple[str, ...]
    expires_at: datetime
    issued_at: datetime | None


@dataclass(frozen=True, slots=True)
class _CachedKeys:
    expires_monotonic: float
    keys: Mapping[str, Any]


class SupabaseJwtVerifier:
    """Verify one pinned Supabase project's asymmetric access tokens."""

    def __init__(
        self,
        config: SupabaseAuthConfig,
        http_client: httpx.AsyncClient,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if http_client.follow_redirects:
            raise ValueError("http_client deve usar follow_redirects=False")
        if getattr(http_client, "_trust_env", None) is not False:
            raise ValueError("http_client deve usar trust_env=False")
        self._config = config
        self._http = http_client
        self._monotonic = monotonic
        self._cache: _CachedKeys | None = None
        self._refresh_lock = asyncio.Lock()
        self._last_jwks_fetch_monotonic: float | None = None

    async def verify(self, token: str) -> SupabaseAuthClaims:
        if (
            not isinstance(token, str)
            or not token
            or len(token.encode("utf-8")) > self._config.max_token_bytes
        ):
            raise SupabaseAuthError("TOKEN_FORMAT")
        if token.count(".") != 2:
            raise SupabaseAuthError("TOKEN_FORMAT")

        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise SupabaseAuthError("TOKEN_HEADER") from exc
        algorithm = header.get("alg")
        kid = header.get("kid")
        token_type = header.get("typ")
        if algorithm not in _ALLOWED_ALGORITHMS:
            raise SupabaseAuthError("TOKEN_ALGORITHM")
        if not isinstance(kid, str) or not _KID_RE.fullmatch(kid):
            raise SupabaseAuthError("TOKEN_KID")
        if token_type is not None and token_type != "JWT":
            raise SupabaseAuthError("TOKEN_TYPE")

        key = await self._key_for(kid)
        if key is None:
            key = await self._refresh_for_unknown_kid(kid)
        if key is None:
            raise SupabaseAuthError("TOKEN_KID_UNKNOWN")

        try:
            payload = jwt.decode(
                token,
                key=key,
                algorithms=[algorithm],
                audience=self._config.audience,
                issuer=self._config.issuer,
                leeway=self._config.clock_skew_seconds,
                options={
                    "require": ["iss", "aud", "exp", "sub", "email"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
            )
        except jwt.ExpiredSignatureError as exc:
            raise SupabaseAuthError("TOKEN_EXPIRED", "Sessão expirada.") from exc
        except jwt.PyJWTError as exc:
            raise SupabaseAuthError("TOKEN_CLAIMS") from exc

        return self._claims(payload)

    async def _key_for(self, kid: str) -> Any | None:
        now = self._monotonic()
        cached = self._cache
        if cached is not None and cached.expires_monotonic > now:
            return cached.keys.get(kid)

        async with self._refresh_lock:
            now = self._monotonic()
            cached = self._cache
            if cached is not None and cached.expires_monotonic > now:
                return cached.keys.get(kid)
            keys = await self._fetch_jwks()
            self._last_jwks_fetch_monotonic = now
            self._cache = _CachedKeys(
                expires_monotonic=now + self._config.jwks_cache_ttl_seconds,
                keys=MappingProxyType(keys),
            )
            return keys.get(kid)

    async def _refresh_for_unknown_kid(self, kid: str) -> Any | None:
        async with self._refresh_lock:
            now = self._monotonic()
            cached = self._cache
            if cached is not None and (known := cached.keys.get(kid)) is not None:
                return known
            last_fetch = self._last_jwks_fetch_monotonic
            if (
                last_fetch is not None
                and now - last_fetch < self._config.unknown_kid_refresh_cooldown_seconds
            ):
                return None
            keys = await self._fetch_jwks()
            self._last_jwks_fetch_monotonic = now
            self._cache = _CachedKeys(
                expires_monotonic=now + self._config.jwks_cache_ttl_seconds,
                keys=MappingProxyType(keys),
            )
            return keys.get(kid)

    async def _fetch_jwks(self) -> dict[str, Any]:
        timeout = httpx.Timeout(self._config.request_timeout_seconds)
        try:
            async with self._http.stream(
                "GET",
                self._config.jwks_url,
                headers={"Accept": "application/jwk-set+json, application/json"},
                follow_redirects=False,
                timeout=timeout,
            ) as response:
                if response.status_code != 200:
                    raise SupabaseAuthError("JWKS_UNAVAILABLE", "Autenticação indisponível.")
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if content_type not in _JWKS_CONTENT_TYPES:
                    raise SupabaseAuthError("JWKS_CONTENT_TYPE", "Autenticação indisponível.")
                declared_length = response.headers.get("content-length")
                if declared_length is not None:
                    try:
                        parsed_length = int(declared_length)
                    except ValueError as exc:
                        raise SupabaseAuthError(
                            "JWKS_CONTENT_LENGTH", "Autenticação indisponível."
                        ) from exc
                    if parsed_length < 0:
                        raise SupabaseAuthError("JWKS_CONTENT_LENGTH", "Autenticação indisponível.")
                    if parsed_length > self._config.max_jwks_bytes:
                        raise SupabaseAuthError("JWKS_TOO_LARGE", "Autenticação indisponível.")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self._config.max_jwks_bytes:
                        raise SupabaseAuthError("JWKS_TOO_LARGE", "Autenticação indisponível.")
        except SupabaseAuthError:
            raise
        except httpx.HTTPError as exc:
            raise SupabaseAuthError("JWKS_UNAVAILABLE", "Autenticação indisponível.") from exc

        try:
            document = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SupabaseAuthError("JWKS_INVALID", "Autenticação indisponível.") from exc
        if not isinstance(document, dict) or set(document) != {"keys"}:
            raise SupabaseAuthError("JWKS_INVALID", "Autenticação indisponível.")
        raw_keys = document["keys"]
        if not isinstance(raw_keys, list) or not 1 <= len(raw_keys) <= self._config.max_jwks_keys:
            raise SupabaseAuthError("JWKS_INVALID", "Autenticação indisponível.")

        parsed: dict[str, Any] = {}
        for jwk in raw_keys:
            if not isinstance(jwk, dict):
                raise SupabaseAuthError("JWKS_INVALID", "Autenticação indisponível.")
            kid = jwk.get("kid")
            algorithm = jwk.get("alg")
            key_type = jwk.get("kty")
            use = jwk.get("use")
            if (
                not isinstance(kid, str)
                or not _KID_RE.fullmatch(kid)
                or kid in parsed
                or algorithm not in _ALLOWED_ALGORITHMS
                or use not in {None, "sig"}
            ):
                raise SupabaseAuthError("JWKS_INVALID", "Autenticação indisponível.")
            try:
                if algorithm == "RS256" and key_type == "RSA":
                    parsed[kid] = RSAAlgorithm.from_jwk(jwk)
                elif algorithm == "ES256" and key_type == "EC" and jwk.get("crv") == "P-256":
                    parsed[kid] = ECAlgorithm.from_jwk(jwk)
                else:
                    raise SupabaseAuthError("JWKS_INVALID", "Autenticação indisponível.")
            except SupabaseAuthError:
                raise
            except (TypeError, ValueError, KeyError) as exc:
                raise SupabaseAuthError("JWKS_INVALID", "Autenticação indisponível.") from exc
        return parsed

    def _claims(self, payload: Mapping[str, Any]) -> SupabaseAuthClaims:
        try:
            subject = UUID(payload["sub"])
        except (ValueError, TypeError, AttributeError) as exc:
            raise SupabaseAuthError("TOKEN_SUBJECT") from exc

        email = payload.get("email")
        if not isinstance(email, str) or len(email) > 320:
            raise SupabaseAuthError("TOKEN_EMAIL")
        try:
            normalized_email = validate_email(email, check_deliverability=False).normalized
        except EmailNotValidError as exc:
            raise SupabaseAuthError("TOKEN_EMAIL") from exc
        expires_at = self._numeric_date(payload.get("exp"), "TOKEN_EXPIRY")
        issued_at = None
        if "iat" in payload:
            issued_at = self._numeric_date(payload["iat"], "TOKEN_ISSUED_AT")
            if issued_at > datetime.now(timezone.utc) + timedelta(
                seconds=self._config.clock_skew_seconds
            ):
                raise SupabaseAuthError("TOKEN_ISSUED_AT")

        audience_claim = payload.get("aud")
        if isinstance(audience_claim, str):
            audience = (audience_claim,)
        elif isinstance(audience_claim, list) and all(
            isinstance(item, str) for item in audience_claim
        ):
            audience = tuple(audience_claim)
        else:
            raise SupabaseAuthError("TOKEN_AUDIENCE")

        provider = self._provider(payload.get("app_metadata"))
        if payload.get("role") != "authenticated":
            raise SupabaseAuthError("TOKEN_ROLE")
        if payload.get("is_anonymous") is not False:
            raise SupabaseAuthError("TOKEN_ANONYMOUS")
        return SupabaseAuthClaims(
            subject=subject,
            email=normalized_email,
            # Supabase access tokens do not promise a top-level
            # ``email_verified`` claim.  The official /auth/v1/user response
            # is the authority for confirmation and is checked by
            # SupabaseUserVerifier below.
            email_verified=False,
            provider=provider,
            role="authenticated",
            is_anonymous=False,
            issuer=self._config.issuer,
            audience=audience,
            expires_at=expires_at,
            issued_at=issued_at,
        )

    @staticmethod
    def _numeric_date(value: Any, code: str) -> datetime:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SupabaseAuthError(code)
        try:
            result = datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise SupabaseAuthError(code) from exc
        return result

    @staticmethod
    def _provider(app_metadata: Any) -> str | None:
        if app_metadata is None:
            return None
        if not isinstance(app_metadata, dict):
            raise SupabaseAuthError("TOKEN_PROVIDER")
        provider = app_metadata.get("provider")
        if provider is None:
            return None
        if not isinstance(provider, str):
            raise SupabaseAuthError("TOKEN_PROVIDER")
        normalized = provider.strip().lower()
        if not _PROVIDER_RE.fullmatch(normalized):
            raise SupabaseAuthError("TOKEN_PROVIDER")
        providers = app_metadata.get("providers")
        if providers is not None:
            if not isinstance(providers, list) or not all(
                isinstance(item, str) for item in providers
            ):
                raise SupabaseAuthError("TOKEN_PROVIDER")
            normalized_providers = {item.strip().lower() for item in providers}
            if normalized not in normalized_providers:
                raise SupabaseAuthError("TOKEN_PROVIDER")
        return normalized


class SupabaseUserVerifier:
    """Combine local JWT verification with the official Supabase user record.

    Signature, issuer, audience and expiry are verified locally first.  The
    authenticated user endpoint then proves that the same subject and e-mail
    are still active and confirmed.  User-editable metadata is never treated
    as verification evidence.
    """

    def __init__(
        self,
        config: SupabaseAuthConfig,
        publishable_key: str,
        jwt_verifier: SupabaseJwtVerifier,
        http_client: httpx.AsyncClient,
    ) -> None:
        if not _PUBLISHABLE_KEY_RE.fullmatch(publishable_key):
            raise ValueError("publishable_key Supabase inválida")
        if http_client.follow_redirects:
            raise ValueError("http_client deve usar follow_redirects=False")
        if getattr(http_client, "_trust_env", None) is not False:
            raise ValueError("http_client deve usar trust_env=False")
        self._config = config
        self._publishable_key = publishable_key
        self._jwt = jwt_verifier
        self._http = http_client

    async def verify(self, token: str) -> SupabaseAuthClaims:
        signed = await self._jwt.verify(token)
        document = await self._fetch_user(token)

        try:
            subject = UUID(document["id"])
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise SupabaseAuthError("USER_SUBJECT") from exc
        if subject != signed.subject:
            raise SupabaseAuthError("USER_SUBJECT_MISMATCH")

        email = document.get("email")
        if not isinstance(email, str) or len(email) > 320:
            raise SupabaseAuthError("USER_EMAIL")
        try:
            normalized_email = validate_email(email, check_deliverability=False).normalized
        except EmailNotValidError as exc:
            raise SupabaseAuthError("USER_EMAIL") from exc
        if normalized_email.casefold() != signed.email.casefold():
            raise SupabaseAuthError("USER_EMAIL_MISMATCH")

        confirmed_at = document.get("email_confirmed_at")
        if not isinstance(confirmed_at, str) or not self._confirmed_timestamp(confirmed_at):
            raise SupabaseAuthError("USER_EMAIL_UNVERIFIED", "Confirme seu e-mail para continuar.")
        if document.get("role") != "authenticated":
            raise SupabaseAuthError("USER_ROLE")
        if document.get("is_anonymous") is not False:
            raise SupabaseAuthError("USER_ANONYMOUS")

        return SupabaseAuthClaims(
            subject=signed.subject,
            email=signed.email,
            email_verified=True,
            provider=signed.provider,
            role=signed.role,
            is_anonymous=False,
            issuer=signed.issuer,
            audience=signed.audience,
            expires_at=signed.expires_at,
            issued_at=signed.issued_at,
        )

    async def _fetch_user(self, token: str) -> dict[str, Any]:
        timeout = httpx.Timeout(self._config.request_timeout_seconds)
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "apikey": self._publishable_key,
        }
        try:
            async with self._http.stream(
                "GET",
                self._config.user_url,
                headers=headers,
                follow_redirects=False,
                timeout=timeout,
            ) as response:
                if response.status_code != 200:
                    raise SupabaseAuthError("USER_UNAVAILABLE", "Autenticação indisponível.")
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if content_type not in _USER_CONTENT_TYPES:
                    raise SupabaseAuthError("USER_CONTENT_TYPE", "Autenticação indisponível.")
                declared_length = response.headers.get("content-length")
                if declared_length is not None:
                    try:
                        parsed_length = int(declared_length)
                    except ValueError as exc:
                        raise SupabaseAuthError(
                            "USER_CONTENT_LENGTH", "Autenticação indisponível."
                        ) from exc
                    if parsed_length < 0:
                        raise SupabaseAuthError("USER_CONTENT_LENGTH", "Autenticação indisponível.")
                    if parsed_length > self._config.max_user_response_bytes:
                        raise SupabaseAuthError("USER_TOO_LARGE", "Autenticação indisponível.")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self._config.max_user_response_bytes:
                        raise SupabaseAuthError("USER_TOO_LARGE", "Autenticação indisponível.")
        except SupabaseAuthError:
            raise
        except httpx.HTTPError as exc:
            raise SupabaseAuthError("USER_UNAVAILABLE", "Autenticação indisponível.") from exc

        try:
            document = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SupabaseAuthError("USER_INVALID", "Autenticação indisponível.") from exc
        if not isinstance(document, dict):
            raise SupabaseAuthError("USER_INVALID", "Autenticação indisponível.")
        return document

    @staticmethod
    def _confirmed_timestamp(value: str) -> bool:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        return parsed.tzinfo is not None

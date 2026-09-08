"""Transport-neutral browser security controls for the platform API."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import secrets
import time
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from fastapi import HTTPException, Request, Response, status
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SESSION_COOKIE = "__Host-ta_session"
CSRF_COOKIE = "__Host-ta_csrf"
CSRF_HEADER = "x-csrf-token"


@dataclass(frozen=True, slots=True)
class ApiSecuritySettings:
    """Security settings with no permissive production defaults."""

    csrf_secret: bytes
    trusted_proxy_cidrs: tuple[str, ...] = ()
    session_ttl_seconds: int = 8 * 60 * 60
    max_request_body_bytes: int = 64 * 1024
    allowed_origins: tuple[str, ...] = ()
    strict_transport_security: bool = False

    def __post_init__(self) -> None:
        if len(self.csrf_secret) < 32:
            raise ValueError("csrf_secret deve conter pelo menos 32 bytes")
        if self.session_ttl_seconds < 300:
            raise ValueError("session_ttl_seconds deve ser no mínimo 300")
        if not 1024 <= self.max_request_body_bytes <= 1024 * 1024:
            raise ValueError("max_request_body_bytes deve estar entre 1 KiB e 1 MiB")
        for cidr in self.trusted_proxy_cidrs:
            ipaddress.ip_network(cidr, strict=False)
        if any(not origin.startswith("https://") for origin in self.allowed_origins):
            raise ValueError("allowed_origins da API devem usar HTTPS")

    @property
    def trusted_proxy_networks(self) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
        return tuple(
            ipaddress.ip_network(value, strict=False) for value in self.trusted_proxy_cidrs
        )


class RateLimiter(Protocol):
    async def allow(self, *, key: str, limit: int, window_seconds: int) -> bool: ...


class RateLimitUnavailable(RuntimeError):
    """The shared limiter could not make an authoritative decision."""


class RequestBodyLimitMiddleware:
    """Reject oversized mutation bodies before request parsing or endpoint code."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return

        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            body.extend(message.get("body", b""))
            if len(body) > self.max_bytes:
                await _send_body_too_large(send)
                return
            more_body = bool(message.get("more_body", False))

        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)


class InMemorySlidingWindowRateLimiter:
    """Bounded-process limiter for development and tests.

    Production must inject a shared atomic implementation (normally Redis), so
    limits cannot be bypassed by selecting another worker.
    """

    def __init__(self, *, clock=time.monotonic, max_keys: int = 50_000) -> None:
        self._clock = clock
        self._max_keys = max_keys
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, *, key: str, limit: int, window_seconds: int) -> bool:
        now = self._clock()
        boundary = now - window_seconds
        async with self._lock:
            events = self._events[key]
            while events and events[0] <= boundary:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            if len(self._events) > self._max_keys:
                self._discard_empty_or_oldest()
            return True

    def _discard_empty_or_oldest(self) -> None:
        for key in tuple(self._events):
            if not self._events[key]:
                del self._events[key]
                return
        oldest_key = min(self._events, key=lambda item: self._events[item][0])
        del self._events[oldest_key]


def client_ip(
    request: Request,
    trusted_networks: Iterable[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> str:
    """Resolve the nearest untrusted hop, never trusting XFF from a direct client."""

    peer_text = request.client.host if request.client else "0.0.0.0"
    try:
        peer = ipaddress.ip_address(peer_text)
    except ValueError:
        return "0.0.0.0"
    networks = tuple(trusted_networks)
    if not any(peer in network for network in networks):
        return peer.compressed

    forwarded = request.headers.get("x-forwarded-for", "")
    try:
        chain = [
            ipaddress.ip_address(part.strip()) for part in forwarded.split(",") if part.strip()
        ]
    except ValueError:
        return peer.compressed
    for hop in reversed([*chain, peer]):
        if not any(hop in network for network in networks):
            return hop.compressed
    return peer.compressed


def rate_key(scope: str, identity: str, ip: str) -> str:
    identity_digest = hashlib.sha256(identity.casefold().encode("utf-8")).hexdigest()
    return f"{scope}:{ip}:{identity_digest}"


def issue_csrf_token(secret: bytes) -> str:
    nonce = secrets.token_bytes(32)
    signature = hmac.digest(secret, nonce, "sha256")
    return f"{_encode(nonce)}.{_encode(signature)}"


def verify_csrf(request: Request, secret: bytes) -> None:
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get(CSRF_HEADER, "")
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise _csrf_error()
    try:
        nonce_text, signature_text = cookie.split(".", 1)
        nonce = _decode(nonce_text)
        supplied_signature = _decode(signature_text)
    except (ValueError, TypeError):
        raise _csrf_error() from None
    expected_signature = hmac.digest(secret, nonce, "sha256")
    if len(nonce) != 32 or not hmac.compare_digest(supplied_signature, expected_signature):
        raise _csrf_error()


def set_csrf_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        CSRF_COOKIE,
        token,
        secure=True,
        httponly=False,
        samesite="strict",
        path="/",
    )


def set_session_cookie(response: Response, token: str, max_age: int) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age,
        secure=True,
        httponly=True,
        samesite="strict",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        secure=True,
        httponly=True,
        samesite="strict",
        path="/",
    )


def _csrf_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "CSRF_INVALID",
            "message": "Sua sessão de segurança expirou. Atualize a página e tente novamente.",
        },
    )


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


async def _send_body_too_large(send: Send) -> None:
    body = (
        b'{"detail":{"code":"REQUEST_TOO_LARGE",'
        b'"message":"A solicitacao excede o tamanho permitido."}}'
    )
    await send(
        {
            "type": "http.response.start",
            "status": status.HTTP_413_CONTENT_TOO_LARGE,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"cache-control", b"no-store"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})

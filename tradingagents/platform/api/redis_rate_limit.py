"""Atomic Redis implementation of the platform rate-limit port."""

from __future__ import annotations

import hashlib
import re
from typing import Final, Protocol

from redis.asyncio import Redis

from tradingagents.platform.api.security import RateLimitUnavailable

_MAX_LIMIT: Final = 1_000_000
_MAX_WINDOW_SECONDS: Final = 86_400
_MAX_KEY_CHARACTERS: Final = 4_096
_NAMESPACE_PATTERN: Final = re.compile(r"\A[a-zA-Z0-9][a-zA-Z0-9:_-]{0,63}\Z")

# A fixed-window counter is sufficient for the current RateLimiter port.  The
# increment, first-use expiry and orphan-TTL repair execute as one Redis script,
# so concurrent API workers cannot exceed the configured counter through a
# read/modify/write race.
_ALLOW_SCRIPT: Final = """
local count = redis.call("INCR", KEYS[1])
local ttl = redis.call("PTTL", KEYS[1])
if count == 1 or ttl < 0 then
    redis.call("PEXPIRE", KEYS[1], ARGV[1])
end
if count <= tonumber(ARGV[2]) then
    return 1
end
return 0
""".strip()


class _RedisEvalClient(Protocol):
    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: str | int,
    ) -> object: ...


class RedisRateLimiter:
    """Shared, atomic limiter for multi-worker production deployments.

    Redis or protocol failures deny the request.  The caller never receives a
    permissive result merely because the shared enforcement service is down.
    Logical identities are hashed before becoming Redis keys, avoiding both
    disclosure and Redis-key injection through attacker-controlled input.
    """

    def __init__(
        self,
        client: Redis | _RedisEvalClient,
        *,
        namespace: str = "tradingagents",
    ) -> None:
        if not isinstance(namespace, str) or not _NAMESPACE_PATTERN.fullmatch(namespace):
            raise ValueError(
                "namespace deve ter 1 a 64 caracteres ASCII seguros e começar por letra ou número"
            )
        self._client = client
        self._prefix = f"{namespace}:rate-limit"

    async def allow(self, *, key: str, limit: int, window_seconds: int) -> bool:
        """Consume one allowance, denying safely on backend/protocol failure."""

        normalized_key = _validate_key(key)
        normalized_limit = _validate_positive_int("limit", limit, maximum=_MAX_LIMIT)
        normalized_window = _validate_positive_int(
            "window_seconds",
            window_seconds,
            maximum=_MAX_WINDOW_SECONDS,
        )
        redis_key = f"{self._prefix}:{hashlib.sha256(normalized_key).hexdigest()}"

        try:
            result = await self._client.eval(
                _ALLOW_SCRIPT,
                1,
                redis_key,
                normalized_window * 1_000,
                normalized_limit,
            )
        except Exception as exc:
            raise RateLimitUnavailable("shared rate limiter unavailable") from exc

        # Redis normally returns int.  Reject booleans and unexpected response
        # types/values instead of allowing a malformed or mocked response.
        if type(result) is not int or result not in {0, 1}:
            raise RateLimitUnavailable("shared rate limiter returned an invalid response")
        return result == 1


def _validate_key(key: str) -> bytes:
    if not isinstance(key, str) or not key or len(key) > _MAX_KEY_CHARACTERS:
        raise ValueError(f"key deve ser texto não vazio com até {_MAX_KEY_CHARACTERS} caracteres")
    try:
        return key.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("key deve conter texto Unicode válido") from exc


def _validate_positive_int(name: str, value: int, *, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} deve ser inteiro entre 1 e {maximum}")
    return value

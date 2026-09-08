from __future__ import annotations

import asyncio
from collections import defaultdict

import pytest

from tradingagents.platform.api.redis_rate_limit import RedisRateLimiter
from tradingagents.platform.api.security import RateLimitUnavailable

_UNSET = object()


class ControlledRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, tuple[str | int, ...]]] = []
        self.counts: defaultdict[str, int] = defaultdict(int)
        self.expiry_ms: dict[str, int] = {}
        self.lock = asyncio.Lock()
        self.result: object = _UNSET
        self.error: Exception | None = None

    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: str | int,
    ) -> object:
        self.calls.append((script, numkeys, keys_and_args))
        if self.error is not None:
            raise self.error
        if self.result is not _UNSET:
            return self.result
        redis_key, ttl_ms, limit = keys_and_args
        assert isinstance(redis_key, str)
        assert isinstance(ttl_ms, int)
        assert isinstance(limit, int)
        async with self.lock:
            self.counts[redis_key] += 1
            self.expiry_ms.setdefault(redis_key, ttl_ms)
            return int(self.counts[redis_key] <= limit)


def test_allows_only_configured_number_of_requests_and_sets_ttl() -> None:
    redis = ControlledRedis()
    limiter = RedisRateLimiter(redis, namespace="tenant-api")

    async def exercise() -> list[bool]:
        return [
            await limiter.allow(key="login:client@example.com", limit=2, window_seconds=15)
            for _ in range(3)
        ]

    outcomes = asyncio.run(exercise())

    assert outcomes == [True, True, False]
    assert len(redis.counts) == 1
    redis_key = next(iter(redis.counts))
    assert redis_key.startswith("tenant-api:rate-limit:")
    assert "client@example.com" not in redis_key
    assert redis.expiry_ms[redis_key] == 15_000
    assert all(call[1] == 1 for call in redis.calls)


def test_concurrent_requests_share_one_atomic_counter() -> None:
    redis = ControlledRedis()
    limiter = RedisRateLimiter(redis)

    async def exercise() -> list[bool]:
        return await asyncio.gather(
            *(limiter.allow(key="same-client", limit=7, window_seconds=60) for _ in range(50))
        )

    outcomes = asyncio.run(exercise())

    assert sum(outcomes) == 7
    assert len(redis.counts) == 1


def test_backend_failure_is_fail_closed() -> None:
    redis = ControlledRedis()
    redis.error = TimeoutError("redis unavailable")

    with pytest.raises(RateLimitUnavailable):
        asyncio.run(RedisRateLimiter(redis).allow(key="client", limit=1, window_seconds=1))


@pytest.mark.parametrize("response", [True, False, None, b"1", "1", -1, 2])
def test_unexpected_redis_response_is_fail_closed(response: object) -> None:
    redis = ControlledRedis()
    redis.result = response

    with pytest.raises(RateLimitUnavailable):
        asyncio.run(RedisRateLimiter(redis).allow(key="client", limit=1, window_seconds=1))


def test_zero_is_an_authoritative_denial() -> None:
    redis = ControlledRedis()
    redis.result = 0
    assert not asyncio.run(
        RedisRateLimiter(redis).allow(key="client", limit=1, window_seconds=1)
    )


@pytest.mark.parametrize(
    "namespace",
    ["", " bad", "bad space", "bad/segment", "x" * 65, "área"],
)
def test_rejects_unsafe_namespace(namespace: str) -> None:
    with pytest.raises(ValueError):
        RedisRateLimiter(ControlledRedis(), namespace=namespace)


@pytest.mark.parametrize(
    ("key", "limit", "window"),
    [
        ("", 1, 1),
        ("client", True, 1),
        ("client", 0, 1),
        ("client", 1_000_001, 1),
        ("client", 1, False),
        ("client", 1, 0),
        ("client", 1, 86_401),
    ],
)
def test_rejects_invalid_inputs_without_contacting_redis(
    key: str,
    limit: int,
    window: int,
) -> None:
    redis = ControlledRedis()
    limiter = RedisRateLimiter(redis)

    with pytest.raises(ValueError):
        asyncio.run(limiter.allow(key=key, limit=limit, window_seconds=window))

    assert redis.calls == []


def test_redis_key_is_stable_and_namespaces_are_isolated() -> None:
    first = ControlledRedis()
    second = ControlledRedis()

    asyncio.run(
        RedisRateLimiter(first, namespace="one").allow(
            key="same", limit=1, window_seconds=10
        )
    )
    asyncio.run(
        RedisRateLimiter(second, namespace="two").allow(
            key="same", limit=1, window_seconds=10
        )
    )

    first_key = first.calls[0][2][0]
    second_key = second.calls[0][2][0]
    assert first_key != second_key
    assert str(first_key).removeprefix("one") == str(second_key).removeprefix("two")

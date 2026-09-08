from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from redis.asyncio import Redis

from tradingagents.platform.api.redis_rate_limit import RedisRateLimiter

pytestmark = pytest.mark.integration


def test_real_redis_enforces_shared_atomic_limit_and_ttl() -> None:
    redis_url = os.environ.get("TRADINGAGENTS_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("TRADINGAGENTS_TEST_REDIS_URL não configurada")

    async def exercise() -> None:
        namespace = f"test-{uuid.uuid4().hex}"
        redis = Redis.from_url(redis_url, decode_responses=False)
        limiter = RedisRateLimiter(redis, namespace=namespace)
        try:
            outcomes = await asyncio.gather(
                *(limiter.allow(key="shared", limit=5, window_seconds=30) for _ in range(40))
            )
            keys = [key async for key in redis.scan_iter(match=f"{namespace}:rate-limit:*")]

            assert sum(outcomes) == 5
            assert len(keys) == 1
            assert await redis.pttl(keys[0]) > 0
        finally:
            keys = [key async for key in redis.scan_iter(match=f"{namespace}:rate-limit:*")]
            if keys:
                await redis.delete(*keys)
            await redis.aclose()

    asyncio.run(exercise())

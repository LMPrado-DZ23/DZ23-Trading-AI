from __future__ import annotations

import asyncio
import os

import pytest
from redis.asyncio import Redis

from tradingagents.platform.api.redis_rate_limit import RedisRateLimiter


@pytest.mark.integration
def test_real_redis_enforces_atomic_cross_instance_limit_and_expiry() -> None:
    redis_url = os.environ.get("TRADINGAGENTS_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("TRADINGAGENTS_TEST_REDIS_URL is not configured")

    async def exercise() -> None:
        first_client = Redis.from_url(redis_url, decode_responses=False)
        second_client = Redis.from_url(redis_url, decode_responses=False)
        await first_client.flushdb()
        try:
            first = RedisRateLimiter(first_client, namespace="integration-a5")
            second = RedisRateLimiter(second_client, namespace="integration-a5")
            decisions = await asyncio.gather(
                *(
                    (first if index % 2 == 0 else second).allow(
                        key="same-tenant-and-client",
                        limit=11,
                        window_seconds=2,
                    )
                    for index in range(80)
                )
            )
            assert sum(decisions) == 11

            keys = await first_client.keys("integration-a5:rate-limit:*")
            assert len(keys) == 1
            ttl_ms = await first_client.pttl(keys[0])
            assert 0 < ttl_ms <= 2_000
        finally:
            await first_client.aclose()
            await second_client.aclose()

    asyncio.run(exercise())

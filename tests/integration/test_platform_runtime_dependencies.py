from __future__ import annotations

import base64
import os

import pytest
from fastapi.testclient import TestClient

from tradingagents.platform.runtime import create_production_app
from tradingagents.platform.settings import PlatformSettings


@pytest.mark.integration
def test_runtime_readiness_checks_restricted_postgres_role_and_real_redis() -> None:
    database_url = os.environ.get("TRADINGAGENTS_TEST_POSTGRES_URL")
    redis_url = os.environ.get("TRADINGAGENTS_TEST_REDIS_URL")
    if not database_url or not redis_url:
        pytest.skip("PostgreSQL and Redis integration URLs are required")

    settings = PlatformSettings(
        environment="production",
        database_url=database_url,
        database_expected_role="tradingagents_app_test",
        public_url="https://trading.example.test",
        allowed_origins=["https://trading.example.test"],
        session_token_pepper="integration-runtime-master-secret-32-bytes-long",
        mfa_encryption_key_b64=base64.urlsafe_b64encode(b"m" * 32).decode(),
        redis_url=redis_url,
        smtp_host="smtp.example.test",
        smtp_username="mailer",
        smtp_password="test-only-smtp-password",
        smtp_sender="noreply@example.test",
        supabase_project_ref="fkswahrxvmueowcjisnp",
        supabase_url="https://fkswahrxvmueowcjisnp.supabase.co",
        supabase_publishable_key="sb_publishable_test-value",
        broker_credential_provider="openbao_transit",
        broker_transit_url="https://openbao:8200",
        broker_transit_token="test-only-transit-token",
    )

    with TestClient(create_production_app(settings), base_url=settings.public_url) as client:
        response = client.get("/health/ready")
        assert response.status_code == 200, response.text
        assert response.json() == {"status": "ready"}

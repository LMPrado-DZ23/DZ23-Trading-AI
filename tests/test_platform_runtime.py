from __future__ import annotations

import base64

from tradingagents.platform.runtime import _derive_secret, create_production_app
from tradingagents.platform.settings import PlatformSettings


def _settings() -> PlatformSettings:
    return PlatformSettings(
        environment="production",
        database_url=("postgresql+psycopg://tradingagents_app:test@db/app?sslmode=require"),
        worker_database_url=(
            "postgresql+psycopg://tradingagents_worker:test@db/app?sslmode=require"
        ),
        public_url="https://trading.dz23.online",
        allowed_origins=["https://trading.dz23.online"],
        trusted_proxy_networks=["172.16.0.0/12"],
        session_token_pepper="runtime-test-master-secret-32-bytes-long",
        mfa_encryption_key_b64=base64.urlsafe_b64encode(b"m" * 32).decode(),
        redis_url="redis://redis:6379/0",
        smtp_host="smtp.example.test",
        smtp_username="mailer",
        smtp_password="smtp-runtime-secret",
        smtp_sender="noreply@example.test",
        supabase_project_ref="fkswahrxvmueowcjisnp",
        supabase_url="https://fkswahrxvmueowcjisnp.supabase.co",
        supabase_publishable_key="sb_publishable_test-value",
        broker_credential_provider="openbao_transit",
        broker_transit_url="https://openbao:8200",
        broker_transit_token="test-only-transit-token",
        llm_transit_token="test-only-llm-transit-token",
    )


def test_composition_root_uses_explicit_production_dependencies_without_connecting() -> None:
    app = create_production_app(_settings())
    assert app.state.database_engine.hide_parameters is True
    assert app.state.redis.connection_pool.connection_kwargs["host"] == "redis"
    assert len(app.router.on_shutdown) == 1


def test_domain_separated_derived_secrets_are_stable_and_distinct() -> None:
    master = b"master-secret-with-at-least-32-bytes"
    csrf = _derive_secret(master, "csrf-signing")
    tokens = _derive_secret(master, "identity-token-hash")
    assert len(csrf) == 32
    assert csrf != tokens
    assert csrf == _derive_secret(master, "csrf-signing")

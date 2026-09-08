from __future__ import annotations

import base64
import importlib

import pytest
from pydantic import ValidationError

from tradingagents.platform.settings import Environment, PlatformSettings, WorkerSettings


def _production(**overrides):
    values = {
        "environment": Environment.PRODUCTION,
        "database_url": (
            "postgresql+psycopg://tradingagents_app:test@db/app?sslmode=verify-full"
        ),
        "public_url": "https://trading.dz23.online",
        "allowed_origins": ["https://trading.dz23.online"],
        "session_token_pepper": "x" * 32,
        "mfa_encryption_key_b64": base64.urlsafe_b64encode(b"m" * 32).decode(),
        "redis_url": "redis://redis:6379/0",
        "smtp_host": "smtp.example.test",
        "smtp_username": "mailer",
        "smtp_password": "smtp-secret",
        "smtp_sender": "noreply@example.test",
        "supabase_project_ref": "fkswahrxvmueowcjisnp",
        "supabase_url": "https://fkswahrxvmueowcjisnp.supabase.co",
        "supabase_publishable_key": "sb_publishable_test-value",
        "broker_credential_provider": "openbao_transit",
        "broker_transit_url": "https://openbao:8200",
        "broker_transit_token": "test-only-transit-token",
        "llm_transit_write_token": "test-only-llm-write-token",
        "worker_job_auth_key": "test-only-worker-job-auth-key-32-bytes",
        "llm_pricing": {
            "openai:gpt-5": {
                "input_usd_per_million": "1",
                "output_usd_per_million": "2",
            }
        },
        "monthly_cost_quota_microusd": 1_000_000,
    }
    values.update(overrides)
    return values


def test_production_settings_are_fail_closed() -> None:
    with pytest.raises(ValidationError, match="pricing catalog"):
        PlatformSettings(
            **_production(llm_pricing={}, monthly_cost_quota_microusd=None),
        )
    with pytest.raises(ValidationError, match="PostgreSQL"):
        PlatformSettings(
            **_production(database_url="sqlite+pysqlite:///app.db"),
        )

    with pytest.raises(ValidationError, match="HTTPS"):
        PlatformSettings(
            **_production(public_url="http://trading.dz23.online"),
        )
    with pytest.raises(ValidationError, match="dedicated runtime role"):
        PlatformSettings(
            **_production(
                database_url="postgresql+psycopg://postgres:test@db/app?sslmode=verify-full"
            )
        )
    with pytest.raises(ValidationError, match="sslmode"):
        PlatformSettings(
            **_production(database_url="postgresql+psycopg://tradingagents_app:test@db/app")
        )


def test_production_requires_strong_pepper_and_secure_origins() -> None:
    with pytest.raises(ValidationError, match="32 bytes"):
        PlatformSettings(
            **_production(session_token_pepper="short"),
        )

    with pytest.raises(ValidationError, match="HTTP origins"):
        PlatformSettings(
            **_production(allowed_origins=["http://trading.dz23.online"]),
        )


def test_production_requires_delivery_redis_and_exact_mfa_key() -> None:
    with pytest.raises(ValidationError, match="SMTP"):
        PlatformSettings(**_production(smtp_password=""))
    with pytest.raises(ValidationError, match="Redis"):
        PlatformSettings(**_production(redis_url="http://redis:6379"))
    with pytest.raises(ValidationError, match="Supabase Auth"):
        PlatformSettings(**_production(supabase_url="https://evil.supabase.co"))
    with pytest.raises(ValidationError, match="exactly 32 bytes"):
        PlatformSettings(
            **_production(mfa_encryption_key_b64=base64.urlsafe_b64encode(b"short").decode())
        )
    with pytest.raises(ValidationError, match="OpenBao Transit"):
        PlatformSettings(**_production(broker_credential_provider="local"))
    with pytest.raises(ValidationError, match="local broker master keys"):
        PlatformSettings(
            **_production(
                broker_credential_keys_json='{"local":"'
                + base64.urlsafe_b64encode(b"b" * 32).decode()
                + '"}'
            )
        )
    with pytest.raises(ValidationError, match="least-privilege capabilities"):
        PlatformSettings(
            **_production(llm_transit_write_token="test-only-transit-token"),
        )
    settings = PlatformSettings(**_production())
    assert settings.secure_cookies is True
    assert settings.mfa_encryption_key == b"m" * 32


def test_csv_environment_values_and_proxy_networks(monkeypatch) -> None:
    monkeypatch.setenv("TRADINGAGENTS_ALLOWED_ORIGINS", "https://one.example,https://two.example")
    monkeypatch.setenv("TRADINGAGENTS_TRUSTED_PROXY_NETWORKS", "127.0.0.1/32,10.0.0.0/8")
    settings = PlatformSettings()
    assert settings.allowed_origins == ["https://one.example", "https://two.example"]
    assert settings.trusted_proxy_networks == ["127.0.0.1/32", "10.0.0.0/8"]


def test_invalid_proxy_and_credentialed_url_are_rejected() -> None:
    with pytest.raises(ValidationError):
        PlatformSettings(trusted_proxy_networks=["not-a-network"])
    with pytest.raises(ValidationError):
        PlatformSettings(public_url="https://user:secret@example.test")


def test_secrets_are_not_exposed_in_repr() -> None:
    settings = PlatformSettings(session_token_pepper="never-show-this-secret-value-123456789")
    assert "never-show" not in repr(settings)


def test_worker_settings_have_no_identity_or_transit_write_capability() -> None:
    settings = WorkerSettings(
        environment="production",
        worker_database_url=(
            "postgresql+psycopg://tradingagents_worker:test@db/app?sslmode=verify-full"
        ),
        worker_job_auth_key="worker-only-job-auth-key-at-least-32-bytes",
        broker_credential_provider="openbao_transit",
        broker_transit_url="https://openbao:8200",
        llm_transit_decrypt_token="decrypt-only-test-token",
        llm_pricing={
            "openai:gpt-5": {
                "input_usd_per_million": 1,
                "output_usd_per_million": 2,
            }
        },
        monthly_cost_quota_microusd=1_000_000,
    )
    assert not hasattr(settings, "session_token_pepper")
    assert not hasattr(settings, "llm_transit_write_token")
    assert not hasattr(settings, "broker_transit_token")


def test_worker_storage_quotas_are_bounded_and_tenant_cap_covers_attempt() -> None:
    with pytest.raises(ValidationError, match="tenant storage quota"):
        WorkerSettings(
            worker_database_url="sqlite+pysqlite:///worker.db",
            worker_max_attempt_bytes=64 * 1024 * 1024,
            worker_max_tenant_bytes=32 * 1024 * 1024,
        )
    with pytest.raises(ValidationError):
        WorkerSettings(
            worker_database_url="sqlite+pysqlite:///worker.db",
            worker_max_attempt_bytes=1024,
        )


def test_remote_postgres_requires_hostname_verification_for_both_processes() -> None:
    with pytest.raises(ValidationError, match="verify-full"):
        PlatformSettings(
            **_production(
                database_url=(
                    "postgresql+psycopg://tradingagents_app:test@db/app?sslmode=require"
                )
            )
        )
    with pytest.raises(ValidationError, match="verify-full"):
        WorkerSettings(
            environment="production",
            worker_database_url=(
                "postgresql+psycopg://tradingagents_worker:test@db/app?sslmode=require"
            ),
            worker_job_auth_key="worker-only-job-auth-key-at-least-32-bytes",
            broker_credential_provider="openbao_transit",
            broker_transit_url="https://openbao:8200",
            llm_transit_decrypt_token="decrypt-only-test-token",
        )


def test_production_processes_reject_cross_boundary_secret_environment(monkeypatch) -> None:
    monkeypatch.setenv("TRADINGAGENTS_WORKER_DATABASE_URL", "postgresql://worker:secret@db/x")
    with pytest.raises(ValidationError, match="API process received forbidden"):
        PlatformSettings(**_production())
    monkeypatch.delenv("TRADINGAGENTS_WORKER_DATABASE_URL")
    monkeypatch.setenv("TRADINGAGENTS_SESSION_TOKEN_PEPPER", "identity-root-must-not-enter-worker")
    with pytest.raises(ValidationError, match="worker process received forbidden"):
        WorkerSettings(
            environment="production",
            worker_database_url=(
                "postgresql+psycopg://tradingagents_worker:test@db/app?sslmode=verify-full"
            ),
            worker_job_auth_key="worker-only-job-auth-key-at-least-32-bytes",
            broker_credential_provider="openbao_transit",
            broker_transit_url="https://openbao:8200",
            llm_transit_decrypt_token="decrypt-only-test-token",
        )


def test_capability_environment_rejection_is_case_insensitive(monkeypatch) -> None:
    module = importlib.import_module("tradingagents.platform.settings")
    monkeypatch.setattr(
        module.os,
        "environ",
        {"tradingagents_session_token_pepper": "must-not-enter-worker"},
    )
    with pytest.raises(ValueError, match="forbidden secret capabilities"):
        module._reject_environment_capabilities(
            ("TRADINGAGENTS_SESSION_TOKEN_PEPPER",),
            "worker",
        )

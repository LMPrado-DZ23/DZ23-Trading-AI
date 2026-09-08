from __future__ import annotations

import base64

import pytest
from pydantic import ValidationError

from tradingagents.platform.settings import Environment, PlatformSettings


def _production(**overrides):
    values = {
        "environment": Environment.PRODUCTION,
        "database_url": (
            "postgresql+psycopg://tradingagents_app:test@db/app?sslmode=require"
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
    }
    values.update(overrides)
    return values


def test_production_settings_are_fail_closed() -> None:
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
                database_url="postgresql+psycopg://postgres:test@db/app?sslmode=require"
            )
        )
    with pytest.raises(ValidationError, match="sslmode"):
        PlatformSettings(
            **_production(
                database_url="postgresql+psycopg://tradingagents_app:test@db/app"
            )
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

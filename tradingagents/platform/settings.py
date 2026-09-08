"""Typed, fail-closed runtime settings for the A5 platform."""

from __future__ import annotations

import base64
import ipaddress
import re
from enum import StrEnum
from typing import Annotated, Self
from urllib.parse import urlparse

from pydantic import BeforeValidator, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Environment(StrEnum):
    TEST = "test"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return value


CsvList = Annotated[list[str], NoDecode, BeforeValidator(_split_csv)]
_DATABASE_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{2,62}$")


class PlatformSettings(BaseSettings):
    """Configuration loaded once at the composition root, never from domain code."""

    model_config = SettingsConfigDict(
        env_prefix="TRADINGAGENTS_",
        env_file=None,
        extra="ignore",
        case_sensitive=False,
        frozen=True,
    )

    environment: Environment = Environment.DEVELOPMENT
    database_url: SecretStr = SecretStr("sqlite+pysqlite:///:memory:")
    database_expected_role: str = "tradingagents_app"
    public_url: str = "http://localhost:8080"
    session_token_pepper: SecretStr = SecretStr("development-only-session-pepper")
    mfa_encryption_key_b64: SecretStr = SecretStr("")
    redis_url: SecretStr = SecretStr("redis://127.0.0.1:6379/0")
    smtp_host: str = ""
    smtp_port: int = Field(default=465, ge=1, le=65_535)
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_sender: str = ""
    smtp_implicit_tls: bool = True
    supabase_project_ref: str = ""
    supabase_url: str = ""
    supabase_publishable_key: SecretStr = SecretStr("")
    allowed_origins: CsvList = Field(default_factory=lambda: ["http://localhost:8080"])
    trusted_proxy_networks: CsvList = Field(default_factory=list)
    session_ttl_seconds: int = Field(default=43_200, ge=300, le=2_592_000)
    recovery_ttl_seconds: int = Field(default=1_800, ge=300, le=86_400)
    max_auth_failures: int = Field(default=5, ge=3, le=20)
    auth_lock_seconds: int = Field(default=900, ge=60, le=86_400)

    @field_validator("public_url")
    @classmethod
    def validate_public_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("public_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("public_url cannot contain credentials, query or fragment")
        return value.rstrip("/")

    @field_validator("allowed_origins")
    @classmethod
    def validate_origins(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("at least one allowed origin is required")
        for value in values:
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("allowed origins must be absolute HTTP(S) origins")
            if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                raise ValueError("allowed origins cannot include paths, query or fragment")
        return values

    @field_validator("trusted_proxy_networks")
    @classmethod
    def validate_proxy_networks(cls, values: list[str]) -> list[str]:
        for value in values:
            ipaddress.ip_network(value, strict=False)
        return values

    @field_validator("database_expected_role")
    @classmethod
    def validate_database_role(cls, value: str) -> str:
        if not _DATABASE_ROLE_RE.fullmatch(value):
            raise ValueError("database_expected_role must be a safe PostgreSQL role name")
        return value

    @model_validator(mode="after")
    def validate_environment_constraints(self) -> Self:
        database_url = self.database_url.get_secret_value()
        pepper = self.session_token_pepper.get_secret_value()
        mfa_key = self.mfa_encryption_key_b64.get_secret_value()
        if len(pepper.encode("utf-8")) < 32 and self.environment in {
            Environment.STAGING,
            Environment.PRODUCTION,
        }:
            raise ValueError("session_token_pepper must contain at least 32 bytes")

        if self.environment in {Environment.STAGING, Environment.PRODUCTION}:
            if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
                raise ValueError("PostgreSQL is mandatory outside development/test")
            database = urlparse(database_url.replace("postgresql+psycopg", "postgresql", 1))
            if not database.hostname or not database.username:
                raise ValueError("PostgreSQL URL must include host and runtime role")
            if database.username != self.database_expected_role:
                raise ValueError("PostgreSQL URL must authenticate as the dedicated runtime role")
            if database.hostname not in {"localhost", "127.0.0.1", "::1"}:
                query = dict(
                    item.split("=", 1)
                    for item in database.query.split("&")
                    if "=" in item
                )
                if query.get("sslmode") not in {"require", "verify-ca", "verify-full"}:
                    raise ValueError("remote PostgreSQL requires sslmode=require or stronger")
            if not self.public_url.startswith("https://"):
                raise ValueError("HTTPS public_url is mandatory outside development/test")
            if any(origin.startswith("http://") for origin in self.allowed_origins):
                raise ValueError("HTTP origins are forbidden outside development/test")
            if not self.redis_url.get_secret_value().startswith(("redis://", "rediss://")):
                raise ValueError("Redis is mandatory outside development/test")
            if not all(
                (
                    self.smtp_host.strip(),
                    self.smtp_username.strip(),
                    self.smtp_password.get_secret_value(),
                    self.smtp_sender.strip(),
                )
            ):
                raise ValueError("SMTP delivery is mandatory outside development/test")
            expected_supabase_url = (
                f"https://{self.supabase_project_ref.strip().lower()}.supabase.co"
            )
            if (
                len(self.supabase_project_ref.strip()) < 10
                or self.supabase_url.rstrip("/") != expected_supabase_url
                or not self.supabase_publishable_key.get_secret_value().startswith(
                    "sb_publishable_"
                )
            ):
                raise ValueError("Supabase Auth configuration is mandatory outside development/test")
            try:
                decoded_key = base64.urlsafe_b64decode(mfa_key + "=" * (-len(mfa_key) % 4))
            except (ValueError, base64.binascii.Error) as exc:
                raise ValueError("mfa_encryption_key_b64 must be valid URL-safe base64") from exc
            if len(decoded_key) != 32:
                raise ValueError("mfa_encryption_key_b64 must decode to exactly 32 bytes")
        return self

    @property
    def mfa_encryption_key(self) -> bytes:
        value = self.mfa_encryption_key_b64.get_secret_value()
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        if len(decoded) != 32:
            raise ValueError("mfa_encryption_key_b64 must decode to exactly 32 bytes")
        return decoded

    @property
    def secure_cookies(self) -> bool:
        return self.environment in {Environment.STAGING, Environment.PRODUCTION}

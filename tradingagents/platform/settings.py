"""Typed, fail-closed runtime settings for the A5 platform."""

from __future__ import annotations

import base64
import hmac
import ipaddress
import json
import os
import re
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self
from urllib.parse import urlparse

from pydantic import BeforeValidator, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from tradingagents.platform.usage import PricingCatalog


class Environment(StrEnum):
    TEST = "test"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class BrokerCredentialProvider(StrEnum):
    LOCAL = "local"
    OPENBAO_TRANSIT = "openbao_transit"


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return value


CsvList = Annotated[list[str], NoDecode, BeforeValidator(_split_csv)]
_DATABASE_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{2,62}$")


def _reject_environment_capabilities(names: tuple[str, ...], boundary: str) -> None:
    ambient = {
        key.casefold(): key
        for key, value in os.environ.items()
        if isinstance(value, str) and value
    }
    present = sorted(
        ambient[name.casefold()] for name in names if name.casefold() in ambient
    )
    if present:
        raise ValueError(
            f"{boundary} process received forbidden secret capabilities: {','.join(present)}"
        )


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
    worker_id: str = "analysis-worker-1"
    worker_runtime_root: str = "/data/worker-runs"
    worker_lease_seconds: int = Field(default=120, ge=30, le=900)
    worker_poll_seconds: float = Field(default=2.0, ge=0.1, le=30.0)
    llm_request_timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    public_url: str = "http://localhost:8080"
    session_token_pepper: SecretStr = SecretStr("development-only-session-pepper")
    worker_job_auth_key: SecretStr = SecretStr(
        "development-only-worker-job-auth-key"
    )
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
    broker_credential_keys_json: SecretStr = SecretStr("")
    broker_active_credential_key_id: str = "broker-credentials-v1"
    broker_credential_provider: BrokerCredentialProvider = BrokerCredentialProvider.LOCAL
    broker_transit_url: str = ""
    broker_transit_key_name: str = "tradingagents-broker-credentials"
    llm_transit_key_name: str = "tradingagents-llm-credentials"
    broker_transit_token: SecretStr = SecretStr("")
    llm_transit_write_token: SecretStr = SecretStr("")
    broker_transit_namespace: str = ""
    allowed_origins: CsvList = Field(default_factory=lambda: ["http://localhost:8080"])
    trusted_proxy_networks: CsvList = Field(default_factory=list)
    llm_pricing: dict[str, dict[str, Decimal]] = Field(default_factory=dict)
    max_active_jobs_per_tenant: int = Field(default=20, ge=1, le=1000)
    monthly_token_quota: int = Field(default=5_000_000, ge=10_000, le=10_000_000_000)
    monthly_cost_quota_microusd: int | None = Field(default=None, ge=10_000, le=1_000_000_000_000)
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

    @field_validator("worker_id")
    @classmethod
    def validate_worker_id(cls, value: str) -> str:
        if not 3 <= len(value) <= 120 or any(ord(character) < 33 for character in value):
            raise ValueError("worker_id is invalid")
        return value

    @field_validator("llm_pricing")
    @classmethod
    def validate_llm_pricing(
        cls, value: dict[str, dict[str, Decimal]]
    ) -> dict[str, dict[str, Decimal]]:
        if len(value) > 500:
            raise ValueError("LLM pricing catalog is too large")
        PricingCatalog(value)
        return value

    @model_validator(mode="after")
    def validate_environment_constraints(self) -> Self:
        database_url = self.database_url.get_secret_value()
        pepper = self.session_token_pepper.get_secret_value()
        worker_job_auth = self.worker_job_auth_key.get_secret_value()
        mfa_key = self.mfa_encryption_key_b64.get_secret_value()
        if len(pepper.encode("utf-8")) < 32 and self.environment in {
            Environment.STAGING,
            Environment.PRODUCTION,
        }:
            raise ValueError("session_token_pepper must contain at least 32 bytes")
        if self.environment in {Environment.STAGING, Environment.PRODUCTION}:
            _reject_environment_capabilities(
                (
                    "TRADINGAGENTS_WORKER_DATABASE_URL",
                    "TRADINGAGENTS_LLM_TRANSIT_DECRYPT_TOKEN",
                ),
                "API",
            )
            if len(worker_job_auth.encode("utf-8")) < 32:
                raise ValueError("worker_job_auth_key must contain at least 32 bytes")
            if hmac.compare_digest(pepper, worker_job_auth):
                raise ValueError("worker_job_auth_key must be independent from identity secrets")

        if self.environment in {Environment.STAGING, Environment.PRODUCTION}:
            if self.llm_request_timeout_seconds >= self.worker_lease_seconds / 2:
                raise ValueError("LLM request timeout must be less than half the worker lease")
            if self.monthly_cost_quota_microusd is None or not self.llm_pricing:
                raise ValueError(
                    "production requires an exact LLM pricing catalog and monthly cost quota"
                )
            if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
                raise ValueError("PostgreSQL is mandatory outside development/test")
            database = urlparse(database_url.replace("postgresql+psycopg", "postgresql", 1))
            if not database.hostname or not database.username:
                raise ValueError("PostgreSQL URL must include host and runtime role")
            if database.username != self.database_expected_role:
                raise ValueError("PostgreSQL URL must authenticate as the dedicated runtime role")
            if database.hostname not in {"localhost", "127.0.0.1", "::1"}:
                query = dict(
                    item.split("=", 1) for item in database.query.split("&") if "=" in item
                )
                if query.get("sslmode") != "verify-full":
                    raise ValueError("remote PostgreSQL requires sslmode=verify-full")
            if not self.public_url.startswith("https://"):
                raise ValueError("HTTPS public_url is mandatory outside development/test")
            if self.broker_transit_key_name == self.llm_transit_key_name:
                raise ValueError("broker and LLM credentials require separate Transit keys")
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
                raise ValueError(
                    "Supabase Auth configuration is mandatory outside development/test"
                )
            if self.broker_credential_provider is not BrokerCredentialProvider.OPENBAO_TRANSIT:
                raise ValueError("external OpenBao Transit is mandatory outside development/test")
            if self.broker_credential_keys_json.get_secret_value():
                raise ValueError("local broker master keys are forbidden with OpenBao Transit")
            transit_url = urlparse(self.broker_transit_url)
            if (
                transit_url.scheme != "https"
                or not transit_url.hostname
                or transit_url.username
                or transit_url.password
                or transit_url.query
                or transit_url.fragment
                or transit_url.path not in {"", "/"}
                or not self.broker_transit_token.get_secret_value()
                or not self.llm_transit_write_token.get_secret_value()
            ):
                raise ValueError("secure OpenBao Transit configuration is mandatory")
            capability_tokens = (
                self.broker_transit_token.get_secret_value(),
                self.llm_transit_write_token.get_secret_value(),
            )
            if any(
                hmac.compare_digest(left, right)
                for index, left in enumerate(capability_tokens)
                for right in capability_tokens[index + 1 :]
            ):
                raise ValueError("Transit tokens require separate least-privilege capabilities")
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

    @property
    def broker_credential_keys(self) -> dict[str, bytes]:
        raw = self.broker_credential_keys_json.get_secret_value()
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("broker_credential_keys_json must be a JSON object") from exc
        if not isinstance(payload, dict) or self.broker_active_credential_key_id not in payload:
            raise ValueError("active broker credential key must exist in the keyring")
        decoded: dict[str, bytes] = {}
        for key_id, encoded in payload.items():
            if not isinstance(key_id, str) or not isinstance(encoded, str):
                raise ValueError("broker credential keyring entries must be strings")
            try:
                key = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            except (ValueError, base64.binascii.Error) as exc:
                raise ValueError("broker credential key is not valid base64") from exc
            if len(key) != 32:
                raise ValueError("broker credential keys must decode to exactly 32 bytes")
            decoded[key_id] = key
        return decoded


class WorkerSettings(BaseSettings):
    """Least-privilege settings for the isolated worker process.

    Identity, session, CSRF, MFA, SMTP, Supabase and Transit-encrypt secrets are
    deliberately absent so they cannot be loaded into the worker address space.
    """

    model_config = SettingsConfigDict(
        env_prefix="TRADINGAGENTS_",
        env_file=None,
        extra="ignore",
        case_sensitive=False,
        frozen=True,
    )

    environment: Environment = Environment.DEVELOPMENT
    worker_database_url: SecretStr = SecretStr("")
    worker_job_auth_key: SecretStr = SecretStr(
        "development-only-worker-job-auth-key"
    )
    worker_id: str = "analysis-worker-1"
    worker_runtime_root: str = "/data/worker-runs"
    worker_lease_seconds: int = Field(default=120, ge=30, le=900)
    worker_poll_seconds: float = Field(default=2.0, ge=0.1, le=30.0)
    llm_request_timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    worker_max_attempt_bytes: int = Field(
        default=256 * 1024 * 1024,
        ge=16 * 1024 * 1024,
        le=1024 * 1024 * 1024,
    )
    worker_max_tenant_bytes: int = Field(
        default=1024 * 1024 * 1024,
        ge=16 * 1024 * 1024,
        le=16 * 1024 * 1024 * 1024,
    )
    broker_credential_provider: BrokerCredentialProvider = BrokerCredentialProvider.LOCAL
    broker_transit_url: str = ""
    llm_transit_key_name: str = "tradingagents-llm-credentials"
    llm_transit_decrypt_token: SecretStr = SecretStr("")
    broker_transit_namespace: str = ""
    llm_pricing: dict[str, dict[str, Decimal]] = Field(default_factory=dict)
    max_active_jobs_per_tenant: int = Field(default=20, ge=1, le=1000)
    monthly_token_quota: int = Field(default=5_000_000, ge=10_000, le=10_000_000_000)
    monthly_cost_quota_microusd: int | None = Field(
        default=None,
        ge=10_000,
        le=1_000_000_000_000,
    )

    @field_validator("worker_id")
    @classmethod
    def validate_worker_id(cls, value: str) -> str:
        if not 3 <= len(value) <= 120 or any(ord(character) < 33 for character in value):
            raise ValueError("worker_id is invalid")
        return value

    @field_validator("llm_pricing")
    @classmethod
    def validate_llm_pricing(
        cls,
        value: dict[str, dict[str, Decimal]],
    ) -> dict[str, dict[str, Decimal]]:
        if len(value) > 500:
            raise ValueError("LLM pricing catalog is too large")
        PricingCatalog(value)
        return value

    @model_validator(mode="after")
    def validate_worker_boundary(self) -> Self:
        database_url = self.worker_database_url.get_secret_value()
        auth_key = self.worker_job_auth_key.get_secret_value()
        if not database_url:
            raise ValueError("worker_database_url is required")
        if self.worker_max_tenant_bytes < self.worker_max_attempt_bytes:
            raise ValueError("tenant storage quota cannot be below attempt quota")
        if self.environment in {Environment.STAGING, Environment.PRODUCTION}:
            _reject_environment_capabilities(
                (
                    "TRADINGAGENTS_DATABASE_URL",
                    "TRADINGAGENTS_SESSION_TOKEN_PEPPER",
                    "TRADINGAGENTS_MFA_ENCRYPTION_KEY_B64",
                    "TRADINGAGENTS_SMTP_PASSWORD",
                    "TRADINGAGENTS_BROKER_TRANSIT_TOKEN",
                    "TRADINGAGENTS_LLM_TRANSIT_WRITE_TOKEN",
                    "TRADINGAGENTS_SUPABASE_PUBLISHABLE_KEY",
                ),
                "worker",
            )
            parsed = urlparse(database_url.replace("postgresql+psycopg", "postgresql", 1))
            if (
                not database_url.startswith(("postgresql://", "postgresql+psycopg://"))
                or parsed.username != "tradingagents_worker"
                or not parsed.hostname
            ):
                raise ValueError("worker_database_url must use the dedicated worker role")
            if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                query = dict(
                    item.split("=", 1) for item in parsed.query.split("&") if "=" in item
                )
                if query.get("sslmode") != "verify-full":
                    raise ValueError("remote worker PostgreSQL requires sslmode=verify-full")
            if len(auth_key.encode("utf-8")) < 32:
                raise ValueError("worker_job_auth_key must contain at least 32 bytes")
            if self.llm_request_timeout_seconds >= self.worker_lease_seconds / 2:
                raise ValueError("LLM request timeout must be less than half the worker lease")
            if self.monthly_cost_quota_microusd is None or not self.llm_pricing:
                raise ValueError(
                    "production worker requires an exact LLM pricing catalog and monthly cost quota"
                )
            transit = urlparse(self.broker_transit_url)
            if (
                self.broker_credential_provider is not BrokerCredentialProvider.OPENBAO_TRANSIT
                or transit.scheme != "https"
                or not transit.hostname
                or transit.username
                or transit.password
                or transit.query
                or transit.fragment
                or transit.path not in {"", "/"}
                or not self.llm_transit_decrypt_token.get_secret_value()
            ):
                raise ValueError("worker requires a decrypt-only OpenBao Transit capability")
        return self

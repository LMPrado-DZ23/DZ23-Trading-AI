"""Strict public API schemas for the identity slice."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
Password = Annotated[str, Field(min_length=12, max_length=128)]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RegisterRequest(ApiModel):
    email: str = Field(min_length=3, max_length=254)
    password: Password
    display_name: str = Field(min_length=2, max_length=120)
    organization_name: str = Field(min_length=2, max_length=160)
    organization_slug: str = Field(
        min_length=3, max_length=80, pattern=r"^[a-z0-9](?:[a-z0-9-]{1,78}[a-z0-9])?$"
    )

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.casefold()
        if not _EMAIL.fullmatch(normalized):
            raise ValueError("email inválido")
        return normalized


class LoginRequest(ApiModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)
    organization_slug: str = Field(
        min_length=3, max_length=80, pattern=r"^[a-z0-9](?:[a-z0-9-]{1,78}[a-z0-9])?$"
    )

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.casefold()
        if not _EMAIL.fullmatch(normalized):
            raise ValueError("email inválido")
        return normalized


class SupabaseExchangeRequest(ApiModel):
    organization_slug: str = Field(
        min_length=3, max_length=80, pattern=r"^[a-z0-9](?:[a-z0-9-]{1,78}[a-z0-9])?$"
    )
    organization_name: str | None = Field(default=None, min_length=2, max_length=160)
    display_name: str | None = Field(default=None, min_length=2, max_length=120)


class RecoveryRequest(ApiModel):
    email: str = Field(min_length=3, max_length=254)
    organization_slug: str = Field(
        min_length=3, max_length=80, pattern=r"^[a-z0-9](?:[a-z0-9-]{1,78}[a-z0-9])?$"
    )

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.casefold()
        if not _EMAIL.fullmatch(normalized):
            raise ValueError("email inválido")
        return normalized


class ResetPasswordRequest(ApiModel):
    recovery_token: str = Field(min_length=32, max_length=2048)
    new_password: Password


class VerificationTokenRequest(ApiModel):
    verification_token: str = Field(min_length=32, max_length=2048)


class PrincipalResponse(ApiModel):
    user_id: UUID
    tenant_id: UUID
    email: str
    display_name: str
    roles: tuple[str, ...]
    email_verified: bool


class MessageResponse(ApiModel):
    message: str


class HealthResponse(ApiModel):
    status: str


class CsrfResponse(ApiModel):
    csrf_token: str


class TermsResponse(ApiModel):
    id: UUID
    document_type: str
    version: str
    content_sha256: str
    publication_uri: str
    published_at: datetime


class ConsentRequest(ApiModel):
    terms_document_id: UUID
    decision: Literal["accepted", "withdrawn"]


class ConsentResponse(ApiModel):
    id: UUID
    terms_document_id: UUID
    decision: str
    recorded_at: datetime


class PrivacyActionRequest(ApiModel):
    kind: Literal["export", "delete"]


class PrivacyResponse(ApiModel):
    id: UUID
    kind: str
    status: str
    requested_at: datetime
    due_at: datetime
    completed_at: datetime | None


class MfaEnrollRequest(ApiModel):
    account_label: str = Field(min_length=3, max_length=320)


class MfaEnrollmentResponse(ApiModel):
    secret: str
    provisioning_uri: str


class MfaActivateRequest(ApiModel):
    code: str = Field(pattern=r"^\d{6}$")


class MfaActivationResponse(ApiModel):
    recovery_codes: tuple[str, ...]


class MfaVerifyRequest(ApiModel):
    code: str | None = Field(default=None, pattern=r"^\d{6}$")
    recovery_code: str | None = Field(default=None, min_length=16, max_length=32)

    @field_validator("recovery_code")
    @classmethod
    def normalize_recovery_code(cls, value: str | None) -> str | None:
        return value.upper() if value else None

    @model_validator(mode="after")
    def exactly_one_method(self):
        if (self.code is None) == (self.recovery_code is None):
            raise ValueError("informe exatamente um método MFA")
        return self


class MfaVerificationResponse(ApiModel):
    method: str
    verified_at: datetime
    valid_until: datetime


class ExchangeCatalogResponse(ApiModel):
    slug: str
    display_name: str
    has_real_testnet: bool
    required_fields: tuple[str, ...]
    live_permission_probe: bool


class BrokerConnectionRequest(ApiModel):
    label: str = Field(min_length=2, max_length=80)
    exchange_slug: str = Field(min_length=2, max_length=40, pattern=r"^[a-z0-9]+$")
    environment: Literal["testnet", "live"]
    api_key: SecretStr = Field(min_length=4, max_length=4096)
    secret: SecretStr = Field(min_length=4, max_length=4096)
    passphrase: SecretStr | None = Field(default=None, min_length=1, max_length=4096)
    uid: SecretStr | None = Field(default=None, min_length=1, max_length=4096)


class BrokerConnectionResponse(ApiModel):
    id: UUID
    label: str
    exchange_slug: str
    display_name: str
    environment: str
    status: str
    credential_version: int
    capability_digest: str
    ccxt_version: str | None
    permission_status: str
    last_diagnostic_code: str | None
    last_checked_at: datetime | None


class BrokerDiagnosticResponse(ApiModel):
    success: bool
    code: str
    market_count: int
    nonzero_asset_count: int
    permissions_verified: bool
    permissions_safe: bool
    permission_status: str
    spot_only: bool
    adapter_version: str


class AnalysisJobRequest(ApiModel):
    symbol: str = Field(min_length=1, max_length=64)
    asset_type: Literal["stock", "crypto"]
    trade_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    market_calendar: Literal["24/7", "XNYS", "BVMF"] | None = None
    mode: Literal["backtest", "shadow", "paper"]
    provider: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_-]+$")
    model: str = Field(min_length=1, max_length=160)
    llm_connection_id: UUID
    llm_credential_version: int = Field(ge=1)
    selected_analysts: tuple[Literal["market"], ...] = ("market",)
    config: dict[str, Any] = Field(default_factory=dict)
    token_budget: int = Field(default=250_000, ge=1_000, le=2_000_000)
    cost_budget_microusd: int | None = Field(default=None, ge=1_000, le=1_000_000_000)


class AnalysisJobCreatedResponse(ApiModel):
    id: UUID
    message: str


class AnalysisJobResponse(ApiModel):
    id: UUID
    run_id: UUID
    status: str
    symbol: str
    asset_type: str
    trade_date: str
    mode: str
    attempts: int
    max_attempts: int
    error_code: str | None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    llm_requests: int
    unmetered_llm_requests: int
    estimated_cost_microusd: int | None
    pricing_version: str | None
    token_budget: int
    cost_budget_microusd: int | None
    created_at: datetime
    completed_at: datetime | None


class AnalysisJobDetailResponse(AnalysisJobResponse):
    result: dict[str, Any] | None
    manifest: dict[str, Any] | None


class LlmProviderResponse(ApiModel):
    slug: str
    display_name: str
    requires_endpoint: bool


class LlmConnectionRequest(ApiModel):
    label: str = Field(min_length=2, max_length=80)
    provider: str = Field(min_length=2, max_length=40, pattern=r"^[a-z0-9_-]+$")
    api_key: SecretStr = Field(min_length=8, max_length=4096)
    base_url: str | None = Field(default=None, min_length=8, max_length=2048)


class LlmConnectionResponse(ApiModel):
    id: UUID
    label: str
    provider: str
    display_name: str
    base_url: str | None
    status: str
    credential_version: int
    created_at: datetime

"""Strict public API schemas for the identity slice."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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

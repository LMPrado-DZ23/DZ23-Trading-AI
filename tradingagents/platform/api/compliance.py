"""Application port and immutable DTOs for compliance HTTP routes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from tradingagents.platform.api.identity import CommandContext, IdentityPrincipal


@dataclass(frozen=True, slots=True)
class TermsView:
    id: UUID
    document_type: str
    version: str
    content_sha256: str
    publication_uri: str
    published_at: datetime


@dataclass(frozen=True, slots=True)
class ConsentView:
    id: UUID
    terms_document_id: UUID
    decision: str
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class PrivacyView:
    id: UUID
    kind: str
    status: str
    requested_at: datetime
    due_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class MfaEnrollmentView:
    secret: str
    provisioning_uri: str


@dataclass(frozen=True, slots=True)
class MfaActivationView:
    recovery_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MfaVerificationView:
    method: str
    verified_at: datetime
    valid_until: datetime


@runtime_checkable
class ComplianceServicePort(Protocol):
    async def latest_terms(self, *, document_type: str) -> TermsView | None: ...

    async def record_consent(
        self,
        *,
        token: str,
        principal: IdentityPrincipal,
        terms_document_id: UUID,
        decision: str,
        context: CommandContext,
    ) -> UUID: ...

    async def list_consents(
        self, *, token: str, principal: IdentityPrincipal
    ) -> tuple[ConsentView, ...]: ...

    async def request_privacy_action(
        self, *, token: str, principal: IdentityPrincipal, kind: str
    ) -> PrivacyView: ...

    async def get_privacy_request(
        self, *, token: str, principal: IdentityPrincipal, request_id: UUID
    ) -> PrivacyView: ...

    async def enroll_mfa(
        self, *, token: str, principal: IdentityPrincipal, account_label: str
    ) -> MfaEnrollmentView: ...

    async def activate_mfa(
        self, *, token: str, principal: IdentityPrincipal, code: str
    ) -> MfaActivationView: ...

    async def verify_mfa(
        self,
        *,
        token: str,
        principal: IdentityPrincipal,
        code: str | None,
        recovery_code: str | None,
    ) -> MfaVerificationView: ...

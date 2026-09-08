"""Async adapter for compliance and MFA domain services."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from uuid import UUID

from anyio import to_thread

from tradingagents.platform.api.compliance import (
    ConsentView,
    MfaActivationView,
    MfaEnrollmentView,
    MfaVerificationView,
    PrivacyView,
    TermsView,
)
from tradingagents.platform.api.identity import (
    CommandContext,
    IdentityError as ApiIdentityError,
    IdentityPrincipal as ApiIdentityPrincipal,
)
from tradingagents.platform.compliance import (
    ComplianceError,
    ComplianceService,
    ConsentDecision,
    MfaService,
    PrivacyRequestKind,
)
from tradingagents.platform.identity import IdentityError, IdentityPrincipal, IdentityService


class SqlAlchemyComplianceAdapter:
    def __init__(
        self,
        *,
        identity: IdentityService,
        compliance: ComplianceService,
        mfa: MfaService,
    ) -> None:
        self._identity = identity
        self._compliance = compliance
        self._mfa = mfa

    async def latest_terms(self, *, document_type: str) -> TermsView | None:
        row = await _run_sync(self._compliance.latest_terms, document_type=document_type)
        if row is None:
            return None
        return TermsView(
            id=row.id,
            document_type=row.document_type,
            version=row.version,
            content_sha256=row.content_sha256,
            publication_uri=row.publication_uri,
            published_at=row.published_at,
        )

    async def record_consent(
        self,
        *,
        token: str,
        principal: ApiIdentityPrincipal,
        terms_document_id: UUID,
        decision: str,
        context: CommandContext,
    ) -> UUID:
        actor = await self._principal(token, principal)
        try:
            return await _run_sync(
                self._compliance.record_consent,
                principal=actor,
                terms_document_id=terms_document_id,
                decision=ConsentDecision(decision),
                client_ip=context.client_ip,
                user_agent=context.user_agent,
            )
        except (ComplianceError, ValueError) as exc:
            raise ApiIdentityError("COMPLIANCE_INVALID") from exc

    async def list_consents(
        self, *, token: str, principal: ApiIdentityPrincipal
    ) -> tuple[ConsentView, ...]:
        actor = await self._principal(token, principal)
        rows = await _run_sync(self._compliance.list_consents, principal=actor)
        return tuple(
            ConsentView(
                id=row.id,
                terms_document_id=row.terms_document_id,
                decision=row.decision,
                recorded_at=row.recorded_at,
            )
            for row in rows
        )

    async def request_privacy_action(
        self, *, token: str, principal: ApiIdentityPrincipal, kind: str
    ) -> PrivacyView:
        actor = await self._principal(token, principal)
        try:
            request_id = await _run_sync(
                self._compliance.request_privacy_action,
                principal=actor,
                kind=PrivacyRequestKind(kind),
            )
            return await self.get_privacy_request(
                token=token,
                principal=principal,
                request_id=request_id,
            )
        except (ComplianceError, ValueError) as exc:
            raise ApiIdentityError("PRIVACY_INVALID") from exc

    async def get_privacy_request(
        self,
        *,
        token: str,
        principal: ApiIdentityPrincipal,
        request_id: UUID,
    ) -> PrivacyView:
        actor = await self._principal(token, principal)
        try:
            row = await _run_sync(
                self._compliance.get_privacy_request,
                principal=actor,
                request_id=request_id,
            )
        except ComplianceError as exc:
            raise ApiIdentityError("PRIVACY_NOT_FOUND") from exc
        return PrivacyView(
            id=row.id,
            kind=row.kind,
            status=row.status,
            requested_at=row.requested_at,
            due_at=row.due_at,
            completed_at=row.completed_at,
        )

    async def enroll_mfa(
        self, *, token: str, principal: ApiIdentityPrincipal, account_label: str
    ) -> MfaEnrollmentView:
        actor = await self._principal(token, principal, require_verified_email=True)
        try:
            enrollment = await _run_sync(
                self._mfa.enroll,
                principal=actor,
                account_label=account_label,
            )
            return MfaEnrollmentView(
                secret=enrollment.secret,
                provisioning_uri=enrollment.provisioning_uri,
            )
        except (ComplianceError, ValueError) as exc:
            raise ApiIdentityError("MFA_ENROLLMENT_INVALID") from exc

    async def activate_mfa(
        self, *, token: str, principal: ApiIdentityPrincipal, code: str
    ) -> MfaActivationView:
        actor = await self._principal(token, principal, require_verified_email=True)
        try:
            activation = await _run_sync(self._mfa.activate, principal=actor, code=code)
            return MfaActivationView(recovery_codes=activation.recovery_codes)
        except (ComplianceError, ValueError) as exc:
            raise ApiIdentityError("MFA_VERIFICATION_FAILED") from exc

    async def verify_mfa(
        self,
        *,
        token: str,
        principal: ApiIdentityPrincipal,
        code: str | None,
        recovery_code: str | None,
    ) -> MfaVerificationView:
        actor = await self._principal(token, principal, require_verified_email=True)
        try:
            verification = await _run_sync(
                self._mfa.verify_step_up,
                principal=actor,
                code=code,
                recovery_code=recovery_code,
            )
            return MfaVerificationView(
                method=verification.method,
                verified_at=verification.verified_at,
                valid_until=verification.valid_until,
            )
        except (ComplianceError, ValueError) as exc:
            raise ApiIdentityError("MFA_VERIFICATION_FAILED") from exc

    async def _principal(
        self,
        token: str,
        expected: ApiIdentityPrincipal,
        *,
        require_verified_email: bool = False,
    ) -> IdentityPrincipal:
        try:
            profile = await _run_sync(self._identity.authenticate_session_token, token=token)
        except IdentityError as exc:
            raise ApiIdentityError("SESSION_INVALID") from exc
        if profile.user_id != expected.user_id or profile.tenant_id != expected.tenant_id:
            raise ApiIdentityError("SESSION_INVALID")
        if require_verified_email and not profile.email_verified:
            raise ApiIdentityError("EMAIL_VERIFICATION_REQUIRED")
        return IdentityPrincipal(
            tenant_id=profile.tenant_id,
            user_id=profile.user_id,
            membership_id=profile.membership_id,
            session_id=profile.session_id,
            role=profile.role,
        )


async def _run_sync(function: Callable, /, *args, **kwargs):
    return await to_thread.run_sync(partial(function, *args, **kwargs))

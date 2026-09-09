"""Tenant-safe compliance and MFA application services."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tradingagents.platform.compliance.crypto import (
    EncryptedSecret,
    RecoveryCodeHasher,
    SecretCryptoPort,
    TotpEngine,
)
from tradingagents.platform.compliance.models import (
    ConsentDecision,
    ConsentRecord,
    MfaCredential,
    MfaCredentialStatus,
    PrivacyRequest,
    PrivacyRequestEvent,
    PrivacyRequestKind,
    PrivacyRequestStatus,
    RecoveryCode,
    TermsDocument,
)
from tradingagents.platform.identity.models import (
    AuditEvent,
    Membership,
    MembershipRole,
    Tenant,
    User,
    UserSession,
)
from tradingagents.platform.identity.service import IdentityPrincipal

_PRIVACY_DUE: Final = timedelta(days=15)
_EXPORT_RETENTION_MAX: Final = timedelta(days=7)
_STEP_UP_TTL: Final = timedelta(minutes=5)


class ComplianceError(RuntimeError):
    """Failure safe to translate into a non-sensitive public response."""


class ComplianceAuthorizationDenied(ComplianceError):
    pass


class ComplianceConflict(ComplianceError):
    pass


class InvalidComplianceTransition(ComplianceError):
    pass


class MfaVerificationFailed(ComplianceError):
    pass


@dataclass(frozen=True, slots=True)
class MfaEnrollment:
    credential_id: UUID
    secret: str = field(repr=False)
    provisioning_uri: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class MfaActivation:
    credential_id: UUID
    recovery_codes: tuple[str, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class MfaVerification:
    tenant_id: UUID
    user_id: UUID
    session_id: UUID
    method: str
    verified_at: datetime
    valid_until: datetime


_TRANSITIONS: Final[dict[PrivacyRequestStatus, set[PrivacyRequestStatus]]] = {
    PrivacyRequestStatus.REQUESTED: {
        PrivacyRequestStatus.IDENTITY_VERIFIED,
        PrivacyRequestStatus.CANCELLED,
    },
    PrivacyRequestStatus.IDENTITY_VERIFIED: {
        PrivacyRequestStatus.PROCESSING,
        PrivacyRequestStatus.CANCELLED,
    },
    PrivacyRequestStatus.PROCESSING: {
        PrivacyRequestStatus.EXPORT_READY,
        PrivacyRequestStatus.DELETION_SCHEDULED,
        PrivacyRequestStatus.REJECTED,
    },
    PrivacyRequestStatus.EXPORT_READY: {
        PrivacyRequestStatus.FULFILLED,
        PrivacyRequestStatus.EXPIRED,
    },
    PrivacyRequestStatus.DELETION_SCHEDULED: {
        PrivacyRequestStatus.FULFILLED,
        PrivacyRequestStatus.REJECTED,
    },
    PrivacyRequestStatus.FULFILLED: set(),
    PrivacyRequestStatus.REJECTED: set(),
    PrivacyRequestStatus.EXPIRED: set(),
    PrivacyRequestStatus.CANCELLED: set(),
}


class ComplianceService:
    """Legal evidence and LGPD workflows over caller-owned SQLAlchemy storage."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _set_tenant_scope(db: Session, tenant_id: UUID) -> None:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )

    @staticmethod
    def _validated_actor(db: Session, principal: IdentityPrincipal) -> MembershipRole:
        now = datetime.now(UTC)
        row = db.execute(
            select(Membership, UserSession, User, Tenant)
            .join(
                UserSession,
                (UserSession.membership_id == Membership.id)
                & (UserSession.tenant_id == Membership.tenant_id)
                & (UserSession.user_id == Membership.user_id),
            )
            .join(User, User.id == Membership.user_id)
            .join(Tenant, Tenant.id == Membership.tenant_id)
            .where(
                Membership.id == principal.membership_id,
                Membership.tenant_id == principal.tenant_id,
                Membership.user_id == principal.user_id,
                UserSession.id == principal.session_id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > now,
                Membership.is_active.is_(True),
                User.is_active.is_(True),
                Tenant.is_active.is_(True),
            )
        ).one_or_none()
        if row is None:
            raise ComplianceAuthorizationDenied("authenticated principal is no longer valid")
        return MembershipRole(row[0].role)

    @staticmethod
    def _audit(
        db: Session,
        principal: IdentityPrincipal,
        *,
        event_type: str,
        target_type: str,
        target_id: UUID,
        details: dict | None = None,
    ) -> None:
        db.add(
            AuditEvent(
                tenant_id=principal.tenant_id,
                actor_user_id=principal.user_id,
                event_type=event_type,
                target_type=target_type,
                target_id=str(target_id),
                details=details or {},
            )
        )

    def publish_terms(
        self,
        *,
        document_type: str,
        version: str,
        content_sha256: str,
        publication_uri: str,
        published_at: datetime,
    ) -> UUID:
        """Trusted control-plane operation; intentionally not exposed as a user API."""

        normalized_type = document_type.strip().lower()
        normalized_version = version.strip()
        normalized_hash = content_sha256.strip().lower()
        normalized_uri = publication_uri.strip()
        if len(normalized_type) < 2 or len(normalized_type) > 80:
            raise ValueError("document_type must contain 2-80 characters")
        if not normalized_version or len(normalized_version) > 40:
            raise ValueError("version must contain 1-40 characters")
        if len(normalized_hash) != 64 or any(c not in "0123456789abcdef" for c in normalized_hash):
            raise ValueError("content_sha256 must be a lowercase SHA-256 digest")
        if not normalized_uri or len(normalized_uri) > 1024:
            raise ValueError("publication_uri must contain 1-1024 characters")
        parsed_uri = urlsplit(normalized_uri)
        if parsed_uri.scheme != "https" or not parsed_uri.hostname:
            raise ValueError("publication_uri must be an absolute HTTPS URL")
        if published_at.tzinfo is None or published_at.utcoffset() is None:
            raise ValueError("published_at must include a timezone")
        if published_at > self._now() + timedelta(minutes=5):
            raise ValueError("published_at cannot be in the future")
        row = TermsDocument(
            document_type=normalized_type,
            version=normalized_version,
            content_sha256=normalized_hash,
            publication_uri=normalized_uri,
            published_at=published_at.astimezone(UTC),
        )
        try:
            with self._sessions() as db, db.begin():
                db.add(row)
                db.flush()
                document_id = row.id
            return document_id
        except IntegrityError as exc:
            raise ComplianceConflict("terms document version already exists") from exc

    def latest_terms(self, *, document_type: str) -> TermsDocument | None:
        normalized_type = document_type.strip().lower()
        if len(normalized_type) < 2 or len(normalized_type) > 80:
            raise ValueError("document_type must contain 2-80 characters")
        with self._sessions() as db:
            return db.scalar(
                select(TermsDocument)
                .where(
                    TermsDocument.document_type == normalized_type,
                    TermsDocument.published_at <= self._now(),
                )
                .order_by(TermsDocument.published_at.desc(), TermsDocument.id.desc())
                .limit(1)
            )

    def record_consent(
        self,
        *,
        principal: IdentityPrincipal,
        terms_document_id: UUID,
        decision: ConsentDecision,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> UUID:
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, principal.tenant_id)
            self._validated_actor(db, principal)
            document = db.get(TermsDocument, terms_document_id)
            if document is None:
                raise ComplianceConflict("terms document does not exist")
            if document.published_at > self._now():
                raise ComplianceConflict("terms document is not published")
            latest = db.scalar(
                select(ConsentRecord)
                .where(
                    ConsentRecord.tenant_id == principal.tenant_id,
                    ConsentRecord.user_id == principal.user_id,
                    ConsentRecord.terms_document_id == document.id,
                )
                .order_by(ConsentRecord.recorded_at.desc(), ConsentRecord.id.desc())
                .with_for_update()
                .limit(1)
            )
            if latest is None and decision is ConsentDecision.WITHDRAWN:
                raise ComplianceConflict("consent cannot be withdrawn before acceptance")
            if latest is not None and latest.decision == decision.value:
                raise ComplianceConflict("consent decision is already current")
            record_id = uuid4()
            recorded_at = self._now()
            if latest is not None:
                recorded_at = max(
                    recorded_at,
                    latest.recorded_at + timedelta(microseconds=1),
                )
            evidence = {
                "consent_record_id": str(record_id),
                "tenant_id": str(principal.tenant_id),
                "user_id": str(principal.user_id),
                "terms_document_id": str(document.id),
                "terms_sha256": document.content_sha256,
                "decision": decision.value,
                "recorded_at": recorded_at.isoformat(),
                "client_ip": client_ip[:64] if client_ip else None,
                "user_agent": user_agent[:512] if user_agent else None,
            }
            evidence_sha256 = hashlib.sha256(
                json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            record = ConsentRecord(
                id=record_id,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                terms_document_id=document.id,
                decision=decision.value,
                evidence_sha256=evidence_sha256,
                client_ip=client_ip[:64] if client_ip else None,
                user_agent=user_agent[:512] if user_agent else None,
                recorded_at=recorded_at,
            )
            db.add(record)
            db.flush()
            self._audit(
                db,
                principal,
                event_type=f"consent.{decision.value}",
                target_type="consent_record",
                target_id=record.id,
                details={"terms_document_id": str(document.id)},
            )
            record_id = record.id
        return record_id

    def list_consents(self, *, principal: IdentityPrincipal) -> list[ConsentRecord]:
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, principal.tenant_id)
            self._validated_actor(db, principal)
            return list(
                db.scalars(
                    select(ConsentRecord)
                    .where(
                        ConsentRecord.tenant_id == principal.tenant_id,
                        ConsentRecord.user_id == principal.user_id,
                    )
                    .order_by(ConsentRecord.recorded_at, ConsentRecord.id)
                )
            )

    def request_privacy_action(
        self, *, principal: IdentityPrincipal, kind: PrivacyRequestKind
    ) -> UUID:
        now = self._now()
        request = PrivacyRequest(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            kind=kind.value,
            status=PrivacyRequestStatus.REQUESTED.value,
            requested_at=now,
            due_at=now + _PRIVACY_DUE,
        )
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, principal.tenant_id)
            self._validated_actor(db, principal)
            db.add(request)
            db.flush()
            db.add(
                PrivacyRequestEvent(
                    tenant_id=principal.tenant_id,
                    request_id=request.id,
                    actor_user_id=principal.user_id,
                    from_status=None,
                    to_status=PrivacyRequestStatus.REQUESTED.value,
                    occurred_at=now,
                )
            )
            self._audit(
                db,
                principal,
                event_type="privacy.requested",
                target_type="privacy_request",
                target_id=request.id,
                details={"kind": kind.value},
            )
            request_id = request.id
        return request_id

    def transition_privacy_request(
        self,
        *,
        principal: IdentityPrincipal,
        request_id: UUID,
        expected_status: PrivacyRequestStatus,
        target_status: PrivacyRequestStatus,
        artifact_expires_at: datetime | None = None,
        retention_until: datetime | None = None,
        reason_code: str | None = None,
    ) -> None:
        now = self._now()
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, principal.tenant_id)
            role = self._validated_actor(db, principal)
            request = db.scalar(
                select(PrivacyRequest)
                .where(
                    PrivacyRequest.id == request_id,
                    PrivacyRequest.tenant_id == principal.tenant_id,
                )
                .with_for_update()
            )
            if request is None:
                raise ComplianceAuthorizationDenied("privacy request is unavailable")
            event_at = max(now, request.updated_at + timedelta(microseconds=1))
            current = PrivacyRequestStatus(request.status)
            if current is not expected_status:
                raise ComplianceConflict("privacy request changed concurrently")
            if target_status not in _TRANSITIONS[current]:
                raise InvalidComplianceTransition(
                    f"transition {current.value} -> {target_status.value} is not allowed"
                )
            owns_request = request.user_id == principal.user_id
            if target_status is PrivacyRequestStatus.CANCELLED:
                if not owns_request:
                    raise ComplianceAuthorizationDenied("only the requester can cancel")
            elif role not in {MembershipRole.OWNER, MembershipRole.ADMIN}:
                raise ComplianceAuthorizationDenied("role cannot process privacy requests")
            request_kind = PrivacyRequestKind(request.kind)
            if target_status is PrivacyRequestStatus.EXPORT_READY:
                if request_kind is not PrivacyRequestKind.EXPORT:
                    raise InvalidComplianceTransition("only exports can become export_ready")
                if (
                    artifact_expires_at is None
                    or artifact_expires_at.tzinfo is None
                    or artifact_expires_at <= now
                    or artifact_expires_at > now + _EXPORT_RETENTION_MAX
                ):
                    raise InvalidComplianceTransition(
                        "export artifact expiry must be within the next seven days"
                    )
                request.artifact_expires_at = artifact_expires_at.astimezone(UTC)
            elif target_status is PrivacyRequestStatus.DELETION_SCHEDULED:
                if request_kind is not PrivacyRequestKind.DELETE:
                    raise InvalidComplianceTransition(
                        "only deletion requests can become deletion_scheduled"
                    )
                if (
                    retention_until is None
                    or retention_until.tzinfo is None
                    or retention_until < now
                ):
                    raise InvalidComplianceTransition(
                        "deletion requires a timezone-aware retention deadline"
                    )
                request.retention_until = retention_until.astimezone(UTC)
            if target_status is PrivacyRequestStatus.REJECTED:
                normalized_reason = (reason_code or "").strip()
                if not normalized_reason or len(normalized_reason) > 80:
                    raise InvalidComplianceTransition("rejection requires a reason code")
                request.rejection_code = normalized_reason
            if target_status in {
                PrivacyRequestStatus.FULFILLED,
                PrivacyRequestStatus.REJECTED,
                PrivacyRequestStatus.EXPIRED,
                PrivacyRequestStatus.CANCELLED,
            }:
                request.completed_at = event_at
            request.status = target_status.value
            request.updated_at = event_at
            db.add(
                PrivacyRequestEvent(
                    tenant_id=principal.tenant_id,
                    request_id=request.id,
                    actor_user_id=principal.user_id,
                    from_status=current.value,
                    to_status=target_status.value,
                    reason_code=request.rejection_code,
                    occurred_at=event_at,
                )
            )
            self._audit(
                db,
                principal,
                event_type="privacy.transitioned",
                target_type="privacy_request",
                target_id=request.id,
                details={"from": current.value, "to": target_status.value},
            )

    def get_privacy_request(
        self, *, principal: IdentityPrincipal, request_id: UUID
    ) -> PrivacyRequest:
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, principal.tenant_id)
            role = self._validated_actor(db, principal)
            query = select(PrivacyRequest).where(
                PrivacyRequest.id == request_id,
                PrivacyRequest.tenant_id == principal.tenant_id,
            )
            if role not in {MembershipRole.OWNER, MembershipRole.ADMIN}:
                query = query.where(PrivacyRequest.user_id == principal.user_id)
            request = db.scalar(query)
            if request is None:
                raise ComplianceAuthorizationDenied("privacy request is unavailable")
            return request


class MfaService:
    """TOTP enrollment and verification without plaintext secret persistence."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        crypto: SecretCryptoPort,
        recovery_hasher: RecoveryCodeHasher,
        issuer: str = "TradingAgents DZ23",
        totp: TotpEngine | None = None,
    ) -> None:
        if not isinstance(crypto, SecretCryptoPort):
            raise TypeError("crypto must implement SecretCryptoPort")
        if not issuer.strip() or len(issuer) > 80:
            raise ValueError("issuer must contain 1-80 characters")
        self._sessions = session_factory
        self._crypto = crypto
        self._recovery = recovery_hasher
        self._issuer = issuer.strip()
        self._totp = totp or TotpEngine()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _context(tenant_id: UUID, user_id: UUID) -> bytes:
        return f"mfa-totp:{tenant_id}:{user_id}".encode("ascii")

    @staticmethod
    def _validated_actor(db: Session, principal: IdentityPrincipal) -> None:
        ComplianceService._validated_actor(db, principal)

    def enroll(self, *, principal: IdentityPrincipal, account_label: str) -> MfaEnrollment:
        normalized_label = account_label.strip()
        if not normalized_label or len(normalized_label) > 320:
            raise ValueError("account_label must contain 1-320 characters")
        secret = self._totp.issue_secret()
        encrypted = self._crypto.encrypt(
            secret.encode("ascii"), context=self._context(principal.tenant_id, principal.user_id)
        )
        try:
            with self._sessions() as db, db.begin():
                ComplianceService._set_tenant_scope(db, principal.tenant_id)
                self._validated_actor(db, principal)
                credential = db.scalar(
                    select(MfaCredential)
                    .where(
                        MfaCredential.tenant_id == principal.tenant_id,
                        MfaCredential.user_id == principal.user_id,
                    )
                    .with_for_update()
                )
                if credential is not None and credential.status == MfaCredentialStatus.ACTIVE.value:
                    raise ComplianceConflict("active MFA must be disabled before re-enrollment")
                if credential is None:
                    credential = MfaCredential(
                        id=uuid4(),
                        tenant_id=principal.tenant_id,
                        user_id=principal.user_id,
                        status=MfaCredentialStatus.PENDING.value,
                        secret_key_id=encrypted.key_id,
                        secret_nonce=encrypted.nonce,
                        secret_ciphertext=encrypted.ciphertext,
                    )
                    db.add(credential)
                else:
                    credential.status = MfaCredentialStatus.PENDING.value
                    credential.secret_key_id = encrypted.key_id
                    credential.secret_nonce = encrypted.nonce
                    credential.secret_ciphertext = encrypted.ciphertext
                    credential.verified_at = None
                    credential.disabled_at = None
                    credential.last_accepted_counter = None
                db.flush()
                ComplianceService._audit(
                    db,
                    principal,
                    event_type="mfa.enrollment_started",
                    target_type="mfa_credential",
                    target_id=credential.id,
                )
                credential_id = credential.id
        except IntegrityError as exc:
            raise ComplianceConflict("MFA enrollment changed concurrently") from exc
        uri = (
            f"otpauth://totp/{quote(self._issuer, safe='')}:{quote(normalized_label, safe='')}"
            f"?secret={secret}&issuer={quote(self._issuer, safe='')}"
            f"&digits={self._totp.digits}&period={self._totp.period_seconds}"
        )
        return MfaEnrollment(
            credential_id=credential_id,
            secret=secret,
            provisioning_uri=uri,
        )

    def activate(
        self,
        *,
        principal: IdentityPrincipal,
        code: str,
        now: datetime | None = None,
        recovery_code_count: int = 10,
    ) -> MfaActivation:
        if recovery_code_count < 8 or recovery_code_count > 20:
            raise ValueError("recovery_code_count must be between 8 and 20")
        verified_at = now or self._now()
        if verified_at.tzinfo is None or verified_at.utcoffset() is None:
            raise ValueError("now must include a timezone")
        with self._sessions() as db, db.begin():
            ComplianceService._set_tenant_scope(db, principal.tenant_id)
            self._validated_actor(db, principal)
            credential = db.scalar(
                select(MfaCredential)
                .where(
                    MfaCredential.tenant_id == principal.tenant_id,
                    MfaCredential.user_id == principal.user_id,
                    MfaCredential.status == MfaCredentialStatus.PENDING.value,
                )
                .with_for_update()
            )
            if credential is None:
                raise MfaVerificationFailed("MFA enrollment is unavailable")
            secret = self._decrypt_secret(credential)
            counter = self._totp.accepted_counter(secret, code, verified_at)
            if counter is None:
                raise MfaVerificationFailed("MFA verification failed")
            credential.status = MfaCredentialStatus.ACTIVE.value
            credential.verified_at = verified_at.astimezone(UTC)
            credential.last_accepted_counter = counter
            recovery_codes = tuple(self._recovery.issue() for _ in range(recovery_code_count))
            db.add_all(
                [
                    RecoveryCode(
                        tenant_id=principal.tenant_id,
                        user_id=principal.user_id,
                        credential_id=credential.id,
                        code_hash=self._recovery.digest(
                            item,
                            tenant_id=str(principal.tenant_id),
                            user_id=str(principal.user_id),
                        ),
                    )
                    for item in recovery_codes
                ]
            )
            ComplianceService._audit(
                db,
                principal,
                event_type="mfa.activated",
                target_type="mfa_credential",
                target_id=credential.id,
                details={"recovery_code_count": recovery_code_count},
            )
            credential_id = credential.id
        return MfaActivation(credential_id=credential_id, recovery_codes=recovery_codes)

    def _decrypt_secret(self, credential: MfaCredential) -> str:
        encrypted = EncryptedSecret(
            key_id=credential.secret_key_id,
            nonce=credential.secret_nonce,
            ciphertext=credential.secret_ciphertext,
        )
        try:
            plaintext = self._crypto.decrypt(
                encrypted, context=self._context(credential.tenant_id, credential.user_id)
            )
            secret = plaintext.decode("ascii")
        except Exception as exc:
            raise MfaVerificationFailed("MFA secret is unavailable") from exc
        if not secret or len(secret) > 256:
            raise MfaVerificationFailed("MFA secret is unavailable")
        return secret

    def verify_step_up(
        self,
        *,
        principal: IdentityPrincipal,
        code: str | None = None,
        recovery_code: str | None = None,
        now: datetime | None = None,
    ) -> MfaVerification:
        if (code is None) == (recovery_code is None):
            raise ValueError("provide exactly one verification method")
        verified_at = now or self._now()
        if verified_at.tzinfo is None or verified_at.utcoffset() is None:
            raise ValueError("now must include a timezone")
        method = "totp" if code is not None else "recovery_code"
        with self._sessions() as db, db.begin():
            ComplianceService._set_tenant_scope(db, principal.tenant_id)
            self._validated_actor(db, principal)
            credential = db.scalar(
                select(MfaCredential)
                .where(
                    MfaCredential.tenant_id == principal.tenant_id,
                    MfaCredential.user_id == principal.user_id,
                    MfaCredential.status == MfaCredentialStatus.ACTIVE.value,
                )
                .with_for_update()
            )
            if credential is None:
                raise MfaVerificationFailed("MFA verification failed")
            if code is not None:
                secret = self._decrypt_secret(credential)
                counter = self._totp.accepted_counter(
                    secret,
                    code,
                    verified_at,
                    after_counter=credential.last_accepted_counter,
                )
                if counter is None:
                    raise MfaVerificationFailed("MFA verification failed")
                credential.last_accepted_counter = counter
            else:
                try:
                    candidate_hash = self._recovery.digest(
                        recovery_code or "",
                        tenant_id=str(principal.tenant_id),
                        user_id=str(principal.user_id),
                    )
                except ValueError as exc:
                    raise MfaVerificationFailed("MFA verification failed") from exc
                stored = db.scalar(
                    select(RecoveryCode)
                    .where(
                        RecoveryCode.tenant_id == principal.tenant_id,
                        RecoveryCode.user_id == principal.user_id,
                        RecoveryCode.credential_id == credential.id,
                        RecoveryCode.code_hash == candidate_hash,
                        RecoveryCode.consumed_at.is_(None),
                    )
                    .with_for_update()
                )
                if stored is None or not self._recovery.matches(stored.code_hash, candidate_hash):
                    raise MfaVerificationFailed("MFA verification failed")
                stored.consumed_at = verified_at.astimezone(UTC)
            user_session = db.scalar(
                select(UserSession)
                .where(
                    UserSession.id == principal.session_id,
                    UserSession.tenant_id == principal.tenant_id,
                    UserSession.user_id == principal.user_id,
                    UserSession.revoked_at.is_(None),
                    UserSession.expires_at > verified_at,
                )
                .with_for_update()
            )
            if user_session is None:
                raise MfaVerificationFailed("MFA verification failed")
            user_session.mfa_verified_at = verified_at.astimezone(UTC)
            user_session.mfa_method = method
            ComplianceService._audit(
                db,
                principal,
                event_type="mfa.step_up_verified",
                target_type="mfa_credential",
                target_id=credential.id,
                details={"method": method, "session_id": str(principal.session_id)},
            )
        return MfaVerification(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            session_id=principal.session_id,
            method=method,
            verified_at=verified_at.astimezone(UTC),
            valid_until=verified_at.astimezone(UTC) + _STEP_UP_TTL,
        )

    def assert_recent_step_up(
        self,
        *,
        principal: IdentityPrincipal,
        now: datetime | None = None,
    ) -> MfaVerification:
        """Fail closed unless this exact live session completed recent MFA."""

        checked_at = now or self._now()
        if checked_at.tzinfo is None or checked_at.utcoffset() is None:
            raise ValueError("now must include a timezone")
        checked_at = checked_at.astimezone(UTC)
        with self._sessions() as db, db.begin():
            ComplianceService._set_tenant_scope(db, principal.tenant_id)
            self._validated_actor(db, principal)
            user_session = db.scalar(
                select(UserSession).where(
                    UserSession.id == principal.session_id,
                    UserSession.tenant_id == principal.tenant_id,
                    UserSession.user_id == principal.user_id,
                    UserSession.revoked_at.is_(None),
                    UserSession.expires_at > checked_at,
                )
            )
            if (
                user_session is None
                or user_session.mfa_verified_at is None
                or user_session.mfa_method not in {"totp", "recovery_code"}
                or user_session.mfa_verified_at + _STEP_UP_TTL < checked_at
            ):
                raise MfaVerificationFailed("recent MFA verification is required")
            return MfaVerification(
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                session_id=principal.session_id,
                method=user_session.mfa_method,
                verified_at=user_session.mfa_verified_at,
                valid_until=user_session.mfa_verified_at + _STEP_UP_TTL,
            )

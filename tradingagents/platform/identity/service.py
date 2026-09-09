"""Transactional identity application service.

Every tenant-scoped lookup includes ``tenant_id`` in its predicate.  Raw
session and password-reset tokens never enter an ORM entity or audit event.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Select, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tradingagents.platform.identity.crypto import PasswordManager, TokenHasher
from tradingagents.platform.identity.models import (
    AuditEvent,
    EmailVerificationToken,
    Membership,
    MembershipRole,
    PasswordResetToken,
    Tenant,
    User,
    UserSession,
)

_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,78}[a-z0-9])?$")
_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,31}$")


class IdentityError(RuntimeError):
    """Base error safe to translate into a non-sensitive API response."""


class AuthenticationFailed(IdentityError):
    pass


class AuthorizationDenied(IdentityError):
    pass


class Conflict(IdentityError):
    pass


class InvalidToken(IdentityError):
    pass


@dataclass(frozen=True)
class RegistrationResult:
    tenant_id: UUID
    user_id: UUID
    membership_id: UUID


@dataclass(frozen=True)
class IssuedSession:
    session_id: UUID
    tenant_id: UUID
    user_id: UUID
    expires_at: datetime
    token: str = field(repr=False)


@dataclass(frozen=True)
class IssuedResetToken:
    reset_id: UUID
    tenant_id: UUID
    user_id: UUID
    expires_at: datetime
    token: str = field(repr=False)


@dataclass(frozen=True)
class IssuedVerificationToken:
    verification_id: UUID
    tenant_id: UUID
    user_id: UUID
    expires_at: datetime
    token: str = field(repr=False)


@dataclass(frozen=True)
class SessionProfile:
    tenant_id: UUID
    user_id: UUID
    membership_id: UUID
    session_id: UUID
    email: str
    display_name: str
    role: MembershipRole
    expires_at: datetime
    email_verified: bool
    session_token_hash: str = field(default="", repr=False)


@dataclass(frozen=True)
class IdentityPrincipal:
    tenant_id: UUID
    user_id: UUID
    membership_id: UUID
    session_id: UUID
    role: MembershipRole
    session_token_hash: str = field(default="", repr=False)


class IdentityService:
    """Tenant-safe identity operations over caller-owned SQLAlchemy storage."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        token_hasher: TokenHasher,
        password_manager: PasswordManager | None = None,
        max_auth_failures: int = 5,
        auth_lock_duration: timedelta = timedelta(minutes=15),
    ) -> None:
        if not 3 <= max_auth_failures <= 20:
            raise ValueError("max_auth_failures must be between 3 and 20")
        if not timedelta(minutes=1) <= auth_lock_duration <= timedelta(days=1):
            raise ValueError("auth_lock_duration must be between one minute and one day")
        self._sessions = session_factory
        self._tokens = token_hasher
        self._passwords = password_manager or PasswordManager()
        self._max_auth_failures = max_auth_failures
        self._auth_lock_duration = auth_lock_duration

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _normalize_email(email: str) -> str:
        normalized = email.strip().casefold()
        if len(normalized) > 320 or normalized.count("@") != 1:
            raise ValueError("invalid email address")
        local, domain = normalized.split("@", 1)
        if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
            raise ValueError("invalid email address")
        return normalized

    @staticmethod
    def _normalize_slug(slug: str) -> str:
        normalized = slug.strip().lower()
        if not _SLUG_RE.fullmatch(normalized):
            raise ValueError("tenant slug must be 3-80 lowercase letters, digits, or hyphens")
        return normalized

    def ready(self) -> bool:
        try:
            with self._sessions() as db:
                return db.scalar(select(1)) == 1
        except Exception:
            return False

    @staticmethod
    def tenant_id_from_token(token: str) -> UUID:
        """Read the non-secret tenant routing prefix from an opaque token."""

        prefix, separator, secret = token.partition(".")
        if not separator or len(secret) < 32:
            raise InvalidToken("token is malformed")
        try:
            return UUID(prefix)
        except ValueError as exc:
            raise InvalidToken("token is malformed") from exc

    @staticmethod
    def _audit(
        db: Session,
        *,
        tenant_id: UUID,
        event_type: str,
        target_type: str,
        target_id: UUID | str,
        actor_user_id: UUID | None = None,
        client_ip: str | None = None,
        details: dict | None = None,
    ) -> None:
        db.add(
            AuditEvent(
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                event_type=event_type,
                target_type=target_type,
                target_id=str(target_id),
                client_ip=client_ip[:64] if client_ip else None,
                details=details or {},
            )
        )

    @staticmethod
    def _set_tenant_scope(db: Session, tenant_id: UUID) -> None:
        """Bind PostgreSQL RLS to this transaction; SQLite tests use predicates."""

        bind = db.get_bind()
        if bind.dialect.name == "postgresql":
            db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )

    @staticmethod
    def _set_registration_user_scope(db: Session, user_id: UUID) -> None:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(
                text("SELECT set_config('app.registration_user_id', :user_id, true)"),
                {"user_id": str(user_id)},
            )

    @staticmethod
    def _set_tenant_slug_scope(db: Session, slug: str) -> None:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(
                text("SELECT set_config('app.tenant_slug', :slug, true)"),
                {"slug": slug},
            )

    @staticmethod
    def _validated_actor(
        db: Session,
        principal: IdentityPrincipal,
        *,
        allowed: set[MembershipRole] | None = None,
    ) -> MembershipRole:
        """Reload identity/role/session bindings instead of trusting a DTO."""

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
            raise AuthorizationDenied("authenticated principal is no longer valid")
        role = MembershipRole(row[0].role)
        if allowed is not None and role not in allowed:
            raise AuthorizationDenied("role is not authorized for this operation")
        return role

    def register_tenant(
        self,
        *,
        tenant_name: str,
        tenant_slug: str,
        owner_email: str,
        display_name: str,
        password: str,
        client_ip: str | None = None,
    ) -> RegistrationResult:
        email_normalized = self._normalize_email(owner_email)
        slug = self._normalize_slug(tenant_slug)
        name = tenant_name.strip()
        normalized_display_name = display_name.strip()
        if not name or len(name) > 160:
            raise ValueError("tenant name must contain 1-160 characters")
        if len(normalized_display_name) < 2 or len(normalized_display_name) > 120:
            raise ValueError("display name must contain 2-120 characters")
        password_hash = self._passwords.hash(password)
        try:
            with self._sessions() as db, db.begin():
                tenant_id = uuid4()
                user_id = uuid4()
                self._set_tenant_scope(db, tenant_id)
                self._set_registration_user_scope(db, user_id)
                tenant = Tenant(id=tenant_id, name=name, slug=slug)
                user = User(
                    id=user_id,
                    email=owner_email.strip(),
                    email_normalized=email_normalized,
                    display_name=normalized_display_name,
                    password_hash=password_hash,
                )
                db.add_all([tenant, user])
                db.flush()
                membership = Membership(
                    id=uuid4(),
                    tenant_id=tenant.id,
                    user_id=user.id,
                    role=MembershipRole.OWNER.value,
                )
                db.add(membership)
                db.flush()
                self._audit(
                    db,
                    tenant_id=tenant.id,
                    actor_user_id=user.id,
                    event_type="tenant.registered",
                    target_type="tenant",
                    target_id=tenant.id,
                    client_ip=client_ip,
                    details={"owner_membership_id": str(membership.id)},
                )
                result = RegistrationResult(tenant.id, user.id, membership.id)
            return result
        except IntegrityError as exc:
            raise Conflict("tenant slug or owner account already exists") from exc

    def authenticate(
        self,
        *,
        tenant_id: UUID,
        email: str,
        password: str,
        ttl: timedelta = timedelta(hours=12),
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> IssuedSession:
        if ttl <= timedelta(0) or ttl > timedelta(days=30):
            raise ValueError("session lifetime must be between zero and 30 days")
        email_normalized = self._normalize_email(email)
        now = self._now()
        token = f"{tenant_id}.{self._tokens.issue()}"
        token_hash = self._tokens.digest(token, purpose=f"session:{tenant_id}")
        issued: IssuedSession | None = None
        authentication_failed = False
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, tenant_id)
            row = db.execute(
                select(User, Membership, Tenant)
                .join(Membership, Membership.user_id == User.id)
                .join(Tenant, Tenant.id == Membership.tenant_id)
                .where(
                    User.email_normalized == email_normalized,
                    Membership.tenant_id == tenant_id,
                )
                .with_for_update()
            ).one_or_none()
            if row is None:
                self._passwords.verify_missing_user(password)
                authentication_failed = True
            else:
                user, membership, tenant = row
                if user.auth_locked_until is not None and user.auth_locked_until > now:
                    self._passwords.verify_missing_user(password)
                    authentication_failed = True
                else:
                    if user.password_hash is None:
                        self._passwords.verify_missing_user(password)
                        password_valid = False
                    else:
                        password_valid = self._passwords.verify(user.password_hash, password)
                    if (
                        not password_valid
                        or not user.is_active
                        or not membership.is_active
                        or not tenant.is_active
                    ):
                        user.failed_login_attempts += 1
                        if user.failed_login_attempts >= self._max_auth_failures:
                            user.auth_locked_until = now + self._auth_lock_duration
                        self._audit(
                            db,
                            tenant_id=tenant_id,
                            actor_user_id=user.id,
                            event_type="authentication.failed",
                            target_type="user",
                            target_id=user.id,
                            client_ip=client_ip,
                            details={"account_locked": user.auth_locked_until is not None},
                        )
                        authentication_failed = True
                    else:
                        user.failed_login_attempts = 0
                        user.auth_locked_until = None
                        if self._passwords.needs_rehash(user.password_hash):
                            user.password_hash = self._passwords.hash(password)
                        session = UserSession(
                            tenant_id=tenant_id,
                            user_id=user.id,
                            membership_id=membership.id,
                            token_hash=token_hash,
                            expires_at=now + ttl,
                            client_ip=client_ip[:64] if client_ip else None,
                            user_agent=user_agent[:512] if user_agent else None,
                        )
                        db.add(session)
                        db.flush()
                        self._audit(
                            db,
                            tenant_id=tenant_id,
                            actor_user_id=user.id,
                            event_type="session.created",
                            target_type="session",
                            target_id=session.id,
                            client_ip=client_ip,
                        )
                        issued = IssuedSession(
                            session_id=session.id,
                            tenant_id=tenant_id,
                            user_id=user.id,
                            expires_at=session.expires_at,
                            token=token,
                        )
        if authentication_failed or issued is None:
            raise AuthenticationFailed("invalid credentials")
        return issued

    def register_external_tenant(
        self,
        *,
        auth_subject: UUID,
        auth_provider: str,
        verified_email: str,
        display_name: str,
        tenant_name: str,
        tenant_slug: str,
        ttl: timedelta = timedelta(hours=12),
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> IssuedSession:
        """Provision an isolated tenant from a verified external identity."""

        provider = auth_provider.strip().lower()
        if not _PROVIDER_RE.fullmatch(provider):
            raise ValueError("external auth provider is invalid")
        if ttl <= timedelta(0) or ttl > timedelta(days=30):
            raise ValueError("session lifetime must be between zero and 30 days")
        email = self._normalize_email(verified_email)
        slug = self._normalize_slug(tenant_slug)
        name = tenant_name.strip()
        normalized_display_name = display_name.strip()
        if not name or len(name) > 160:
            raise ValueError("tenant name must contain 1-160 characters")
        if len(normalized_display_name) < 2 or len(normalized_display_name) > 120:
            raise ValueError("display name must contain 2-120 characters")
        now = self._now()
        tenant_id = uuid4()
        user_id = uuid4()
        token = f"{tenant_id}.{self._tokens.issue()}"
        token_hash = self._tokens.digest(token, purpose=f"session:{tenant_id}")
        try:
            with self._sessions() as db, db.begin():
                self._set_tenant_scope(db, tenant_id)
                self._set_registration_user_scope(db, user_id)
                tenant = Tenant(id=tenant_id, name=name, slug=slug)
                user = User(
                    id=user_id,
                    email=verified_email.strip(),
                    email_normalized=email,
                    display_name=normalized_display_name,
                    password_hash=None,
                    auth_subject=auth_subject,
                    auth_provider=provider,
                    email_verified_at=now,
                )
                membership = Membership(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    user_id=user_id,
                    role=MembershipRole.OWNER.value,
                )
                db.add_all([tenant, user])
                db.flush()
                db.add(membership)
                db.flush()
                session = UserSession(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    membership_id=membership.id,
                    token_hash=token_hash,
                    expires_at=now + ttl,
                    client_ip=client_ip[:64] if client_ip else None,
                    user_agent=user_agent[:512] if user_agent else None,
                )
                db.add(session)
                db.flush()
                self._audit(
                    db,
                    tenant_id=tenant_id,
                    actor_user_id=user_id,
                    event_type="external_identity.registered",
                    target_type="user",
                    target_id=user_id,
                    client_ip=client_ip,
                    details={"provider": provider, "membership_id": str(membership.id)},
                )
                return IssuedSession(
                    session_id=session.id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    expires_at=session.expires_at,
                    token=token,
                )
        except IntegrityError as exc:
            raise Conflict("external identity, email, or tenant already exists") from exc

    def authenticate_external(
        self,
        *,
        auth_subject: UUID,
        auth_provider: str,
        organization_slug: str,
        ttl: timedelta = timedelta(hours=12),
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> IssuedSession:
        provider = auth_provider.strip().lower()
        if not _PROVIDER_RE.fullmatch(provider):
            raise AuthenticationFailed("invalid external identity")
        if ttl <= timedelta(0) or ttl > timedelta(days=30):
            raise ValueError("session lifetime must be between zero and 30 days")
        tenant_id = self.tenant_id_by_slug(organization_slug)
        now = self._now()
        token = f"{tenant_id}.{self._tokens.issue()}"
        token_hash = self._tokens.digest(token, purpose=f"session:{tenant_id}")
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, tenant_id)
            row = db.execute(
                select(User, Membership, Tenant)
                .join(Membership, Membership.user_id == User.id)
                .join(Tenant, Tenant.id == Membership.tenant_id)
                .where(
                    User.auth_subject == auth_subject,
                    User.auth_provider == provider,
                    Membership.tenant_id == tenant_id,
                    User.is_active.is_(True),
                    Membership.is_active.is_(True),
                    Tenant.is_active.is_(True),
                )
            ).one_or_none()
            if row is None:
                raise AuthenticationFailed("invalid external identity")
            user, membership, _tenant = row
            session = UserSession(
                tenant_id=tenant_id,
                user_id=user.id,
                membership_id=membership.id,
                token_hash=token_hash,
                expires_at=now + ttl,
                client_ip=client_ip[:64] if client_ip else None,
                user_agent=user_agent[:512] if user_agent else None,
            )
            db.add(session)
            db.flush()
            self._audit(
                db,
                tenant_id=tenant_id,
                actor_user_id=user.id,
                event_type="external_identity.authenticated",
                target_type="session",
                target_id=session.id,
                client_ip=client_ip,
                details={"provider": provider},
            )
            return IssuedSession(
                session_id=session.id,
                tenant_id=tenant_id,
                user_id=user.id,
                expires_at=session.expires_at,
                token=token,
            )

    def authenticate_session(
        self, *, tenant_id: UUID, token: str, touch: bool = False
    ) -> IdentityPrincipal:
        now = self._now()
        token_hash = self._tokens.digest(token, purpose=f"session:{tenant_id}")
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, tenant_id)
            row = db.execute(
                select(UserSession, Membership, User, Tenant)
                .join(Membership, Membership.id == UserSession.membership_id)
                .join(User, User.id == UserSession.user_id)
                .join(Tenant, Tenant.id == UserSession.tenant_id)
                .where(
                    UserSession.tenant_id == tenant_id,
                    UserSession.token_hash == token_hash,
                )
            ).one_or_none()
            if row is None:
                raise AuthenticationFailed("invalid session")
            user_session, membership, user, tenant = row
            if (
                user_session.revoked_at is not None
                or user_session.expires_at <= now
                or not membership.is_active
                or not user.is_active
                or not tenant.is_active
                or membership.tenant_id != tenant_id
            ):
                raise AuthenticationFailed("invalid session")
            if touch:
                user_session.last_seen_at = now
            return IdentityPrincipal(
                tenant_id=tenant_id,
                user_id=user.id,
                membership_id=membership.id,
                session_id=user_session.id,
                role=MembershipRole(membership.role),
                session_token_hash=token_hash,
            )

    def authenticate_session_token(self, *, token: str, touch: bool = False) -> SessionProfile:
        tenant_id = self.tenant_id_from_token(token)
        principal = self.authenticate_session(tenant_id=tenant_id, token=token, touch=touch)
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, tenant_id)
            row = db.execute(
                select(UserSession, Membership, User)
                .join(Membership, Membership.id == UserSession.membership_id)
                .join(User, User.id == UserSession.user_id)
                .where(
                    UserSession.id == principal.session_id,
                    UserSession.tenant_id == tenant_id,
                    Membership.id == principal.membership_id,
                    User.id == principal.user_id,
                )
            ).one_or_none()
            if row is None:
                raise AuthenticationFailed("invalid session")
            user_session, membership, user = row
            return SessionProfile(
                tenant_id=tenant_id,
                user_id=user.id,
                membership_id=membership.id,
                session_id=user_session.id,
                email=user.email_normalized,
                display_name=user.display_name,
                role=MembershipRole(membership.role),
                expires_at=user_session.expires_at,
                email_verified=user.email_verified_at is not None,
                session_token_hash=user_session.token_hash,
            )

    def tenant_id_by_slug(self, slug: str) -> UUID:
        normalized = self._normalize_slug(slug)
        with self._sessions() as db:
            self._set_tenant_slug_scope(db, normalized)
            tenant_id = db.scalar(
                select(Tenant.id).where(Tenant.slug == normalized, Tenant.is_active.is_(True))
            )
        if tenant_id is None:
            raise AuthenticationFailed("invalid credentials")
        return tenant_id

    def revoke_session(
        self, *, principal: IdentityPrincipal, session_id: UUID, client_ip: str | None = None
    ) -> None:
        now = self._now()
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, principal.tenant_id)
            actor_role = self._validated_actor(db, principal)
            target = db.execute(
                select(UserSession).where(
                    UserSession.id == session_id,
                    UserSession.tenant_id == principal.tenant_id,
                )
            ).scalar_one_or_none()
            if target is None:
                raise AuthorizationDenied("session not found in tenant")
            can_revoke = target.user_id == principal.user_id or actor_role in {
                MembershipRole.OWNER,
                MembershipRole.ADMIN,
            }
            if not can_revoke:
                raise AuthorizationDenied("role cannot revoke another user's session")
            if target.revoked_at is None:
                target.revoked_at = now
                self._audit(
                    db,
                    tenant_id=principal.tenant_id,
                    actor_user_id=principal.user_id,
                    event_type="session.revoked",
                    target_type="session",
                    target_id=target.id,
                    client_ip=client_ip,
                )

    def issue_password_reset(
        self,
        *,
        tenant_id: UUID,
        email: str,
        ttl: timedelta = timedelta(minutes=30),
        client_ip: str | None = None,
    ) -> IssuedResetToken | None:
        """Issue reset material for a delivery adapter; callers must hide misses."""

        if ttl <= timedelta(0) or ttl > timedelta(hours=24):
            raise ValueError("reset lifetime must be between zero and 24 hours")
        normalized = self._normalize_email(email)
        now = self._now()
        raw_token = f"{tenant_id}.{self._tokens.issue()}"
        token_hash = self._tokens.digest(raw_token, purpose=f"reset:{tenant_id}")
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, tenant_id)
            row = db.execute(
                select(User, Membership)
                .join(Membership, Membership.user_id == User.id)
                .where(
                    User.email_normalized == normalized,
                    Membership.tenant_id == tenant_id,
                    User.is_active.is_(True),
                    Membership.is_active.is_(True),
                )
            ).one_or_none()
            if row is None:
                return None
            user, _membership = row
            reset = PasswordResetToken(
                tenant_id=tenant_id,
                user_id=user.id,
                token_hash=token_hash,
                expires_at=now + ttl,
            )
            db.add(reset)
            db.flush()
            self._audit(
                db,
                tenant_id=tenant_id,
                actor_user_id=user.id,
                event_type="password_reset.issued",
                target_type="password_reset",
                target_id=reset.id,
                client_ip=client_ip,
            )
            return IssuedResetToken(
                reset_id=reset.id,
                tenant_id=tenant_id,
                user_id=user.id,
                expires_at=reset.expires_at,
                token=raw_token,
            )

    def issue_email_verification(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        ttl: timedelta = timedelta(hours=24),
        client_ip: str | None = None,
    ) -> IssuedVerificationToken | None:
        if ttl <= timedelta(0) or ttl > timedelta(days=7):
            raise ValueError("verification lifetime must be between zero and 7 days")
        now = self._now()
        raw_token = f"{tenant_id}.{self._tokens.issue()}"
        token_hash = self._tokens.digest(raw_token, purpose=f"verify-email:{tenant_id}")
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, tenant_id)
            row = db.execute(
                select(User, Membership)
                .join(Membership, Membership.user_id == User.id)
                .where(
                    User.id == user_id,
                    Membership.tenant_id == tenant_id,
                    User.is_active.is_(True),
                    Membership.is_active.is_(True),
                )
            ).one_or_none()
            if row is None:
                raise AuthorizationDenied("user is not active in tenant")
            user, _membership = row
            if user.email_verified_at is not None:
                return None
            verification = EmailVerificationToken(
                tenant_id=tenant_id,
                user_id=user_id,
                token_hash=token_hash,
                expires_at=now + ttl,
            )
            db.add(verification)
            db.flush()
            self._audit(
                db,
                tenant_id=tenant_id,
                actor_user_id=user_id,
                event_type="email_verification.issued",
                target_type="email_verification",
                target_id=verification.id,
                client_ip=client_ip,
            )
            return IssuedVerificationToken(
                verification_id=verification.id,
                tenant_id=tenant_id,
                user_id=user_id,
                expires_at=verification.expires_at,
                token=raw_token,
            )

    def consume_email_verification_token(self, *, token: str, client_ip: str | None = None) -> UUID:
        tenant_id = self.tenant_id_from_token(token)
        token_hash = self._tokens.digest(token, purpose=f"verify-email:{tenant_id}")
        now = self._now()
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, tenant_id)
            verification = db.scalar(
                select(EmailVerificationToken).where(
                    EmailVerificationToken.tenant_id == tenant_id,
                    EmailVerificationToken.token_hash == token_hash,
                )
            )
            if (
                verification is None
                or verification.consumed_at is not None
                or verification.expires_at <= now
            ):
                raise InvalidToken("verification token is invalid or expired")
            claimed = db.execute(
                update(EmailVerificationToken)
                .where(
                    EmailVerificationToken.id == verification.id,
                    EmailVerificationToken.tenant_id == tenant_id,
                    EmailVerificationToken.consumed_at.is_(None),
                    EmailVerificationToken.expires_at > now,
                )
                .values(consumed_at=now)
            )
            if claimed.rowcount != 1:
                raise InvalidToken("verification token is invalid or expired")
            user = db.scalar(select(User).where(User.id == verification.user_id))
            if user is None or not user.is_active:
                raise InvalidToken("verification token is invalid or expired")
            user.email_verified_at = now
            db.execute(
                update(EmailVerificationToken)
                .where(
                    EmailVerificationToken.tenant_id == tenant_id,
                    EmailVerificationToken.user_id == user.id,
                    EmailVerificationToken.consumed_at.is_(None),
                )
                .values(consumed_at=now)
            )
            self._audit(
                db,
                tenant_id=tenant_id,
                actor_user_id=user.id,
                event_type="email_verification.completed",
                target_type="user",
                target_id=user.id,
                client_ip=client_ip,
            )
            return user.id

    def consume_password_reset(
        self,
        *,
        tenant_id: UUID,
        token: str,
        new_password: str,
        client_ip: str | None = None,
    ) -> UUID:
        password_hash = self._passwords.hash(new_password)
        token_hash = self._tokens.digest(token, purpose=f"reset:{tenant_id}")
        now = self._now()
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, tenant_id)
            reset = db.execute(
                select(PasswordResetToken).where(
                    PasswordResetToken.tenant_id == tenant_id,
                    PasswordResetToken.token_hash == token_hash,
                )
            ).scalar_one_or_none()
            if reset is None or reset.consumed_at is not None or reset.expires_at <= now:
                raise InvalidToken("reset token is invalid or expired")

            claimed = db.execute(
                update(PasswordResetToken)
                .where(
                    PasswordResetToken.id == reset.id,
                    PasswordResetToken.tenant_id == tenant_id,
                    PasswordResetToken.consumed_at.is_(None),
                    PasswordResetToken.expires_at > now,
                )
                .values(consumed_at=now)
            )
            if claimed.rowcount != 1:
                raise InvalidToken("reset token is invalid or expired")

            user = db.execute(
                select(User)
                .join(Membership, Membership.user_id == User.id)
                .where(User.id == reset.user_id, Membership.tenant_id == tenant_id)
            ).scalar_one_or_none()
            if user is None or not user.is_active:
                raise InvalidToken("reset token is invalid or expired")
            user.password_hash = password_hash
            user.failed_login_attempts = 0
            user.auth_locked_until = None
            db.execute(
                update(UserSession)
                .where(
                    UserSession.tenant_id == tenant_id,
                    UserSession.user_id == user.id,
                    UserSession.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
            self._audit(
                db,
                tenant_id=tenant_id,
                actor_user_id=user.id,
                event_type="password_reset.consumed",
                target_type="user",
                target_id=user.id,
                client_ip=client_ip,
            )
            return user.id

    def consume_password_reset_token(
        self, *, token: str, new_password: str, client_ip: str | None = None
    ) -> UUID:
        tenant_id = self.tenant_id_from_token(token)
        return self.consume_password_reset(
            tenant_id=tenant_id,
            token=token,
            new_password=new_password,
            client_ip=client_ip,
        )

    def list_audit_events(
        self, *, principal: IdentityPrincipal, limit: int = 100
    ) -> tuple[AuditEvent, ...]:
        if limit < 1 or limit > 500:
            raise ValueError("audit limit must be between 1 and 500")
        statement: Select[tuple[AuditEvent]] = (
            select(AuditEvent)
            .where(AuditEvent.tenant_id == principal.tenant_id)
            .order_by(AuditEvent.occurred_at.desc())
            .limit(limit)
        )
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, principal.tenant_id)
            self._validated_actor(
                db,
                principal,
                allowed={MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.AUDITOR},
            )
            return tuple(db.scalars(statement))

    def grant_membership(
        self,
        *,
        principal: IdentityPrincipal,
        user_id: UUID,
        role: MembershipRole,
        client_ip: str | None = None,
    ) -> UUID:
        """Attach an existing global user to the actor's tenant."""

        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, principal.tenant_id)
            actor_role = self._validated_actor(
                db,
                principal,
                allowed={MembershipRole.OWNER, MembershipRole.ADMIN},
            )
            if actor_role is MembershipRole.ADMIN and role in {
                MembershipRole.OWNER,
                MembershipRole.ADMIN,
            }:
                raise AuthorizationDenied("an administrator cannot grant owner or administrator")
            user = db.get(User, user_id)
            if user is None or not user.is_active:
                raise Conflict("target user does not exist or is inactive")
            existing = db.scalar(
                select(Membership).where(
                    Membership.tenant_id == principal.tenant_id,
                    Membership.user_id == user_id,
                )
            )
            if existing is not None:
                raise Conflict("membership already exists")
            membership = Membership(
                tenant_id=principal.tenant_id,
                user_id=user_id,
                role=role.value,
            )
            db.add(membership)
            db.flush()
            self._audit(
                db,
                tenant_id=principal.tenant_id,
                actor_user_id=principal.user_id,
                event_type="membership.granted",
                target_type="membership",
                target_id=membership.id,
                client_ip=client_ip,
                details={"user_id": str(user_id), "role": role.value},
            )
            return membership.id

    def change_membership_role(
        self,
        *,
        principal: IdentityPrincipal,
        membership_id: UUID,
        role: MembershipRole,
        client_ip: str | None = None,
    ) -> None:
        """Change a role without allowing admins to control privileged roles."""

        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, principal.tenant_id)
            actor_role = self._validated_actor(
                db,
                principal,
                allowed={MembershipRole.OWNER, MembershipRole.ADMIN},
            )
            target = db.scalar(
                select(Membership).where(
                    Membership.id == membership_id,
                    Membership.tenant_id == principal.tenant_id,
                )
            )
            if target is None:
                raise AuthorizationDenied("membership not found in tenant")
            old_role = MembershipRole(target.role)
            if actor_role is MembershipRole.ADMIN and (
                old_role in {MembershipRole.OWNER, MembershipRole.ADMIN}
                or role in {MembershipRole.OWNER, MembershipRole.ADMIN}
            ):
                raise AuthorizationDenied("an administrator cannot control privileged roles")
            if old_role is MembershipRole.OWNER and role is not MembershipRole.OWNER:
                owner_count = db.scalar(
                    select(func.count())
                    .select_from(Membership)
                    .where(
                        Membership.tenant_id == principal.tenant_id,
                        Membership.role == MembershipRole.OWNER.value,
                        Membership.is_active.is_(True),
                    )
                )
                if owner_count == 1:
                    raise Conflict("the last active owner cannot be demoted")
            target.role = role.value
            self._audit(
                db,
                tenant_id=principal.tenant_id,
                actor_user_id=principal.user_id,
                event_type="membership.role_changed",
                target_type="membership",
                target_id=membership_id,
                client_ip=client_ip,
                details={"old_role": old_role.value, "new_role": role.value},
            )

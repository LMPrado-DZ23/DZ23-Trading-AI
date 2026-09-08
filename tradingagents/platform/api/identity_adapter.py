"""Async HTTP adapter for the transactional SQLAlchemy identity service."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Protocol

from anyio import to_thread

from tradingagents.platform.api.email_delivery import IdentityDeliveryUnavailable
from tradingagents.platform.api.identity import (
    AuthenticatedSession,
    CommandContext,
    IdentityError as ApiIdentityError,
    IdentityPrincipal as ApiIdentityPrincipal,
)
from tradingagents.platform.identity import (
    AuthenticationFailed,
    Conflict,
    IdentityError,
    IdentityPrincipal,
    IdentityService,
    InvalidToken,
    IssuedSession,
    SessionProfile,
)
from tradingagents.platform.supabase_auth import SupabaseAuthClaims, SupabaseAuthError


class SupabaseVerifierPort(Protocol):
    """Verify one social access token without exposing provider credentials."""

    def verify(self, token: str) -> Awaitable[SupabaseAuthClaims]: ...


class RecoveryDeliveryPort(Protocol):
    """Deliver one recovery link without logging the raw token."""

    def __call__(self, *, recipient: str, token: str, expires_at: datetime) -> Awaitable[None]: ...


class VerificationDeliveryPort(Protocol):
    """Deliver one e-mail verification link without logging its raw token."""

    def __call__(self, *, recipient: str, token: str, expires_at: datetime) -> Awaitable[None]: ...


class ReadinessCheckPort(Protocol):
    def __call__(self) -> Awaitable[bool]: ...


@dataclass(frozen=True, slots=True)
class SqlAlchemyIdentityAdapter:
    service: IdentityService
    recovery_delivery: RecoveryDeliveryPort
    verification_delivery: VerificationDeliveryPort
    session_ttl: timedelta = timedelta(hours=12)
    recovery_ttl: timedelta = timedelta(minutes=30)
    verification_ttl: timedelta = timedelta(hours=24)
    supabase_verifier: SupabaseVerifierPort | None = None
    readiness_checks: tuple[ReadinessCheckPort, ...] = ()

    async def register(
        self,
        *,
        email: str,
        password: str,
        display_name: str,
        organization_name: str,
        organization_slug: str,
        context: CommandContext,
    ) -> AuthenticatedSession:
        try:
            result = await _run_sync(
                self.service.register_tenant,
                tenant_name=organization_name,
                tenant_slug=organization_slug,
                owner_email=email,
                display_name=display_name,
                password=password,
                client_ip=context.client_ip,
            )
            issued = await _run_sync(
                self.service.authenticate,
                tenant_id=result.tenant_id,
                email=email,
                password=password,
                ttl=self.session_ttl,
                client_ip=context.client_ip,
                user_agent=context.user_agent,
            )
            verification = await _run_sync(
                self.service.issue_email_verification,
                tenant_id=result.tenant_id,
                user_id=result.user_id,
                ttl=self.verification_ttl,
                client_ip=context.client_ip,
            )
            if verification is None:
                raise ApiIdentityError("REGISTRATION_INVALID")
            with suppress(IdentityDeliveryUnavailable):
                await self.verification_delivery(
                    recipient=email,
                    token=verification.token,
                    expires_at=verification.expires_at,
                )
            return await self._to_api_session(issued)
        except Conflict as exc:
            raise ApiIdentityError("REGISTRATION_CONFLICT") from exc
        except (IdentityError, ValueError) as exc:
            raise ApiIdentityError("REGISTRATION_INVALID") from exc

    async def authenticate(
        self,
        *,
        email: str,
        password: str,
        organization_slug: str,
        context: CommandContext,
    ) -> AuthenticatedSession:
        try:
            tenant_id = await _run_sync(self.service.tenant_id_by_slug, organization_slug)
            issued = await _run_sync(
                self.service.authenticate,
                tenant_id=tenant_id,
                email=email,
                password=password,
                ttl=self.session_ttl,
                client_ip=context.client_ip,
                user_agent=context.user_agent,
            )
            return await self._to_api_session(issued)
        except (AuthenticationFailed, ValueError) as exc:
            raise ApiIdentityError("INVALID_CREDENTIALS") from exc

    async def exchange_supabase_token(
        self,
        *,
        access_token: str,
        organization_slug: str,
        organization_name: str | None,
        display_name: str | None,
        context: CommandContext,
    ) -> AuthenticatedSession:
        if self.supabase_verifier is None:
            raise ApiIdentityError("SOCIAL_AUTH_UNAVAILABLE")
        try:
            claims = await self.supabase_verifier.verify(access_token)
            remaining = claims.expires_at - datetime.now(UTC)
            if remaining <= timedelta(0):
                raise ApiIdentityError("SOCIAL_AUTH_INVALID")
            external_session_ttl = min(self.session_ttl, remaining)
            try:
                issued = await _run_sync(
                    self.service.authenticate_external,
                    auth_subject=claims.subject,
                    auth_provider="supabase",
                    organization_slug=organization_slug,
                    ttl=external_session_ttl,
                    client_ip=context.client_ip,
                    user_agent=context.user_agent,
                )
            except AuthenticationFailed:
                if organization_name is None or display_name is None:
                    raise ApiIdentityError("SOCIAL_ONBOARDING_REQUIRED") from None
                issued = await _run_sync(
                    self.service.register_external_tenant,
                    auth_subject=claims.subject,
                    auth_provider="supabase",
                    verified_email=claims.email,
                    display_name=display_name,
                    tenant_name=organization_name,
                    tenant_slug=organization_slug,
                    ttl=external_session_ttl,
                    client_ip=context.client_ip,
                    user_agent=context.user_agent,
                )
            return await self._to_api_session(issued)
        except ApiIdentityError:
            raise
        except (SupabaseAuthError, IdentityError, ValueError) as exc:
            raise ApiIdentityError("SOCIAL_AUTH_INVALID") from exc

    async def resolve_session(self, *, token: str, context: CommandContext) -> ApiIdentityPrincipal:
        try:
            profile = await _run_sync(
                self.service.authenticate_session_token,
                token=token,
                touch=True,
            )
            return _api_principal(profile)
        except (IdentityError, ValueError) as exc:
            raise ApiIdentityError("SESSION_INVALID") from exc

    async def revoke_session(self, *, token: str, context: CommandContext) -> None:
        try:
            profile = await _run_sync(
                self.service.authenticate_session_token,
                token=token,
            )
            principal = IdentityPrincipal(
                tenant_id=profile.tenant_id,
                user_id=profile.user_id,
                membership_id=profile.membership_id,
                session_id=profile.session_id,
                role=profile.role,
            )
            await _run_sync(
                self.service.revoke_session,
                principal=principal,
                session_id=profile.session_id,
                client_ip=context.client_ip,
            )
        except IdentityError as exc:
            raise ApiIdentityError("SESSION_INVALID") from exc

    async def request_password_reset(
        self,
        *,
        email: str,
        organization_slug: str,
        context: CommandContext,
    ) -> None:
        try:
            tenant_id = await _run_sync(self.service.tenant_id_by_slug, organization_slug)
        except (AuthenticationFailed, ValueError):
            return
        reset = await _run_sync(
            self.service.issue_password_reset,
            tenant_id=tenant_id,
            email=email,
            ttl=self.recovery_ttl,
            client_ip=context.client_ip,
        )
        if reset is not None:
            with suppress(IdentityDeliveryUnavailable):
                await self.recovery_delivery(
                    recipient=email,
                    token=reset.token,
                    expires_at=reset.expires_at,
                )

    async def reset_password(
        self,
        *,
        recovery_token: str,
        new_password: str,
        context: CommandContext,
    ) -> None:
        try:
            await _run_sync(
                self.service.consume_password_reset_token,
                token=recovery_token,
                new_password=new_password,
                client_ip=context.client_ip,
            )
        except (InvalidToken, ValueError) as exc:
            raise ApiIdentityError("RECOVERY_INVALID") from exc

    async def request_email_verification(
        self,
        *,
        token: str,
        principal: ApiIdentityPrincipal,
        context: CommandContext,
    ) -> None:
        try:
            profile = await _run_sync(
                self.service.authenticate_session_token,
                token=token,
            )
            if profile.user_id != principal.user_id or profile.tenant_id != principal.tenant_id:
                raise ApiIdentityError("SESSION_INVALID")
            if profile.email_verified:
                return
            verification = await _run_sync(
                self.service.issue_email_verification,
                tenant_id=profile.tenant_id,
                user_id=profile.user_id,
                ttl=self.verification_ttl,
                client_ip=context.client_ip,
            )
            if verification is None:
                return
            with suppress(IdentityDeliveryUnavailable):
                await self.verification_delivery(
                    recipient=profile.email,
                    token=verification.token,
                    expires_at=verification.expires_at,
                )
        except ApiIdentityError:
            raise
        except (IdentityError, ValueError) as exc:
            raise ApiIdentityError("SESSION_INVALID") from exc

    async def verify_email(
        self,
        *,
        verification_token: str,
        context: CommandContext,
    ) -> None:
        try:
            await _run_sync(
                self.service.consume_email_verification_token,
                token=verification_token,
                client_ip=context.client_ip,
            )
        except (InvalidToken, ValueError) as exc:
            raise ApiIdentityError("VERIFICATION_INVALID") from exc

    async def ready(self) -> bool:
        if not await _run_sync(self.service.ready):
            return False
        for check in self.readiness_checks:
            try:
                if not await check():
                    return False
            except Exception:
                return False
        return True

    async def _to_api_session(self, issued: IssuedSession) -> AuthenticatedSession:
        profile = await _run_sync(
            self.service.authenticate_session_token,
            token=issued.token,
        )
        return AuthenticatedSession(token=issued.token, principal=_api_principal(profile))


def _api_principal(profile: SessionProfile) -> ApiIdentityPrincipal:
    return ApiIdentityPrincipal(
        user_id=profile.user_id,
        tenant_id=profile.tenant_id,
        email=profile.email,
        display_name=profile.display_name,
        roles=(profile.role.value,),
        email_verified=profile.email_verified,
        session_id=profile.session_id,
        expires_at=profile.expires_at,
    )


async def _run_sync(function: Callable, /, *args, **kwargs):
    return await to_thread.run_sync(partial(function, *args, **kwargs))

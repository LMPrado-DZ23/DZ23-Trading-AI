"""Ports used by the HTTP identity boundary.

Concrete persistence, password hashing, email delivery and audit logging belong
to an infrastructure adapter.  Keeping this as a port prevents the web layer
from silently acquiring a second identity or tenant model.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID


@dataclass(frozen=True, slots=True)
class CommandContext:
    """Security-relevant request metadata supplied to identity operations."""

    request_id: str
    client_ip: str
    user_agent: str | None


@dataclass(frozen=True, slots=True)
class IdentityPrincipal:
    """Authenticated identity with a server-authoritative tenant binding."""

    user_id: UUID
    tenant_id: UUID
    email: str
    display_name: str
    roles: tuple[str, ...]
    email_verified: bool
    session_id: UUID
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("expires_at deve incluir fuso horário")
        if not self.roles:
            raise ValueError("a identidade deve possuir ao menos um papel")


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    """Opaque browser session created by the identity service."""

    token: str
    principal: IdentityPrincipal

    def __post_init__(self) -> None:
        if len(self.token) < 32:
            raise ValueError("o token opaco de sessão deve conter ao menos 32 caracteres")


class IdentityError(Exception):
    """Expected identity failure safe to translate into a public error code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@runtime_checkable
class IdentityServicePort(Protocol):
    """Application port implemented by the durable identity service."""

    async def register(
        self,
        *,
        email: str,
        password: str,
        display_name: str,
        organization_name: str,
        organization_slug: str,
        context: CommandContext,
    ) -> AuthenticatedSession: ...

    async def authenticate(
        self, *, email: str, password: str, organization_slug: str, context: CommandContext
    ) -> AuthenticatedSession: ...

    async def exchange_supabase_token(
        self,
        *,
        access_token: str,
        organization_slug: str,
        organization_name: str | None,
        display_name: str | None,
        context: CommandContext,
    ) -> AuthenticatedSession: ...

    async def resolve_session(
        self, *, token: str, context: CommandContext
    ) -> IdentityPrincipal: ...

    async def revoke_session(self, *, token: str, context: CommandContext) -> None: ...

    async def request_password_reset(
        self, *, email: str, organization_slug: str, context: CommandContext
    ) -> None:
        """Request delivery while hiding whether the account exists."""
        ...

    async def reset_password(
        self, *, recovery_token: str, new_password: str, context: CommandContext
    ) -> None: ...

    async def request_email_verification(
        self, *, token: str, principal: IdentityPrincipal, context: CommandContext
    ) -> None: ...

    async def verify_email(self, *, verification_token: str, context: CommandContext) -> None: ...

    async def ready(self) -> bool: ...

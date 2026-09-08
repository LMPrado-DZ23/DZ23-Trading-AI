"""Composition root for the production identity platform."""

from __future__ import annotations

import hashlib
import hmac
from datetime import timedelta

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from tradingagents.platform.api import (
    ApiSecuritySettings,
    SqlAlchemyIdentityAdapter,
    create_platform_app,
)
from tradingagents.platform.api.compliance_adapter import SqlAlchemyComplianceAdapter
from tradingagents.platform.api.email_delivery import (
    IdentityEmailKind,
    SmtpIdentityTokenDelivery,
    SmtpSettings,
)
from tradingagents.platform.api.redis_rate_limit import RedisRateLimiter
from tradingagents.platform.compliance import (
    AesGcmSecretCrypto,
    ComplianceService,
    MfaService,
    RecoveryCodeHasher,
)
from tradingagents.platform.identity import IdentityService, TokenHasher
from tradingagents.platform.settings import Environment, PlatformSettings
from tradingagents.platform.supabase_auth import (
    SupabaseAuthConfig,
    SupabaseJwtVerifier,
    SupabaseUserVerifier,
)


def _derive_secret(master: bytes, purpose: str) -> bytes:
    if len(master) < 32:
        raise ValueError("master identity secret must contain at least 32 bytes")
    return hmac.new(master, purpose.encode("ascii"), hashlib.sha256).digest()


def create_production_app(settings: PlatformSettings | None = None) -> FastAPI:
    """Build the API with all runtime dependencies injected explicitly."""

    effective = settings or PlatformSettings()
    master = effective.session_token_pepper.get_secret_value().encode("utf-8")
    engine = create_engine(
        effective.database_url.get_secret_value(),
        pool_pre_ping=True,
        hide_parameters=True,
    )

    @event.listens_for(engine, "connect")
    def assert_runtime_database_role(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(
                "SELECT current_user, rolsuper, rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user"
            )
            row = cursor.fetchone()
        finally:
            cursor.close()
        if (
            row is None
            or row[0] != effective.database_expected_role
            or row[1] is not False
            or row[2] is not False
        ):
            raise RuntimeError("database runtime role is unsafe or does not match configuration")
    factory = sessionmaker(engine, expire_on_commit=False)
    identity = IdentityService(
        factory,
        token_hasher=TokenHasher(_derive_secret(master, "identity-token-hash")),
        max_auth_failures=effective.max_auth_failures,
        auth_lock_duration=timedelta(seconds=effective.auth_lock_seconds),
    )
    supabase_http = httpx.AsyncClient(
        follow_redirects=False,
        trust_env=False,
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )
    supabase_config = SupabaseAuthConfig(
        project_ref=effective.supabase_project_ref,
        project_url=effective.supabase_url,
    )
    supabase_jwt_verifier = SupabaseJwtVerifier(supabase_config, supabase_http)
    supabase_verifier = SupabaseUserVerifier(
        supabase_config,
        effective.supabase_publishable_key.get_secret_value(),
        supabase_jwt_verifier,
        supabase_http,
    )

    smtp = SmtpSettings(
        host=effective.smtp_host,
        port=effective.smtp_port,
        username=effective.smtp_username,
        password=effective.smtp_password.get_secret_value(),
        sender=effective.smtp_sender,
        use_implicit_tls=effective.smtp_implicit_tls,
    )
    compliance = ComplianceService(factory)
    mfa = MfaService(
        factory,
        crypto=AesGcmSecretCrypto(
            key=effective.mfa_encryption_key,
            key_id="platform-mfa-key-v1",
        ),
        recovery_hasher=RecoveryCodeHasher(_derive_secret(master, "mfa-recovery-codes")),
    )
    redis = Redis.from_url(
        effective.redis_url.get_secret_value(),
        decode_responses=False,
        socket_connect_timeout=5,
        socket_timeout=5,
    )

    async def redis_ready() -> bool:
        return bool(await redis.ping())

    adapter = SqlAlchemyIdentityAdapter(
        service=identity,
        recovery_delivery=SmtpIdentityTokenDelivery(
            settings=smtp,
            public_url=effective.public_url,
            kind=IdentityEmailKind.RECOVER,
        ),
        verification_delivery=SmtpIdentityTokenDelivery(
            settings=smtp,
            public_url=effective.public_url,
            kind=IdentityEmailKind.VERIFY,
        ),
        session_ttl=timedelta(seconds=effective.session_ttl_seconds),
        recovery_ttl=timedelta(seconds=effective.recovery_ttl_seconds),
        supabase_verifier=supabase_verifier,
        readiness_checks=(redis_ready,),
    )
    app = create_platform_app(
        identity_service=adapter,
        security=ApiSecuritySettings(
            csrf_secret=_derive_secret(master, "csrf-signing"),
            trusted_proxy_cidrs=tuple(effective.trusted_proxy_networks),
            session_ttl_seconds=effective.session_ttl_seconds,
            allowed_origins=tuple(effective.allowed_origins),
            strict_transport_security=effective.environment is Environment.PRODUCTION,
        ),
        rate_limiter=RedisRateLimiter(redis),
        compliance_service=SqlAlchemyComplianceAdapter(
            identity=identity,
            compliance=compliance,
            mfa=mfa,
        ),
    )
    app.state.database_engine = engine
    app.state.redis = redis
    app.state.supabase_http = supabase_http

    async def close_runtime_resources() -> None:
        """Release network pools before the process finishes."""

        await supabase_http.aclose()
        await redis.aclose()
        engine.dispose()

    app.router.add_event_handler("shutdown", close_runtime_resources)
    return app

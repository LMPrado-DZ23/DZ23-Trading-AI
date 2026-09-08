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
from tradingagents.platform.api.connections_adapter import SqlAlchemyBrokerConnectionAdapter
from tradingagents.platform.api.email_delivery import (
    IdentityEmailKind,
    SmtpIdentityTokenDelivery,
    SmtpSettings,
)
from tradingagents.platform.api.jobs_adapter import SqlAlchemyAnalysisJobAdapter
from tradingagents.platform.api.llm_connections_adapter import SqlAlchemyLlmConnectionAdapter
from tradingagents.platform.api.redis_rate_limit import RedisRateLimiter
from tradingagents.platform.compliance import (
    AesGcmSecretCrypto,
    ComplianceService,
    MfaService,
    RecoveryCodeHasher,
)
from tradingagents.platform.connections import (
    CCXTReadOnlyDiagnostics,
    ConnectionService,
    CredentialKeyring,
    TransitCredentialKeyring,
)
from tradingagents.platform.identity import IdentityService, TokenHasher
from tradingagents.platform.jobs import JobService
from tradingagents.platform.llm_connections import LlmConnectionService
from tradingagents.platform.settings import (
    BrokerCredentialProvider,
    Environment,
    PlatformSettings,
)
from tradingagents.platform.supabase_auth import (
    SupabaseAuthConfig,
    SupabaseJwtVerifier,
    SupabaseUserVerifier,
)
from tradingagents.platform.usage import PricingCatalog


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
                "SELECT current_user, rolsuper, rolcreatedb, rolcreaterole, rolreplication, "
                "rolbypassrls, "
                "EXISTS (SELECT 1 FROM pg_roles candidate "
                "WHERE candidate.rolname <> current_user "
                "AND pg_has_role(current_user, candidate.oid, 'MEMBER')) "
                "FROM pg_roles WHERE rolname = current_user"
            )
            row = cursor.fetchone()
        finally:
            cursor.close()
        if (
            row is None
            or row[0] != effective.database_expected_role
            or row[1] is not False
            or any(value is not False for value in row[1:])
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
    configured_broker_keys = effective.broker_credential_keys_json.get_secret_value()
    if effective.broker_credential_provider is BrokerCredentialProvider.OPENBAO_TRANSIT:
        broker_crypto = TransitCredentialKeyring(
            base_url=effective.broker_transit_url,
            key_name=effective.broker_transit_key_name,
            token=effective.broker_transit_token.get_secret_value(),
            namespace=effective.broker_transit_namespace or None,
        )
        llm_crypto = TransitCredentialKeyring(
            base_url=effective.broker_transit_url,
            key_name=effective.llm_transit_key_name,
            token=effective.llm_transit_write_token.get_secret_value(),
            namespace=effective.broker_transit_namespace or None,
        )
    else:
        broker_keys = (
            effective.broker_credential_keys
            if configured_broker_keys
            else {"development-derived-v1": _derive_secret(master, "broker-credentials")}
        )
        active_broker_key = (
            effective.broker_active_credential_key_id
            if configured_broker_keys
            else "development-derived-v1"
        )
        broker_crypto = CredentialKeyring(
            keys=broker_keys,
            active_key_id=active_broker_key,
        )
        llm_crypto = CredentialKeyring(
            keys={
                "development-llm-v1": _derive_secret(
                    effective.worker_job_auth_key.get_secret_value().encode("utf-8"),
                    "llm-credentials",
                )
            },
            active_key_id="development-llm-v1",
        )
    connection_service = ConnectionService(
        factory,
        keyring=broker_crypto,
        diagnostics=CCXTReadOnlyDiagnostics(
            resolve_hosts=effective.environment in {Environment.STAGING, Environment.PRODUCTION}
        ),
        idempotency_digest_key=_derive_secret(master, "broker-idempotency-digest"),
    )
    llm_connection_service = LlmConnectionService(
        factory,
        keyring=llm_crypto,
        idempotency_digest_key=_derive_secret(master, "llm-idempotency-digest"),
    )
    pricing_catalog = PricingCatalog(effective.llm_pricing)
    job_service = JobService(
        factory,
        idempotency_digest_key=effective.worker_job_auth_key.get_secret_value().encode("utf-8"),
        llm_reference_validator=llm_connection_service,
        max_active_jobs_per_tenant=effective.max_active_jobs_per_tenant,
        monthly_token_quota=effective.monthly_token_quota,
        monthly_cost_quota_microusd=effective.monthly_cost_quota_microusd,
        pricing_catalog_version=pricing_catalog.version,
        pricing_catalog=pricing_catalog,
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
        broker_connection_service=SqlAlchemyBrokerConnectionAdapter(
            identity=identity,
            mfa=mfa,
            connections=connection_service,
        ),
        analysis_job_service=SqlAlchemyAnalysisJobAdapter(
            identity=identity,
            jobs=job_service,
        ),
        llm_connection_service=SqlAlchemyLlmConnectionAdapter(
            identity=identity,
            mfa=mfa,
            connections=llm_connection_service,
        ),
    )
    app.state.database_engine = engine
    app.state.redis = redis
    app.state.supabase_http = supabase_http

    async def close_runtime_resources() -> None:
        """Release network pools before the process finishes."""

        await supabase_http.aclose()
        await redis.aclose()
        close_broker_crypto = getattr(broker_crypto, "close", None)
        if callable(close_broker_crypto):
            close_broker_crypto()
        close_llm_crypto = getattr(llm_crypto, "close", None)
        if callable(close_llm_crypto):
            close_llm_crypto()
        engine.dispose()

    app.router.add_event_handler("shutdown", close_runtime_resources)
    return app

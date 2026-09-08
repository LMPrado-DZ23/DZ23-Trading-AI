"""Composition root for the isolated analysis worker process."""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from tradingagents.platform.connections import CredentialKeyring, TransitCredentialKeyring
from tradingagents.platform.jobs import JobService
from tradingagents.platform.llm_connections import LlmConnectionService
from tradingagents.platform.settings import (
    BrokerCredentialProvider,
    Environment,
    PlatformSettings,
)
from tradingagents.platform.usage import PricingCatalog
from tradingagents.platform.worker import ProductionGraphExecutor, WorkerConfig, WorkerRunner


def _derive_secret(master: bytes, purpose: str) -> bytes:
    if len(master) < 32:
        raise ValueError("master identity secret must contain at least 32 bytes")
    return hmac.new(master, purpose.encode("ascii"), hashlib.sha256).digest()


@dataclass(slots=True)
class WorkerRuntime:
    runner: WorkerRunner
    engine: Engine
    credential_crypto: object
    poll_seconds: float

    def close(self) -> None:
        close = getattr(self.credential_crypto, "close", None)
        if callable(close):
            close()
        self.engine.dispose()


def create_worker_runtime(settings: PlatformSettings | None = None) -> WorkerRuntime:
    effective = settings or PlatformSettings()
    # This is a dedicated worker process. Restrictive umask ensures every
    # checkpoint/report/cache file created by legacy libraries remains private.
    if os.name == "posix":
        os.umask(0o077)
    if effective.environment in {Environment.STAGING, Environment.PRODUCTION} and (
        os.environ.get("TRADINGAGENTS_DISABLE_DOTENV", "").strip().lower()
        not in {"1", "true", "yes", "on"}
    ):
        raise RuntimeError("production worker requires TRADINGAGENTS_DISABLE_DOTENV=true")
    database_url = effective.worker_database_url.get_secret_value()
    if not database_url:
        raise RuntimeError("TRADINGAGENTS_WORKER_DATABASE_URL is required")
    engine = create_engine(database_url, pool_pre_ping=True, hide_parameters=True)

    @event.listens_for(engine, "connect")
    def assert_worker_database_role(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(
                "SELECT current_user, rolsuper, rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user"
            )
            row = cursor.fetchone()
        finally:
            cursor.close()
        if row is None or row[0] != "tradingagents_worker" or row[1] or row[2]:
            raise RuntimeError("worker database role is unsafe")

    factory = sessionmaker(engine, expire_on_commit=False)
    master = effective.session_token_pepper.get_secret_value().encode("utf-8")
    if effective.broker_credential_provider is BrokerCredentialProvider.OPENBAO_TRANSIT:
        crypto = TransitCredentialKeyring(
            base_url=effective.broker_transit_url,
            key_name=effective.llm_transit_key_name,
            token=effective.llm_transit_token.get_secret_value(),
            namespace=effective.broker_transit_namespace or None,
        )
    else:
        crypto = CredentialKeyring(
            keys={"development-llm-v1": _derive_secret(master, "llm-credentials")},
            active_key_id="development-llm-v1",
        )
    llm_connections = LlmConnectionService(
        factory,
        keyring=crypto,
        idempotency_digest_key=_derive_secret(master, "llm-idempotency-digest"),
    )
    jobs = JobService(
        factory,
        idempotency_digest_key=_derive_secret(master, "analysis-job-idempotency"),
        llm_reference_validator=llm_connections,
        max_active_jobs_per_tenant=effective.max_active_jobs_per_tenant,
        monthly_token_quota=effective.monthly_token_quota,
        monthly_cost_quota_microusd=effective.monthly_cost_quota_microusd,
    )
    runner = WorkerRunner(
        jobs=jobs,
        credentials=llm_connections,
        executor=ProductionGraphExecutor(
            runtime_root=Path(effective.worker_runtime_root),
            pricing=PricingCatalog(effective.llm_pricing),
            request_timeout_seconds=effective.llm_request_timeout_seconds,
        ),
        config=WorkerConfig(
            worker_id=effective.worker_id,
            lease_seconds=effective.worker_lease_seconds,
        ),
    )
    return WorkerRuntime(
        runner=runner,
        engine=engine,
        credential_crypto=crypto,
        poll_seconds=effective.worker_poll_seconds,
    )

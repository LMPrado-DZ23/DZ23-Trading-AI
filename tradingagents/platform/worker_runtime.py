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
    WorkerSettings,
)
from tradingagents.platform.usage import PricingCatalog
from tradingagents.platform.worker import ProductionGraphExecutor, WorkerConfig, WorkerRunner


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


def create_worker_runtime(settings: WorkerSettings | None = None) -> WorkerRuntime:
    effective = settings or WorkerSettings()
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
        if row is None or row[0] != "tradingagents_worker" or any(row[1:]):
            raise RuntimeError("worker database role is unsafe")

    factory = sessionmaker(engine, expire_on_commit=False)
    worker_job_auth = effective.worker_job_auth_key.get_secret_value().encode("utf-8")
    if effective.broker_credential_provider is BrokerCredentialProvider.OPENBAO_TRANSIT:
        crypto = TransitCredentialKeyring(
            base_url=effective.broker_transit_url,
            key_name=effective.llm_transit_key_name,
            token=effective.llm_transit_decrypt_token.get_secret_value(),
            namespace=effective.broker_transit_namespace or None,
        )
    else:
        crypto = CredentialKeyring(
            keys={
                "development-llm-v1": hmac.new(
                    worker_job_auth,
                    b"llm-credentials",
                    hashlib.sha256,
                ).digest()
            },
            active_key_id="development-llm-v1",
        )
    llm_connections = LlmConnectionService(
        factory,
        keyring=crypto,
        idempotency_digest_key=None,
    )
    pricing_catalog = PricingCatalog(effective.llm_pricing)
    jobs = JobService(
        factory,
        idempotency_digest_key=worker_job_auth,
        llm_reference_validator=llm_connections,
        max_active_jobs_per_tenant=effective.max_active_jobs_per_tenant,
        monthly_token_quota=effective.monthly_token_quota,
        monthly_cost_quota_microusd=effective.monthly_cost_quota_microusd,
        pricing_catalog_version=pricing_catalog.version,
        pricing_catalog=pricing_catalog,
    )
    runner = WorkerRunner(
        jobs=jobs,
        credentials=llm_connections,
        executor=ProductionGraphExecutor(
            runtime_root=Path(effective.worker_runtime_root),
            pricing=pricing_catalog,
            request_timeout_seconds=effective.llm_request_timeout_seconds,
            max_attempt_bytes=effective.worker_max_attempt_bytes,
            max_tenant_bytes=effective.worker_max_tenant_bytes,
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

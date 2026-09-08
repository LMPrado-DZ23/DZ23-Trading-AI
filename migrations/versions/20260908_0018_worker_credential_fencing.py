"""Fence worker credential and usage access to one live job lease.

Revision ID: 20260908_0018
Revises: 20260908_0017
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_0018"
down_revision = "20260908_0017"
branch_labels = None
depends_on = None

_JOB_ID = "NULLIF((SELECT current_setting('app.worker_job_id', true)), '')::uuid"
_TENANT_ID = "NULLIF((SELECT current_setting('app.worker_tenant_id', true)), '')::uuid"
_LEASE_ID = "NULLIF((SELECT current_setting('app.worker_lease_id', true)), '')::uuid"
_LEASE_OWNER = "NULLIF((SELECT current_setting('app.worker_lease_owner', true)), '')"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.add_column(
        "analysis_job_usage_records",
        sa.Column(
            "accounting_basis",
            sa.String(32),
            nullable=False,
            server_default="measured",
        ),
    )
    op.alter_column(
        "analysis_job_usage_records",
        "accounting_basis",
        server_default=None,
    )
    op.create_check_constraint(
        "ck_job_usage_accounting_basis",
        "analysis_job_usage_records",
        "accounting_basis IN ('measured','conservative_reservation')",
    )

    op.add_column("llm_credential_access_events", sa.Column("job_id", sa.Uuid(), nullable=True))
    op.add_column("llm_credential_access_events", sa.Column("lease_id", sa.Uuid(), nullable=True))
    op.add_column(
        "llm_credential_access_events",
        sa.Column("attempt_number", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_llm_access_tenant_job",
        "llm_credential_access_events",
        "analysis_jobs",
        ["tenant_id", "job_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_llm_access_job_attempt",
        "llm_credential_access_events",
        ["job_id", "attempt_number"],
    )

    # A credential version is a cryptographic fact referenced by immutable job
    # provenance. Rotation appends a new row and advances the connection's
    # active version; ciphertext belonging to an existing version is immutable.
    op.execute(sa.text("REVOKE UPDATE, DELETE ON llm_credentials FROM tradingagents_app"))
    op.execute(sa.text("REVOKE UPDATE, DELETE ON llm_credentials FROM tradingagents_worker"))
    op.execute(
        sa.text(
            "CREATE OR REPLACE FUNCTION reject_llm_credential_mutation() "
            "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
            "'llm credential versions are append-only'; END $$"
        )
    )
    op.execute(
        sa.text(
            "CREATE OR REPLACE FUNCTION enforce_llm_active_credential_advance() "
            "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF "
            "NEW.active_credential_version IS DISTINCT FROM OLD.active_credential_version THEN "
            "IF NEW.active_credential_version <> OLD.active_credential_version + 1 THEN "
            "RAISE EXCEPTION 'llm active credential version must advance by one'; END IF; "
            "IF NOT EXISTS (SELECT 1 FROM llm_credentials credential WHERE "
            "credential.tenant_id = NEW.tenant_id AND credential.connection_id = NEW.id "
            "AND credential.version = NEW.active_credential_version) THEN RAISE EXCEPTION "
            "'llm active credential version has no immutable row'; END IF; END IF; "
            "RETURN NEW; END $$"
        )
    )
    op.execute(
        sa.text(
            "CREATE CONSTRAINT TRIGGER llm_active_credential_advance "
            "AFTER UPDATE OF active_credential_version ON llm_connections "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "
            "enforce_llm_active_credential_advance()"
        )
    )
    op.execute(sa.text("DROP TRIGGER IF EXISTS llm_credentials_append_only ON llm_credentials"))
    op.execute(
        sa.text(
            "CREATE TRIGGER llm_credentials_append_only BEFORE UPDATE OR DELETE ON "
            "llm_credentials FOR EACH ROW EXECUTE FUNCTION reject_llm_credential_mutation()"
        )
    )

    # The queue remains globally claimable, but active rows are visible only to
    # the transaction scoped to their unexpired lease. Expired rows remain
    # visible solely so any worker can reap them using the transition trigger.
    op.execute(sa.text("DROP POLICY IF EXISTS analysis_jobs_worker ON analysis_jobs"))
    op.execute(
        sa.text(
            "CREATE POLICY analysis_jobs_worker ON analysis_jobs TO tradingagents_worker "
            "USING (status = 'queued' OR (status IN ('leased','running','cancel_requested') "
            "AND id = " + _JOB_ID + " AND tenant_id = " + _TENANT_ID + " "
            "AND lease_id = " + _LEASE_ID + " AND lease_owner = " + _LEASE_OWNER + " "
            "AND lease_expires_at > clock_timestamp()) OR "
            "(status IN ('leased','running','cancel_requested') "
            "AND lease_expires_at <= clock_timestamp()) OR "
            "(status IN ('succeeded','failed','cancelled') AND id = " + _JOB_ID + " "
            "AND tenant_id = " + _TENANT_ID + ")) "
            "WITH CHECK (status = 'queued' OR (id = " + _JOB_ID + " "
            "AND tenant_id = " + _TENANT_ID + " AND lease_id = " + _LEASE_ID + " "
            "AND lease_owner = " + _LEASE_OWNER + ") OR "
            "(status IN ('succeeded','failed','cancelled') AND id = " + _JOB_ID + " "
            "AND tenant_id = " + _TENANT_ID + "))"
        )
    )

    credential_binding = (
        "EXISTS (SELECT 1 FROM analysis_jobs AS job WHERE job.id = " + _JOB_ID + " "
        "AND job.tenant_id = " + _TENANT_ID + " AND job.lease_id = " + _LEASE_ID + " "
        "AND job.lease_owner = " + _LEASE_OWNER + " "
        "AND job.lease_expires_at > clock_timestamp() "
        "AND job.status IN ('leased','running','cancel_requested') "
        "AND job.tenant_id = llm_credentials.tenant_id "
        "AND job.provider_snapshot->>'connection_id' = llm_credentials.connection_id::text "
        "AND (job.provider_snapshot->>'credential_version')::integer = "
        "llm_credentials.version)"
    )
    connection_binding = (
        "EXISTS (SELECT 1 FROM analysis_jobs AS job WHERE job.id = " + _JOB_ID + " "
        "AND job.tenant_id = " + _TENANT_ID + " AND job.lease_id = " + _LEASE_ID + " "
        "AND job.lease_owner = " + _LEASE_OWNER + " "
        "AND job.lease_expires_at > clock_timestamp() "
        "AND job.status IN ('leased','running','cancel_requested') "
        "AND job.tenant_id = llm_connections.tenant_id "
        "AND job.provider_snapshot->>'connection_id' = llm_connections.id::text "
        "AND (job.provider_snapshot->>'credential_version')::integer = "
        "llm_connections.active_credential_version)"
    )

    op.execute(sa.text("DROP POLICY IF EXISTS llm_connections_worker ON llm_connections"))
    op.execute(
        sa.text(
            "CREATE POLICY llm_connections_worker ON llm_connections TO tradingagents_worker "
            "USING (" + connection_binding + ") WITH CHECK (false)"
        )
    )
    op.execute(sa.text("DROP POLICY IF EXISTS llm_credentials_worker ON llm_credentials"))
    op.execute(
        sa.text(
            "CREATE POLICY llm_credentials_worker ON llm_credentials TO tradingagents_worker "
            "USING (" + credential_binding + ") WITH CHECK (false)"
        )
    )
    op.execute(
        sa.text(
            "DROP POLICY IF EXISTS llm_credential_access_events_worker "
            "ON llm_credential_access_events"
        )
    )
    op.execute(
        sa.text(
            "CREATE POLICY llm_credential_access_events_worker ON "
            "llm_credential_access_events TO tradingagents_worker USING (false) "
            "WITH CHECK (EXISTS (SELECT 1 FROM analysis_jobs AS job WHERE job.id = "
            + _JOB_ID + " AND job.tenant_id = " + _TENANT_ID + " "
            "AND job.lease_id = " + _LEASE_ID + " AND job.lease_owner = "
            + _LEASE_OWNER + " AND job.lease_expires_at > clock_timestamp() "
            "AND job.status IN ('leased','running','cancel_requested') "
            "AND job.tenant_id = llm_credential_access_events.tenant_id "
            "AND job.provider_snapshot->>'connection_id' = "
            "llm_credential_access_events.connection_id::text "
            "AND (job.provider_snapshot->>'credential_version')::integer = "
            "llm_credential_access_events.credential_version "
            "AND job.id = llm_credential_access_events.job_id "
            "AND job.lease_id = llm_credential_access_events.lease_id "
            "AND job.attempts = llm_credential_access_events.attempt_number) "
            "AND actor_user_id = "
            "(SELECT requested_by_user_id FROM analysis_jobs WHERE id = " + _JOB_ID + "))"
        )
    )

    usage_row_binding = (
        "EXISTS (SELECT 1 FROM analysis_jobs AS job WHERE job.id = " + _JOB_ID + " "
        "AND job.tenant_id = " + _TENANT_ID + " AND job.lease_id = " + _LEASE_ID + " "
        "AND job.lease_owner = " + _LEASE_OWNER + " "
        "AND job.status IN ('leased','running','cancel_requested') "
        "AND job.id = analysis_job_usage_records.job_id "
        "AND job.tenant_id = analysis_job_usage_records.tenant_id "
        "AND job.run_id = analysis_job_usage_records.run_id "
        "AND job.attempts = analysis_job_usage_records.attempt_number "
        "AND job.lease_id = analysis_job_usage_records.lease_id)"
    )
    usage_insert_binding = (
        "(" + usage_row_binding + " AND EXISTS (SELECT 1 FROM analysis_jobs AS job "
        "WHERE job.id = " + _JOB_ID + " AND (job.lease_expires_at > clock_timestamp() "
        "OR (job.lease_expires_at <= clock_timestamp() "
        "AND analysis_job_usage_records.input_tokens = job.reserved_token_budget "
        "AND analysis_job_usage_records.output_tokens = 0 "
        "AND analysis_job_usage_records.total_tokens = job.reserved_token_budget "
        "AND analysis_job_usage_records.llm_requests = 1 "
        "AND analysis_job_usage_records.unmetered_llm_requests = 0 "
        "AND analysis_job_usage_records.accounting_basis = 'conservative_reservation' "
        "AND analysis_job_usage_records.estimated_cost_microusd IS NOT DISTINCT FROM "
        "job.reserved_cost_budget_microusd))))"
    )
    op.execute(sa.text("DROP POLICY IF EXISTS job_usage_worker ON analysis_job_usage_records"))
    op.execute(
        sa.text(
            "CREATE POLICY job_usage_worker ON analysis_job_usage_records "
            "TO tradingagents_worker USING (" + usage_row_binding + ") "
            "WITH CHECK (" + usage_insert_binding + ")"
        )
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(sa.text("DROP POLICY IF EXISTS job_usage_worker ON analysis_job_usage_records"))
    op.execute(
        sa.text(
            "CREATE POLICY job_usage_worker ON analysis_job_usage_records "
            "TO tradingagents_worker USING (true) WITH CHECK (true)"
        )
    )
    op.execute(
        sa.text(
            "DROP POLICY IF EXISTS llm_credential_access_events_worker "
            "ON llm_credential_access_events"
        )
    )
    op.execute(
        sa.text(
            "CREATE POLICY llm_credential_access_events_worker ON "
            "llm_credential_access_events TO tradingagents_worker USING (false) "
            "WITH CHECK (true)"
        )
    )
    op.execute(sa.text("DROP POLICY IF EXISTS llm_credentials_worker ON llm_credentials"))
    op.execute(
        sa.text(
            "CREATE POLICY llm_credentials_worker ON llm_credentials TO "
            "tradingagents_worker USING (true) WITH CHECK (false)"
        )
    )
    op.execute(sa.text("DROP POLICY IF EXISTS llm_connections_worker ON llm_connections"))
    op.execute(
        sa.text(
            "CREATE POLICY llm_connections_worker ON llm_connections TO "
            "tradingagents_worker USING (true) WITH CHECK (false)"
        )
    )
    op.execute(sa.text("DROP POLICY IF EXISTS analysis_jobs_worker ON analysis_jobs"))
    op.execute(
        sa.text(
            "CREATE POLICY analysis_jobs_worker ON analysis_jobs TO tradingagents_worker "
            "USING (true) WITH CHECK (true)"
        )
    )
    op.execute(sa.text("DROP TRIGGER IF EXISTS llm_credentials_append_only ON llm_credentials"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS reject_llm_credential_mutation()"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS llm_active_credential_advance ON llm_connections"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS enforce_llm_active_credential_advance()"))
    op.execute(sa.text("GRANT UPDATE, DELETE ON llm_credentials TO tradingagents_app"))
    op.drop_index("ix_llm_access_job_attempt", table_name="llm_credential_access_events")
    op.drop_constraint(
        "fk_llm_access_tenant_job",
        "llm_credential_access_events",
        type_="foreignkey",
    )
    op.drop_column("llm_credential_access_events", "attempt_number")
    op.drop_column("llm_credential_access_events", "lease_id")
    op.drop_column("llm_credential_access_events", "job_id")
    op.drop_constraint(
        "ck_job_usage_accounting_basis",
        "analysis_job_usage_records",
        type_="check",
    )
    op.drop_column("analysis_job_usage_records", "accounting_basis")

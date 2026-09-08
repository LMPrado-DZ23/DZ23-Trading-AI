"""Create durable tenant analysis jobs and a restricted worker role.

Revision ID: 20260908_0007
Revises: 20260908_0006
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_0007"
down_revision = "20260908_0006"
branch_labels = None
depends_on = None

_TENANT_ID = "NULLIF((SELECT current_setting('app.tenant_id', true)), '')::uuid"


def upgrade() -> None:
    timestamp = sa.DateTime(timezone=True)
    op.create_table(
        "analysis_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("workflow_schema_version", sa.Integer(), nullable=False),
        sa.Column("run_context", sa.JSON(), nullable=True),
        sa.Column("run_context_digest", sa.String(64), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("manifest", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", timestamp, nullable=False),
        sa.Column("lease_id", sa.Uuid(), nullable=True),
        sa.Column("lease_owner", sa.String(120), nullable=True),
        sa.Column("lease_expires_at", timestamp, nullable=True),
        sa.Column("heartbeat_at", timestamp, nullable=True),
        sa.Column("started_at", timestamp, nullable=True),
        sa.Column("completed_at", timestamp, nullable=True),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('queued','leased','running','cancel_requested','succeeded',"
            "'failed','cancelled')",
            name="ck_analysis_job_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_analysis_job_attempts"),
        sa.CheckConstraint("max_attempts BETWEEN 1 AND 10", name="ck_analysis_job_max_attempts"),
        sa.CheckConstraint(
            "length(payload_digest) = 64 AND length(request_digest) = 64",
            name="ck_analysis_job_digests",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            name="fk_analysis_job_requester_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_analysis_job_run"),
        sa.UniqueConstraint("tenant_id", "idempotency_key_hash", name="uq_job_tenant_idem"),
    )
    op.create_index("ix_analysis_job_tenant_created", "analysis_jobs", ["tenant_id", "created_at"])
    op.create_index(
        "ix_analysis_job_claim",
        "analysis_jobs",
        ["status", "available_at", "created_at"],
    )
    op.create_index("ix_analysis_job_lease", "analysis_jobs", ["lease_expires_at"])
    _install_postgres_guards()


def _install_postgres_guards() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(sa.text("ALTER TABLE analysis_jobs ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE analysis_jobs FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            "CREATE POLICY analysis_jobs_tenant ON analysis_jobs TO tradingagents_app "
            f"USING (tenant_id = {_TENANT_ID}) WITH CHECK (tenant_id = {_TENANT_ID})"
        )
    )
    op.execute(
        sa.text(
            "DO $$ DECLARE role_row record; BEGIN "
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls INTO role_row "
            "FROM pg_roles WHERE rolname = 'tradingagents_worker'; "
            "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tradingagents_worker') THEN "
            "CREATE ROLE tradingagents_worker LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOINHERIT NOBYPASSRLS; "
            "ELSIF role_row.rolsuper OR role_row.rolcreatedb OR role_row.rolcreaterole "
            "OR role_row.rolbypassrls THEN RAISE EXCEPTION "
            "'existing tradingagents_worker role has unsafe attributes'; END IF; END $$"
        )
    )
    op.execute(
        sa.text(
            "DO $$ BEGIN EXECUTE format('GRANT CONNECT ON DATABASE %I TO "
            "tradingagents_worker', current_database()); END $$"
        )
    )
    op.execute(sa.text("GRANT USAGE ON SCHEMA public TO tradingagents_worker"))
    op.execute(
        sa.text(
            "CREATE POLICY analysis_jobs_worker ON analysis_jobs TO tradingagents_worker "
            "USING (true) WITH CHECK (true)"
        )
    )
    op.execute(sa.text("GRANT SELECT, INSERT ON analysis_jobs TO tradingagents_app"))
    op.execute(
        sa.text(
            "GRANT UPDATE (status, completed_at, updated_at) ON analysis_jobs TO tradingagents_app"
        )
    )
    op.execute(sa.text("GRANT SELECT ON analysis_jobs TO tradingagents_worker"))
    op.execute(
        sa.text(
            "GRANT UPDATE (status, run_context, run_context_digest, result, manifest, "
            "error_code, attempts, available_at, lease_id, lease_owner, lease_expires_at, "
            "heartbeat_at, started_at, completed_at, updated_at) ON analysis_jobs "
            "TO tradingagents_worker"
        )
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("REVOKE ALL ON analysis_jobs FROM tradingagents_worker"))
        op.execute(sa.text("REVOKE USAGE ON SCHEMA public FROM tradingagents_worker"))
        op.execute(
            sa.text(
                "DO $$ BEGIN EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM "
                "tradingagents_worker', current_database()); END $$"
            )
        )
        op.execute(sa.text("DROP ROLE IF EXISTS tradingagents_worker"))
    op.drop_index("ix_analysis_job_lease", table_name="analysis_jobs")
    op.drop_index("ix_analysis_job_claim", table_name="analysis_jobs")
    op.drop_index("ix_analysis_job_tenant_created", table_name="analysis_jobs")
    op.drop_table("analysis_jobs")

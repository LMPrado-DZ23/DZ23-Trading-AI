"""Create an append-only, time-attributed usage ledger.

Revision ID: 20260908_0014
Revises: 20260908_0013
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_0014"
down_revision = "20260908_0013"
branch_labels = None
depends_on = None

_TENANT_ID = "NULLIF((SELECT current_setting('app.tenant_id', true)), '')::uuid"


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_analysis_job_tenant_id", "analysis_jobs", ["tenant_id", "id"]
    )
    op.create_table(
        "analysis_job_usage_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("lease_id", sa.Uuid(), nullable=False),
        sa.Column("usage_digest", sa.String(64), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False),
        sa.Column("llm_requests", sa.Integer(), nullable=False),
        sa.Column("unmetered_llm_requests", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_microusd", sa.BigInteger(), nullable=True),
        sa.Column("pricing_version", sa.String(64), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["analysis_jobs.tenant_id", "analysis_jobs.id"],
            name="fk_job_usage_tenant_job",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("job_id", "attempt_number", name="uq_job_usage_attempt"),
        sa.CheckConstraint("attempt_number > 0", name="ck_job_usage_attempt"),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 "
            "AND total_tokens = input_tokens + output_tokens",
            name="ck_job_usage_tokens",
        ),
        sa.CheckConstraint(
            "llm_requests >= 0 AND unmetered_llm_requests BETWEEN 0 AND llm_requests",
            name="ck_job_usage_requests",
        ),
        sa.CheckConstraint(
            "estimated_cost_microusd IS NULL OR estimated_cost_microusd >= 0",
            name="ck_job_usage_cost",
        ),
        sa.CheckConstraint("length(usage_digest) = 64", name="ck_job_usage_digest"),
    )
    op.create_index(
        "ix_job_usage_tenant_occurred",
        "analysis_job_usage_records",
        ["tenant_id", "occurred_at"],
    )
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(sa.text("ALTER TABLE analysis_job_usage_records ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE analysis_job_usage_records FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            "CREATE POLICY job_usage_tenant ON analysis_job_usage_records "
            "TO tradingagents_app "
            f"USING (tenant_id = {_TENANT_ID})"
        )
    )
    op.execute(
        sa.text(
            "CREATE POLICY job_usage_worker ON analysis_job_usage_records "
            "TO tradingagents_worker USING (true) WITH CHECK (true)"
        )
    )
    op.execute(sa.text("REVOKE ALL ON analysis_job_usage_records FROM PUBLIC"))
    op.execute(
        sa.text("GRANT SELECT ON analysis_job_usage_records TO tradingagents_app")
    )
    op.execute(
        sa.text(
            "GRANT SELECT, INSERT ON analysis_job_usage_records TO tradingagents_worker"
        )
    )
    op.execute(
        sa.text(
            "CREATE FUNCTION reject_job_usage_mutation() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
            "'analysis job usage is append-only'; END $$"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER job_usage_append_only BEFORE UPDATE OR DELETE "
            "ON analysis_job_usage_records FOR EACH ROW "
            "EXECUTE FUNCTION reject_job_usage_mutation()"
        )
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("DROP TRIGGER IF EXISTS job_usage_append_only ON analysis_job_usage_records"))
        op.execute(sa.text("DROP FUNCTION IF EXISTS reject_job_usage_mutation()"))
    op.drop_index("ix_job_usage_tenant_occurred", table_name="analysis_job_usage_records")
    op.drop_table("analysis_job_usage_records")
    op.drop_constraint("uq_analysis_job_tenant_id", "analysis_jobs", type_="unique")

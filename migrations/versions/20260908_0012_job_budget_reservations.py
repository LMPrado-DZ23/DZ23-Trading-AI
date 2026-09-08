"""Reserve per-job token and cost budgets for race-free tenant admission.

Revision ID: 20260908_0012
Revises: 20260908_0011
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_0012"
down_revision = "20260908_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analysis_jobs",
        sa.Column(
            "reserved_token_budget", sa.BigInteger(), nullable=False, server_default="250000"
        ),
    )
    op.add_column(
        "analysis_jobs",
        sa.Column("reserved_cost_budget_microusd", sa.BigInteger(), nullable=True),
    )
    op.create_check_constraint(
        "ck_analysis_job_token_budget", "analysis_jobs", "reserved_token_budget > 0"
    )
    op.create_check_constraint(
        "ck_analysis_job_cost_budget",
        "analysis_jobs",
        "reserved_cost_budget_microusd IS NULL OR reserved_cost_budget_microusd > 0",
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text(
                "GRANT INSERT (reserved_token_budget, reserved_cost_budget_microusd) "
                "ON analysis_jobs TO tradingagents_app"
            )
        )
        op.execute(
            sa.text(
                "GRANT SELECT (reserved_token_budget, reserved_cost_budget_microusd) "
                "ON analysis_jobs TO tradingagents_worker"
            )
        )
        op.execute(
            sa.text(
                "CREATE OR REPLACE FUNCTION reject_analysis_job_identity_mutation() "
                "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                "IF NEW.id IS DISTINCT FROM OLD.id OR NEW.run_id IS DISTINCT FROM OLD.run_id "
                "OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id "
                "OR NEW.requested_by_user_id IS DISTINCT FROM OLD.requested_by_user_id "
                "OR NEW.payload::text IS DISTINCT FROM OLD.payload::text "
                "OR NEW.payload_digest IS DISTINCT FROM OLD.payload_digest "
                "OR NEW.idempotency_key_hash IS DISTINCT FROM OLD.idempotency_key_hash "
                "OR NEW.request_digest IS DISTINCT FROM OLD.request_digest "
                "OR NEW.workflow_schema_version IS DISTINCT FROM OLD.workflow_schema_version "
                "OR NEW.max_attempts IS DISTINCT FROM OLD.max_attempts "
                "OR NEW.reserved_token_budget IS DISTINCT FROM OLD.reserved_token_budget "
                "OR NEW.reserved_cost_budget_microusd IS DISTINCT FROM "
                "OLD.reserved_cost_budget_microusd "
                "OR NEW.created_at IS DISTINCT FROM OLD.created_at "
                "OR (OLD.run_context IS NOT NULL AND NEW.run_context::text IS DISTINCT FROM OLD.run_context::text) "
                "OR (OLD.run_context_digest IS NOT NULL AND NEW.run_context_digest IS DISTINCT "
                "FROM OLD.run_context_digest) THEN RAISE EXCEPTION "
                "'analysis job immutable identity cannot be changed'; END IF; RETURN NEW; END $$"
            )
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text(
                "REVOKE SELECT (reserved_token_budget, reserved_cost_budget_microusd) "
                "ON analysis_jobs FROM tradingagents_worker"
            )
        )
    op.drop_constraint("ck_analysis_job_cost_budget", "analysis_jobs", type_="check")
    op.drop_constraint("ck_analysis_job_token_budget", "analysis_jobs", type_="check")
    op.drop_column("analysis_jobs", "reserved_cost_budget_microusd")
    op.drop_column("analysis_jobs", "reserved_token_budget")
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text(
                "CREATE OR REPLACE FUNCTION reject_analysis_job_identity_mutation() "
                "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                "IF NEW.id IS DISTINCT FROM OLD.id OR NEW.run_id IS DISTINCT FROM OLD.run_id "
                "OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id "
                "OR NEW.requested_by_user_id IS DISTINCT FROM OLD.requested_by_user_id "
                "OR NEW.payload::text IS DISTINCT FROM OLD.payload::text "
                "OR NEW.payload_digest IS DISTINCT FROM OLD.payload_digest "
                "OR NEW.idempotency_key_hash IS DISTINCT FROM OLD.idempotency_key_hash "
                "OR NEW.request_digest IS DISTINCT FROM OLD.request_digest "
                "OR NEW.workflow_schema_version IS DISTINCT FROM OLD.workflow_schema_version "
                "OR NEW.max_attempts IS DISTINCT FROM OLD.max_attempts "
                "OR NEW.created_at IS DISTINCT FROM OLD.created_at "
                "OR (OLD.run_context IS NOT NULL AND NEW.run_context::text IS DISTINCT "
                "FROM OLD.run_context::text) OR (OLD.run_context_digest IS NOT NULL AND "
                "NEW.run_context_digest IS DISTINCT FROM OLD.run_context_digest) THEN "
                "RAISE EXCEPTION 'analysis job immutable identity cannot be changed'; "
                "END IF; RETURN NEW; END $$"
            )
        )

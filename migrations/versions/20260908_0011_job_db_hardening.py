"""Restrict job mutation columns and freeze authenticated job identity.

Revision ID: 20260908_0011
Revises: 20260908_0010
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_0011"
down_revision = "20260908_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(sa.text("REVOKE UPDATE, DELETE ON analysis_jobs FROM tradingagents_app"))
    op.execute(sa.text("REVOKE UPDATE, DELETE ON analysis_jobs FROM tradingagents_worker"))
    op.execute(
        sa.text(
            "GRANT UPDATE (status, completed_at, updated_at) ON analysis_jobs TO tradingagents_app"
        )
    )
    op.execute(
        sa.text(
            "GRANT UPDATE (status, run_context, run_context_digest, result, manifest, "
            "error_code, attempts, available_at, lease_id, lease_owner, lease_expires_at, "
            "heartbeat_at, started_at, completed_at, updated_at, input_tokens, "
            "output_tokens, total_tokens, llm_requests, unmetered_llm_requests, "
            "estimated_cost_microusd, pricing_version) ON analysis_jobs "
            "TO tradingagents_worker"
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
            "OR NEW.created_at IS DISTINCT FROM OLD.created_at "
            "OR (OLD.run_context IS NOT NULL AND NEW.run_context::text IS DISTINCT FROM OLD.run_context::text) "
            "OR (OLD.run_context_digest IS NOT NULL AND NEW.run_context_digest IS DISTINCT "
            "FROM OLD.run_context_digest) THEN RAISE EXCEPTION "
            "'analysis job immutable identity cannot be changed'; END IF; RETURN NEW; END $$"
        )
    )
    op.execute(sa.text("DROP TRIGGER IF EXISTS analysis_job_identity_immutable ON analysis_jobs"))
    op.execute(
        sa.text(
            "CREATE TRIGGER analysis_job_identity_immutable BEFORE UPDATE ON analysis_jobs "
            "FOR EACH ROW EXECUTE FUNCTION reject_analysis_job_identity_mutation()"
        )
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(sa.text("DROP TRIGGER IF EXISTS analysis_job_identity_immutable ON analysis_jobs"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS reject_analysis_job_identity_mutation()"))
    op.execute(sa.text("REVOKE UPDATE ON analysis_jobs FROM tradingagents_app"))
    op.execute(sa.text("REVOKE UPDATE ON analysis_jobs FROM tradingagents_worker"))
    op.execute(sa.text("GRANT UPDATE, DELETE ON analysis_jobs TO tradingagents_app"))
    op.execute(sa.text("GRANT UPDATE ON analysis_jobs TO tradingagents_worker"))

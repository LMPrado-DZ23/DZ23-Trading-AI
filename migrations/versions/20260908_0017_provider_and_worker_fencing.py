"""Bind provider provenance and enforce worker lease fencing in PostgreSQL.

Revision ID: 20260908_0017
Revises: 20260908_0016
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_0017"
down_revision = "20260908_0016"
branch_labels = None
depends_on = None


def _identity_guard(*, include_provider_snapshot: bool) -> None:
    provider_clause = (
        "OR NEW.provider_snapshot::text IS DISTINCT FROM OLD.provider_snapshot::text "
        if include_provider_snapshot
        else ""
    )
    op.execute(
        sa.text(
            "CREATE OR REPLACE FUNCTION reject_analysis_job_identity_mutation() "
            "RETURNS trigger LANGUAGE plpgsql AS $$ DECLARE scoped_lease text; "
            "scoped_owner text; expired_reap boolean; BEGIN "
            "IF NEW.id IS DISTINCT FROM OLD.id OR NEW.run_id IS DISTINCT FROM OLD.run_id "
            "OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id "
            "OR NEW.requested_by_user_id IS DISTINCT FROM OLD.requested_by_user_id "
            "OR NEW.payload::text IS DISTINCT FROM OLD.payload::text "
            "OR NEW.payload_digest IS DISTINCT FROM OLD.payload_digest "
            "OR NEW.idempotency_key_hash IS DISTINCT FROM OLD.idempotency_key_hash "
            "OR NEW.request_digest IS DISTINCT FROM OLD.request_digest "
            "OR NEW.workflow_schema_version IS DISTINCT FROM OLD.workflow_schema_version "
            + provider_clause
            + "OR NEW.max_attempts IS DISTINCT FROM OLD.max_attempts "
            "OR NEW.reserved_token_budget IS DISTINCT FROM OLD.reserved_token_budget "
            "OR NEW.reserved_cost_budget_microusd IS DISTINCT FROM "
            "OLD.reserved_cost_budget_microusd "
            "OR NEW.pricing_catalog_version IS DISTINCT FROM OLD.pricing_catalog_version "
            "OR NEW.created_at IS DISTINCT FROM OLD.created_at "
            "OR (OLD.run_context IS NOT NULL AND NEW.run_context::text IS DISTINCT FROM "
            "OLD.run_context::text) OR (OLD.run_context_digest IS NOT NULL AND "
            "NEW.run_context_digest IS DISTINCT FROM OLD.run_context_digest) THEN "
            "RAISE EXCEPTION 'analysis job immutable identity cannot be changed'; END IF; "
            "IF current_user = 'tradingagents_app' AND NOT ("
            "(OLD.status = 'queued' AND NEW.status = 'cancelled' AND NEW.completed_at IS NOT NULL) "
            "OR (OLD.status IN ('leased','running') AND NEW.status = 'cancel_requested' "
            "AND NEW.completed_at IS NOT DISTINCT FROM OLD.completed_at)) THEN "
            "RAISE EXCEPTION 'application role job transition is forbidden'; END IF; "
            "IF current_user = 'tradingagents_worker' THEN "
            "IF OLD.status IN ('succeeded','failed','cancelled') THEN "
            "RAISE EXCEPTION 'terminal analysis job is immutable'; END IF; "
            "scoped_lease := current_setting('app.worker_lease_id', true); "
            "scoped_owner := current_setting('app.worker_lease_owner', true); "
            "IF OLD.status = 'queued' THEN "
            "IF NEW.status <> 'leased' OR NEW.lease_id IS NULL OR NEW.lease_owner IS NULL "
            "OR scoped_lease IS DISTINCT FROM NEW.lease_id::text "
            "OR scoped_owner IS DISTINCT FROM NEW.lease_owner "
            "OR NEW.attempts <> OLD.attempts + 1 THEN "
            "RAISE EXCEPTION 'worker claim transition is not fenced'; END IF; "
            "ELSE "
            "expired_reap := OLD.lease_expires_at <= clock_timestamp() "
            "AND NEW.status IN ('failed','cancelled') AND NEW.lease_id IS NULL; "
            "IF NOT expired_reap AND (scoped_lease IS DISTINCT FROM OLD.lease_id::text "
            "OR scoped_owner IS DISTINCT FROM OLD.lease_owner) THEN "
            "RAISE EXCEPTION 'worker lease fence does not match current lease'; END IF; "
            "IF NOT ((OLD.status = 'leased' AND NEW.status IN "
            "('leased','running','queued','failed','cancelled')) OR "
            "(OLD.status = 'running' AND NEW.status IN "
            "('running','queued','succeeded','failed','cancelled')) OR "
            "(OLD.status = 'cancel_requested' AND NEW.status IN "
            "('cancel_requested','cancelled'))) THEN "
            "RAISE EXCEPTION 'worker job transition is forbidden'; END IF; "
            "IF NEW.status IN ('leased','running','cancel_requested') AND "
            "(NEW.lease_id IS DISTINCT FROM OLD.lease_id OR "
            "NEW.lease_owner IS DISTINCT FROM OLD.lease_owner) THEN "
            "RAISE EXCEPTION 'active worker lease identity cannot change'; END IF; "
            "IF NEW.status IN ('queued','succeeded','failed','cancelled') AND "
            "(NEW.lease_id IS NOT NULL OR NEW.lease_owner IS NOT NULL OR "
            "NEW.lease_expires_at IS NOT NULL) THEN "
            "RAISE EXCEPTION 'inactive job must not retain a worker lease'; END IF; "
            "END IF; END IF; RETURN NEW; END $$"
        )
    )


def upgrade() -> None:
    op.add_column(
        "analysis_jobs",
        sa.Column(
            "provider_snapshot",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    if op.get_bind().dialect.name != "postgresql":
        return
    _identity_guard(include_provider_snapshot=True)
    op.execute(
        sa.text(
            "CREATE OR REPLACE FUNCTION reject_llm_connection_route_mutation() "
            "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF "
            "NEW.provider IS DISTINCT FROM OLD.provider OR "
            "NEW.base_url IS DISTINCT FROM OLD.base_url THEN RAISE EXCEPTION "
            "'llm provider route is immutable'; END IF; RETURN NEW; END $$"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER llm_connection_route_immutable BEFORE UPDATE ON "
            "llm_connections FOR EACH ROW EXECUTE FUNCTION "
            "reject_llm_connection_route_mutation()"
        )
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text(
                "DROP TRIGGER IF EXISTS llm_connection_route_immutable ON llm_connections"
            )
        )
        op.execute(sa.text("DROP FUNCTION IF EXISTS reject_llm_connection_route_mutation()"))
        _identity_guard(include_provider_snapshot=False)
    op.drop_column("analysis_jobs", "provider_snapshot")

"""Bind pricing provenance and restrict app job state transitions.

Revision ID: 20260908_0015
Revises: 20260908_0014
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_0015"
down_revision = "20260908_0014"
branch_labels = None
depends_on = None


def _install_guard(*, include_pricing: bool) -> None:
    pricing_clause = (
        "OR NEW.pricing_catalog_version IS DISTINCT FROM OLD.pricing_catalog_version "
        if include_pricing
        else ""
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
            + pricing_clause
            + "OR NEW.created_at IS DISTINCT FROM OLD.created_at "
            "OR (OLD.run_context IS NOT NULL AND NEW.run_context::text IS DISTINCT FROM "
            "OLD.run_context::text) OR (OLD.run_context_digest IS NOT NULL AND "
            "NEW.run_context_digest IS DISTINCT FROM OLD.run_context_digest) THEN "
            "RAISE EXCEPTION 'analysis job immutable identity cannot be changed'; END IF; "
            "IF current_user = 'tradingagents_app' AND NOT ("
            "(OLD.status = 'queued' AND NEW.status = 'cancelled' AND "
            "NEW.completed_at IS NOT NULL) OR "
            "(OLD.status IN ('leased','running') AND NEW.status = 'cancel_requested' "
            "AND NEW.completed_at IS NOT DISTINCT FROM OLD.completed_at)) THEN "
            "RAISE EXCEPTION 'application role job transition is forbidden'; END IF; "
            "RETURN NEW; END $$"
        )
    )


def upgrade() -> None:
    op.add_column(
        "analysis_jobs",
        sa.Column(
            "pricing_catalog_version",
            sa.String(16),
            nullable=False,
            server_default="44136fa355b3678a",
        ),
    )
    op.create_check_constraint(
        "ck_analysis_job_pricing_catalog_version",
        "analysis_jobs",
        "length(pricing_catalog_version) = 16",
    )
    if op.get_bind().dialect.name == "postgresql":
        _install_guard(include_pricing=True)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        _install_guard(include_pricing=False)
    op.drop_constraint(
        "ck_analysis_job_pricing_catalog_version", "analysis_jobs", type_="check"
    )
    op.drop_column("analysis_jobs", "pricing_catalog_version")

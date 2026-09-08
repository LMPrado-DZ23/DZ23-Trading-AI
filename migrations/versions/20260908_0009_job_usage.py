"""Add auditable LLM token and estimated-cost counters to analysis jobs.

Revision ID: 20260908_0009
Revises: 20260908_0008
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_0009"
down_revision = "20260908_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("input_tokens", "output_tokens", "total_tokens"):
        op.add_column(
            "analysis_jobs",
            sa.Column(name, sa.BigInteger(), nullable=False, server_default="0"),
        )
    for name in ("llm_requests", "unmetered_llm_requests"):
        op.add_column(
            "analysis_jobs",
            sa.Column(name, sa.Integer(), nullable=False, server_default="0"),
        )
    op.add_column(
        "analysis_jobs", sa.Column("estimated_cost_microusd", sa.BigInteger(), nullable=True)
    )
    op.add_column("analysis_jobs", sa.Column("pricing_version", sa.String(64), nullable=True))
    op.create_check_constraint(
        "ck_analysis_job_token_usage",
        "analysis_jobs",
        "input_tokens >= 0 AND output_tokens >= 0 AND total_tokens = input_tokens + output_tokens",
    )
    op.create_check_constraint(
        "ck_analysis_job_request_usage",
        "analysis_jobs",
        "llm_requests >= 0 AND unmetered_llm_requests BETWEEN 0 AND llm_requests",
    )
    op.create_check_constraint(
        "ck_analysis_job_cost_usage",
        "analysis_jobs",
        "estimated_cost_microusd IS NULL OR estimated_cost_microusd >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_analysis_job_cost_usage", "analysis_jobs", type_="check")
    op.drop_constraint("ck_analysis_job_request_usage", "analysis_jobs", type_="check")
    op.drop_constraint("ck_analysis_job_token_usage", "analysis_jobs", type_="check")
    for name in (
        "pricing_version",
        "estimated_cost_microusd",
        "unmetered_llm_requests",
        "llm_requests",
        "total_tokens",
        "output_tokens",
        "input_tokens",
    ):
        op.drop_column("analysis_jobs", name)

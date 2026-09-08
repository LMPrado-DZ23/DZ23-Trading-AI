"""Add a bounded expired-lease claim index.

Revision ID: 20260908_0016
Revises: 20260908_0015
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_0016"
down_revision = "20260908_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.create_index(
            "ix_analysis_job_expired_active_lease",
            "analysis_jobs",
            ["lease_expires_at", "id"],
            postgresql_where=sa.text(
                "status IN ('leased','running','cancel_requested')"
            ),
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_index(
            "ix_analysis_job_expired_active_lease",
            table_name="analysis_jobs",
        )

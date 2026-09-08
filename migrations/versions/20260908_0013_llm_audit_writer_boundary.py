"""Restrict LLM credential audit writes to the isolated worker.

Revision ID: 20260908_0013
Revises: 20260908_0012
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_0013"
down_revision = "20260908_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text("REVOKE INSERT ON llm_credential_access_events FROM tradingagents_app")
        )


def downgrade() -> None:
    # Earlier secure migrations do not grant this authority to the app role.
    return

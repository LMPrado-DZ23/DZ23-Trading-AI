"""Make LLM credential access evidence append-only in PostgreSQL.

Revision ID: 20260908_0010
Revises: 20260908_0009
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_0010"
down_revision = "20260908_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        sa.text("REVOKE UPDATE, DELETE ON llm_credential_access_events FROM tradingagents_app")
    )
    op.execute(sa.text("REVOKE INSERT ON llm_credential_access_events FROM tradingagents_app"))
    op.execute(
        sa.text(
            "CREATE OR REPLACE FUNCTION reject_llm_access_event_mutation() "
            "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
            "'llm credential access events are append-only'; END $$"
        )
    )
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS llm_access_events_append_only ON llm_credential_access_events"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER llm_access_events_append_only BEFORE UPDATE OR DELETE ON "
            "llm_credential_access_events FOR EACH ROW EXECUTE FUNCTION "
            "reject_llm_access_event_mutation()"
        )
    )


def downgrade() -> None:
    # 0008 already establishes the same append-only trigger and least-privilege
    # grants. Downgrading to 0009 must preserve that earlier contract.
    return

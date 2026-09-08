"""Create encrypted tenant LLM connections and worker access audit.

Revision ID: 20260908_0008
Revises: 20260908_0007
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_0008"
down_revision = "20260908_0007"
branch_labels = None
depends_on = None

_TENANT_ID = "NULLIF((SELECT current_setting('app.tenant_id', true)), '')::uuid"


def upgrade() -> None:
    timestamp = sa.DateTime(timezone=True)
    op.create_table(
        "llm_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("label_normalized", sa.String(80), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("base_url", sa.String(2048), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("active_credential_version", sa.Integer(), nullable=False),
        sa.Column("disabled_at", timestamp, nullable=True),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('ready','disabled')", name="ck_llm_connection_status"),
        sa.CheckConstraint(
            "active_credential_version >= 1", name="ck_llm_credential_version_positive"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            name="fk_llm_connection_creator_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_llm_connection_tenant_binding"),
        sa.UniqueConstraint("tenant_id", "label_normalized", name="uq_llm_connection_tenant_label"),
    )
    op.create_index("ix_llm_connection_tenant_status", "llm_connections", ["tenant_id", "status"])
    op.create_table(
        "llm_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("key_id", sa.String(160), nullable=False),
        sa.Column("wrapped_key_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("wrapped_key", sa.LargeBinary(), nullable=False),
        sa.Column("payload_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("version >= 1", name="ck_llm_credential_version_row"),
        sa.CheckConstraint("length(request_digest) = 64", name="ck_llm_request_digest"),
        sa.CheckConstraint("length(wrapped_key_nonce) IN (0, 12)", name="ck_llm_wrapped_key_nonce"),
        sa.CheckConstraint("length(wrapped_key) BETWEEN 1 AND 8192", name="ck_llm_wrapped_key"),
        sa.CheckConstraint("length(payload_nonce) = 12", name="ck_llm_payload_nonce"),
        sa.ForeignKeyConstraint(
            ["connection_id", "tenant_id"],
            ["llm_connections.id", "llm_connections.tenant_id"],
            name="fk_llm_credential_connection",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            name="fk_llm_credential_creator_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "connection_id", "version", name="uq_llm_credential_connection_version"
        ),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key_hash", name="uq_llm_credential_tenant_idem"
        ),
    )
    op.create_index(
        "ix_llm_credential_tenant_connection",
        "llm_credentials",
        ["tenant_id", "connection_id"],
    )
    op.create_table(
        "llm_credential_access_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("credential_version", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(24), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("occurred_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.CheckConstraint("purpose IN ('analysis','rotation')", name="ck_llm_access_purpose"),
        sa.CheckConstraint(
            "outcome IN ('allowed','denied','failed')", name="ck_llm_access_outcome"
        ),
        sa.ForeignKeyConstraint(
            ["connection_id", "tenant_id"],
            ["llm_connections.id", "llm_connections.tenant_id"],
            name="fk_llm_access_connection",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            name="fk_llm_access_actor_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_llm_access_tenant_time",
        "llm_credential_access_events",
        ["tenant_id", "occurred_at"],
    )
    _install_postgres_guards()


def _install_postgres_guards() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in (
        "llm_connections",
        "llm_credentials",
        "llm_credential_access_events",
    ):
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(
            sa.text(
                f"CREATE POLICY {table}_tenant ON {table} TO tradingagents_app "
                f"USING (tenant_id = {_TENANT_ID}) WITH CHECK (tenant_id = {_TENANT_ID})"
            )
        )
    op.execute(
        sa.text(
            "CREATE POLICY llm_connections_worker ON llm_connections TO "
            "tradingagents_worker USING (true) WITH CHECK (false)"
        )
    )
    op.execute(
        sa.text(
            "CREATE POLICY llm_credentials_worker ON llm_credentials TO "
            "tradingagents_worker USING (true) WITH CHECK (false)"
        )
    )
    op.execute(
        sa.text(
            "CREATE POLICY llm_credential_access_events_worker ON "
            "llm_credential_access_events TO tradingagents_worker USING (false) "
            "WITH CHECK (true)"
        )
    )
    op.execute(
        sa.text(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON llm_connections, llm_credentials "
            "TO tradingagents_app"
        )
    )
    op.execute(sa.text("GRANT SELECT ON llm_credential_access_events TO tradingagents_app"))
    op.execute(sa.text("GRANT SELECT ON llm_connections, llm_credentials TO tradingagents_worker"))
    op.execute(sa.text("GRANT INSERT ON llm_credential_access_events TO tradingagents_worker"))
    op.execute(
        sa.text(
            "CREATE OR REPLACE FUNCTION reject_llm_access_event_mutation() "
            "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
            "'llm credential access events are append-only'; END $$"
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
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text(
                "DROP TRIGGER IF EXISTS llm_access_events_append_only ON "
                "llm_credential_access_events"
            )
        )
        op.execute(sa.text("DROP FUNCTION IF EXISTS reject_llm_access_event_mutation()"))
        op.execute(
            sa.text(
                "REVOKE ALL ON llm_connections, llm_credentials, "
                "llm_credential_access_events FROM tradingagents_worker"
            )
        )
    op.drop_index("ix_llm_access_tenant_time", table_name="llm_credential_access_events")
    op.drop_table("llm_credential_access_events")
    op.drop_index("ix_llm_credential_tenant_connection", table_name="llm_credentials")
    op.drop_table("llm_credentials")
    op.drop_index("ix_llm_connection_tenant_status", table_name="llm_connections")
    op.drop_table("llm_connections")

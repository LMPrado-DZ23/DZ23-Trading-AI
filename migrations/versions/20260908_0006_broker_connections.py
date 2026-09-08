"""Create encrypted tenant broker connections and access audit.

Revision ID: 20260908_0006
Revises: 20260908_0005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_0006"
down_revision = "20260908_0005"
branch_labels = None
depends_on = None

_TENANT_ID = "NULLIF((SELECT current_setting('app.tenant_id', true)), '')::uuid"


def upgrade() -> None:
    timestamp = sa.DateTime(timezone=True)
    op.create_table(
        "broker_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("label_normalized", sa.String(80), nullable=False),
        sa.Column("exchange_slug", sa.String(40), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("active_credential_version", sa.Integer(), nullable=False),
        sa.Column("capability_digest", sa.String(64), nullable=False),
        sa.Column("ccxt_version", sa.String(40), nullable=True),
        sa.Column("permission_status", sa.String(24), nullable=False),
        sa.Column("last_diagnostic_code", sa.String(80), nullable=True),
        sa.Column("last_checked_at", timestamp, nullable=True),
        sa.Column("diagnostic_lease_id", sa.Uuid(), nullable=True),
        sa.Column("diagnostic_lease_expires_at", timestamp, nullable=True),
        sa.Column("disabled_at", timestamp, nullable=True),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "environment IN ('testnet','live')", name="ck_broker_connection_environment"
        ),
        sa.CheckConstraint(
            "status IN ('pending_diagnostic','ready_readonly','attention_required',"
            "'rejected','disabled')",
            name="ck_broker_connection_status",
        ),
        sa.CheckConstraint(
            "permission_status IN ('verified_safe','unsafe','unverifiable','not_supported')",
            name="ck_broker_connection_permission_status",
        ),
        sa.CheckConstraint(
            "length(capability_digest) = 64", name="ck_broker_connection_capability_digest"
        ),
        sa.CheckConstraint(
            "active_credential_version >= 1", name="ck_broker_credential_version_positive"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            name="fk_broker_connection_creator_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_broker_connection_tenant_binding"),
        sa.UniqueConstraint(
            "tenant_id", "label_normalized", name="uq_broker_connection_tenant_label"
        ),
    )
    op.create_index(
        "ix_broker_connection_tenant_status",
        "broker_connections",
        ["tenant_id", "status"],
    )
    op.create_table(
        "broker_credentials",
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
        sa.CheckConstraint("version >= 1", name="ck_broker_credential_version_positive_row"),
        sa.CheckConstraint(
            "length(request_digest) = 64", name="ck_broker_credential_request_digest"
        ),
        sa.CheckConstraint(
            "length(wrapped_key_nonce) IN (0, 12)",
            name="ck_broker_wrapped_key_nonce_length",
        ),
        sa.CheckConstraint(
            "length(wrapped_key) BETWEEN 1 AND 8192", name="ck_broker_wrapped_key_length"
        ),
        sa.CheckConstraint(
            "length(payload_nonce) = 12", name="ck_broker_payload_nonce_length"
        ),
        sa.ForeignKeyConstraint(
            ["connection_id", "tenant_id"],
            ["broker_connections.id", "broker_connections.tenant_id"],
            name="fk_broker_credential_connection",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            name="fk_broker_credential_creator_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "connection_id", "version", name="uq_broker_credential_connection_version"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key_hash",
            name="uq_broker_credential_tenant_idempotency",
        ),
    )
    op.create_index(
        "ix_broker_credential_tenant_connection",
        "broker_credentials",
        ["tenant_id", "connection_id"],
    )
    op.create_table(
        "broker_credential_access_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("credential_version", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(40), nullable=False),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("occurred_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "purpose IN ('diagnostic','rotation','execution')", name="ck_broker_access_purpose"
        ),
        sa.CheckConstraint(
            "outcome IN ('allowed','denied','failed')", name="ck_broker_access_outcome"
        ),
        sa.ForeignKeyConstraint(
            ["connection_id", "tenant_id"],
            ["broker_connections.id", "broker_connections.tenant_id"],
            name="fk_broker_access_connection",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            name="fk_broker_access_actor_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_broker_access_tenant_time",
        "broker_credential_access_events",
        ["tenant_id", "occurred_at"],
    )
    _install_database_guards()


def _install_database_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        for table in (
            "broker_connections",
            "broker_credentials",
            "broker_credential_access_events",
        ):
            op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
            op.execute(sa.text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))
            op.execute(
                sa.text(
                    f'CREATE POLICY "{table}_tenant_isolation" ON "{table}" '
                    f"USING (tenant_id = {_TENANT_ID}) "
                    f"WITH CHECK (tenant_id = {_TENANT_ID})"
                )
            )
        op.execute(
            sa.text(
                "CREATE FUNCTION broker_reject_access_mutation() RETURNS trigger "
                "LANGUAGE plpgsql SET search_path = pg_catalog AS $$ BEGIN RAISE EXCEPTION "
                "'credential access events are append-only'; END; $$"
            )
        )
        op.execute(
            sa.text(
                "CREATE TRIGGER broker_access_no_mutation BEFORE UPDATE OR DELETE "
                "ON broker_credential_access_events FOR EACH ROW EXECUTE FUNCTION "
                "broker_reject_access_mutation()"
            )
        )
        op.execute(
            sa.text(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON broker_connections "
                "TO tradingagents_app"
            )
        )
        op.execute(
            sa.text(
                "GRANT SELECT, INSERT, DELETE ON broker_credentials TO tradingagents_app"
            )
        )
        op.execute(
            sa.text(
                "GRANT SELECT, INSERT ON broker_credential_access_events "
                "TO tradingagents_app"
            )
        )
    elif dialect == "sqlite":
        op.execute(
            sa.text(
                "CREATE TRIGGER broker_access_no_update BEFORE UPDATE ON "
                "broker_credential_access_events BEGIN SELECT RAISE(ABORT, "
                "'credential access events are append-only'); END"
            )
        )
        op.execute(
            sa.text(
                "CREATE TRIGGER broker_access_no_delete BEFORE DELETE ON "
                "broker_credential_access_events BEGIN SELECT RAISE(ABORT, "
                "'credential access events are append-only'); END"
            )
        )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            sa.text(
                "DROP TRIGGER IF EXISTS broker_access_no_mutation "
                "ON broker_credential_access_events"
            )
        )
        op.execute(sa.text("DROP FUNCTION IF EXISTS broker_reject_access_mutation()"))
    elif dialect == "sqlite":
        op.execute(sa.text("DROP TRIGGER IF EXISTS broker_access_no_update"))
        op.execute(sa.text("DROP TRIGGER IF EXISTS broker_access_no_delete"))

    op.drop_index("ix_broker_access_tenant_time", table_name="broker_credential_access_events")
    op.drop_table("broker_credential_access_events")
    op.drop_index("ix_broker_credential_tenant_connection", table_name="broker_credentials")
    op.drop_table("broker_credentials")
    op.drop_index("ix_broker_connection_tenant_status", table_name="broker_connections")
    op.drop_table("broker_connections")

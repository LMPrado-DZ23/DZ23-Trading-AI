"""Create tenant-aware identity and audit tables.

Revision ID: 20260908_0001
Revises: None
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_0001"
down_revision = None
branch_labels = None
depends_on = None

_TENANT_TABLES = (
    "identity_memberships",
    "identity_sessions",
    "identity_password_reset_tokens",
    "identity_email_verification_tokens",
    "identity_audit_events",
)


def upgrade() -> None:
    timestamp = sa.DateTime(timezone=True)
    op.create_table(
        "identity_tenants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("length(slug) >= 3", name="ck_identity_tenant_slug_length"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "identity_users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("email_normalized", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=True),
        sa.Column("auth_subject", sa.Uuid(), nullable=True),
        sa.Column("auth_provider", sa.String(32), nullable=True),
        sa.Column("email_verified_at", timestamp, nullable=True),
        sa.Column(
            "failed_login_attempts", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("auth_locked_until", timestamp, nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "failed_login_attempts >= 0",
            name="ck_identity_user_failed_login_nonnegative",
        ),
        sa.UniqueConstraint("auth_subject"),
    )
    op.create_table(
        "identity_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "role IN ('owner','admin','operator','viewer','auditor')",
            name="ck_identity_membership_role",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["identity_tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["identity_users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_identity_membership_tenant_user"),
        sa.UniqueConstraint(
            "id", "tenant_id", "user_id", name="uq_identity_membership_identity_binding"
        ),
    )
    op.create_index(
        "ix_identity_membership_tenant_role",
        "identity_memberships",
        ["tenant_id", "role"],
    )
    op.create_index(
        "ix_identity_membership_user",
        "identity_memberships",
        ["user_id"],
    )
    op.create_table(
        "identity_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("membership_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", timestamp, nullable=False),
        sa.Column("revoked_at", timestamp, nullable=True),
        sa.Column("last_seen_at", timestamp, nullable=True),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.Column("client_ip", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.ForeignKeyConstraint(
            ["membership_id", "tenant_id", "user_id"],
            [
                "identity_memberships.id",
                "identity_memberships.tenant_id",
                "identity_memberships.user_id",
            ],
            name="fk_identity_session_membership_binding",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_identity_session_tenant_user", "identity_sessions", ["tenant_id", "user_id"]
    )
    op.create_index(
        "ix_identity_session_active_lookup",
        "identity_sessions",
        ["tenant_id", "token_hash", "revoked_at"],
    )
    op.create_table(
        "identity_password_reset_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", timestamp, nullable=False),
        sa.Column("consumed_at", timestamp, nullable=True),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            name="fk_identity_reset_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_identity_reset_tenant_hash",
        "identity_password_reset_tokens",
        ["tenant_id", "token_hash"],
    )
    op.create_table(
        "identity_email_verification_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", timestamp, nullable=False),
        sa.Column("consumed_at", timestamp, nullable=True),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            name="fk_identity_verification_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_identity_verification_tenant_hash",
        "identity_email_verification_tokens",
        ["tenant_id", "token_hash"],
    )
    op.create_table(
        "identity_audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("target_type", sa.String(100), nullable=False),
        sa.Column("target_id", sa.String(100), nullable=False),
        sa.Column("occurred_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.Column("client_ip", sa.String(64), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["identity_tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["identity_users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_identity_audit_tenant_time",
        "identity_audit_events",
        ["tenant_id", "occurred_at"],
    )
    _install_database_guards()


def _install_database_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(sa.text('ALTER TABLE "identity_tenants" ENABLE ROW LEVEL SECURITY'))
        op.execute(sa.text('ALTER TABLE "identity_tenants" FORCE ROW LEVEL SECURITY'))
        op.execute(
            sa.text(
                'CREATE POLICY "identity_tenants_isolation" ON "identity_tenants" '
                "USING (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid OR "
                "slug = NULLIF(current_setting('app.tenant_slug', true), '')) "
                "WITH CHECK (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
            )
        )
        op.execute(sa.text('ALTER TABLE "identity_users" ENABLE ROW LEVEL SECURITY'))
        op.execute(sa.text('ALTER TABLE "identity_users" FORCE ROW LEVEL SECURITY'))
        op.execute(
            sa.text(
                'CREATE POLICY "identity_users_isolation" ON "identity_users" '
                "USING (EXISTS (SELECT 1 FROM identity_memberships m "
                "WHERE m.user_id = identity_users.id AND m.tenant_id = "
                "NULLIF(current_setting('app.tenant_id', true), '')::uuid)) "
                "WITH CHECK (id = NULLIF(current_setting('app.registration_user_id', true), '')::uuid "
                "OR EXISTS (SELECT 1 FROM identity_memberships m WHERE m.user_id = "
                "identity_users.id AND m.tenant_id = "
                "NULLIF(current_setting('app.tenant_id', true), '')::uuid))"
            )
        )
        for table in _TENANT_TABLES:
            op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
            op.execute(sa.text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))
            op.execute(
                sa.text(
                    f'CREATE POLICY "{table}_tenant_isolation" ON "{table}" '
                    "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
                    "WITH CHECK (tenant_id = "
                    "NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
                )
            )
        op.execute(
            sa.text(
                "CREATE FUNCTION identity_reject_audit_mutation() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
                "'identity audit events are append-only'; END; $$"
            )
        )
        op.execute(
            sa.text(
                "CREATE TRIGGER identity_audit_no_mutation BEFORE UPDATE OR DELETE "
                "ON identity_audit_events FOR EACH ROW EXECUTE FUNCTION "
                "identity_reject_audit_mutation()"
            )
        )
    elif dialect == "sqlite":
        op.execute(
            sa.text(
                "CREATE TRIGGER identity_audit_no_update BEFORE UPDATE ON identity_audit_events "
                "BEGIN SELECT RAISE(ABORT, 'identity audit events are append-only'); END"
            )
        )
        op.execute(
            sa.text(
                "CREATE TRIGGER identity_audit_no_delete BEFORE DELETE ON identity_audit_events "
                "BEGIN SELECT RAISE(ABORT, 'identity audit events are append-only'); END"
            )
        )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            sa.text("DROP TRIGGER IF EXISTS identity_audit_no_mutation ON identity_audit_events")
        )
        op.execute(sa.text("DROP FUNCTION IF EXISTS identity_reject_audit_mutation()"))
    elif dialect == "sqlite":
        op.execute(sa.text("DROP TRIGGER IF EXISTS identity_audit_no_update"))
        op.execute(sa.text("DROP TRIGGER IF EXISTS identity_audit_no_delete"))

    op.drop_index("ix_identity_audit_tenant_time", table_name="identity_audit_events")
    op.drop_table("identity_audit_events")
    op.drop_index("ix_identity_reset_tenant_hash", table_name="identity_password_reset_tokens")
    op.drop_index(
        "ix_identity_verification_tenant_hash",
        table_name="identity_email_verification_tokens",
    )
    op.drop_table("identity_email_verification_tokens")
    op.drop_table("identity_password_reset_tokens")
    op.drop_index("ix_identity_session_active_lookup", table_name="identity_sessions")
    op.drop_index("ix_identity_session_tenant_user", table_name="identity_sessions")
    op.drop_table("identity_sessions")
    op.drop_index("ix_identity_membership_tenant_role", table_name="identity_memberships")
    op.drop_index("ix_identity_membership_user", table_name="identity_memberships")
    op.drop_table("identity_memberships")
    op.drop_table("identity_users")
    op.drop_table("identity_tenants")

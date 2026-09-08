"""Create compliance, LGPD, consent, and MFA tables.

Revision ID: 20260908_0002
Revises: 20260908_0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_0002"
down_revision = "20260908_0001"
branch_labels = None
depends_on = None

_TENANT_TABLES = (
    "compliance_consent_records",
    "compliance_privacy_requests",
    "compliance_privacy_request_events",
    "compliance_mfa_credentials",
    "compliance_recovery_codes",
)

_APPEND_ONLY_TABLES = (
    "compliance_terms_documents",
    "compliance_consent_records",
    "compliance_privacy_request_events",
)


def upgrade() -> None:
    timestamp = sa.DateTime(timezone=True)
    with op.batch_alter_table("identity_sessions") as batch:
        batch.add_column(sa.Column("mfa_verified_at", timestamp, nullable=True))
        batch.add_column(sa.Column("mfa_method", sa.String(24), nullable=True))
        batch.create_check_constraint(
            "ck_identity_session_mfa_method",
            "mfa_method IS NULL OR mfa_method IN ('totp','recovery_code')",
        )
    op.create_table(
        "compliance_terms_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_type", sa.String(80), nullable=False),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("publication_uri", sa.String(1024), nullable=False),
        sa.Column("published_at", timestamp, nullable=False),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("length(document_type) >= 2", name="ck_compliance_terms_type_length"),
        sa.CheckConstraint("length(content_sha256) = 64", name="ck_compliance_terms_sha_length"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_type", "version", name="uq_compliance_terms_type_version"),
    )
    op.create_table(
        "compliance_consent_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("terms_document_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("client_ip", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("recorded_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "decision IN ('accepted','withdrawn')", name="ck_compliance_consent_decision"
        ),
        sa.CheckConstraint("length(evidence_sha256) = 64", name="ck_compliance_consent_sha"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            name="fk_compliance_consent_membership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["terms_document_id"],
            ["compliance_terms_documents.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_compliance_consent_tenant_user_time",
        "compliance_consent_records",
        ["tenant_id", "user_id", "recorded_at"],
    )
    op.create_table(
        "compliance_privacy_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("requested_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.Column("due_at", timestamp, nullable=False),
        sa.Column("updated_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", timestamp, nullable=True),
        sa.Column("artifact_expires_at", timestamp, nullable=True),
        sa.Column("retention_until", timestamp, nullable=True),
        sa.Column("rejection_code", sa.String(80), nullable=True),
        sa.CheckConstraint("kind IN ('export','delete')", name="ck_compliance_privacy_kind"),
        sa.CheckConstraint(
            "status IN ('requested','identity_verified','processing','export_ready',"
            "'deletion_scheduled','fulfilled','rejected','expired','cancelled')",
            name="ck_compliance_privacy_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            name="fk_compliance_privacy_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_compliance_privacy_tenant_binding"),
    )
    op.create_index(
        "ix_compliance_privacy_tenant_user",
        "compliance_privacy_requests",
        ["tenant_id", "user_id", "requested_at"],
    )
    op.create_index(
        "ix_compliance_privacy_due",
        "compliance_privacy_requests",
        ["status", "due_at"],
    )
    op.create_table(
        "compliance_privacy_request_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("from_status", sa.String(32), nullable=True),
        sa.Column("to_status", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(80), nullable=True),
        sa.Column("occurred_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["request_id", "tenant_id"],
            ["compliance_privacy_requests.id", "compliance_privacy_requests.tenant_id"],
            name="fk_compliance_privacy_event_request",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            name="fk_compliance_privacy_event_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_compliance_privacy_event_tenant_time",
        "compliance_privacy_request_events",
        ["tenant_id", "occurred_at"],
    )
    op.create_table(
        "compliance_mfa_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("secret_key_id", sa.String(160), nullable=False),
        sa.Column("secret_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("last_accepted_counter", sa.Integer(), nullable=True),
        sa.Column("verified_at", timestamp, nullable=True),
        sa.Column("disabled_at", timestamp, nullable=True),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('pending','active','disabled')", name="ck_compliance_mfa_status"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            name="fk_compliance_mfa_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_compliance_mfa_tenant_user"),
        sa.UniqueConstraint("id", "tenant_id", "user_id", name="uq_compliance_mfa_binding"),
    )
    op.create_index(
        "ix_compliance_mfa_tenant_status",
        "compliance_mfa_credentials",
        ["tenant_id", "status"],
    )
    op.create_table(
        "compliance_recovery_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("credential_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("consumed_at", timestamp, nullable=True),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["credential_id", "tenant_id", "user_id"],
            [
                "compliance_mfa_credentials.id",
                "compliance_mfa_credentials.tenant_id",
                "compliance_mfa_credentials.user_id",
            ],
            name="fk_compliance_recovery_mfa",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("credential_id", "code_hash", name="uq_compliance_recovery_hash"),
    )
    op.create_index(
        "ix_compliance_recovery_tenant_user",
        "compliance_recovery_codes",
        ["tenant_id", "user_id"],
    )
    _install_database_guards()


def _install_database_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
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
                "CREATE FUNCTION compliance_reject_evidence_mutation() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
                "'compliance evidence is append-only'; END; $$"
            )
        )
        for table in _APPEND_ONLY_TABLES:
            op.execute(
                sa.text(
                    f'CREATE TRIGGER "{table}_no_mutation" BEFORE UPDATE OR DELETE '
                    f'ON "{table}" FOR EACH ROW EXECUTE FUNCTION '
                    "compliance_reject_evidence_mutation()"
                )
            )
    elif dialect == "sqlite":
        for table in _APPEND_ONLY_TABLES:
            op.execute(
                sa.text(
                    f'CREATE TRIGGER "{table}_no_update" BEFORE UPDATE ON "{table}" '
                    "BEGIN SELECT RAISE(ABORT, 'compliance evidence is append-only'); END"
                )
            )
            op.execute(
                sa.text(
                    f'CREATE TRIGGER "{table}_no_delete" BEFORE DELETE ON "{table}" '
                    "BEGIN SELECT RAISE(ABORT, 'compliance evidence is append-only'); END"
                )
            )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        for table in _APPEND_ONLY_TABLES:
            op.execute(sa.text(f'DROP TRIGGER IF EXISTS "{table}_no_mutation" ON "{table}"'))
        op.execute(sa.text("DROP FUNCTION IF EXISTS compliance_reject_evidence_mutation()"))
    elif dialect == "sqlite":
        for table in _APPEND_ONLY_TABLES:
            op.execute(sa.text(f'DROP TRIGGER IF EXISTS "{table}_no_update"'))
            op.execute(sa.text(f'DROP TRIGGER IF EXISTS "{table}_no_delete"'))

    op.drop_index("ix_compliance_recovery_tenant_user", table_name="compliance_recovery_codes")
    op.drop_table("compliance_recovery_codes")
    op.drop_index("ix_compliance_mfa_tenant_status", table_name="compliance_mfa_credentials")
    op.drop_table("compliance_mfa_credentials")
    op.drop_index(
        "ix_compliance_privacy_event_tenant_time",
        table_name="compliance_privacy_request_events",
    )
    op.drop_table("compliance_privacy_request_events")
    op.drop_index("ix_compliance_privacy_due", table_name="compliance_privacy_requests")
    op.drop_index("ix_compliance_privacy_tenant_user", table_name="compliance_privacy_requests")
    op.drop_table("compliance_privacy_requests")
    op.drop_index("ix_compliance_consent_tenant_user_time", table_name="compliance_consent_records")
    op.drop_table("compliance_consent_records")
    op.drop_table("compliance_terms_documents")
    with op.batch_alter_table("identity_sessions") as batch:
        batch.drop_constraint("ck_identity_session_mfa_method", type_="check")
        batch.drop_column("mfa_method")
        batch.drop_column("mfa_verified_at")

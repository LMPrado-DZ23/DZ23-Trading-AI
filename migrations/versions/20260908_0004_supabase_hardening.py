"""Harden Supabase RLS, trigger functions, and foreign-key indexes.

Revision ID: 20260908_0004
Revises: 20260908_0003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_0004"
down_revision = "20260908_0003"
branch_labels = None
depends_on = None

_IDENTITY_TENANT_TABLES = (
    "identity_memberships",
    "identity_sessions",
    "identity_password_reset_tokens",
    "identity_email_verification_tokens",
    "identity_audit_events",
)

_COMPLIANCE_TENANT_TABLES = (
    "compliance_consent_records",
    "compliance_privacy_requests",
    "compliance_privacy_request_events",
    "compliance_mfa_credentials",
    "compliance_recovery_codes",
)

_FOREIGN_KEY_INDEXES = (
    (
        "ix_compliance_consent_terms_document",
        "compliance_consent_records",
        ("terms_document_id",),
    ),
    (
        "ix_compliance_privacy_event_actor",
        "compliance_privacy_request_events",
        ("tenant_id", "actor_user_id"),
    ),
    (
        "ix_compliance_privacy_event_request",
        "compliance_privacy_request_events",
        ("request_id", "tenant_id"),
    ),
    (
        "ix_compliance_recovery_credential",
        "compliance_recovery_codes",
        ("credential_id", "tenant_id", "user_id"),
    ),
    (
        "ix_identity_audit_actor",
        "identity_audit_events",
        ("tenant_id", "actor_user_id"),
    ),
    (
        "ix_identity_verification_membership",
        "identity_email_verification_tokens",
        ("tenant_id", "user_id"),
    ),
    (
        "ix_identity_reset_membership",
        "identity_password_reset_tokens",
        ("tenant_id", "user_id"),
    ),
    (
        "ix_identity_session_membership",
        "identity_sessions",
        ("membership_id", "tenant_id", "user_id"),
    ),
)

_TENANT_ID = "(SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
_TENANT_SLUG = "(SELECT NULLIF(current_setting('app.tenant_slug', true), ''))"
_REGISTRATION_USER_ID = (
    "(SELECT NULLIF(current_setting('app.registration_user_id', true), '')::uuid)"
)


def _replace_tenant_policy(table: str, *, optimized: bool) -> None:
    policy = f"{table}_tenant_isolation"
    op.execute(sa.text(f'DROP POLICY IF EXISTS "{policy}" ON "{table}"'))
    tenant_id = (
        _TENANT_ID
        if optimized
        else "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    )
    op.execute(
        sa.text(
            f'CREATE POLICY "{policy}" ON "{table}" '
            f"USING (tenant_id = {tenant_id}) WITH CHECK (tenant_id = {tenant_id})"
        )
    )


def _replace_identity_policies(*, optimized: bool) -> None:
    tenant_id = (
        _TENANT_ID
        if optimized
        else "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    )
    tenant_slug = (
        _TENANT_SLUG
        if optimized
        else "NULLIF(current_setting('app.tenant_slug', true), '')"
    )
    registration_user_id = (
        _REGISTRATION_USER_ID
        if optimized
        else "NULLIF(current_setting('app.registration_user_id', true), '')::uuid"
    )

    op.execute(
        sa.text('DROP POLICY IF EXISTS "identity_tenants_isolation" ON "identity_tenants"')
    )
    op.execute(
        sa.text(
            'CREATE POLICY "identity_tenants_isolation" ON "identity_tenants" '
            f"USING (id = {tenant_id} OR slug = {tenant_slug}) "
            f"WITH CHECK (id = {tenant_id})"
        )
    )
    op.execute(
        sa.text('DROP POLICY IF EXISTS "identity_users_isolation" ON "identity_users"')
    )
    op.execute(
        sa.text(
            'CREATE POLICY "identity_users_isolation" ON "identity_users" '
            "USING (EXISTS (SELECT 1 FROM identity_memberships m "
            f"WHERE m.user_id = identity_users.id AND m.tenant_id = {tenant_id})) "
            f"WITH CHECK (id = {registration_user_id} OR EXISTS (SELECT 1 "
            "FROM identity_memberships m WHERE m.user_id = identity_users.id "
            f"AND m.tenant_id = {tenant_id}))"
        )
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(sa.text('ALTER TABLE "alembic_version" ENABLE ROW LEVEL SECURITY'))
    op.execute(
        sa.text('ALTER TABLE "compliance_terms_documents" ENABLE ROW LEVEL SECURITY')
    )
    op.execute(
        sa.text('ALTER TABLE "compliance_terms_documents" FORCE ROW LEVEL SECURITY')
    )
    op.execute(
        sa.text(
            'CREATE POLICY "compliance_terms_runtime_read" '
            'ON "compliance_terms_documents" FOR SELECT TO tradingagents_app USING (true)'
        )
    )

    op.execute(
        sa.text(
            "ALTER FUNCTION public.identity_reject_audit_mutation() "
            "SET search_path = pg_catalog"
        )
    )
    op.execute(
        sa.text(
            "ALTER FUNCTION public.compliance_reject_evidence_mutation() "
            "SET search_path = pg_catalog"
        )
    )

    _replace_identity_policies(optimized=True)
    for table in (*_IDENTITY_TENANT_TABLES, *_COMPLIANCE_TENANT_TABLES):
        _replace_tenant_policy(table, optimized=True)

    for name, table, columns in _FOREIGN_KEY_INDEXES:
        op.create_index(name, table, list(columns))


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    for name, table, _columns in reversed(_FOREIGN_KEY_INDEXES):
        op.drop_index(name, table_name=table)

    _replace_identity_policies(optimized=False)
    for table in (*_IDENTITY_TENANT_TABLES, *_COMPLIANCE_TENANT_TABLES):
        _replace_tenant_policy(table, optimized=False)

    op.execute(
        sa.text("ALTER FUNCTION public.identity_reject_audit_mutation() RESET search_path")
    )
    op.execute(
        sa.text("ALTER FUNCTION public.compliance_reject_evidence_mutation() RESET search_path")
    )
    op.execute(
        sa.text(
            'DROP POLICY IF EXISTS "compliance_terms_runtime_read" '
            'ON "compliance_terms_documents"'
        )
    )
    op.execute(
        sa.text('ALTER TABLE "compliance_terms_documents" NO FORCE ROW LEVEL SECURITY')
    )
    op.execute(
        sa.text('ALTER TABLE "compliance_terms_documents" DISABLE ROW LEVEL SECURITY')
    )
    op.execute(sa.text('ALTER TABLE "alembic_version" DISABLE ROW LEVEL SECURITY'))

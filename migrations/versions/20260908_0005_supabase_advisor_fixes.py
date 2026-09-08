"""Resolve the remaining Supabase database advisor findings.

Revision ID: 20260908_0005
Revises: 20260908_0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_0005"
down_revision = "20260908_0004"
branch_labels = None
depends_on = None

_TENANT_TABLES = (
    "identity_memberships",
    "identity_sessions",
    "identity_password_reset_tokens",
    "identity_email_verification_tokens",
    "identity_audit_events",
    "compliance_consent_records",
    "compliance_privacy_requests",
    "compliance_privacy_request_events",
    "compliance_mfa_credentials",
    "compliance_recovery_codes",
)

_TENANT_ID = "NULLIF((SELECT current_setting('app.tenant_id', true)), '')::uuid"
_TENANT_SLUG = "NULLIF((SELECT current_setting('app.tenant_slug', true)), '')"
_REGISTRATION_USER_ID = (
    "NULLIF((SELECT current_setting('app.registration_user_id', true)), '')::uuid"
)


def _replace_policies(*, optimized: bool) -> None:
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
    for table in _TENANT_TABLES:
        policy = f"{table}_tenant_isolation"
        op.execute(sa.text(f'DROP POLICY IF EXISTS "{policy}" ON "{table}"'))
        op.execute(
            sa.text(
                f'CREATE POLICY "{policy}" ON "{table}" '
                f"USING (tenant_id = {tenant_id}) "
                f"WITH CHECK (tenant_id = {tenant_id})"
            )
        )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _replace_policies(optimized=True)
    op.drop_index("ix_identity_audit_actor", table_name="identity_audit_events")
    op.create_index(
        "ix_identity_audit_actor", "identity_audit_events", ["actor_user_id"]
    )
    op.execute(
        sa.text(
            'CREATE POLICY "alembic_version_no_runtime_access" '
            'ON "alembic_version" FOR ALL TO public USING (false) WITH CHECK (false)'
        )
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        sa.text(
            'DROP POLICY IF EXISTS "alembic_version_no_runtime_access" '
            'ON "alembic_version"'
        )
    )
    op.drop_index("ix_identity_audit_actor", table_name="identity_audit_events")
    op.create_index(
        "ix_identity_audit_actor",
        "identity_audit_events",
        ["tenant_id", "actor_user_id"],
    )
    _replace_policies(optimized=False)

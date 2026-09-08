"""Create the least-privilege PostgreSQL runtime role.

Revision ID: 20260908_0003
Revises: 20260908_0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_0003"
down_revision = "20260908_0002"
branch_labels = None
depends_on = None

_ROLE = "tradingagents_app"
_READ_WRITE_TABLES = (
    "identity_tenants",
    "identity_users",
    "identity_memberships",
    "identity_sessions",
    "identity_password_reset_tokens",
    "identity_email_verification_tokens",
    "compliance_privacy_requests",
    "compliance_mfa_credentials",
    "compliance_recovery_codes",
)
_APPEND_ONLY_TABLES = (
    "identity_audit_events",
    "compliance_consent_records",
    "compliance_privacy_request_events",
)
_READ_ONLY_TABLES = ("compliance_terms_documents",)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        sa.text(
            "DO $$ DECLARE role_row record; BEGIN "
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls INTO role_row "
            "FROM pg_roles WHERE rolname = 'tradingagents_app'; "
            "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tradingagents_app') THEN "
            "CREATE ROLE tradingagents_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOINHERIT NOBYPASSRLS; "
            "ELSIF role_row.rolsuper OR role_row.rolcreatedb OR role_row.rolcreaterole "
            "OR role_row.rolbypassrls THEN "
            "RAISE EXCEPTION 'existing tradingagents_app role has unsafe attributes'; "
            "END IF; END $$"
        )
    )
    op.execute(
        sa.text(
            "DO $$ BEGIN EXECUTE format('GRANT CONNECT ON DATABASE %I TO tradingagents_app', "
            "current_database()); END $$"
        )
    )
    op.execute(sa.text("GRANT USAGE ON SCHEMA public TO tradingagents_app"))
    op.execute(sa.text("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC"))
    for table in _READ_WRITE_TABLES:
        op.execute(
            sa.text(
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "{table}" '
                "TO tradingagents_app"
            )
        )
    for table in _APPEND_ONLY_TABLES:
        op.execute(
            sa.text(f'GRANT SELECT, INSERT ON TABLE "{table}" TO tradingagents_app')
        )
    for table in _READ_ONLY_TABLES:
        op.execute(sa.text(f'GRANT SELECT ON TABLE "{table}" TO tradingagents_app'))


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in (*_READ_WRITE_TABLES, *_APPEND_ONLY_TABLES, *_READ_ONLY_TABLES):
        op.execute(sa.text(f'REVOKE ALL ON TABLE "{table}" FROM tradingagents_app'))
    op.execute(sa.text("REVOKE USAGE ON SCHEMA public FROM tradingagents_app"))
    op.execute(
        sa.text(
            "DO $$ BEGIN EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM tradingagents_app', "
            "current_database()); END $$"
        )
    )
    op.execute(sa.text("DROP ROLE IF EXISTS tradingagents_app"))

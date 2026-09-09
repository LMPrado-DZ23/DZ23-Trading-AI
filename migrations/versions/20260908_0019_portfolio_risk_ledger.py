"""Create tenant portfolio, suitability, mandate, ledger and risk controls.

Revision ID: 20260908_0019
Revises: 20260908_0018
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_0019"
down_revision = "20260908_0018"
branch_labels = None
depends_on = None

_TENANT_ID = "NULLIF((SELECT current_setting('app.tenant_id', true)), '')::uuid"
_PROFILES = "'preservation','conservative','moderate','growth','aggressive'"


def upgrade() -> None:
    timestamp = sa.DateTime(timezone=True)
    money = sa.Numeric(36, 12)
    op.create_table(
        "portfolio_suitability_assessments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("requested_profile", sa.String(24), nullable=False),
        sa.Column("maximum_profile", sa.String(24), nullable=False),
        sa.Column("effective_profile", sa.String(24), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("assessed_at", timestamp, nullable=False),
        sa.Column("expires_at", timestamp, nullable=False),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("version >= 1", name="ck_suitability_version"),
        sa.CheckConstraint("policy_version >= 1", name="ck_suitability_policy_version"),
        sa.CheckConstraint(f"requested_profile IN ({_PROFILES})", name="ck_suitability_requested_profile"),
        sa.CheckConstraint(f"maximum_profile IN ({_PROFILES})", name="ck_suitability_maximum_profile"),
        sa.CheckConstraint(f"effective_profile IN ({_PROFILES})", name="ck_suitability_effective_profile"),
        sa.CheckConstraint("status IN ('valid','expired','revoked')", name="ck_suitability_status"),
        sa.CheckConstraint("length(digest) = 64", name="ck_suitability_digest"),
        sa.CheckConstraint("expires_at > assessed_at", name="ck_suitability_window"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            name="fk_portfolio_suitability_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_suitability_tenant_id"),
        sa.UniqueConstraint("tenant_id", "user_id", "version", name="uq_suitability_version"),
    )
    op.create_index("ix_suitability_tenant_user", "portfolio_suitability_assessments", ["tenant_id", "user_id", "version"])

    op.create_table(
        "portfolios",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("base_currency", sa.String(12), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("balance_version", sa.Integer(), nullable=False),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('active','paused','closed')", name="ck_portfolio_status"),
        sa.CheckConstraint("balance_version >= 0", name="ck_portfolio_balance_version"),
        sa.CheckConstraint("base_currency = upper(base_currency)", name="ck_portfolio_currency_upper"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "owner_user_id"],
            ["identity_memberships.tenant_id", "identity_memberships.user_id"],
            name="fk_portfolio_owner_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_portfolio_tenant_id"),
        sa.UniqueConstraint("tenant_id", "owner_user_id", "name", name="uq_portfolio_owner_name"),
    )
    op.create_index("ix_portfolio_tenant_owner", "portfolios", ["tenant_id", "owner_user_id"])

    op.create_table(
        "portfolio_mandates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("portfolio_id", sa.Uuid(), nullable=False),
        sa.Column("suitability_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("profile", sa.String(24), nullable=False),
        sa.Column("mode", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("policy_digest", sa.String(64), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("limits", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("effective_at", timestamp, nullable=False),
        sa.Column("ended_at", timestamp, nullable=True),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("version >= 1", name="ck_mandate_version"),
        sa.CheckConstraint("policy_version >= 1", name="ck_mandate_policy_version"),
        sa.CheckConstraint(f"profile IN ({_PROFILES})", name="ck_mandate_profile"),
        sa.CheckConstraint("mode IN ('backtest','shadow','paper','live_supervised','live_automated')", name="ck_mandate_mode"),
        sa.CheckConstraint("status IN ('draft','active','paused','circuit_broken','closed')", name="ck_mandate_status"),
        sa.CheckConstraint("length(policy_digest) = 64 AND length(digest) = 64", name="ck_mandate_digests"),
        sa.ForeignKeyConstraint(["portfolio_id", "tenant_id"], ["portfolios.id", "portfolios.tenant_id"], name="fk_mandate_portfolio", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["suitability_id", "tenant_id"], ["portfolio_suitability_assessments.id", "portfolio_suitability_assessments.tenant_id"], name="fk_mandate_suitability", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tenant_id", "created_by_user_id"], ["identity_memberships.tenant_id", "identity_memberships.user_id"], name="fk_mandate_creator_membership", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_mandate_tenant_id"),
        sa.UniqueConstraint("tenant_id", "portfolio_id", "version", name="uq_mandate_version"),
    )
    op.create_index("ix_mandate_portfolio_status", "portfolio_mandates", ["tenant_id", "portfolio_id", "status"])

    op.create_table(
        "portfolio_ledger_transactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("portfolio_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("reference_type", sa.String(32), nullable=False),
        sa.Column("reference_id", sa.String(128), nullable=False),
        sa.Column("currency", sa.String(12), nullable=False),
        sa.Column("amount", money, nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", timestamp, nullable=False),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("amount > 0", name="ck_ledger_transaction_amount"),
        sa.CheckConstraint("length(digest) = 64", name="ck_ledger_transaction_digest"),
        sa.CheckConstraint("currency = upper(currency)", name="ck_ledger_transaction_currency"),
        sa.ForeignKeyConstraint(["portfolio_id", "tenant_id"], ["portfolios.id", "portfolios.tenant_id"], name="fk_ledger_transaction_portfolio", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tenant_id", "created_by_user_id"], ["identity_memberships.tenant_id", "identity_memberships.user_id"], name="fk_ledger_transaction_actor", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_ledger_transaction_tenant_id"),
        sa.UniqueConstraint("tenant_id", "portfolio_id", "idempotency_key_hash", name="uq_ledger_idempotency"),
    )
    op.create_index("ix_ledger_transaction_portfolio_time", "portfolio_ledger_transactions", ["tenant_id", "portfolio_id", "occurred_at"])

    op.create_table(
        "portfolio_ledger_postings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("portfolio_id", sa.Uuid(), nullable=False),
        sa.Column("account", sa.String(64), nullable=False),
        sa.Column("asset", sa.String(64), nullable=False),
        sa.Column("quantity", money, nullable=False),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("quantity <> 0", name="ck_ledger_posting_quantity"),
        sa.ForeignKeyConstraint(["transaction_id", "tenant_id"], ["portfolio_ledger_transactions.id", "portfolio_ledger_transactions.tenant_id"], name="fk_ledger_posting_transaction", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["portfolio_id", "tenant_id"], ["portfolios.id", "portfolios.tenant_id"], name="fk_ledger_posting_portfolio", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ledger_posting_portfolio_account", "portfolio_ledger_postings", ["tenant_id", "portfolio_id", "account", "asset"])

    op.create_table(
        "portfolio_cash_reservations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("portfolio_id", sa.Uuid(), nullable=False),
        sa.Column("mandate_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("currency", sa.String(12), nullable=False),
        sa.Column("amount", money, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("purpose_digest", sa.String(64), nullable=False),
        sa.Column("expires_at", timestamp, nullable=False),
        sa.Column("released_at", timestamp, nullable=True),
        sa.Column("consumed_at", timestamp, nullable=True),
        sa.Column("created_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("amount > 0", name="ck_reservation_amount"),
        sa.CheckConstraint("status IN ('active','consumed','released','expired')", name="ck_reservation_status"),
        sa.CheckConstraint("length(purpose_digest) = 64", name="ck_reservation_digest"),
        sa.ForeignKeyConstraint(["portfolio_id", "tenant_id"], ["portfolios.id", "portfolios.tenant_id"], name="fk_reservation_portfolio", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["mandate_id", "tenant_id"], ["portfolio_mandates.id", "portfolio_mandates.tenant_id"], name="fk_reservation_mandate", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "portfolio_id", "idempotency_key_hash", name="uq_reservation_idempotency"),
    )
    op.create_index("ix_reservation_active", "portfolio_cash_reservations", ["tenant_id", "portfolio_id", "status", "expires_at"])

    op.create_table(
        "portfolio_positions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("portfolio_id", sa.Uuid(), nullable=False),
        sa.Column("symbol", sa.String(64), nullable=False),
        sa.Column("quantity", money, nullable=False),
        sa.Column("average_cost", money, nullable=False),
        sa.Column("realized_pnl", money, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", timestamp, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("quantity >= 0", name="ck_position_quantity"),
        sa.CheckConstraint("average_cost >= 0", name="ck_position_average_cost"),
        sa.CheckConstraint("version >= 1", name="ck_position_version"),
        sa.ForeignKeyConstraint(["portfolio_id", "tenant_id"], ["portfolios.id", "portfolios.tenant_id"], name="fk_position_portfolio", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "portfolio_id", "symbol", name="uq_position_symbol"),
    )
    op.create_index("ix_position_portfolio", "portfolio_positions", ["tenant_id", "portfolio_id"])

    op.create_table(
        "portfolio_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("portfolio_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("cash", money, nullable=False),
        sa.Column("reserved_cash", money, nullable=False),
        sa.Column("market_value", money, nullable=False),
        sa.Column("equity", money, nullable=False),
        sa.Column("realized_pnl", money, nullable=False),
        sa.Column("unrealized_pnl", money, nullable=False),
        sa.Column("high_water_mark", money, nullable=False),
        sa.Column("drawdown", sa.Numeric(18, 12), nullable=False),
        sa.Column("positions", sa.JSON(), nullable=False),
        sa.Column("prices", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("captured_at", timestamp, nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_snapshot_sequence"),
        sa.CheckConstraint("cash >= 0 AND reserved_cash >= 0", name="ck_snapshot_cash"),
        sa.CheckConstraint("equity >= 0 AND high_water_mark >= 0", name="ck_snapshot_equity"),
        sa.CheckConstraint("drawdown >= 0 AND drawdown <= 1", name="ck_snapshot_drawdown"),
        sa.CheckConstraint("length(digest) = 64", name="ck_snapshot_digest"),
        sa.ForeignKeyConstraint(["portfolio_id", "tenant_id"], ["portfolios.id", "portfolios.tenant_id"], name="fk_snapshot_portfolio", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "portfolio_id", "sequence", name="uq_snapshot_sequence"),
    )
    op.create_index("ix_snapshot_portfolio_time", "portfolio_snapshots", ["tenant_id", "portfolio_id", "captured_at"])

    op.create_table(
        "portfolio_circuit_breakers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("portfolio_id", sa.Uuid(), nullable=False),
        sa.Column("mandate_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("observed_value", money, nullable=False),
        sa.Column("threshold_value", money, nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column("triggered_at", timestamp, nullable=False),
        sa.Column("reset_at", timestamp, nullable=True),
        sa.Column("reset_by_user_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint("status IN ('open','reset')", name="ck_breaker_status"),
        sa.CheckConstraint("length(evidence_digest) = 64", name="ck_breaker_digest"),
        sa.ForeignKeyConstraint(["portfolio_id", "tenant_id"], ["portfolios.id", "portfolios.tenant_id"], name="fk_breaker_portfolio", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["mandate_id", "tenant_id"], ["portfolio_mandates.id", "portfolio_mandates.tenant_id"], name="fk_breaker_mandate", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_breaker_open", "portfolio_circuit_breakers", ["tenant_id", "portfolio_id", "status"])
    _install_guards()


def _install_guards() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    tables = (
        "portfolio_suitability_assessments", "portfolios", "portfolio_mandates",
        "portfolio_ledger_transactions", "portfolio_ledger_postings",
        "portfolio_cash_reservations", "portfolio_positions", "portfolio_snapshots",
        "portfolio_circuit_breakers",
    )
    for table in tables:
        op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'CREATE POLICY "{table}_tenant_isolation" ON "{table}" TO tradingagents_app USING (tenant_id = {_TENANT_ID}) WITH CHECK (tenant_id = {_TENANT_ID})'))
    append_only = (
        "portfolio_suitability_assessments", "portfolio_mandates",
        "portfolio_ledger_transactions", "portfolio_ledger_postings", "portfolio_snapshots",
    )
    op.execute(sa.text("CREATE FUNCTION portfolio_reject_history_mutation() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$ BEGIN RAISE EXCEPTION 'portfolio history is append-only'; END; $$"))
    for table in append_only:
        op.execute(sa.text(f'CREATE TRIGGER "{table}_append_only" BEFORE UPDATE OR DELETE ON "{table}" FOR EACH ROW EXECUTE FUNCTION portfolio_reject_history_mutation()'))
        op.execute(sa.text(f'GRANT SELECT, INSERT ON "{table}" TO tradingagents_app'))
    for table in ("portfolios", "portfolio_cash_reservations", "portfolio_positions", "portfolio_circuit_breakers"):
        op.execute(sa.text(f'GRANT SELECT, INSERT, UPDATE ON "{table}" TO tradingagents_app'))
    op.execute(sa.text("CREATE FUNCTION portfolio_assert_balanced_ledger() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$ DECLARE tx uuid; total numeric; BEGIN tx := COALESCE(NEW.transaction_id, OLD.transaction_id); SELECT COALESCE(sum(quantity), 0) INTO total FROM public.portfolio_ledger_postings WHERE transaction_id = tx; IF total <> 0 THEN RAISE EXCEPTION 'ledger transaction is not balanced'; END IF; RETURN NULL; END; $$"))
    op.execute(sa.text("CREATE CONSTRAINT TRIGGER portfolio_ledger_balanced AFTER INSERT OR UPDATE OR DELETE ON portfolio_ledger_postings DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION portfolio_assert_balanced_ledger()"))


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("DROP TRIGGER IF EXISTS portfolio_ledger_balanced ON portfolio_ledger_postings"))
        op.execute(sa.text("DROP FUNCTION IF EXISTS portfolio_assert_balanced_ledger()"))
        op.execute(sa.text("DROP FUNCTION IF EXISTS portfolio_reject_history_mutation() CASCADE"))
    for table in (
        "portfolio_circuit_breakers", "portfolio_snapshots", "portfolio_positions",
        "portfolio_cash_reservations", "portfolio_ledger_postings",
        "portfolio_ledger_transactions", "portfolio_mandates", "portfolios",
        "portfolio_suitability_assessments",
    ):
        op.drop_table(table)

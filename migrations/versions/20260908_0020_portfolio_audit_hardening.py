"""Close portfolio audit findings around authority, ledger and valuation.

Revision ID: 20260908_0020
Revises: 20260908_0019
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_0020"
down_revision = "20260908_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    timestamp = sa.DateTime(timezone=True)
    money = sa.Numeric(36, 12)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                "DO $$ BEGIN IF EXISTS (SELECT 1 FROM portfolio_positions LIMIT 1) "
                "OR EXISTS (SELECT 1 FROM portfolio_snapshots LIMIT 1) THEN "
                "RAISE EXCEPTION 'M4 positions/snapshots require trusted provenance migration'; "
                "END IF; END $$"
            )
        )
    else:
        unsafe_legacy_rows = sum(
            int(bind.scalar(sa.text(f"SELECT count(*) FROM {table}")) or 0)
            for table in ("portfolio_positions", "portfolio_snapshots")
        )
        if unsafe_legacy_rows:
            raise RuntimeError(
                "M4 positions/snapshots lack trusted asset-class or valuation provenance; "
                "export and reconcile them before applying 20260908_0020"
            )
    if bind.dialect.name == "sqlite":
        _upgrade_sqlite(timestamp, money)
        return

    op.add_column("portfolios", sa.Column("current_mandate_id", sa.Uuid(), nullable=True))
    op.add_column(
        "portfolios",
        sa.Column("unit_count", money, nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "portfolios",
        sa.Column("last_nav_per_unit", money, nullable=False, server_default=sa.text("1")),
    )
    op.create_check_constraint("ck_portfolio_unit_count", "portfolios", "unit_count >= 0")
    op.create_check_constraint(
        "ck_portfolio_nav_positive", "portfolios", "last_nav_per_unit > 0"
    )
    op.execute(
        sa.text(
            "UPDATE portfolios AS p SET unit_count = GREATEST(COALESCE(("
            "SELECT sum(CASE WHEN t.event_type = 'funding' THEN t.amount "
            "WHEN t.event_type = 'withdrawal' THEN -t.amount ELSE 0 END) "
            "FROM portfolio_ledger_transactions t WHERE t.tenant_id = p.tenant_id "
            "AND t.portfolio_id = p.id), 0), 0)"
        )
    )

    op.create_unique_constraint(
        "uq_mandate_identity_portfolio",
        "portfolio_mandates",
        ["id", "tenant_id", "portfolio_id"],
    )
    op.drop_constraint("ck_mandate_mode", "portfolio_mandates", type_="check")
    op.create_check_constraint(
        "ck_mandate_mode",
        "portfolio_mandates",
        "mode IN ('backtest','shadow','paper')",
    )
    op.execute(
        sa.text(
            "UPDATE portfolios AS p SET current_mandate_id = latest.id "
            "FROM (SELECT DISTINCT ON (tenant_id, portfolio_id) id, tenant_id, portfolio_id "
            "FROM portfolio_mandates ORDER BY tenant_id, portfolio_id, version DESC) AS latest "
            "WHERE p.tenant_id = latest.tenant_id AND p.id = latest.portfolio_id"
        )
    )
    op.create_foreign_key(
        "fk_portfolio_current_mandate",
        "portfolios",
        "portfolio_mandates",
        ["current_mandate_id", "tenant_id", "id"],
        ["id", "tenant_id", "portfolio_id"],
        ondelete="RESTRICT",
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text(
                "DO $$ BEGIN IF EXISTS (SELECT 1 FROM public.portfolios p "
                "JOIN public.portfolio_mandates m ON m.id = p.current_mandate_id "
                "AND m.tenant_id = p.tenant_id AND m.portfolio_id = p.id "
                "LEFT JOIN LATERAL (SELECT s.id, s.status, s.expires_at, "
                "s.effective_profile FROM public.portfolio_suitability_assessments s "
                "WHERE s.tenant_id = p.tenant_id AND s.user_id = p.owner_user_id "
                "ORDER BY s.version DESC LIMIT 1) current_s ON true "
                "WHERE m.status = 'active' AND (current_s.id IS NULL "
                "OR m.suitability_id <> current_s.id OR current_s.status <> 'valid' "
                "OR current_s.expires_at <= clock_timestamp() OR "
                "CASE m.profile WHEN 'preservation' THEN 0 WHEN 'conservative' THEN 1 "
                "WHEN 'moderate' THEN 2 WHEN 'growth' THEN 3 "
                "WHEN 'aggressive' THEN 4 ELSE 99 END > "
                "CASE current_s.effective_profile WHEN 'preservation' THEN 0 "
                "WHEN 'conservative' THEN 1 WHEN 'moderate' THEN 2 "
                "WHEN 'growth' THEN 3 WHEN 'aggressive' THEN 4 ELSE -1 END)) THEN "
                "RAISE EXCEPTION 'M4 active mandate requires current valid suitability'; "
                "END IF; END $$"
            )
        )
        op.execute(
            sa.text(
                "CREATE FUNCTION portfolio_assert_current_mandate() RETURNS trigger "
                "LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$ "
                "DECLARE pid uuid; tenant uuid; pointed uuid; latest uuid; "
                "active_status text; active_suitability uuid; active_profile text; "
                "owner_id uuid; latest_suitability uuid; latest_status text; "
                "latest_expires timestamptz; latest_profile text; violations bigint; BEGIN "
                "IF TG_TABLE_NAME = 'portfolio_suitability_assessments' THEN "
                "IF TG_OP = 'DELETE' THEN tenant := OLD.tenant_id; owner_id := OLD.user_id; "
                "ELSE tenant := NEW.tenant_id; owner_id := NEW.user_id; END IF; "
                "SELECT count(*) INTO violations FROM public.portfolios p "
                "JOIN public.portfolio_mandates m ON m.id = p.current_mandate_id "
                "AND m.tenant_id = p.tenant_id AND m.portfolio_id = p.id "
                "LEFT JOIN LATERAL (SELECT s.id, s.status, s.expires_at, s.effective_profile "
                "FROM public.portfolio_suitability_assessments s WHERE s.tenant_id = p.tenant_id "
                "AND s.user_id = p.owner_user_id ORDER BY s.version DESC LIMIT 1) current_s ON true "
                "WHERE p.tenant_id = tenant AND p.owner_user_id = owner_id AND m.status = 'active' "
                "AND (current_s.id IS NULL OR m.suitability_id <> current_s.id "
                "OR current_s.status <> 'valid' OR current_s.expires_at <= clock_timestamp() "
                "OR CASE m.profile WHEN 'preservation' THEN 0 WHEN 'conservative' THEN 1 "
                "WHEN 'moderate' THEN 2 WHEN 'growth' THEN 3 WHEN 'aggressive' THEN 4 ELSE 99 END "
                "> CASE current_s.effective_profile WHEN 'preservation' THEN 0 "
                "WHEN 'conservative' THEN 1 WHEN 'moderate' THEN 2 WHEN 'growth' THEN 3 "
                "WHEN 'aggressive' THEN 4 ELSE -1 END); "
                "IF violations <> 0 THEN RAISE EXCEPTION "
                "'active mandate is incompatible with latest suitability'; END IF; RETURN NULL; "
                "END IF; "
                "IF TG_TABLE_NAME = 'portfolios' THEN pid := NEW.id; tenant := NEW.tenant_id; "
                "ELSE pid := NEW.portfolio_id; tenant := NEW.tenant_id; END IF; "
                "SELECT current_mandate_id INTO pointed FROM public.portfolios "
                "WHERE id = pid AND tenant_id = tenant; "
                "SELECT id INTO latest FROM public.portfolio_mandates "
                "WHERE portfolio_id = pid AND tenant_id = tenant ORDER BY version DESC LIMIT 1; "
                "IF pointed IS DISTINCT FROM latest THEN "
                "RAISE EXCEPTION 'portfolio current mandate does not reference latest version'; "
                "END IF; "
                "SELECT m.status, m.suitability_id, m.profile, p.owner_user_id "
                "INTO active_status, active_suitability, active_profile, owner_id "
                "FROM public.portfolios p LEFT JOIN public.portfolio_mandates m "
                "ON m.id = p.current_mandate_id AND m.tenant_id = p.tenant_id "
                "AND m.portfolio_id = p.id WHERE p.id = pid AND p.tenant_id = tenant; "
                "IF active_status = 'active' THEN "
                "SELECT s.id, s.status, s.expires_at, s.effective_profile "
                "INTO latest_suitability, latest_status, latest_expires, latest_profile "
                "FROM public.portfolio_suitability_assessments s WHERE s.tenant_id = tenant "
                "AND s.user_id = owner_id ORDER BY s.version DESC LIMIT 1; "
                "IF latest_suitability IS NULL OR active_suitability <> latest_suitability "
                "OR latest_status <> 'valid' OR latest_expires <= clock_timestamp() "
                "OR CASE active_profile WHEN 'preservation' THEN 0 WHEN 'conservative' THEN 1 "
                "WHEN 'moderate' THEN 2 WHEN 'growth' THEN 3 WHEN 'aggressive' THEN 4 ELSE 99 END "
                "> CASE latest_profile WHEN 'preservation' THEN 0 WHEN 'conservative' THEN 1 "
                "WHEN 'moderate' THEN 2 WHEN 'growth' THEN 3 WHEN 'aggressive' THEN 4 ELSE -1 END "
                "THEN RAISE EXCEPTION 'active mandate is incompatible with latest suitability'; "
                "END IF; END IF; RETURN NULL; END; $$"
            )
        )
        op.execute(
            sa.text(
                "CREATE CONSTRAINT TRIGGER portfolio_current_mandate_pointer "
                "AFTER INSERT OR UPDATE OF current_mandate_id, owner_user_id ON portfolios "
                "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
                "EXECUTE FUNCTION portfolio_assert_current_mandate()"
            )
        )
        op.execute(
            sa.text(
                "CREATE CONSTRAINT TRIGGER mandate_current_pointer "
                "AFTER INSERT OR UPDATE OF status, suitability_id, profile, version "
                "ON portfolio_mandates DEFERRABLE INITIALLY DEFERRED "
                "FOR EACH ROW EXECUTE FUNCTION portfolio_assert_current_mandate()"
            )
        )
        op.execute(
            sa.text(
                "CREATE CONSTRAINT TRIGGER suitability_active_mandate_guard "
                "AFTER INSERT OR UPDATE OR DELETE ON portfolio_suitability_assessments "
                "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
                "EXECUTE FUNCTION portfolio_assert_current_mandate()"
            )
        )

    op.add_column(
        "portfolio_ledger_transactions",
        sa.Column(
            "provenance",
            sa.String(32),
            nullable=True,
        ),
    )
    op.add_column(
        "portfolio_ledger_transactions",
        sa.Column(
            "verification_status",
            sa.String(24),
            nullable=True,
        ),
    )
    op.add_column(
        "portfolio_ledger_transactions",
        sa.Column("unit_delta", money, nullable=True),
    )
    op.add_column(
        "portfolio_ledger_transactions",
        sa.Column("nav_per_unit", money, nullable=True),
    )
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS portfolio_ledger_transactions_append_only "
            "ON portfolio_ledger_transactions"
        )
    )
    op.execute(
        sa.text(
            "UPDATE portfolio_ledger_transactions SET "
            "provenance = CASE WHEN event_type IN ('funding','withdrawal') "
            "THEN 'user_declared' "
            "ELSE 'internal' END, "
            "verification_status = CASE WHEN event_type IN ('funding','withdrawal') "
            "THEN 'unverified' "
            "ELSE 'internal' END, "
            "unit_delta = CASE WHEN event_type = 'funding' THEN amount "
            "WHEN event_type = 'withdrawal' THEN -amount ELSE 0 END, "
            "nav_per_unit = 1"
        )
    )
    for column in ("provenance", "verification_status", "unit_delta", "nav_per_unit"):
        op.alter_column("portfolio_ledger_transactions", column, nullable=False)
    op.create_check_constraint(
        "ck_ledger_transaction_provenance",
        "portfolio_ledger_transactions",
        "provenance IN ('user_declared','broker_reconciled','internal')",
    )
    op.create_check_constraint(
        "ck_ledger_transaction_verification",
        "portfolio_ledger_transactions",
        "verification_status IN ('unverified','verified','internal')",
    )
    op.create_check_constraint(
        "ck_ledger_transaction_provenance_coherence",
        "portfolio_ledger_transactions",
        "(provenance = 'user_declared' AND verification_status = 'unverified' "
        "AND unit_delta <> 0) OR "
        "(provenance = 'broker_reconciled' AND verification_status = 'verified') OR "
        "(provenance = 'internal' AND verification_status = 'internal' "
        "AND unit_delta = 0)",
    )
    op.create_check_constraint(
        "ck_ledger_transaction_nav",
        "portfolio_ledger_transactions",
        "nav_per_unit > 0",
    )
    op.create_unique_constraint(
        "uq_ledger_transaction_identity_portfolio",
        "portfolio_ledger_transactions",
        ["id", "tenant_id", "portfolio_id"],
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER portfolio_ledger_transactions_append_only "
            "BEFORE UPDATE OR DELETE ON portfolio_ledger_transactions FOR EACH ROW "
            "EXECUTE FUNCTION portfolio_reject_history_mutation()"
        )
    )
    op.drop_constraint(
        "fk_ledger_posting_transaction",
        "portfolio_ledger_postings",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_ledger_posting_transaction",
        "portfolio_ledger_postings",
        "portfolio_ledger_transactions",
        ["transaction_id", "tenant_id", "portfolio_id"],
        ["id", "tenant_id", "portfolio_id"],
        ondelete="RESTRICT",
    )

    op.add_column(
        "portfolio_positions",
        sa.Column(
            "asset_class",
            sa.String(16),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_position_asset_class",
        "portfolio_positions",
        "asset_class IN ('crypto','stock')",
    )

    snapshot_columns = (
        sa.Column("unit_count", money, nullable=False, server_default=sa.text("1")),
        sa.Column("nav_per_unit", money, nullable=False, server_default=sa.text("1")),
        sa.Column("high_water_nav", money, nullable=False, server_default=sa.text("1")),
        sa.Column(
            "daily_drawdown",
            sa.Numeric(18, 12),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "valuation_source",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'market_data_pit'"),
        ),
        sa.Column("valuation_observed_at", timestamp, nullable=True),
        sa.Column("valuation_available_at", timestamp, nullable=True),
        sa.Column("valuation_digest", sa.String(64), nullable=True),
    )
    for column in snapshot_columns:
        op.add_column("portfolio_snapshots", column)
    op.alter_column("portfolio_snapshots", "valuation_observed_at", nullable=False)
    op.alter_column("portfolio_snapshots", "valuation_available_at", nullable=False)
    op.alter_column("portfolio_snapshots", "valuation_digest", nullable=False)
    op.create_check_constraint(
        "ck_snapshot_unit_count", "portfolio_snapshots", "unit_count > 0"
    )
    op.create_check_constraint(
        "ck_snapshot_nav",
        "portfolio_snapshots",
        "nav_per_unit > 0 AND high_water_nav > 0",
    )
    op.create_check_constraint(
        "ck_snapshot_daily_drawdown",
        "portfolio_snapshots",
        "daily_drawdown >= 0 AND daily_drawdown <= 1",
    )
    op.create_check_constraint(
        "ck_snapshot_valuation_source",
        "portfolio_snapshots",
        "valuation_source IN ('broker_reconciliation','market_data_pit')",
    )
    op.create_check_constraint(
        "ck_snapshot_valuation_digest",
        "portfolio_snapshots",
        "length(valuation_digest) = 64",
    )
    op.create_check_constraint(
        "ck_snapshot_valuation_timeline",
        "portfolio_snapshots",
        "valuation_observed_at <= valuation_available_at AND "
        "valuation_available_at <= captured_at",
    )

    if op.get_bind().dialect.name == "postgresql":
        _replace_ledger_guard()


def _replace_ledger_guard() -> None:
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS portfolio_ledger_balanced "
            "ON portfolio_ledger_postings"
        )
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS portfolio_assert_balanced_ledger()"))
    op.execute(
        sa.text(
            "CREATE FUNCTION portfolio_assert_complete_ledger() RETURNS trigger "
            "LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$ "
            "DECLARE tx uuid; posting_count bigint; total numeric; cash_count bigint; "
            "external_count bigint; tx_amount numeric; invalid_rows bigint; BEGIN "
            "IF TG_TABLE_NAME = 'portfolio_ledger_transactions' THEN tx := NEW.id; "
            "ELSE tx := COALESCE(NEW.transaction_id, OLD.transaction_id); END IF; "
            "SELECT t.amount, count(p.id), COALESCE(sum(p.quantity), 0), "
            "count(p.id) FILTER (WHERE p.account = 'cash'), "
            "count(p.id) FILTER (WHERE p.account = 'external'), "
            "count(p.id) FILTER (WHERE p.portfolio_id <> t.portfolio_id "
            "OR p.tenant_id <> t.tenant_id OR p.asset <> t.currency) "
            "INTO tx_amount, posting_count, total, cash_count, external_count, invalid_rows "
            "FROM public.portfolio_ledger_transactions t "
            "LEFT JOIN public.portfolio_ledger_postings p ON p.transaction_id = t.id "
            "WHERE t.id = tx GROUP BY t.amount, t.currency; "
            "IF NOT FOUND OR posting_count <> 2 OR total <> 0 OR cash_count <> 1 "
            "OR external_count <> 1 OR invalid_rows <> 0 OR NOT EXISTS "
            "(SELECT 1 FROM public.portfolio_ledger_postings p "
            "WHERE p.transaction_id = tx AND p.account = 'cash' "
            "AND abs(p.quantity) = tx_amount) THEN "
            "RAISE EXCEPTION 'ledger transaction is incomplete or inconsistent'; END IF; "
            "RETURN NULL; END; $$"
        )
    )
    op.execute(
        sa.text(
            "CREATE CONSTRAINT TRIGGER portfolio_ledger_complete_transaction "
            "AFTER INSERT ON portfolio_ledger_transactions DEFERRABLE INITIALLY DEFERRED "
            "FOR EACH ROW EXECUTE FUNCTION portfolio_assert_complete_ledger()"
        )
    )
    op.execute(
        sa.text(
            "CREATE CONSTRAINT TRIGGER portfolio_ledger_complete_posting "
            "AFTER INSERT OR UPDATE OR DELETE ON portfolio_ledger_postings "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            "EXECUTE FUNCTION portfolio_assert_complete_ledger()"
        )
    )


def _upgrade_sqlite(timestamp: sa.DateTime, money: sa.Numeric) -> None:
    """Keep the developer SQLite schema usable; production invariants live in PostgreSQL."""

    with op.batch_alter_table("portfolio_mandates") as batch:
        batch.drop_constraint("ck_mandate_mode", type_="check")
        batch.create_check_constraint(
            "ck_mandate_mode", "mode IN ('backtest','shadow','paper')"
        )
        batch.create_unique_constraint(
            "uq_mandate_identity_portfolio", ["id", "tenant_id", "portfolio_id"]
        )
    with op.batch_alter_table("portfolios") as batch:
        batch.add_column(sa.Column("current_mandate_id", sa.Uuid(), nullable=True))
        batch.add_column(
            sa.Column("unit_count", money, nullable=False, server_default=sa.text("0"))
        )
        batch.add_column(
            sa.Column(
                "last_nav_per_unit", money, nullable=False, server_default=sa.text("1")
            )
        )
        batch.create_check_constraint("ck_portfolio_unit_count", "unit_count >= 0")
        batch.create_check_constraint("ck_portfolio_nav_positive", "last_nav_per_unit > 0")
        batch.create_foreign_key(
            "fk_portfolio_current_mandate",
            "portfolio_mandates",
            ["current_mandate_id", "tenant_id", "id"],
            ["id", "tenant_id", "portfolio_id"],
            ondelete="RESTRICT",
        )
    op.execute(
        sa.text(
            "UPDATE portfolios SET current_mandate_id = (SELECT id FROM portfolio_mandates "
            "WHERE tenant_id = portfolios.tenant_id AND portfolio_id = portfolios.id "
            "ORDER BY version DESC LIMIT 1), unit_count = CASE WHEN COALESCE(("
            "SELECT sum(CASE WHEN event_type = 'funding' THEN amount "
            "WHEN event_type = 'withdrawal' THEN -amount ELSE 0 END) "
            "FROM portfolio_ledger_transactions WHERE tenant_id = portfolios.tenant_id "
            "AND portfolio_id = portfolios.id), 0) > 0 THEN COALESCE(("
            "SELECT sum(CASE WHEN event_type = 'funding' THEN amount "
            "WHEN event_type = 'withdrawal' THEN -amount ELSE 0 END) "
            "FROM portfolio_ledger_transactions WHERE tenant_id = portfolios.tenant_id "
            "AND portfolio_id = portfolios.id), 0) ELSE 0 END"
        )
    )
    with op.batch_alter_table("portfolio_ledger_transactions") as batch:
        batch.add_column(
            sa.Column(
                "provenance",
                sa.String(32),
                nullable=False,
                server_default=sa.text("'internal'"),
            )
        )
        batch.add_column(
            sa.Column(
                "verification_status",
                sa.String(24),
                nullable=False,
                server_default=sa.text("'internal'"),
            )
        )
        batch.add_column(
            sa.Column("unit_delta", money, nullable=False, server_default=sa.text("0"))
        )
        batch.add_column(
            sa.Column("nav_per_unit", money, nullable=False, server_default=sa.text("1"))
        )
        batch.create_check_constraint(
            "ck_ledger_transaction_provenance",
            "provenance IN ('user_declared','broker_reconciled','internal')",
        )
        batch.create_check_constraint(
            "ck_ledger_transaction_verification",
            "verification_status IN ('unverified','verified','internal')",
        )
        batch.create_check_constraint(
            "ck_ledger_transaction_provenance_coherence",
            "(provenance = 'user_declared' AND verification_status = 'unverified' "
            "AND unit_delta <> 0) OR "
            "(provenance = 'broker_reconciled' AND verification_status = 'verified') OR "
            "(provenance = 'internal' AND verification_status = 'internal' "
            "AND unit_delta = 0)",
        )
        batch.create_check_constraint("ck_ledger_transaction_nav", "nav_per_unit > 0")
        batch.create_unique_constraint(
            "uq_ledger_transaction_identity_portfolio",
            ["id", "tenant_id", "portfolio_id"],
        )
    op.execute(
        sa.text(
            "UPDATE portfolio_ledger_transactions SET provenance = CASE "
            "WHEN event_type IN ('funding','withdrawal') THEN 'user_declared' "
            "ELSE 'internal' END, verification_status = CASE "
            "WHEN event_type IN ('funding','withdrawal') THEN 'unverified' "
            "ELSE 'internal' END, unit_delta = CASE WHEN event_type = 'funding' "
            "THEN amount WHEN event_type = 'withdrawal' THEN -amount ELSE 0 END"
        )
    )
    with op.batch_alter_table("portfolio_ledger_postings") as batch:
        batch.drop_constraint("fk_ledger_posting_transaction", type_="foreignkey")
        batch.create_foreign_key(
            "fk_ledger_posting_transaction",
            "portfolio_ledger_transactions",
            ["transaction_id", "tenant_id", "portfolio_id"],
            ["id", "tenant_id", "portfolio_id"],
            ondelete="RESTRICT",
        )
    with op.batch_alter_table("portfolio_positions") as batch:
        batch.add_column(
            sa.Column(
                "asset_class",
                sa.String(16),
                nullable=False,
                server_default=sa.text("'crypto'"),
            )
        )
        batch.create_check_constraint(
            "ck_position_asset_class", "asset_class IN ('crypto','stock')"
        )
    with op.batch_alter_table("portfolio_snapshots") as batch:
        batch.add_column(
            sa.Column("unit_count", money, nullable=False, server_default=sa.text("1"))
        )
        batch.add_column(
            sa.Column("nav_per_unit", money, nullable=False, server_default=sa.text("1"))
        )
        batch.add_column(
            sa.Column("high_water_nav", money, nullable=False, server_default=sa.text("1"))
        )
        batch.add_column(
            sa.Column(
                "daily_drawdown",
                sa.Numeric(18, 12),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch.add_column(
            sa.Column(
                "valuation_source",
                sa.String(32),
                nullable=False,
                server_default=sa.text("'market_data_pit'"),
            )
        )
        batch.add_column(sa.Column("valuation_observed_at", timestamp, nullable=False))
        batch.add_column(sa.Column("valuation_available_at", timestamp, nullable=False))
        batch.add_column(sa.Column("valuation_digest", sa.String(64), nullable=False))
        batch.create_check_constraint("ck_snapshot_unit_count", "unit_count > 0")
        batch.create_check_constraint(
            "ck_snapshot_nav", "nav_per_unit > 0 AND high_water_nav > 0"
        )
        batch.create_check_constraint(
            "ck_snapshot_daily_drawdown", "daily_drawdown >= 0 AND daily_drawdown <= 1"
        )
        batch.create_check_constraint(
            "ck_snapshot_valuation_source",
            "valuation_source IN ('broker_reconciliation','market_data_pit')",
        )
        batch.create_check_constraint(
            "ck_snapshot_valuation_digest", "length(valuation_digest) = 64"
        )
        batch.create_check_constraint(
            "ck_snapshot_valuation_timeline",
            "valuation_observed_at <= valuation_available_at AND "
            "valuation_available_at <= captured_at",
        )
def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        _downgrade_sqlite()
        return
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS suitability_active_mandate_guard "
            "ON portfolio_suitability_assessments"
        )
    )
    op.execute(
        sa.text("DROP TRIGGER IF EXISTS mandate_current_pointer ON portfolio_mandates")
    )
    op.execute(
        sa.text("DROP TRIGGER IF EXISTS portfolio_current_mandate_pointer ON portfolios")
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS portfolio_assert_current_mandate()"))
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS portfolio_ledger_complete_posting "
            "ON portfolio_ledger_postings"
        )
    )
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS portfolio_ledger_complete_transaction "
            "ON portfolio_ledger_transactions"
        )
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS portfolio_assert_complete_ledger()"))

    for constraint in (
        "ck_snapshot_valuation_timeline",
        "ck_snapshot_valuation_digest",
        "ck_snapshot_valuation_source",
        "ck_snapshot_daily_drawdown",
        "ck_snapshot_nav",
        "ck_snapshot_unit_count",
    ):
        op.drop_constraint(constraint, "portfolio_snapshots", type_="check")
    for column in (
        "valuation_digest",
        "valuation_available_at",
        "valuation_observed_at",
        "valuation_source",
        "daily_drawdown",
        "high_water_nav",
        "nav_per_unit",
        "unit_count",
    ):
        op.drop_column("portfolio_snapshots", column)

    op.drop_constraint("ck_position_asset_class", "portfolio_positions", type_="check")
    op.drop_column("portfolio_positions", "asset_class")

    op.drop_constraint(
        "fk_ledger_posting_transaction",
        "portfolio_ledger_postings",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_ledger_posting_transaction",
        "portfolio_ledger_postings",
        "portfolio_ledger_transactions",
        ["transaction_id", "tenant_id"],
        ["id", "tenant_id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "uq_ledger_transaction_identity_portfolio",
        "portfolio_ledger_transactions",
        type_="unique",
    )
    for constraint in (
        "ck_ledger_transaction_nav",
        "ck_ledger_transaction_provenance_coherence",
        "ck_ledger_transaction_verification",
        "ck_ledger_transaction_provenance",
    ):
        op.drop_constraint(constraint, "portfolio_ledger_transactions", type_="check")
    for column in ("nav_per_unit", "unit_delta", "verification_status", "provenance"):
        op.drop_column("portfolio_ledger_transactions", column)

    op.drop_constraint("fk_portfolio_current_mandate", "portfolios", type_="foreignkey")
    op.drop_constraint(
        "uq_mandate_identity_portfolio", "portfolio_mandates", type_="unique"
    )
    op.drop_constraint("ck_mandate_mode", "portfolio_mandates", type_="check")
    op.create_check_constraint(
        "ck_mandate_mode",
        "portfolio_mandates",
        "mode IN ('backtest','shadow','paper','live_supervised','live_automated')",
    )
    for constraint in ("ck_portfolio_nav_positive", "ck_portfolio_unit_count"):
        op.drop_constraint(constraint, "portfolios", type_="check")
    for column in ("last_nav_per_unit", "unit_count", "current_mandate_id"):
        op.drop_column("portfolios", column)

    op.execute(
        sa.text(
            "CREATE FUNCTION portfolio_assert_balanced_ledger() RETURNS trigger "
            "LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$ "
            "DECLARE tx uuid; total numeric; BEGIN "
            "tx := COALESCE(NEW.transaction_id, OLD.transaction_id); "
            "SELECT COALESCE(sum(quantity), 0) INTO total "
            "FROM public.portfolio_ledger_postings WHERE transaction_id = tx; "
            "IF total <> 0 THEN RAISE EXCEPTION 'ledger transaction is not balanced'; "
            "END IF; RETURN NULL; END; $$"
        )
    )
    op.execute(
        sa.text(
            "CREATE CONSTRAINT TRIGGER portfolio_ledger_balanced "
            "AFTER INSERT OR UPDATE OR DELETE ON portfolio_ledger_postings "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            "EXECUTE FUNCTION portfolio_assert_balanced_ledger()"
        )
    )


def _downgrade_sqlite() -> None:
    with op.batch_alter_table("portfolio_snapshots") as batch:
        for constraint in (
            "ck_snapshot_valuation_timeline",
            "ck_snapshot_valuation_digest",
            "ck_snapshot_valuation_source",
            "ck_snapshot_daily_drawdown",
            "ck_snapshot_nav",
            "ck_snapshot_unit_count",
        ):
            batch.drop_constraint(constraint, type_="check")
        for column in (
            "valuation_digest",
            "valuation_available_at",
            "valuation_observed_at",
            "valuation_source",
            "daily_drawdown",
            "high_water_nav",
            "nav_per_unit",
            "unit_count",
        ):
            batch.drop_column(column)
    with op.batch_alter_table("portfolio_positions") as batch:
        batch.drop_constraint("ck_position_asset_class", type_="check")
        batch.drop_column("asset_class")
    with op.batch_alter_table("portfolio_ledger_postings") as batch:
        batch.drop_constraint("fk_ledger_posting_transaction", type_="foreignkey")
        batch.create_foreign_key(
            "fk_ledger_posting_transaction",
            "portfolio_ledger_transactions",
            ["transaction_id", "tenant_id"],
            ["id", "tenant_id"],
            ondelete="RESTRICT",
        )
    with op.batch_alter_table("portfolio_ledger_transactions") as batch:
        batch.drop_constraint(
            "uq_ledger_transaction_identity_portfolio", type_="unique"
        )
        for constraint in (
            "ck_ledger_transaction_nav",
            "ck_ledger_transaction_provenance_coherence",
            "ck_ledger_transaction_verification",
            "ck_ledger_transaction_provenance",
        ):
            batch.drop_constraint(constraint, type_="check")
        for column in ("nav_per_unit", "unit_delta", "verification_status", "provenance"):
            batch.drop_column(column)
    with op.batch_alter_table("portfolios") as batch:
        batch.drop_constraint("fk_portfolio_current_mandate", type_="foreignkey")
        batch.drop_constraint("ck_portfolio_nav_positive", type_="check")
        batch.drop_constraint("ck_portfolio_unit_count", type_="check")
        batch.drop_column("last_nav_per_unit")
        batch.drop_column("unit_count")
        batch.drop_column("current_mandate_id")
    with op.batch_alter_table("portfolio_mandates") as batch:
        batch.drop_constraint("uq_mandate_identity_portfolio", type_="unique")
        batch.drop_constraint("ck_mandate_mode", type_="check")
        batch.create_check_constraint(
            "ck_mandate_mode",
            "mode IN ('backtest','shadow','paper','live_supervised','live_automated')",
        )

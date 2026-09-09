"""Move capital controls behind operation-bound database ports.

Revision ID: 20260909_0022
Revises: 20260909_0021
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260909_0022"
down_revision = "20260909_0021"
branch_labels = None
depends_on = None

_ZERO_DIGEST = "0" * 64


def _execute(sql: str) -> None:
    op.execute(sa.text(sql))


def upgrade() -> None:
    snapshot_digest_column = sa.Column(
        "source_state_digest",
        sa.String(64),
        nullable=False,
        server_default=sa.text(f"'{_ZERO_DIGEST}'"),
    )
    reservation_columns = (
        sa.Column("snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("snapshot_digest", sa.String(64), nullable=True),
        sa.Column("source_state_digest", sa.String(64), nullable=True),
    )
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table("portfolio_snapshots") as batch:
            batch.add_column(snapshot_digest_column)
            batch.create_check_constraint(
                "ck_snapshot_source_state_digest",
                "length(source_state_digest) = 64",
            )
            batch.create_unique_constraint(
                "uq_snapshot_identity_portfolio",
                ["id", "tenant_id", "portfolio_id"],
            )
        with op.batch_alter_table("portfolio_cash_reservations") as batch:
            for column in reservation_columns:
                batch.add_column(column)
            batch.create_foreign_key(
                "fk_reservation_snapshot",
                "portfolio_snapshots",
                ["snapshot_id", "tenant_id", "portfolio_id"],
                ["id", "tenant_id", "portfolio_id"],
                ondelete="RESTRICT",
            )
        _execute(
            "UPDATE portfolios SET balance_version=balance_version+1 WHERE EXISTS "
            "(SELECT 1 FROM portfolio_cash_reservations reservation WHERE "
            "reservation.tenant_id=portfolios.tenant_id AND "
            "reservation.portfolio_id=portfolios.id AND reservation.status='active')"
        )
        _execute(
            "UPDATE portfolio_cash_reservations SET status='expired' WHERE status='active'"
        )
        with op.batch_alter_table("portfolio_cash_reservations") as batch:
            batch.create_check_constraint(
                "ck_reservation_active_snapshot",
                "status <> 'active' OR (snapshot_id IS NOT NULL AND "
                "length(snapshot_digest) = 64 AND length(source_state_digest) = 64)",
            )
        op.create_index(
            "uq_portfolio_single_active_reservation",
            "portfolio_cash_reservations",
            ["tenant_id", "portfolio_id"],
            unique=True,
            sqlite_where=sa.text("status = 'active'"),
        )
        return
    op.add_column("portfolio_snapshots", snapshot_digest_column)
    op.create_check_constraint(
        "ck_snapshot_source_state_digest",
        "portfolio_snapshots",
        "length(source_state_digest) = 64",
    )
    op.create_unique_constraint(
        "uq_snapshot_identity_portfolio",
        "portfolio_snapshots",
        ["id", "tenant_id", "portfolio_id"],
    )
    for column in reservation_columns:
        op.add_column("portfolio_cash_reservations", column)
    op.create_foreign_key(
        "fk_reservation_snapshot",
        "portfolio_cash_reservations",
        "portfolio_snapshots",
        ["snapshot_id", "tenant_id", "portfolio_id"],
        ["id", "tenant_id", "portfolio_id"],
        ondelete="RESTRICT",
    )
    op.alter_column(
        "portfolio_snapshots", "source_state_digest", server_default=None
    )
    if dialect != "postgresql":
        return
    _replace_verifier()
    _install_nonce_maintenance()
    _install_policy_guard()
    _install_source_state_digest()
    _restrict_control_tables()
    _replace_reserve_port()
    _install_snapshot_port()


def _replace_verifier() -> None:
    _execute(
        "CREATE OR REPLACE FUNCTION tradingagents_private.portfolio_verify_capability("
        "p_audience text,p_fields text[],p_key_id text,p_session_id uuid,p_token_hash text,"
        "p_issued_at bigint,p_expires_at bigint,p_nonce text,p_mac text,p_consume boolean) "
        "RETURNS TABLE(tenant_id uuid,user_id uuid,membership_id uuid,member_role text) "
        "LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on "
        "AS $$ DECLARE v_key bytea; v_message bytea := "
        "decode('54412d4341502d563100','hex'); v_field text; v_expected text; "
        "v_now bigint := floor(extract(epoch FROM clock_timestamp())); BEGIN IF p_key_id "
        "IS NULL OR p_session_id IS NULL OR p_token_hash !~ '^[0-9a-f]{64}$' OR p_nonce "
        "!~ '^[0-9a-f]{64}$' OR p_mac !~ '^[0-9a-f]{64}$' OR p_issued_at IS NULL OR "
        "p_expires_at IS NULL OR p_expires_at<=p_issued_at OR p_expires_at-p_issued_at>30 "
        "OR p_issued_at>v_now+5 OR p_expires_at<=v_now THEN RAISE EXCEPTION "
        "'PORTFOLIO_CAPABILITY_INVALID' USING ERRCODE='P0001'; END IF; SELECT key_material "
        "INTO v_key FROM tradingagents_private.portfolio_capability_keys WHERE "
        "key_id=p_key_id; IF NOT FOUND THEN RAISE EXCEPTION "
        "'PORTFOLIO_CAPABILITY_UNAVAILABLE' USING ERRCODE='P0001'; END IF; FOREACH v_field "
        "IN ARRAY ARRAY[p_audience,p_session_id::text,p_token_hash] || p_fields || "
        "ARRAY[p_issued_at::text,p_expires_at::text,p_nonce] LOOP IF v_field IS NULL OR "
        "octet_length(convert_to(v_field,'UTF8'))>4096 THEN RAISE EXCEPTION "
        "'PORTFOLIO_CAPABILITY_INVALID' USING ERRCODE='P0001'; END IF; v_message := "
        "v_message || tradingagents_private.portfolio_cap_field(v_field); END LOOP; "
        "v_expected := encode(tradingagents_private.portfolio_hmac_sha256(v_key,v_message),"
        "'hex'); IF NOT tradingagents_private.portfolio_secure_equal(v_expected,p_mac) "
        "THEN RAISE EXCEPTION 'PORTFOLIO_CAPABILITY_INVALID' USING ERRCODE='P0001'; END "
        "IF; PERFORM set_config('app.portfolio_session_id',p_session_id::text,true); IF "
        "p_consume THEN BEGIN INSERT INTO tradingagents_private.portfolio_capability_nonces"
        "(key_id,nonce,expires_at) VALUES (p_key_id,p_nonce,to_timestamp(p_expires_at)); "
        "EXCEPTION WHEN unique_violation THEN RAISE EXCEPTION "
        "'PORTFOLIO_CAPABILITY_REPLAY' USING ERRCODE='P0001'; END; END IF; RETURN QUERY "
        "SELECT s.tenant_id,s.user_id,s.membership_id,m.role::text FROM "
        "public.identity_sessions s JOIN public.identity_memberships m ON "
        "m.id=s.membership_id AND m.tenant_id=s.tenant_id AND m.user_id=s.user_id JOIN "
        "public.identity_users u ON u.id=s.user_id JOIN public.identity_tenants t ON "
        "t.id=s.tenant_id WHERE s.id=p_session_id AND s.token_hash=p_token_hash AND "
        "s.revoked_at IS NULL AND s.expires_at>clock_timestamp() AND m.is_active AND "
        "u.is_active AND t.is_active; IF NOT FOUND THEN RAISE EXCEPTION "
        "'PORTFOLIO_SESSION_FORBIDDEN' USING ERRCODE='P0001'; END IF; END $$"
    )


def _install_nonce_maintenance() -> None:
    _execute(
        "CREATE INDEX ix_portfolio_capability_nonces_expires_at ON "
        "tradingagents_private.portfolio_capability_nonces(expires_at)"
    )
    _execute(
        "CREATE FUNCTION tradingagents_private.portfolio_prune_capability_nonces_v1("
        "p_batch_limit integer DEFAULT 1000) RETURNS integer LANGUAGE plpgsql SECURITY "
        "DEFINER SET search_path=pg_catalog SET row_security=on AS $$ DECLARE "
        "v_removed integer; BEGIN IF p_batch_limit IS NULL OR p_batch_limit NOT BETWEEN "
        "1 AND 10000 THEN RAISE EXCEPTION 'NONCE_PRUNE_LIMIT_INVALID' USING "
        "ERRCODE='P0001'; END IF; WITH expired AS (SELECT ctid FROM "
        "tradingagents_private.portfolio_capability_nonces WHERE expires_at<="
        "clock_timestamp() ORDER BY expires_at FOR UPDATE SKIP LOCKED LIMIT "
        "p_batch_limit), removed AS (DELETE FROM "
        "tradingagents_private.portfolio_capability_nonces target USING expired WHERE "
        "target.ctid=expired.ctid RETURNING 1) SELECT count(*)::integer INTO v_removed "
        "FROM removed; RETURN v_removed; END $$"
    )
    signature = "portfolio_prune_capability_nonces_v1(integer)"
    _execute(
        f"ALTER FUNCTION tradingagents_private.{signature} OWNER TO "
        "tradingagents_reservation_api"
    )
    _execute(
        f"REVOKE ALL ON FUNCTION tradingagents_private.{signature} FROM PUBLIC,"
        "tradingagents_app"
    )
    _execute(
        f"GRANT EXECUTE ON FUNCTION tradingagents_private.{signature} TO "
        "tradingagents_worker"
    )
    _execute("GRANT USAGE ON SCHEMA tradingagents_private TO tradingagents_worker")


def _install_policy_guard() -> None:
    policies = {
        "preservation": (
            "46d79652f184972c85dca4b3e6884a5630eaf3ffc8e38cbdb0fefe00aed4adb5",
            '{"max_adv_participation":"0.005","max_correlated_bucket_weight":"0.12",'
            '"max_crypto_weight":"0.10","max_daily_drawdown":"0.01",'
            '"max_order_notional_weight":"0.02","max_position_weight":"0.05",'
            '"max_total_drawdown":"0.05","minimum_cash_weight":"0.60",'
            '"rebalance_band":"0.015","target_annual_volatility":"0.04"}',
        ),
        "conservative": (
            "1711efccfea1b6bc1bb2f79b40ab10e36585c906f7fad1bdb885e433a6ced3df",
            '{"max_adv_participation":"0.0075","max_correlated_bucket_weight":"0.20",'
            '"max_crypto_weight":"0.20","max_daily_drawdown":"0.015",'
            '"max_order_notional_weight":"0.03","max_position_weight":"0.08",'
            '"max_total_drawdown":"0.08","minimum_cash_weight":"0.40",'
            '"rebalance_band":"0.02","target_annual_volatility":"0.07"}',
        ),
        "moderate": (
            "b2c9c04cab83884bd862179c860ac2235eeb252ffc4b267ff506aa8c9599e644",
            '{"max_adv_participation":"0.01","max_correlated_bucket_weight":"0.30",'
            '"max_crypto_weight":"0.35","max_daily_drawdown":"0.02",'
            '"max_order_notional_weight":"0.05","max_position_weight":"0.12",'
            '"max_total_drawdown":"0.12","minimum_cash_weight":"0.25",'
            '"rebalance_band":"0.025","target_annual_volatility":"0.10"}',
        ),
        "growth": (
            "2b3168bee1faccf3023eae41407ef7c981b5a2f23ad44184ebc93a637384f6fa",
            '{"max_adv_participation":"0.015","max_correlated_bucket_weight":"0.40",'
            '"max_crypto_weight":"0.55","max_daily_drawdown":"0.03",'
            '"max_order_notional_weight":"0.08","max_position_weight":"0.18",'
            '"max_total_drawdown":"0.18","minimum_cash_weight":"0.15",'
            '"rebalance_band":"0.03","target_annual_volatility":"0.15"}',
        ),
        "aggressive": (
            "6c287aef4e19b8ed323a15db69fc10a826730c293f85117424e34a842803bfa3",
            '{"max_adv_participation":"0.02","max_correlated_bucket_weight":"0.55",'
            '"max_crypto_weight":"0.75","max_daily_drawdown":"0.05",'
            '"max_order_notional_weight":"0.12","max_position_weight":"0.25",'
            '"max_total_drawdown":"0.25","minimum_cash_weight":"0.08",'
            '"rebalance_band":"0.04","target_annual_volatility":"0.22"}',
        ),
    }
    cases = " ".join(
        f"WHEN '{profile}' THEN p_digest='{digest}' AND p_limits='{limits}'::jsonb"
        for profile, (digest, limits) in policies.items()
    )
    _execute(
        "CREATE FUNCTION tradingagents_private.portfolio_policy_valid_v1(p_profile text,"
        "p_version integer,p_digest text,p_limits jsonb) RETURNS boolean LANGUAGE sql "
        "IMMUTABLE SET search_path=pg_catalog AS $$ SELECT p_version=1 AND CASE p_profile "
        f"{cases} ELSE false END $$"
    )
    _execute(
        "ALTER FUNCTION tradingagents_private.portfolio_policy_valid_v1(text,integer,text,"
        "jsonb) OWNER TO tradingagents_reservation_api"
    )
    _execute(
        "REVOKE ALL ON FUNCTION tradingagents_private.portfolio_policy_valid_v1(text,"
        "integer,text,jsonb) FROM PUBLIC,tradingagents_app,tradingagents_worker"
    )
    _execute(
        "CREATE FUNCTION tradingagents_private.portfolio_validate_mandate_policy_v1() "
        "RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS "
        "$$ BEGIN IF NOT tradingagents_private.portfolio_policy_valid_v1(NEW.profile::text,"
        "NEW.policy_version,NEW.policy_digest,NEW.limits::jsonb) THEN RAISE EXCEPTION "
        "'POLICY_VERSION_MISMATCH' USING ERRCODE='P0001'; END IF; RETURN NEW; END $$"
    )
    _execute(
        "ALTER FUNCTION tradingagents_private.portfolio_validate_mandate_policy_v1() "
        "OWNER TO tradingagents_reservation_api"
    )
    _execute(
        "REVOKE ALL ON FUNCTION tradingagents_private.portfolio_validate_mandate_policy_v1() "
        "FROM PUBLIC,tradingagents_app,tradingagents_worker"
    )
    _execute(
        "CREATE TRIGGER portfolio_mandate_policy_v1 BEFORE INSERT OR UPDATE OF profile,"
        "policy_version,policy_digest,limits ON public.portfolio_mandates FOR EACH ROW "
        "EXECUTE FUNCTION tradingagents_private.portfolio_validate_mandate_policy_v1()"
    )


def _install_source_state_digest() -> None:
    _execute(
        "CREATE FUNCTION tradingagents_private.portfolio_source_state_digest_v1("
        "p_tenant_id uuid,p_portfolio_id uuid) RETURNS text LANGUAGE sql STABLE SECURITY "
        "DEFINER SET search_path=pg_catalog SET row_security=on AS $$ WITH tx AS (SELECT "
        "count(*) n,coalesce(max(id::text),'') tail FROM public.portfolio_ledger_transactions "
        "WHERE tenant_id=p_tenant_id AND portfolio_id=p_portfolio_id), post AS (SELECT "
        "count(*) n,coalesce(max(id::text),'') tail FROM public.portfolio_ledger_postings "
        "WHERE tenant_id=p_tenant_id AND portfolio_id=p_portfolio_id), pos AS (SELECT "
        "count(*) n,coalesce(max(id::text),'') tail,coalesce(sum(version),0) versions FROM "
        "public.portfolio_positions WHERE tenant_id=p_tenant_id AND "
        "portfolio_id=p_portfolio_id) SELECT encode(sha256(convert_to(tx.n::text||'|'||"
        "tx.tail||'|'||post.n::text||'|'||post.tail||'|'||pos.n::text||'|'||pos.tail||'|'||"
        "pos.versions::text,'UTF8')),'hex') FROM tx,post,pos $$"
    )
    _execute(
        "ALTER FUNCTION tradingagents_private.portfolio_source_state_digest_v1(uuid,uuid) "
        "OWNER TO tradingagents_reservation_api"
    )
    _execute(
        "REVOKE ALL ON FUNCTION tradingagents_private.portfolio_source_state_digest_v1("
        "uuid,uuid) FROM PUBLIC,tradingagents_app,tradingagents_worker"
    )


def _restrict_control_tables() -> None:
    _execute(
        "WITH expired AS (UPDATE public.portfolio_cash_reservations SET "
        "status='expired' WHERE status='active' RETURNING tenant_id,portfolio_id), "
        "affected AS (SELECT DISTINCT tenant_id,portfolio_id FROM expired) UPDATE "
        "public.portfolios portfolio SET balance_version=portfolio.balance_version+1 "
        "FROM affected WHERE portfolio.tenant_id=affected.tenant_id AND "
        "portfolio.id=affected.portfolio_id"
    )
    # The legacy status transition queues constraint-trigger events. PostgreSQL
    # refuses the following ALTER TABLE while those events remain pending.
    _execute("SET CONSTRAINTS ALL IMMEDIATE")
    _execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM public.portfolio_cash_reservations WHERE "
        "status='active' GROUP BY tenant_id,portfolio_id HAVING count(*)>1) THEN RAISE "
        "EXCEPTION 'M4_MULTIPLE_ACTIVE_RESERVATIONS'; END IF; END $$"
    )
    _execute(
        "ALTER TABLE public.portfolio_cash_reservations ADD CONSTRAINT "
        "ck_reservation_active_snapshot CHECK (status<>'active' OR (snapshot_id IS NOT "
        "NULL AND length(snapshot_digest)=64 AND length(source_state_digest)=64))"
    )
    _execute(
        "CREATE UNIQUE INDEX uq_portfolio_single_active_reservation ON "
        "public.portfolio_cash_reservations(tenant_id,portfolio_id) WHERE status='active'"
    )
    for table in ("portfolio_positions", "portfolio_snapshots", "portfolio_circuit_breakers"):
        _execute(
            f'DROP POLICY IF EXISTS "{table}_app_insert" ON public."{table}"'
        )
        _execute(
            f'DROP POLICY IF EXISTS "{table}_app_write" ON public."{table}"'
        )
        _execute(
            "REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON "
            f'public."{table}" FROM tradingagents_app,tradingagents_worker,PUBLIC'
        )
    _execute(
        "GRANT SELECT ON public.portfolio_positions,public.portfolio_ledger_transactions "
        "TO tradingagents_reservation_api"
    )
    _execute(
        "GRANT INSERT ON public.portfolio_snapshots,public.portfolio_circuit_breakers TO "
        "tradingagents_reservation_api"
    )
    _execute(
        "GRANT UPDATE (balance_version,last_nav_per_unit) ON public.portfolios TO "
        "tradingagents_reservation_api"
    )
    for table in ("portfolio_snapshots", "portfolio_circuit_breakers"):
        _execute(
            f'CREATE POLICY "{table}_reservation_api_insert" ON public."{table}" FOR '
            "INSERT TO tradingagents_reservation_api WITH CHECK (tenant_id="
            "NULLIF(current_setting('app.tenant_id',true),'')::uuid)"
        )


def _replace_reserve_port() -> None:
    _execute(
        "DROP FUNCTION tradingagents_private.portfolio_reserve_cash_v1(uuid,numeric,text,"
        "text,bigint,text,uuid,text,bigint,bigint,text,text)"
    )
    _execute(
        "CREATE FUNCTION tradingagents_private.portfolio_reserve_cash_v1("
        "p_portfolio_id uuid,p_amount numeric,p_purpose_digest text,"
        "p_idempotency_key_hash text,p_ttl_microseconds bigint,p_snapshot_id uuid,"
        "p_snapshot_digest text,p_key_id text,p_session_id uuid,p_token_hash text,"
        "p_issued_at bigint,p_expires_at bigint,p_nonce text,p_mac text) RETURNS "
        "public.portfolio_cash_reservations LANGUAGE plpgsql SECURITY DEFINER SET "
        "search_path=pg_catalog SET row_security=on AS $$ DECLARE v_identity record; "
        "v_now timestamptz:=clock_timestamp(); v_portfolio public.portfolios%ROWTYPE; "
        "v_mandate public.portfolio_mandates%ROWTYPE; v_suitability "
        "public.portfolio_suitability_assessments%ROWTYPE; v_latest uuid; v_existing "
        "public.portfolio_cash_reservations%ROWTYPE; v_snapshot "
        "public.portfolio_snapshots%ROWTYPE; v_created "
        "public.portfolio_cash_reservations%ROWTYPE; v_cash numeric; v_reserved numeric; "
        "v_amount_text text; v_source_digest text; v_expired_count integer; BEGIN "
        "v_amount_text:=p_amount::text; "
        "IF position('.' in v_amount_text)>0 THEN v_amount_text:=trim(trailing '.' FROM "
        "trim(trailing '0' FROM v_amount_text)); END IF; IF v_amount_text IN ('','-0') "
        "THEN v_amount_text:='0'; END IF; SELECT * INTO v_identity FROM "
        "tradingagents_private.portfolio_verify_capability('portfolio.reserve.v1',ARRAY["
        "p_portfolio_id::text,v_amount_text,p_purpose_digest,p_idempotency_key_hash,"
        "p_ttl_microseconds::text,p_snapshot_id::text,p_snapshot_digest],p_key_id,"
        "p_session_id,p_token_hash,p_issued_at,p_expires_at,p_nonce,p_mac,false); IF "
        "v_identity.member_role NOT IN ('owner','admin','operator') THEN RAISE EXCEPTION "
        "'PORTFOLIO_SESSION_FORBIDDEN' USING ERRCODE='P0001'; END IF; PERFORM set_config("
        "'app.tenant_id',v_identity.tenant_id::text,true); IF p_amount IS NULL OR "
        "p_amount<=0 OR p_amount::text IN ('NaN','Infinity','-Infinity') OR p_amount IS "
        "DISTINCT FROM round(p_amount,12) THEN RAISE EXCEPTION "
        "'RESERVATION_AMOUNT_INVALID' USING ERRCODE='P0001'; END IF; IF "
        "p_ttl_microseconds NOT BETWEEN 30000000 AND 3600000000 THEN RAISE EXCEPTION "
        "'RESERVATION_TTL_INVALID' USING ERRCODE='P0001'; END IF; IF p_purpose_digest "
        "!~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'PURPOSE_DIGEST_INVALID' USING "
        "ERRCODE='P0001'; END IF; IF p_idempotency_key_hash !~ '^[0-9a-f]{64}$' THEN "
        "RAISE EXCEPTION 'IDEMPOTENCY_KEY_INVALID' USING ERRCODE='P0001'; END IF; SELECT "
        "* INTO v_portfolio FROM public.portfolios WHERE tenant_id=v_identity.tenant_id "
        "AND id=p_portfolio_id FOR UPDATE; IF NOT FOUND OR v_portfolio.status<>'active' "
        "THEN RAISE EXCEPTION 'PORTFOLIO_NOT_ACTIVE' USING ERRCODE='P0001'; END IF; "
        "v_now:=clock_timestamp(); SELECT * INTO v_identity FROM "
        "tradingagents_private.portfolio_verify_capability('portfolio.reserve.v1',ARRAY["
        "p_portfolio_id::text,v_amount_text,p_purpose_digest,p_idempotency_key_hash,"
        "p_ttl_microseconds::text,p_snapshot_id::text,p_snapshot_digest],p_key_id,"
        "p_session_id,p_token_hash,p_issued_at,p_expires_at,p_nonce,p_mac,true); IF "
        "v_identity.member_role NOT IN ('owner','admin','operator') THEN RAISE EXCEPTION "
        "'PORTFOLIO_SESSION_FORBIDDEN' USING ERRCODE='P0001'; END IF; PERFORM set_config("
        "'app.tenant_id',v_identity.tenant_id::text,true); "
        "SELECT id INTO v_latest FROM public.portfolio_mandates WHERE tenant_id="
        "v_identity.tenant_id AND portfolio_id=p_portfolio_id ORDER BY version DESC LIMIT "
        "1; SELECT * INTO v_mandate FROM public.portfolio_mandates WHERE tenant_id="
        "v_identity.tenant_id AND portfolio_id=p_portfolio_id AND id="
        "v_portfolio.current_mandate_id; SELECT * INTO v_suitability FROM "
        "public.portfolio_suitability_assessments WHERE tenant_id=v_identity.tenant_id "
        "AND user_id=v_portfolio.owner_user_id ORDER BY version DESC LIMIT 1; IF "
        "v_mandate.id IS NULL OR v_mandate.id IS DISTINCT FROM v_latest OR "
        "v_mandate.status<>'active' OR v_mandate.mode NOT IN ('backtest','shadow','paper') "
        "OR v_mandate.effective_at>v_now OR v_mandate.ended_at IS NOT NULL OR "
        "v_suitability.id IS NULL OR v_mandate.suitability_id IS DISTINCT FROM "
        "v_suitability.id OR v_suitability.status<>'valid' OR v_suitability.expires_at<="
        "v_now OR NOT tradingagents_private.portfolio_policy_valid_v1(v_mandate.profile::"
        "text,v_mandate.policy_version,v_mandate.policy_digest,v_mandate.limits::jsonb) "
        "THEN RAISE EXCEPTION 'ACTIVE_MANDATE_REQUIRED' USING ERRCODE='P0001'; END IF; "
        "UPDATE public.portfolio_cash_reservations SET status='expired' WHERE tenant_id="
        "v_identity.tenant_id AND portfolio_id=p_portfolio_id AND status='active' AND "
        "expires_at<=v_now; GET DIAGNOSTICS v_expired_count=ROW_COUNT; IF "
        "v_expired_count>0 THEN UPDATE public.portfolios SET balance_version="
        "balance_version+1 WHERE tenant_id=v_identity.tenant_id AND id=p_portfolio_id; "
        "END IF; "
        "SELECT * INTO v_existing FROM public.portfolio_cash_reservations WHERE tenant_id="
        "v_identity.tenant_id AND portfolio_id=p_portfolio_id AND idempotency_key_hash="
        "p_idempotency_key_hash FOR UPDATE; IF FOUND THEN IF v_existing.amount IS "
        "DISTINCT FROM p_amount OR v_existing.purpose_digest IS DISTINCT FROM "
        "p_purpose_digest THEN RAISE EXCEPTION 'IDEMPOTENCY_PAYLOAD_MISMATCH' USING "
        "ERRCODE='P0001'; END IF; IF v_existing.status<>'active' OR v_existing.expires_at"
        "<=v_now OR v_existing.mandate_id IS DISTINCT FROM v_mandate.id OR "
        "v_existing.snapshot_id IS DISTINCT FROM p_snapshot_id OR "
        "v_existing.snapshot_digest IS DISTINCT FROM p_snapshot_digest THEN RAISE "
        "EXCEPTION 'IDEMPOTENCY_RESERVATION_FINALIZED' USING ERRCODE='P0001'; END IF; "
        "END IF; SELECT * INTO v_snapshot FROM public.portfolio_snapshots WHERE tenant_id="
        "v_identity.tenant_id AND portfolio_id=p_portfolio_id ORDER BY sequence DESC LIMIT "
        "1; v_source_digest:=tradingagents_private.portfolio_source_state_digest_v1("
        "v_identity.tenant_id,p_portfolio_id); IF v_snapshot.id IS NULL THEN RAISE "
        "EXCEPTION 'BASELINE_SNAPSHOT_REQUIRED' USING ERRCODE='P0001'; END IF; IF "
        "v_snapshot.id IS DISTINCT FROM p_snapshot_id OR v_snapshot.digest IS DISTINCT "
        "FROM p_snapshot_digest OR v_snapshot.source_state_digest IS DISTINCT FROM "
        "v_source_digest OR (v_existing.id IS NOT NULL AND "
        "v_existing.source_state_digest IS DISTINCT FROM v_snapshot.source_state_digest) "
        "OR v_snapshot.valuation_available_at<v_now-interval '15 minutes' "
        "OR v_snapshot.captured_at<v_now-interval '15 minutes' OR v_snapshot.captured_at<"
        "v_mandate.effective_at THEN RAISE EXCEPTION 'ALLOCATION_BASELINE_STALE' USING "
        "ERRCODE='P0001'; END IF; IF EXISTS (SELECT 1 FROM "
        "public.portfolio_circuit_breakers WHERE tenant_id=v_identity.tenant_id AND "
        "portfolio_id=p_portfolio_id AND status='open') THEN RAISE EXCEPTION "
        "'CIRCUIT_BREAKER_OPEN' USING ERRCODE='P0001'; END IF; IF v_existing.id IS NOT "
        "NULL THEN RETURN v_existing; END IF; IF EXISTS (SELECT 1 FROM "
        "public.portfolio_cash_reservations WHERE tenant_id=v_identity.tenant_id AND "
        "portfolio_id=p_portfolio_id AND status='active') THEN RAISE "
        "EXCEPTION 'ACTIVE_RESERVATION_EXISTS' USING ERRCODE='P0001'; END IF; SELECT "
        "coalesce(sum(quantity),0) INTO v_cash FROM public.portfolio_ledger_postings WHERE "
        "tenant_id=v_identity.tenant_id AND portfolio_id=p_portfolio_id AND account='cash' "
        "AND asset=v_portfolio.base_currency; SELECT coalesce(sum(amount),0) INTO "
        "v_reserved FROM public.portfolio_cash_reservations WHERE tenant_id="
        "v_identity.tenant_id AND portfolio_id=p_portfolio_id AND status='active' AND "
        "expires_at>v_now; IF p_amount>v_cash-v_reserved THEN RAISE EXCEPTION "
        "'INSUFFICIENT_AVAILABLE_CASH' USING ERRCODE='P0001'; END IF; INSERT INTO "
        "public.portfolio_cash_reservations(id,tenant_id,portfolio_id,mandate_id,"
        "idempotency_key_hash,currency,amount,status,purpose_digest,snapshot_id,"
        "snapshot_digest,source_state_digest,expires_at,created_at) "
        "VALUES(gen_random_uuid(),v_identity.tenant_id,p_portfolio_id,v_mandate.id,"
        "p_idempotency_key_hash,v_portfolio.base_currency,p_amount,'active',"
        "p_purpose_digest,v_snapshot.id,v_snapshot.digest,v_snapshot.source_state_digest,"
        "v_now+(p_ttl_microseconds*interval '1 microsecond'),v_now) "
        "RETURNING * INTO v_created; UPDATE public.portfolios SET balance_version="
        "balance_version+1 WHERE tenant_id=v_identity.tenant_id AND id=p_portfolio_id; "
        "RETURN v_created; END $$"
    )
    signature = (
        "portfolio_reserve_cash_v1(uuid,numeric,text,text,bigint,uuid,text,text,uuid,text,"
        "bigint,bigint,text,text)"
    )
    _execute(
        f"ALTER FUNCTION tradingagents_private.{signature} OWNER TO "
        "tradingagents_reservation_api"
    )
    _execute(
        f"REVOKE ALL ON FUNCTION tradingagents_private.{signature} FROM PUBLIC,"
        "tradingagents_worker"
    )
    _execute(
        f"GRANT EXECUTE ON FUNCTION tradingagents_private.{signature} TO tradingagents_app"
    )


def _install_snapshot_port() -> None:
    _execute(
        """
CREATE FUNCTION tradingagents_private.portfolio_capture_snapshot_v1(
    p_portfolio_id uuid,
    p_valuation_source text,
    p_observed_microseconds bigint,
    p_available_microseconds bigint,
    p_evidence_digest text,
    p_prices_text text,
    p_key_id text,
    p_session_id uuid,
    p_token_hash text,
    p_issued_at bigint,
    p_expires_at bigint,
    p_nonce text,
    p_mac text
) RETURNS public.portfolio_snapshots
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog
SET row_security=on
AS $$
DECLARE
    v_identity record;
    v_now timestamptz := clock_timestamp();
    v_observed timestamptz;
    v_available timestamptz;
    v_prices jsonb;
    v_prices_digest text;
    v_source_digest text;
    v_portfolio public.portfolios%ROWTYPE;
    v_mandate public.portfolio_mandates%ROWTYPE;
    v_suitability public.portfolio_suitability_assessments%ROWTYPE;
    v_latest_mandate uuid;
    v_previous public.portfolio_snapshots%ROWTYPE;
    v_daily_baseline public.portfolio_snapshots%ROWTYPE;
    v_opening_nav numeric;
    v_cash numeric;
    v_reserved numeric;
    v_market_value numeric;
    v_realized numeric;
    v_unrealized numeric;
    v_equity numeric;
    v_nav numeric;
    v_high_water_nav numeric;
    v_high_water numeric;
    v_drawdown numeric;
    v_daily_baseline_nav numeric;
    v_daily_drawdown numeric;
    v_sequence bigint;
    v_positions jsonb;
    v_digest_message bytea := decode('54412d534e415053484f542d563100','hex');
    v_digest_field text;
    v_snapshot_digest text;
    v_created public.portfolio_snapshots%ROWTYPE;
    v_daily_limit numeric;
    v_total_limit numeric;
    v_breaker_kind text;
    v_breaker_observed numeric;
    v_breaker_threshold numeric;
BEGIN
    IF p_prices_text IS NULL
       OR octet_length(convert_to(p_prices_text,'UTF8')) > 1048576
       OR p_valuation_source NOT IN ('broker_reconciliation','market_data_pit')
       OR p_evidence_digest !~ '^[0-9a-f]{64}$'
       OR p_observed_microseconds IS NULL
       OR p_available_microseconds IS NULL
       OR p_observed_microseconds <= 0
       OR p_observed_microseconds > p_available_microseconds THEN
        RAISE EXCEPTION 'VALUATION_INPUT_INVALID' USING ERRCODE='P0001';
    END IF;
    v_prices_digest := encode(sha256(convert_to(p_prices_text,'UTF8')),'hex');
    SELECT * INTO v_identity
    FROM tradingagents_private.portfolio_verify_capability(
        'portfolio.snapshot.v1',
        ARRAY[p_portfolio_id::text,p_valuation_source,p_observed_microseconds::text,
              p_available_microseconds::text,p_evidence_digest,v_prices_digest],
        p_key_id,p_session_id,p_token_hash,p_issued_at,p_expires_at,p_nonce,p_mac,false
    );
    IF v_identity.member_role NOT IN ('owner','admin','operator') THEN
        RAISE EXCEPTION 'PORTFOLIO_SESSION_FORBIDDEN' USING ERRCODE='P0001';
    END IF;
    PERFORM set_config('app.tenant_id',v_identity.tenant_id::text,true);
    v_observed := to_timestamp(p_observed_microseconds::numeric / 1000000);
    v_available := to_timestamp(p_available_microseconds::numeric / 1000000);
    IF v_observed > v_available OR v_available > v_now THEN
        RAISE EXCEPTION 'VALUATION_TIMELINE_INVALID' USING ERRCODE='P0001';
    END IF;
    BEGIN
        v_prices := p_prices_text::jsonb;
    EXCEPTION WHEN others THEN
        RAISE EXCEPTION 'VALUATION_PRICES_INVALID' USING ERRCODE='P0001';
    END;
    IF jsonb_typeof(v_prices) <> 'object' OR EXISTS (
        SELECT 1 FROM jsonb_each(v_prices) item
        WHERE jsonb_typeof(item.value) <> 'string'
           OR item.key = ''
           OR octet_length(convert_to(item.key,'UTF8')) > 64
           OR CASE
                WHEN octet_length(convert_to(item.value #>> '{}','UTF8')) <= 39
                 AND (item.value #>> '{}')
                     ~ '^(0|[1-9][0-9]{0,25})([.][0-9]{1,12})?$'
                THEN (item.value #>> '{}')::numeric <= 0
                ELSE true
              END
    ) THEN
        RAISE EXCEPTION 'VALUATION_PRICES_INVALID' USING ERRCODE='P0001';
    END IF;
    SELECT * INTO v_portfolio FROM public.portfolios
    WHERE tenant_id=v_identity.tenant_id AND id=p_portfolio_id FOR UPDATE;
    IF NOT FOUND OR v_portfolio.status <> 'active' THEN
        RAISE EXCEPTION 'PORTFOLIO_NOT_ACTIVE' USING ERRCODE='P0001';
    END IF;
    v_now := clock_timestamp();
    SELECT * INTO v_identity
    FROM tradingagents_private.portfolio_verify_capability(
        'portfolio.snapshot.v1',
        ARRAY[p_portfolio_id::text,p_valuation_source,p_observed_microseconds::text,
              p_available_microseconds::text,p_evidence_digest,v_prices_digest],
        p_key_id,p_session_id,p_token_hash,p_issued_at,p_expires_at,p_nonce,p_mac,true
    );
    IF v_identity.member_role NOT IN ('owner','admin','operator') THEN
        RAISE EXCEPTION 'PORTFOLIO_SESSION_FORBIDDEN' USING ERRCODE='P0001';
    END IF;
    PERFORM set_config('app.tenant_id',v_identity.tenant_id::text,true);
    SELECT id INTO v_latest_mandate FROM public.portfolio_mandates
    WHERE tenant_id=v_identity.tenant_id AND portfolio_id=p_portfolio_id
    ORDER BY version DESC LIMIT 1;
    SELECT * INTO v_mandate FROM public.portfolio_mandates
    WHERE tenant_id=v_identity.tenant_id AND portfolio_id=p_portfolio_id
      AND id=v_portfolio.current_mandate_id;
    SELECT * INTO v_suitability FROM public.portfolio_suitability_assessments
    WHERE tenant_id=v_identity.tenant_id AND user_id=v_portfolio.owner_user_id
    ORDER BY version DESC LIMIT 1;
    IF v_mandate.id IS NULL
       OR v_mandate.id IS DISTINCT FROM v_latest_mandate
       OR v_mandate.status <> 'active'
       OR v_mandate.mode NOT IN ('shadow','paper')
       OR v_mandate.effective_at > v_now
       OR v_mandate.ended_at IS NOT NULL
       OR v_suitability.id IS NULL
       OR v_mandate.suitability_id IS DISTINCT FROM v_suitability.id
       OR v_suitability.status <> 'valid'
       OR v_suitability.expires_at <= v_now
       OR NOT tradingagents_private.portfolio_policy_valid_v1(
            v_mandate.profile::text,v_mandate.policy_version,
            v_mandate.policy_digest,v_mandate.limits::jsonb
       ) THEN
        RAISE EXCEPTION 'ACTIVE_MANDATE_REQUIRED' USING ERRCODE='P0001';
    END IF;
    IF (SELECT count(*) FROM public.portfolio_positions
        WHERE tenant_id=v_identity.tenant_id AND portfolio_id=p_portfolio_id)
       <> (SELECT count(*) FROM jsonb_object_keys(v_prices))
       OR EXISTS (
            SELECT 1 FROM public.portfolio_positions position
            WHERE position.tenant_id=v_identity.tenant_id
              AND position.portfolio_id=p_portfolio_id
              AND NOT (v_prices ? position.symbol)
       ) THEN
        RAISE EXCEPTION 'VALUATION_SYMBOL_SET_MISMATCH' USING ERRCODE='P0001';
    END IF;
    SELECT
        coalesce(sum(position.quantity*(v_prices->>position.symbol)::numeric),0),
        coalesce(sum(position.realized_pnl),0),
        coalesce(sum(((v_prices->>position.symbol)::numeric-position.average_cost)
                     * position.quantity),0),
        coalesce(jsonb_object_agg(
            position.symbol,
            jsonb_build_object(
                'asset_class',position.asset_class,
                'quantity',position.quantity::text,
                'average_cost',position.average_cost::text,
                'market_value',(position.quantity*(v_prices->>position.symbol)::numeric)::text
            ) ORDER BY position.symbol
        ),'{}'::jsonb)
    INTO v_market_value,v_realized,v_unrealized,v_positions
    FROM public.portfolio_positions position
    WHERE position.tenant_id=v_identity.tenant_id
      AND position.portfolio_id=p_portfolio_id;
    SELECT coalesce(sum(quantity),0) INTO v_cash
    FROM public.portfolio_ledger_postings
    WHERE tenant_id=v_identity.tenant_id AND portfolio_id=p_portfolio_id
      AND account='cash' AND asset=v_portfolio.base_currency;
    SELECT coalesce(sum(amount),0) INTO v_reserved
    FROM public.portfolio_cash_reservations
    WHERE tenant_id=v_identity.tenant_id AND portfolio_id=p_portfolio_id
      AND status='active' AND expires_at>v_now;
    v_equity := v_cash + v_market_value;
    IF v_portfolio.unit_count <= 0 OR v_equity <= 0 OR v_reserved < 0 THEN
        RAISE EXCEPTION 'PORTFOLIO_NAV_INVALID' USING ERRCODE='P0001';
    END IF;
    v_nav := round(v_equity/v_portfolio.unit_count,12);
    SELECT * INTO v_previous FROM public.portfolio_snapshots
    WHERE tenant_id=v_identity.tenant_id AND portfolio_id=p_portfolio_id
    ORDER BY sequence DESC LIMIT 1;
    v_sequence := coalesce(v_previous.sequence,0)+1;
    SELECT nav_per_unit INTO v_opening_nav FROM public.portfolio_ledger_transactions
    WHERE tenant_id=v_identity.tenant_id AND portfolio_id=p_portfolio_id
      AND unit_delta>0 ORDER BY occurred_at,id LIMIT 1;
    v_opening_nav := coalesce(v_opening_nav,v_portfolio.last_nav_per_unit);
    v_high_water_nav := greatest(v_nav,coalesce(v_previous.high_water_nav,v_opening_nav));
    v_high_water := v_high_water_nav*v_portfolio.unit_count;
    v_drawdown := (v_high_water_nav-v_nav)/v_high_water_nav;
    SELECT * INTO v_daily_baseline FROM public.portfolio_snapshots
    WHERE tenant_id=v_identity.tenant_id AND portfolio_id=p_portfolio_id
      AND captured_at >= date_trunc('day',v_now AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
      AND captured_at < ((date_trunc('day',v_now AT TIME ZONE 'UTC')+interval '1 day')
                         AT TIME ZONE 'UTC')
    ORDER BY sequence LIMIT 1;
    v_daily_baseline_nav := coalesce(
        v_daily_baseline.nav_per_unit,v_previous.nav_per_unit,v_opening_nav
    );
    v_daily_drawdown := CASE WHEN v_daily_baseline_nav>v_nav
        THEN (v_daily_baseline_nav-v_nav)/v_daily_baseline_nav ELSE 0 END;
    v_source_digest := tradingagents_private.portfolio_source_state_digest_v1(
        v_identity.tenant_id,p_portfolio_id
    );
    FOREACH v_digest_field IN ARRAY ARRAY[
        v_identity.tenant_id::text,p_portfolio_id::text,v_sequence::text,
        v_cash::text,v_reserved::text,v_market_value::text,v_equity::text,
        v_realized::text,v_unrealized::text,v_high_water::text,v_drawdown::text,
        v_portfolio.unit_count::text,v_nav::text,v_high_water_nav::text,
        v_daily_drawdown::text,v_positions::text,v_prices::text,v_source_digest,
        p_valuation_source,p_observed_microseconds::text,p_available_microseconds::text,
        p_evidence_digest,extract(epoch FROM v_now)::text
    ] LOOP
        v_digest_message := v_digest_message
            || tradingagents_private.portfolio_cap_field(v_digest_field);
    END LOOP;
    v_snapshot_digest := encode(sha256(v_digest_message),'hex');
    INSERT INTO public.portfolio_snapshots(
        id,tenant_id,portfolio_id,sequence,cash,reserved_cash,market_value,equity,
        realized_pnl,unrealized_pnl,high_water_mark,drawdown,unit_count,nav_per_unit,
        high_water_nav,daily_drawdown,positions,prices,source_state_digest,digest,
        valuation_source,valuation_observed_at,valuation_available_at,valuation_digest,
        captured_at
    ) VALUES (
        gen_random_uuid(),v_identity.tenant_id,p_portfolio_id,v_sequence,v_cash,v_reserved,
        v_market_value,v_equity,v_realized,v_unrealized,v_high_water,v_drawdown,
        v_portfolio.unit_count,v_nav,v_high_water_nav,v_daily_drawdown,v_positions,v_prices,
        v_source_digest,v_snapshot_digest,p_valuation_source,v_observed,v_available,
        p_evidence_digest,v_now
    ) RETURNING * INTO v_created;
    UPDATE public.portfolios SET last_nav_per_unit=v_nav
    WHERE tenant_id=v_identity.tenant_id AND id=p_portfolio_id;
    CASE v_mandate.profile::text
        WHEN 'preservation' THEN v_daily_limit:=0.01; v_total_limit:=0.05;
        WHEN 'conservative' THEN v_daily_limit:=0.015; v_total_limit:=0.08;
        WHEN 'moderate' THEN v_daily_limit:=0.02; v_total_limit:=0.12;
        WHEN 'growth' THEN v_daily_limit:=0.03; v_total_limit:=0.18;
        WHEN 'aggressive' THEN v_daily_limit:=0.05; v_total_limit:=0.25;
        ELSE RAISE EXCEPTION 'POLICY_VERSION_MISMATCH' USING ERRCODE='P0001';
    END CASE;
    IF v_daily_drawdown>=v_daily_limit THEN
        v_breaker_kind:='daily_drawdown';
        v_breaker_observed:=v_daily_drawdown;
        v_breaker_threshold:=v_daily_limit;
    ELSIF v_drawdown>=v_total_limit THEN
        v_breaker_kind:='maximum_drawdown';
        v_breaker_observed:=v_drawdown;
        v_breaker_threshold:=v_total_limit;
    END IF;
    IF v_breaker_kind IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.portfolio_circuit_breakers
        WHERE tenant_id=v_identity.tenant_id AND portfolio_id=p_portfolio_id
          AND status='open'
    ) THEN
        INSERT INTO public.portfolio_circuit_breakers(
            id,tenant_id,portfolio_id,mandate_id,kind,status,observed_value,
            threshold_value,evidence_digest,triggered_at
        ) VALUES (
            gen_random_uuid(),v_identity.tenant_id,p_portfolio_id,v_mandate.id,
            v_breaker_kind,'open',v_breaker_observed,v_breaker_threshold,
            v_snapshot_digest,v_now
        );
    END IF;
    RETURN v_created;
END
$$
"""
    )
    signature = (
        "portfolio_capture_snapshot_v1(uuid,text,bigint,bigint,text,text,text,uuid,text,"
        "bigint,bigint,text,text)"
    )
    _execute(
        f"ALTER FUNCTION tradingagents_private.{signature} OWNER TO "
        "tradingagents_reservation_api"
    )
    _execute(
        f"REVOKE ALL ON FUNCTION tradingagents_private.{signature} FROM PUBLIC,"
        "tradingagents_worker"
    )
    _execute(
        f"GRANT EXECUTE ON FUNCTION tradingagents_private.{signature} TO tradingagents_app"
    )


def downgrade() -> None:
    raise RuntimeError("irreversible security migration")

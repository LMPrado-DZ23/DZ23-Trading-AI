"""Bind portfolio authority to an authenticated session capability.

Revision ID: 20260909_0021
Revises: 20260908_0020
"""

from __future__ import annotations

import hashlib
import hmac
import os

import sqlalchemy as sa
from alembic import context, op

revision = "20260909_0021"
down_revision = "20260908_0020"
branch_labels = None
depends_on = None

_KID = "portfolio-capability-v1"
_PORTFOLIO_TABLES = (
    "portfolio_suitability_assessments",
    "portfolios",
    "portfolio_mandates",
    "portfolio_ledger_transactions",
    "portfolio_ledger_postings",
    "portfolio_cash_reservations",
    "portfolio_positions",
    "portfolio_snapshots",
    "portfolio_circuit_breakers",
)
_APPEND_ONLY = {
    "portfolio_suitability_assessments",
    "portfolio_mandates",
    "portfolio_ledger_transactions",
    "portfolio_ledger_postings",
    "portfolio_snapshots",
}


def _execute(sql: str) -> None:
    op.execute(sa.text(sql))


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _install_capability_store()
    _install_crypto_helpers()
    _install_verifier()
    _replace_app_policies()
    _replace_reservation_ports()
    if not context.is_offline_mode():
        _provision_key()


def _install_capability_store() -> None:
    _execute(
        "CREATE TABLE tradingagents_private.portfolio_capability_keys ("
        "key_id text PRIMARY KEY, key_material bytea NOT NULL, created_at timestamptz "
        "NOT NULL DEFAULT clock_timestamp(), CHECK (octet_length(key_material) >= 32))"
    )
    _execute(
        "CREATE TABLE tradingagents_private.portfolio_capability_nonces ("
        "key_id text NOT NULL, nonce text NOT NULL, expires_at timestamptz NOT NULL, "
        "PRIMARY KEY (key_id, nonce), CHECK (nonce ~ '^[0-9a-f]{64}$'))"
    )
    for table in ("portfolio_capability_keys", "portfolio_capability_nonces"):
        _execute(
            f"ALTER TABLE tradingagents_private.{table} OWNER TO "
            "tradingagents_reservation_api"
        )
        _execute(
            f"REVOKE ALL ON tradingagents_private.{table} FROM PUBLIC, "
            "tradingagents_app, tradingagents_worker"
        )


def _install_crypto_helpers() -> None:
    _execute(
        "CREATE FUNCTION tradingagents_private.portfolio_hmac_sha256(p_key bytea, "
        "p_message bytea) RETURNS bytea LANGUAGE plpgsql IMMUTABLE STRICT "
        "SET search_path = pg_catalog AS $$ DECLARE v_key bytea := p_key; "
        "v_block bytea; v_inner bytea := decode(repeat('00',64),'hex'); "
        "v_outer bytea := decode(repeat('00',64),'hex'); i integer; BEGIN "
        "IF octet_length(v_key) > 64 THEN v_key := sha256(v_key); END IF; "
        "v_block := v_key || decode(repeat('00',64-octet_length(v_key)),'hex'); "
        "FOR i IN 0..63 LOOP v_inner := set_byte(v_inner,i,get_byte(v_block,i) # 54); "
        "v_outer := set_byte(v_outer,i,get_byte(v_block,i) # 92); END LOOP; "
        "RETURN sha256(v_outer || sha256(v_inner || p_message)); END $$"
    )
    _execute(
        "CREATE FUNCTION tradingagents_private.portfolio_cap_field(p_value text) "
        "RETURNS bytea LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog AS $$ "
        "SELECT int4send(octet_length(convert_to(p_value,'UTF8'))) || "
        "convert_to(p_value,'UTF8') $$"
    )
    _execute(
        "CREATE FUNCTION tradingagents_private.portfolio_secure_equal(p_left text, "
        "p_right text) RETURNS boolean LANGUAGE plpgsql IMMUTABLE STRICT "
        "SET search_path = pg_catalog AS $$ DECLARE i integer; mismatch integer; BEGIN "
        "IF octet_length(p_left) <> octet_length(p_right) THEN RETURN false; END IF; "
        "mismatch := 0; FOR i IN 0..octet_length(p_left)-1 LOOP mismatch := mismatch | "
        "(get_byte(convert_to(p_left,'UTF8'),i) # get_byte(convert_to(p_right,'UTF8'),i)); "
        "END LOOP; RETURN mismatch = 0; END $$"
    )
    for signature in (
        "portfolio_hmac_sha256(bytea,bytea)",
        "portfolio_cap_field(text)",
        "portfolio_secure_equal(text,text)",
    ):
        _execute(
            f"ALTER FUNCTION tradingagents_private.{signature} OWNER TO "
            "tradingagents_reservation_api"
        )
        _execute(
            f"REVOKE ALL ON FUNCTION tradingagents_private.{signature} FROM PUBLIC, "
            "tradingagents_app, tradingagents_worker"
        )


def _install_verifier() -> None:
    _execute(
        "CREATE FUNCTION tradingagents_private.portfolio_verify_capability("
        "p_audience text, p_fields text[], p_key_id text, p_session_id uuid, "
        "p_token_hash text, p_issued_at bigint, p_expires_at bigint, p_nonce text, "
        "p_mac text, p_consume boolean) RETURNS TABLE(tenant_id uuid, user_id uuid, "
        "membership_id uuid, member_role text) LANGUAGE plpgsql SECURITY DEFINER "
        "SET search_path = pg_catalog SET row_security = on AS $$ DECLARE v_key bytea; "
        "v_message bytea := decode('54412d4341502d563100','hex'); v_field text; "
        "v_expected text; v_now bigint := floor(extract(epoch FROM clock_timestamp())); BEGIN "
        "IF p_key_id IS NULL OR p_session_id IS NULL OR p_token_hash !~ '^[0-9a-f]{64}$' "
        "OR p_nonce !~ '^[0-9a-f]{64}$' OR p_mac !~ '^[0-9a-f]{64}$' "
        "OR p_issued_at IS NULL OR p_expires_at IS NULL OR p_expires_at <= p_issued_at "
        "OR p_expires_at - p_issued_at > 30 OR p_issued_at > v_now + 5 "
        "OR p_expires_at < v_now THEN RAISE EXCEPTION 'PORTFOLIO_CAPABILITY_INVALID' "
        "USING ERRCODE='P0001'; END IF; SELECT key_material INTO v_key FROM "
        "tradingagents_private.portfolio_capability_keys WHERE key_id=p_key_id; "
        "IF NOT FOUND THEN RAISE EXCEPTION 'PORTFOLIO_CAPABILITY_UNAVAILABLE' "
        "USING ERRCODE='P0001'; END IF; FOREACH v_field IN ARRAY "
        "ARRAY[p_audience,p_session_id::text,p_token_hash] || p_fields || "
        "ARRAY[p_issued_at::text,p_expires_at::text,p_nonce] LOOP "
        "IF v_field IS NULL OR octet_length(convert_to(v_field,'UTF8')) > 4096 THEN "
        "RAISE EXCEPTION 'PORTFOLIO_CAPABILITY_INVALID' USING ERRCODE='P0001'; END IF; "
        "v_message := v_message || tradingagents_private.portfolio_cap_field(v_field); "
        "END LOOP; v_expected := encode(tradingagents_private.portfolio_hmac_sha256("
        "v_key,v_message),'hex'); IF NOT tradingagents_private.portfolio_secure_equal("
        "v_expected,p_mac) THEN RAISE EXCEPTION 'PORTFOLIO_CAPABILITY_INVALID' "
        "USING ERRCODE='P0001'; END IF; PERFORM set_config('app.portfolio_session_id',"
        "p_session_id::text,true); IF p_consume THEN DELETE FROM "
        "tradingagents_private.portfolio_capability_nonces WHERE expires_at < "
        "clock_timestamp(); BEGIN INSERT INTO tradingagents_private.portfolio_capability_nonces "
        "(key_id,nonce,expires_at) VALUES (p_key_id,p_nonce,to_timestamp(p_expires_at)); "
        "EXCEPTION WHEN unique_violation THEN RAISE EXCEPTION 'PORTFOLIO_CAPABILITY_REPLAY' "
        "USING ERRCODE='P0001'; END; END IF; RETURN QUERY SELECT s.tenant_id,s.user_id,"
        "s.membership_id,m.role::text FROM public.identity_sessions s JOIN "
        "public.identity_memberships m ON m.id=s.membership_id AND m.tenant_id=s.tenant_id "
        "AND m.user_id=s.user_id JOIN public.identity_users u ON u.id=s.user_id JOIN "
        "public.identity_tenants t ON t.id=s.tenant_id WHERE s.id=p_session_id "
        "AND s.token_hash=p_token_hash AND s.revoked_at IS NULL AND s.expires_at > "
        "clock_timestamp() AND m.is_active AND u.is_active AND t.is_active; "
        "IF NOT FOUND THEN RAISE EXCEPTION 'PORTFOLIO_SESSION_FORBIDDEN' "
        "USING ERRCODE='P0001'; END IF; END $$"
    )
    _execute(
        "ALTER FUNCTION tradingagents_private.portfolio_verify_capability(text,text[],text,"
        "uuid,text,bigint,bigint,text,text,boolean) OWNER TO tradingagents_reservation_api"
    )
    _execute(
        "REVOKE ALL ON FUNCTION tradingagents_private.portfolio_verify_capability(text,"
        "text[],text,uuid,text,bigint,bigint,text,text,boolean) FROM PUBLIC,"
        "tradingagents_app,tradingagents_worker"
    )
    _execute(
        "CREATE FUNCTION tradingagents_private.portfolio_current_tenant(p_write boolean) "
        "RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER STABLE SET search_path=pg_catalog "
        "SET row_security=on AS $$ DECLARE v_identity record; v_pid text; v_txid text; BEGIN "
        "v_pid := pg_backend_pid()::text; v_txid := txid_current()::text; SELECT * INTO "
        "v_identity FROM tradingagents_private.portfolio_verify_capability("
        "'portfolio.session.v1',ARRAY[v_pid,v_txid],"
        "NULLIF(current_setting('app.portfolio_context_kid',true),''),"
        "NULLIF(current_setting('app.portfolio_session_id',true),'')::uuid,"
        "NULLIF(current_setting('app.portfolio_token_hash',true),''),"
        "NULLIF(current_setting('app.portfolio_context_issued_at',true),'')::bigint,"
        "NULLIF(current_setting('app.portfolio_context_expires_at',true),'')::bigint,"
        "NULLIF(current_setting('app.portfolio_context_nonce',true),''),"
        "NULLIF(current_setting('app.portfolio_context_mac',true),''),false); "
        "IF p_write AND v_identity.member_role NOT IN ('owner','admin','operator') THEN "
        "RAISE EXCEPTION 'PORTFOLIO_SESSION_FORBIDDEN' USING ERRCODE='P0001'; END IF; "
        "RETURN v_identity.tenant_id; EXCEPTION WHEN invalid_text_representation THEN "
        "RAISE EXCEPTION 'PORTFOLIO_CAPABILITY_INVALID' USING ERRCODE='P0001'; END $$"
    )
    _execute(
        "ALTER FUNCTION tradingagents_private.portfolio_current_tenant(boolean) OWNER TO "
        "tradingagents_reservation_api"
    )
    _execute(
        "REVOKE ALL ON FUNCTION tradingagents_private.portfolio_current_tenant(boolean) "
        "FROM PUBLIC,tradingagents_worker"
    )
    _execute(
        "GRANT EXECUTE ON FUNCTION tradingagents_private.portfolio_current_tenant(boolean) "
        "TO tradingagents_app"
    )
    _execute(
        "CREATE FUNCTION tradingagents_private.portfolio_capability_ready(p_key_id text, "
        "p_fingerprint text) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER STABLE "
        "SET search_path=pg_catalog AS $$ DECLARE v_fingerprint text; BEGIN SELECT "
        "encode(sha256(key_material),'hex') INTO v_fingerprint FROM "
        "tradingagents_private.portfolio_capability_keys WHERE key_id=p_key_id; RETURN "
        "v_fingerprint IS NOT NULL AND tradingagents_private.portfolio_secure_equal("
        "v_fingerprint,p_fingerprint); END $$"
    )
    _execute(
        "ALTER FUNCTION tradingagents_private.portfolio_capability_ready(text,text) OWNER "
        "TO tradingagents_reservation_api"
    )
    _execute(
        "REVOKE ALL ON FUNCTION tradingagents_private.portfolio_capability_ready(text,text) "
        "FROM PUBLIC,tradingagents_worker"
    )
    _execute(
        "GRANT EXECUTE ON FUNCTION "
        "tradingagents_private.portfolio_capability_ready(text,text) TO tradingagents_app"
    )


def _replace_app_policies() -> None:
    for table in _PORTFOLIO_TABLES:
        _execute(f'DROP POLICY IF EXISTS "{table}_tenant_isolation" ON public."{table}"')
        _execute(
            f'CREATE POLICY "{table}_app_read" ON public."{table}" FOR SELECT TO '
            "tradingagents_app USING (tenant_id = "
            "tradingagents_private.portfolio_current_tenant(false))"
        )
        if table in _APPEND_ONLY:
            _execute(
                f'CREATE POLICY "{table}_app_insert" ON public."{table}" FOR INSERT TO '
                "tradingagents_app WITH CHECK (tenant_id = "
                "tradingagents_private.portfolio_current_tenant(true))"
            )
        else:
            _execute(
                f'CREATE POLICY "{table}_app_write" ON public."{table}" FOR ALL TO '
                "tradingagents_app USING (tenant_id = "
                "tradingagents_private.portfolio_current_tenant(true)) WITH CHECK "
                "(tenant_id = tradingagents_private.portfolio_current_tenant(true))"
            )


def _replace_reservation_ports() -> None:
    _execute(
        "DROP FUNCTION tradingagents_private.portfolio_reserve_cash_v1(uuid,numeric,text,"
        "text,integer)"
    )
    _execute("DROP FUNCTION tradingagents_private.portfolio_release_cash_v1(uuid)")
    _execute(
        "CREATE FUNCTION tradingagents_private.portfolio_reserve_cash_v1("
        "p_portfolio_id uuid,p_amount numeric,p_purpose_digest text,"
        "p_idempotency_key_hash text,p_ttl_microseconds bigint,p_key_id text,"
        "p_session_id uuid,p_token_hash text,p_issued_at bigint,p_expires_at bigint,"
        "p_nonce text,p_mac text) RETURNS public.portfolio_cash_reservations LANGUAGE "
        "plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $$ "
        "DECLARE v_identity record; v_now timestamptz:=clock_timestamp(); "
        "v_portfolio public.portfolios%ROWTYPE; v_mandate public.portfolio_mandates%ROWTYPE; "
        "v_suitability public.portfolio_suitability_assessments%ROWTYPE; v_latest uuid; "
        "v_existing public.portfolio_cash_reservations%ROWTYPE; "
        "v_created public.portfolio_cash_reservations%ROWTYPE; v_cash numeric; "
        "v_reserved numeric; v_amount_text text; BEGIN v_amount_text:=p_amount::text; "
        "IF position('.' in v_amount_text)>0 THEN v_amount_text:=trim(trailing '.' FROM "
        "trim(trailing '0' FROM v_amount_text)); END IF; IF v_amount_text IN ('','-0') THEN "
        "v_amount_text:='0'; END IF; SELECT * INTO v_identity FROM "
        "tradingagents_private.portfolio_verify_capability('portfolio.reserve.v1',"
        "ARRAY[p_portfolio_id::text,v_amount_text,p_purpose_digest,p_idempotency_key_hash,"
        "p_ttl_microseconds::text],p_key_id,p_session_id,p_token_hash,p_issued_at,"
        "p_expires_at,p_nonce,p_mac,true); IF v_identity.member_role NOT IN "
        "('owner','admin','operator') THEN RAISE EXCEPTION 'PORTFOLIO_SESSION_FORBIDDEN' "
        "USING ERRCODE='P0001'; END IF; PERFORM set_config('app.tenant_id',"
        "v_identity.tenant_id::text,true); IF p_amount IS NULL OR p_amount<=0 OR "
        "p_amount::text IN ('NaN','Infinity','-Infinity') OR p_amount IS DISTINCT FROM "
        "round(p_amount,12) THEN RAISE EXCEPTION 'RESERVATION_AMOUNT_INVALID' USING "
        "ERRCODE='P0001'; END IF; IF p_ttl_microseconds NOT BETWEEN 30000000 AND 3600000000 "
        "THEN RAISE EXCEPTION 'RESERVATION_TTL_INVALID' USING ERRCODE='P0001'; END IF; "
        "IF p_purpose_digest !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION "
        "'PURPOSE_DIGEST_INVALID' USING ERRCODE='P0001'; END IF; IF "
        "p_idempotency_key_hash !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION "
        "'IDEMPOTENCY_KEY_INVALID' USING ERRCODE='P0001'; END IF; SELECT * INTO "
        "v_portfolio FROM public.portfolios WHERE tenant_id=v_identity.tenant_id AND "
        "id=p_portfolio_id FOR UPDATE; IF NOT FOUND OR v_portfolio.status<>'active' THEN "
        "RAISE EXCEPTION 'PORTFOLIO_NOT_ACTIVE' USING ERRCODE='P0001'; END IF; SELECT id "
        "INTO v_latest FROM public.portfolio_mandates WHERE tenant_id=v_identity.tenant_id "
        "AND portfolio_id=p_portfolio_id ORDER BY version DESC LIMIT 1; SELECT * INTO "
        "v_mandate FROM public.portfolio_mandates WHERE tenant_id=v_identity.tenant_id AND "
        "portfolio_id=p_portfolio_id AND id=v_portfolio.current_mandate_id; SELECT * INTO "
        "v_suitability FROM public.portfolio_suitability_assessments WHERE "
        "tenant_id=v_identity.tenant_id AND user_id=v_portfolio.owner_user_id ORDER BY "
        "version DESC LIMIT 1; IF v_mandate.id IS NULL OR v_mandate.id IS DISTINCT FROM "
        "v_latest OR v_mandate.status<>'active' OR v_mandate.mode NOT IN "
        "('backtest','shadow','paper') OR v_mandate.effective_at>v_now OR "
        "v_mandate.ended_at IS NOT NULL OR v_suitability.id IS NULL OR "
        "v_mandate.suitability_id IS DISTINCT FROM v_suitability.id OR "
        "v_suitability.status<>'valid' OR v_suitability.expires_at<=v_now THEN "
        "RAISE EXCEPTION 'ACTIVE_MANDATE_REQUIRED' USING ERRCODE='P0001'; END IF; "
        "IF NOT EXISTS (SELECT 1 FROM public.portfolio_snapshots WHERE "
        "tenant_id=v_identity.tenant_id AND portfolio_id=p_portfolio_id) THEN "
        "RAISE EXCEPTION 'BASELINE_SNAPSHOT_REQUIRED' USING ERRCODE='P0001'; END IF; "
        "IF EXISTS (SELECT 1 FROM public.portfolio_circuit_breakers WHERE "
        "tenant_id=v_identity.tenant_id AND portfolio_id=p_portfolio_id AND status='open') "
        "THEN RAISE EXCEPTION 'CIRCUIT_BREAKER_OPEN' USING ERRCODE='P0001'; END IF; "
        "SELECT * INTO v_existing FROM public.portfolio_cash_reservations WHERE "
        "tenant_id=v_identity.tenant_id AND portfolio_id=p_portfolio_id AND "
        "idempotency_key_hash=p_idempotency_key_hash FOR UPDATE; IF FOUND THEN IF "
        "v_existing.amount IS DISTINCT FROM p_amount OR v_existing.purpose_digest IS "
        "DISTINCT FROM p_purpose_digest THEN RAISE EXCEPTION 'IDEMPOTENCY_PAYLOAD_MISMATCH' "
        "USING ERRCODE='P0001'; END IF; IF v_existing.status<>'active' OR "
        "v_existing.expires_at<=v_now OR v_existing.mandate_id IS DISTINCT FROM "
        "v_mandate.id THEN RAISE EXCEPTION 'IDEMPOTENCY_RESERVATION_FINALIZED' USING "
        "ERRCODE='P0001'; END IF; RETURN v_existing; END IF; SELECT COALESCE(sum(quantity),0) "
        "INTO v_cash FROM public.portfolio_ledger_postings WHERE "
        "tenant_id=v_identity.tenant_id AND portfolio_id=p_portfolio_id AND account='cash' "
        "AND asset=v_portfolio.base_currency; SELECT COALESCE(sum(amount),0) INTO v_reserved "
        "FROM public.portfolio_cash_reservations WHERE tenant_id=v_identity.tenant_id AND "
        "portfolio_id=p_portfolio_id AND status='active' AND expires_at>v_now; IF p_amount>"
        "v_cash-v_reserved THEN RAISE EXCEPTION 'INSUFFICIENT_AVAILABLE_CASH' USING "
        "ERRCODE='P0001'; END IF; INSERT INTO public.portfolio_cash_reservations "
        "(id,tenant_id,portfolio_id,mandate_id,idempotency_key_hash,currency,amount,status,"
        "purpose_digest,expires_at,created_at) VALUES (gen_random_uuid(),"
        "v_identity.tenant_id,p_portfolio_id,v_mandate.id,p_idempotency_key_hash,"
        "v_portfolio.base_currency,p_amount,'active',p_purpose_digest,v_now + "
        "(p_ttl_microseconds * interval '1 microsecond'),v_now) RETURNING * INTO v_created; "
        "UPDATE public.portfolios SET balance_version=balance_version+1 WHERE "
        "tenant_id=v_identity.tenant_id AND id=p_portfolio_id; RETURN v_created; END $$"
    )
    _execute(
        "CREATE FUNCTION tradingagents_private.portfolio_release_cash_v1("
        "p_reservation_id uuid,p_key_id text,p_session_id uuid,p_token_hash text,"
        "p_issued_at bigint,p_expires_at bigint,p_nonce text,p_mac text) RETURNS text "
        "LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on "
        "AS $$ DECLARE v_identity record; v_portfolio uuid; v_status text; "
        "v_now timestamptz:=clock_timestamp(); BEGIN SELECT * INTO v_identity FROM "
        "tradingagents_private.portfolio_verify_capability('portfolio.release.v1',"
        "ARRAY[p_reservation_id::text],p_key_id,p_session_id,p_token_hash,p_issued_at,"
        "p_expires_at,p_nonce,p_mac,true); IF v_identity.member_role NOT IN "
        "('owner','admin','operator') THEN RAISE EXCEPTION 'PORTFOLIO_SESSION_FORBIDDEN' "
        "USING ERRCODE='P0001'; END IF; PERFORM set_config('app.tenant_id',"
        "v_identity.tenant_id::text,true); SELECT portfolio_id INTO v_portfolio FROM "
        "public.portfolio_cash_reservations WHERE tenant_id=v_identity.tenant_id AND "
        "id=p_reservation_id; IF NOT FOUND THEN RETURN NULL; END IF; PERFORM 1 FROM "
        "public.portfolios WHERE tenant_id=v_identity.tenant_id AND id=v_portfolio FOR "
        "UPDATE; SELECT status INTO v_status FROM public.portfolio_cash_reservations WHERE "
        "tenant_id=v_identity.tenant_id AND portfolio_id=v_portfolio AND id=p_reservation_id "
        "FOR UPDATE; IF v_status='active' THEN UPDATE public.portfolio_cash_reservations SET "
        "status='released',released_at=v_now WHERE tenant_id=v_identity.tenant_id AND "
        "portfolio_id=v_portfolio AND id=p_reservation_id; UPDATE public.portfolios SET "
        "balance_version=balance_version+1 WHERE tenant_id=v_identity.tenant_id AND "
        "id=v_portfolio; RETURN 'released'; END IF; RETURN v_status; END $$"
    )
    signatures = (
        "portfolio_reserve_cash_v1(uuid,numeric,text,text,bigint,text,uuid,text,bigint,"
        "bigint,text,text)",
        "portfolio_release_cash_v1(uuid,text,uuid,text,bigint,bigint,text,text)",
    )
    for signature in signatures:
        _execute(
            f"ALTER FUNCTION tradingagents_private.{signature} OWNER TO "
            "tradingagents_reservation_api"
        )
        _execute(
            f"REVOKE ALL ON FUNCTION tradingagents_private.{signature} FROM PUBLIC,"
            "tradingagents_worker"
        )
        _execute(
            f"GRANT EXECUTE ON FUNCTION tradingagents_private.{signature} TO "
            "tradingagents_app"
        )


def _provision_key() -> None:
    master_value = os.environ.get("TRADINGAGENTS_SESSION_TOKEN_PEPPER", "")
    master = master_value.encode("utf-8")
    if len(master) < 32:
        raise RuntimeError(
            "TRADINGAGENTS_SESSION_TOKEN_PEPPER with at least 32 bytes is required "
            "to provision the portfolio capability key"
        )
    key = hmac.new(master, _KID.encode("ascii"), hashlib.sha256).digest()
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO tradingagents_private.portfolio_capability_keys "
            "(key_id,key_material) VALUES (:key_id,:key_material) ON CONFLICT (key_id) "
            "DO UPDATE SET key_material=EXCLUDED.key_material,created_at=clock_timestamp()"
        ),
        {"key_id": _KID, "key_material": key},
    )


def downgrade() -> None:
    raise RuntimeError(
        "20260909_0021 is a one-way security migration; restore from a verified backup"
    )

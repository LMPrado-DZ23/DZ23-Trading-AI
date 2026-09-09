"""Bind reservation TTL and recheck release capabilities after locks.

Revision ID: 20260909_0023
Revises: 20260909_0022
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260909_0023"
down_revision = "20260909_0022"
branch_labels = None
depends_on = None

_RESERVE_SIGNATURE = (
    "uuid,numeric,text,text,bigint,uuid,text,text,uuid,text,bigint,bigint,text,text"
)
_RELEASE_SIGNATURE = "uuid,text,uuid,text,bigint,bigint,text,text"


def _execute(sql: str) -> None:
    op.execute(sa.text(sql))


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _bind_reservation_ttl()
    _replace_release_port()


def _bind_reservation_ttl() -> None:
    _execute(
        "ALTER FUNCTION tradingagents_private.portfolio_reserve_cash_v1("
        f"{_RESERVE_SIGNATURE}) RENAME TO portfolio_reserve_cash_v1_0022_impl"
    )
    _execute(
        "REVOKE ALL ON FUNCTION "
        "tradingagents_private.portfolio_reserve_cash_v1_0022_impl("
        f"{_RESERVE_SIGNATURE}) FROM PUBLIC,tradingagents_app,tradingagents_worker"
    )
    _execute(
        """
        CREATE FUNCTION tradingagents_private.portfolio_reserve_cash_v1(
            p_portfolio_id uuid,
            p_amount numeric,
            p_purpose_digest text,
            p_idempotency_key_hash text,
            p_ttl_microseconds bigint,
            p_snapshot_id uuid,
            p_snapshot_digest text,
            p_key_id text,
            p_session_id uuid,
            p_token_hash text,
            p_issued_at bigint,
            p_expires_at bigint,
            p_nonce text,
            p_mac text
        ) RETURNS public.portfolio_cash_reservations
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog
        SET row_security=on
        AS $$
        DECLARE
            v_result public.portfolio_cash_reservations%ROWTYPE;
        BEGIN
            SELECT * INTO v_result
            FROM tradingagents_private.portfolio_reserve_cash_v1_0022_impl(
                p_portfolio_id,
                p_amount,
                p_purpose_digest,
                p_idempotency_key_hash,
                p_ttl_microseconds,
                p_snapshot_id,
                p_snapshot_digest,
                p_key_id,
                p_session_id,
                p_token_hash,
                p_issued_at,
                p_expires_at,
                p_nonce,
                p_mac
            );
            IF (v_result.expires_at - v_result.created_at) IS DISTINCT FROM
               (p_ttl_microseconds * interval '1 microsecond') THEN
                RAISE EXCEPTION 'IDEMPOTENCY_PAYLOAD_MISMATCH' USING ERRCODE='P0001';
            END IF;
            RETURN v_result;
        END
        $$
        """
    )
    _execute(
        "ALTER FUNCTION tradingagents_private.portfolio_reserve_cash_v1("
        f"{_RESERVE_SIGNATURE}) OWNER TO tradingagents_reservation_api"
    )
    _execute(
        "REVOKE ALL ON FUNCTION tradingagents_private.portfolio_reserve_cash_v1("
        f"{_RESERVE_SIGNATURE}) FROM PUBLIC,tradingagents_worker"
    )
    _execute(
        "GRANT EXECUTE ON FUNCTION tradingagents_private.portfolio_reserve_cash_v1("
        f"{_RESERVE_SIGNATURE}) TO tradingagents_app"
    )


def _replace_release_port() -> None:
    _execute(
        "DROP FUNCTION tradingagents_private.portfolio_release_cash_v1("
        f"{_RELEASE_SIGNATURE})"
    )
    _execute(
        """
        CREATE FUNCTION tradingagents_private.portfolio_release_cash_v1(
            p_reservation_id uuid,
            p_key_id text,
            p_session_id uuid,
            p_token_hash text,
            p_issued_at bigint,
            p_expires_at bigint,
            p_nonce text,
            p_mac text
        ) RETURNS text
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path=pg_catalog
        SET row_security=on
        AS $$
        DECLARE
            v_identity record;
            v_portfolio uuid;
            v_status text;
            v_now timestamptz;
        BEGIN
            SELECT * INTO v_identity FROM
            tradingagents_private.portfolio_verify_capability(
                'portfolio.release.v1',
                ARRAY[p_reservation_id::text],
                p_key_id,
                p_session_id,
                p_token_hash,
                p_issued_at,
                p_expires_at,
                p_nonce,
                p_mac,
                false
            );
            IF v_identity.member_role NOT IN ('owner','admin','operator') THEN
                RAISE EXCEPTION 'PORTFOLIO_SESSION_FORBIDDEN' USING ERRCODE='P0001';
            END IF;
            PERFORM set_config('app.tenant_id',v_identity.tenant_id::text,true);
            SELECT portfolio_id INTO v_portfolio
            FROM public.portfolio_cash_reservations
            WHERE tenant_id=v_identity.tenant_id AND id=p_reservation_id;
            IF FOUND THEN
                PERFORM 1 FROM public.portfolios
                WHERE tenant_id=v_identity.tenant_id AND id=v_portfolio
                FOR UPDATE;
            END IF;
            v_now:=clock_timestamp();
            SELECT * INTO v_identity FROM
            tradingagents_private.portfolio_verify_capability(
                'portfolio.release.v1',
                ARRAY[p_reservation_id::text],
                p_key_id,
                p_session_id,
                p_token_hash,
                p_issued_at,
                p_expires_at,
                p_nonce,
                p_mac,
                true
            );
            IF v_identity.member_role NOT IN ('owner','admin','operator') THEN
                RAISE EXCEPTION 'PORTFOLIO_SESSION_FORBIDDEN' USING ERRCODE='P0001';
            END IF;
            PERFORM set_config('app.tenant_id',v_identity.tenant_id::text,true);
            IF v_portfolio IS NULL THEN
                RETURN NULL;
            END IF;
            SELECT status INTO v_status
            FROM public.portfolio_cash_reservations
            WHERE tenant_id=v_identity.tenant_id
              AND portfolio_id=v_portfolio
              AND id=p_reservation_id
            FOR UPDATE;
            IF v_status='active' THEN
                UPDATE public.portfolio_cash_reservations
                SET status='released',released_at=v_now
                WHERE tenant_id=v_identity.tenant_id
                  AND portfolio_id=v_portfolio
                  AND id=p_reservation_id;
                UPDATE public.portfolios
                SET balance_version=balance_version+1
                WHERE tenant_id=v_identity.tenant_id AND id=v_portfolio;
                RETURN 'released';
            END IF;
            RETURN v_status;
        END
        $$
        """
    )
    _execute(
        "ALTER FUNCTION tradingagents_private.portfolio_release_cash_v1("
        f"{_RELEASE_SIGNATURE}) OWNER TO tradingagents_reservation_api"
    )
    _execute(
        "REVOKE ALL ON FUNCTION tradingagents_private.portfolio_release_cash_v1("
        f"{_RELEASE_SIGNATURE}) FROM PUBLIC,tradingagents_worker"
    )
    _execute(
        "GRANT EXECUTE ON FUNCTION tradingagents_private.portfolio_release_cash_v1("
        f"{_RELEASE_SIGNATURE}) TO tradingagents_app"
    )


def downgrade() -> None:
    raise RuntimeError(
        "20260909_0023 is a one-way security migration; restore from a verified backup"
    )

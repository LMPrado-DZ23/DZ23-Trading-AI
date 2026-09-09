"""Enforce reservation replay parity at the public PostgreSQL port.

Revision ID: 20260909_0024
Revises: 20260909_0023
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260909_0024"
down_revision = "20260909_0023"
branch_labels = None
depends_on = None

_RESERVE_SIGNATURE = (
    "uuid,numeric,text,text,bigint,uuid,text,text,uuid,text,bigint,bigint,text,text"
)


def _execute(sql: str) -> None:
    op.execute(sa.text(sql))


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _execute(
        """
        CREATE OR REPLACE FUNCTION tradingagents_private.portfolio_reserve_cash_v1(
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
            v_identity record;
            v_existing public.portfolio_cash_reservations%ROWTYPE;
            v_result public.portfolio_cash_reservations%ROWTYPE;
            v_amount_text text;
        BEGIN
            v_amount_text:=p_amount::text;
            IF position('.' in v_amount_text)>0 THEN
                v_amount_text:=trim(trailing '.' FROM trim(trailing '0' FROM v_amount_text));
            END IF;
            IF v_amount_text IN ('','-0') THEN
                v_amount_text:='0';
            END IF;
            SELECT * INTO v_identity FROM
            tradingagents_private.portfolio_verify_capability(
                'portfolio.reserve.v1',
                ARRAY[
                    p_portfolio_id::text,
                    v_amount_text,
                    p_purpose_digest,
                    p_idempotency_key_hash,
                    p_ttl_microseconds::text,
                    p_snapshot_id::text,
                    p_snapshot_digest
                ],
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
            IF p_ttl_microseconds NOT BETWEEN 30000000 AND 3600000000 THEN
                RAISE EXCEPTION 'RESERVATION_TTL_INVALID' USING ERRCODE='P0001';
            END IF;
            PERFORM set_config('app.tenant_id',v_identity.tenant_id::text,true);
            SELECT * INTO v_existing
            FROM public.portfolio_cash_reservations
            WHERE tenant_id=v_identity.tenant_id
              AND portfolio_id=p_portfolio_id
              AND idempotency_key_hash=p_idempotency_key_hash;
            IF FOUND AND
               (v_existing.expires_at - v_existing.created_at) IS DISTINCT FROM
               (p_ttl_microseconds * interval '1 microsecond') THEN
                RAISE EXCEPTION 'IDEMPOTENCY_PAYLOAD_MISMATCH' USING ERRCODE='P0001';
            END IF;
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


def downgrade() -> None:
    raise RuntimeError(
        "20260909_0024 is a one-way security migration; restore from a verified backup"
    )

"""M0 contracts: immutable, digest-bound and financially strict."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from tradingagents.platform.contracts import (
    Approval,
    ExecutionIntent,
    OrderSide,
    OrderType,
    RunMode,
)


def _intent(**overrides):
    now = datetime.now(UTC).replace(microsecond=0)
    data = {
        "intent_id": uuid4(),
        "tenant_id": uuid4(),
        "proposal_id": uuid4(),
        "analysis_run_id": uuid4(),
        "mandate_id": uuid4(),
        "mandate_version": 1,
        "mandate_digest": "a" * 64,
        "policy_digest": "b" * 64,
        "signal_digest": "c" * 64,
        "snapshot_digest": "d" * 64,
        "connection_id": uuid4(),
        "credential_version": 3,
        "exchange": "binance",
        "mode": RunMode.PAPER,
        "symbol": "BTC/USDT",
        "side": OrderSide.BUY,
        "order_type": OrderType.LIMIT,
        "quantity": Decimal("0.001"),
        "limit_price": Decimal("50000.00"),
        "max_slippage_bps": 25,
        "created_at": now,
        "expires_at": now + timedelta(minutes=5),
        "idempotency_key": "tenant:proposal:sequence-1",
    }
    data.update(overrides)
    return ExecutionIntent.create(**data)


def test_intent_is_frozen_and_digest_verifies():
    intent = _intent()
    assert intent.verify_digest()
    with pytest.raises(ValidationError):
        intent.quantity = Decimal("2")


def test_any_material_change_changes_digest():
    first = _intent()
    second = _intent(
        intent_id=first.intent_id,
        tenant_id=first.tenant_id,
        proposal_id=first.proposal_id,
        analysis_run_id=first.analysis_run_id,
        mandate_id=first.mandate_id,
        connection_id=first.connection_id,
        quantity=Decimal("0.002"),
        created_at=first.created_at,
        expires_at=first.expires_at,
    )
    assert first.digest != second.digest


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-1", "0"])
def test_quantity_must_be_positive_and_finite(bad):
    with pytest.raises(ValidationError):
        _intent(quantity=Decimal(bad))


def test_limit_price_is_mandatory_and_finite():
    with pytest.raises(ValidationError):
        _intent(limit_price=None)
    with pytest.raises(ValidationError):
        _intent(limit_price=Decimal("Infinity"))


def test_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        _intent(leverage=100)


def test_approval_is_bound_to_intent_tenant_mfa_and_expiry():
    intent = _intent()
    approval = Approval(
        approval_id=uuid4(),
        tenant_id=intent.tenant_id,
        approver_user_id=uuid4(),
        intent_digest=intent.digest,
        mfa_verified=True,
        approved_at=intent.created_at,
        expires_at=intent.expires_at,
    )
    assert intent.is_approved_by(approval, at=intent.created_at + timedelta(seconds=1))
    assert not intent.is_approved_by(
        approval.model_copy(update={"intent_digest": "e" * 64}),
        at=intent.created_at + timedelta(seconds=1),
    )
    assert not intent.is_approved_by(approval, at=intent.expires_at)


def test_live_requires_mfa_but_paper_does_not():
    live = _intent(mode=RunMode.LIVE_SUPERVISED)
    no_mfa = Approval(
        approval_id=uuid4(),
        tenant_id=live.tenant_id,
        approver_user_id=uuid4(),
        intent_digest=live.digest,
        mfa_verified=False,
        approved_at=live.created_at,
        expires_at=live.expires_at,
    )
    assert not live.is_approved_by(no_mfa, at=live.created_at)

    paper = _intent()
    paper_approval = no_mfa.model_copy(
        update={"tenant_id": paper.tenant_id, "intent_digest": paper.digest}
    )
    assert paper.is_approved_by(paper_approval, at=paper.created_at)

"""M6a immutable multi-account execution scope contracts."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from uuid import UUID

import pytest
from pydantic import ValidationError

from tradingagents.platform.contracts import (
    MandateStatus,
    OrderSide,
    OrderType,
    RiskProfile,
    RunMode,
)
from tradingagents.platform.execution import (
    CapitalExecutionScopeV2,
    ExecutionEnvironment,
    ExecutionIntentV2,
    MandateBindingV2,
    PortfolioAccountScopeV2,
    RebalanceProposalV2,
    TargetAllocationV2,
)

TENANT_ID = UUID("00000000-0000-4000-8000-000000000001")
PORTFOLIO_ID = UUID("00000000-0000-4000-8000-000000000002")
ACCOUNT_ID = UUID("00000000-0000-4000-8000-000000000003")
EXTERNAL_ACCOUNT_ID = UUID("00000000-0000-4000-8000-000000000011")
CONNECTION_ID = UUID("00000000-0000-4000-8000-000000000004")
MANDATE_ID = UUID("00000000-0000-4000-8000-000000000005")
PROPOSAL_ID = UUID("00000000-0000-4000-8000-000000000006")
RUN_ID = UUID("00000000-0000-4000-8000-000000000007")
INTENT_ID = UUID("00000000-0000-4000-8000-000000000008")
CHILD_ORDER_ID = UUID("00000000-0000-4000-8000-000000000014")
NOW = datetime(2026, 9, 13, 12, tzinfo=UTC)


def _account(**updates: object) -> PortfolioAccountScopeV2:
    data: dict[str, object] = {
        "tenant_id": TENANT_ID,
        "portfolio_id": PORTFOLIO_ID,
        "portfolio_account_id": ACCOUNT_ID,
        "external_account_id": EXTERNAL_ACCOUNT_ID,
        "venue_slug": "binance",
        "environment": ExecutionEnvironment.TESTNET,
    }
    data.update(updates)
    return PortfolioAccountScopeV2(**data)


def _mandate(**updates: object) -> MandateBindingV2:
    data: dict[str, object] = {
        "mandate_id": MANDATE_ID,
        "tenant_id": TENANT_ID,
        "portfolio_id": PORTFOLIO_ID,
        "version": 3,
        "profile": RiskProfile.MODERATE,
        "mode": RunMode.PAPER,
        "status": MandateStatus.ACTIVE,
        "policy_digest": "a" * 64,
    }
    data.update(updates)
    return MandateBindingV2(**data)


def _proposal(**updates: object) -> RebalanceProposalV2:
    data: dict[str, object] = {
        "proposal_id": PROPOSAL_ID,
        "tenant_id": TENANT_ID,
        "portfolio_id": PORTFOLIO_ID,
        "analysis_run_id": RUN_ID,
        "mandate": _mandate(),
        "base_currency": "BRL",
        "portfolio_nav_base": Decimal("10000"),
        "maximum_turnover": Decimal("1"),
        "account_scopes": (_account(),),
        "allocations": (
            TargetAllocationV2(
                portfolio_account_id=ACCOUNT_ID,
                symbol="BTC/BRL",
                quote_currency="BRL",
                quote_to_base_rate=Decimal("1"),
                target_weight=Decimal("0.10"),
                target_notional_base=Decimal("1000"),
                max_trade_notional_quote=Decimal("1100"),
                price_snapshot_digest="7" * 64,
            ),
        ),
        "risk_digest": "b" * 64,
        "snapshot_digest": "c" * 64,
        "fx_snapshot_digest": "6" * 64,
        "created_at": NOW,
        "expires_at": NOW + timedelta(minutes=10),
    }
    data.update(updates)
    return RebalanceProposalV2(**data)


def _scope(proposal: RebalanceProposalV2 | None = None, **updates: object):
    proposal = proposal or _proposal()
    data: dict[str, object] = {
        "account": proposal.account_scopes[0],
        "broker_connection_id": CONNECTION_ID,
        "venue_capability_digest": "9" * 64,
        "credential_version": 7,
        "credential_key_fingerprint": "d" * 64,
        "mandate_id": proposal.mandate.mandate_id,
        "mandate_version": proposal.mandate.version,
        "mandate_digest": proposal.mandate.digest,
        "risk_policy_digest": proposal.risk_digest,
        "account_snapshot_digest": proposal.snapshot_digest,
    }
    data.update(updates)
    return CapitalExecutionScopeV2(**data)


def _intent(**updates: object) -> ExecutionIntentV2:
    proposal = updates.pop("proposal", _proposal())
    assert isinstance(proposal, RebalanceProposalV2)
    data: dict[str, object] = {
        "intent_id": INTENT_ID,
        "child_order_id": CHILD_ORDER_ID,
        "tranche_index": 1,
        "proposal": proposal,
        "scope": _scope(proposal),
        "symbol": "BTC/BRL",
        "side": OrderSide.BUY,
        "order_type": OrderType.LIMIT,
        "quantity": Decimal("0.002"),
        "limit_price": Decimal("500000.00"),
        "max_slippage_bps": 25,
        "sizing_decision_digest": "e" * 64,
        "risk_reservation_id": UUID("00000000-0000-4000-8000-000000000013"),
        "risk_reservation_digest": "f" * 64,
        "created_at": NOW + timedelta(seconds=1),
        "expires_at": NOW + timedelta(minutes=5),
    }
    data.update(updates)
    return ExecutionIntentV2(**data)


def test_account_scope_is_continuous_and_contains_no_credential_material() -> None:
    scope = _account()
    assert "credential_version" not in type(scope).model_fields
    assert "credential_key_fingerprint" not in type(scope).model_fields
    assert not any("secret" in field or "key" in field for field in type(scope).model_fields)
    assert len(scope.digest) == 64
    with pytest.raises(ValidationError):
        scope.portfolio_account_id = UUID("00000000-0000-4000-8000-000000000099")


def test_capital_scope_rotates_without_changing_continuous_account_identity() -> None:
    first = _scope()
    rotated = first.model_copy(
        update={"credential_version": 8, "credential_key_fingerprint": "e" * 64}
    )
    assert first.account == rotated.account
    assert first.account.digest == rotated.account.digest
    assert first.digest != rotated.digest


def test_mandate_and_proposal_reject_cross_portfolio_references() -> None:
    other_portfolio = UUID("00000000-0000-4000-8000-000000000099")
    with pytest.raises(ValidationError, match="mandate must belong"):
        _proposal(mandate=_mandate(portfolio_id=other_portfolio))
    with pytest.raises(ValidationError, match="account scope must belong"):
        _proposal(account_scopes=(_account(portfolio_id=other_portfolio),))


def test_proposal_rejects_cross_tenant_duplicate_and_noncanonical_accounts() -> None:
    second = _account(
        portfolio_account_id=UUID("00000000-0000-4000-8000-000000000009"),
        external_account_id=UUID("00000000-0000-4000-8000-000000000012"),
    )
    with pytest.raises(ValidationError, match="proposal tenant"):
        _proposal(mandate=_mandate(tenant_id=UUID(int=99)))
    with pytest.raises(ValidationError, match="must be unique"):
        _proposal(account_scopes=(_account(), _account()))
    with pytest.raises(ValidationError, match="canonical account order"):
        _proposal(account_scopes=(second, _account()))
    with pytest.raises(ValidationError, match="external accounts must be unique"):
        _proposal(
            account_scopes=(
                _account(),
                second.model_copy(update={"external_account_id": EXTERNAL_ACCOUNT_ID}),
            )
        )


def test_proposal_requires_canonical_unique_allocations_and_bounded_total() -> None:
    btc = TargetAllocationV2(
        portfolio_account_id=ACCOUNT_ID,
        symbol="BTC/BRL",
        quote_currency="BRL",
        quote_to_base_rate=Decimal("1"),
        target_weight=Decimal("0.6"),
        target_notional_base=Decimal("6000"),
        max_trade_notional_quote=Decimal("6000"),
        price_snapshot_digest="7" * 64,
    )
    eth = TargetAllocationV2(
        portfolio_account_id=ACCOUNT_ID,
        symbol="ETH/BRL",
        quote_currency="BRL",
        quote_to_base_rate=Decimal("1"),
        target_weight=Decimal("0.5"),
        target_notional_base=Decimal("5000"),
        max_trade_notional_quote=Decimal("5000"),
        price_snapshot_digest="8" * 64,
    )
    with pytest.raises(ValidationError, match="target weights"):
        _proposal(allocations=(btc, eth))
    with pytest.raises(ValidationError, match="canonical account-symbol order"):
        _proposal(allocations=(eth, btc.model_copy(update={"target_weight": Decimal("0.4")})))
    with pytest.raises(ValidationError, match="unique account-symbol pairs"):
        _proposal(allocations=(btc, btc.model_copy(update={"target_weight": Decimal("0.4")})))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("quantity", 0.1),
        ("quantity", True),
        ("quantity", Decimal("NaN")),
        ("quantity", Decimal("1e38")),
        ("limit_price", Decimal("0.0000000000000000001")),
        ("limit_price", Decimal("0")),
    ],
)
def test_intent_requires_exact_bounded_positive_decimals(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _intent(**{field: value})


@pytest.mark.parametrize(
    "timestamp",
    [
        datetime(2026, 9, 13, 12),
        datetime(2026, 9, 13, 9, tzinfo=timezone(timedelta(hours=-3))),
    ],
)
def test_timestamps_must_be_aware_utc(timestamp: datetime) -> None:
    with pytest.raises(ValidationError, match="UTC|timezone"):
        _proposal(created_at=timestamp)
    with pytest.raises(ValidationError, match="UTC|timezone"):
        _intent(created_at=timestamp)


def test_capital_authority_lifetimes_are_bounded() -> None:
    with pytest.raises(ValidationError, match="one hour"):
        _proposal(expires_at=NOW + timedelta(hours=1, seconds=1))
    with pytest.raises(ValidationError, match="fifteen minutes"):
        _intent(expires_at=NOW + timedelta(minutes=15, seconds=2))


def test_authority_identifiers_reject_nil_uuid() -> None:
    with pytest.raises(ValidationError, match="nil UUID"):
        _account(portfolio_account_id=UUID(int=0))
    with pytest.raises(ValidationError, match="nil UUID"):
        _intent(risk_reservation_id=UUID(int=0))


@pytest.mark.parametrize(
    ("scope_update", "message"),
    [
        ({"mandate_digest": "e" * 64}, "mandate digest"),
        ({"risk_policy_digest": "e" * 64}, "risk digest"),
        ({"account_snapshot_digest": "e" * 64}, "snapshot digest"),
    ],
)
def test_intent_rejects_all_point_in_time_binding_mismatches(
    scope_update: dict[str, object], message: str
) -> None:
    proposal = _proposal()
    with pytest.raises(ValidationError, match=message):
        _intent(proposal=proposal, scope=_scope(proposal, **scope_update))


def test_intent_rejects_cross_portfolio_account_and_unproposed_symbol() -> None:
    proposal = _proposal()
    foreign = _account(
        portfolio_id=UUID("00000000-0000-4000-8000-000000000099"),
        portfolio_account_id=UUID("00000000-0000-4000-8000-000000000098"),
    )
    with pytest.raises(ValidationError, match="proposal tenant and portfolio"):
        _intent(proposal=proposal, scope=_scope(proposal, account=foreign))
    with pytest.raises(ValidationError, match="not present"):
        _intent(symbol="ETH/BRL")


def test_intent_rejects_relabelled_account_and_environment_mode_mismatch() -> None:
    proposal = _proposal()
    relabelled = proposal.account_scopes[0].model_copy(
        update={
            "external_account_id": UUID("00000000-0000-4000-8000-000000000099")
        }
    )
    with pytest.raises(ValidationError, match="not authorized"):
        _intent(proposal=proposal, scope=_scope(proposal, account=relabelled))
    live_account = _account(environment=ExecutionEnvironment.LIVE)
    live_proposal = _proposal(account_scopes=(live_account,))
    with pytest.raises(ValidationError, match="execution mode"):
        _intent(proposal=live_proposal, scope=_scope(live_proposal))


def test_intent_digest_and_client_order_seed_are_stable_and_domain_separated() -> None:
    first = _intent()
    second = _intent()
    changed = _intent(quantity=Decimal("0.0021"))
    assert first.digest == second.digest
    assert first.client_order_seed == second.client_order_seed
    assert first.digest != first.client_order_seed
    assert first.digest != changed.digest
    assert first.client_order_seed != changed.client_order_seed
    assert len(first.digest) == len(first.client_order_seed) == 64


def test_client_order_seed_is_bound_to_persisted_child_order_not_request_wrapper() -> None:
    first = _intent()
    relabelled_request = _intent(
        intent_id=UUID("00000000-0000-4000-8000-000000000099")
    )
    another_child = _intent(
        child_order_id=UUID("00000000-0000-4000-8000-000000000098")
    )
    assert first.digest != relabelled_request.digest
    assert first.client_order_seed == relabelled_request.client_order_seed
    assert first.client_order_seed != another_child.client_order_seed


def test_invalid_model_copy_cannot_acquire_an_authoritative_digest() -> None:
    forged = _intent().model_copy(update={"quantity": Decimal("-1")})
    with pytest.raises(ValidationError):
        _ = forged.digest
    with pytest.raises(ValidationError):
        _ = forged.client_order_seed


def test_connection_and_capability_rotate_without_fragmenting_account_identity() -> None:
    first = _scope()
    rotated = first.model_copy(
        update={
            "broker_connection_id": UUID("00000000-0000-4000-8000-000000000099"),
            "venue_capability_digest": "8" * 64,
        }
    )
    assert first.account.digest == rotated.account.digest
    assert first.digest != rotated.digest


def test_intent_is_bound_to_one_account_allocation_and_notional_cap() -> None:
    proposal = _proposal()
    other_account_id = UUID("00000000-0000-4000-8000-000000000009")
    wrong_account_allocation = proposal.allocations[0].model_copy(
        update={"portfolio_account_id": other_account_id}
    )
    with pytest.raises(ValidationError, match="allocation account"):
        _proposal(allocations=(wrong_account_allocation,))
    with pytest.raises(ValidationError, match="authorized trade notional"):
        _intent(quantity=Decimal("1"), limit_price=Decimal("500000"))


def test_proposal_binds_nav_fx_and_aggregate_turnover() -> None:
    allocation = _proposal().allocations[0]
    with pytest.raises(ValidationError, match="NAV times target weight"):
        _proposal(
            allocations=(
                allocation.model_copy(
                    update={"target_notional_base": Decimal("999")}
                ),
            )
        )
    with pytest.raises(ValidationError, match="portfolio turnover"):
        _proposal(
            maximum_turnover=Decimal("0.1"),
            allocations=(allocation,),
        )


def test_digest_and_weight_validation_ignore_ambient_decimal_context() -> None:
    baseline = _intent()
    with localcontext() as context:
        context.prec = 1
        repeated = _intent()
    assert repeated.digest == baseline.digest


def test_nested_contracts_are_exact_and_extras_are_forbidden() -> None:
    with pytest.raises(ValidationError):
        PortfolioAccountScopeV2(
            tenant_id=str(TENANT_ID),
            portfolio_id=PORTFOLIO_ID,
            portfolio_account_id=ACCOUNT_ID,
            external_account_id=EXTERNAL_ACCOUNT_ID,
            venue_slug="binance",
            environment=ExecutionEnvironment.TESTNET,
        )
    with pytest.raises(ValidationError):
        _account(credential_version=1)
    with pytest.raises(ValidationError):
        _intent(scope=_scope().model_dump(mode="python"))


def test_contract_field_names_cannot_hold_raw_credentials() -> None:
    contract_types = (
        PortfolioAccountScopeV2,
        CapitalExecutionScopeV2,
        MandateBindingV2,
        TargetAllocationV2,
        RebalanceProposalV2,
        ExecutionIntentV2,
    )
    forbidden = {"api_key", "secret", "password", "token", "private_key", "credential"}
    for contract_type in contract_types:
        fields = set(contract_type.model_fields)
        assert fields.isdisjoint(forbidden)

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

import tradingagents.platform.backtest.simulation_economic_ledger as ledger_module
from tradingagents.platform.backtest.contracts import (
    M5_UNVERIFIED_LIMITATIONS,
    CorporateActionType,
    canonical_digest,
)
from tradingagents.platform.backtest.simulation_economic_ledger import (
    DividendPaymentStatusV2,
    EconomicEventKindV2,
    EconomicLedgerPolicyV2,
    EconomicPositionV2,
    EconomicSessionConflictError,
    EconomicSessionTransitionV2,
    EconomicStageV2,
    FillEligibilityV2,
    PostingDirectionV2,
    SimulationEconomicLedgerError,
    SimulationEconomicLedgerStateV2,
    _commit_simulation_economic_session_v2,
    create_simulation_economic_ledger_v2,
)
from tradingagents.platform.backtest.simulation_economics import (
    ECONOMIC_APPLICATION_PRECEDENCE_DIGEST,
    AuthorizedCorporateCashConversion,
    AuthorizedEconomicEventView,
    CorporateActionApplication,
    CorporateActionProjection,
    EconomicEventView,
    EconomicPhase,
    FxConversion,
    FxConversionMode,
    FxRateProjection,
)
from tradingagents.platform.backtest.simulation_ledger import (
    ExecutionFill,
    SimulationOrder,
    SimulationSide,
)

TENANT_ID = UUID(int=1)
BACKTEST_ID = UUID(int=2)
LEDGER_ID = UUID(int=3)
INSTRUMENT_A = UUID(int=50)
INSTRUMENT_B = UUID(int=60)
DATASETS = {INSTRUMENT_A: UUID(int=150), INSTRUMENT_B: UUID(int=160)}
MANIFESTS = {INSTRUMENT_A: "d" * 64, INSTRUMENT_B: "e" * 64}
SPLIT_ID = UUID(int=501)
DIVIDEND_ID = UUID(int=502)
DELISTING_ID = UUID(int=503)
SPEC_DIGEST = "a" * 64
TRADE_POLICY_DIGEST = "b" * 64
ECONOMICS_POLICY_DIGEST = "c" * 64
OPENED_AT = datetime(2024, 1, 1, tzinfo=UTC)
DEFAULT_ORDER_ID = UUID(int=601)


def _digest(payload: dict) -> str:
    def normalize(value):
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="python")
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return tuple(normalize(item) for item in value)
        return value

    return canonical_digest({key: normalize(value) for key, value in payload.items()})


def _policy() -> EconomicLedgerPolicyV2:
    return EconomicLedgerPolicyV2(
        tenant_id=TENANT_ID,
        backtest_id=BACKTEST_ID,
        ledger_id=LEDGER_ID,
        backtest_spec_digest=SPEC_DIGEST,
        trade_ledger_policy_digest=TRADE_POLICY_DIGEST,
        economics_policy_digest=ECONOMICS_POLICY_DIGEST,
        opened_at=OPENED_AT,
        functional_currency="USD",
        money_quantum=Decimal("0.01"),
        initial_cash=Decimal("1000.00"),
        instrument_ids=(INSTRUMENT_A, INSTRUMENT_B),
        initial_positions=(
            EconomicPositionV2(
                instrument_id=INSTRUMENT_A,
                quantity=Decimal("10"),
                cost_basis=Decimal("100.00"),
                currency="USD",
            ),
            EconomicPositionV2(
                instrument_id=INSTRUMENT_B,
                quantity=Decimal("4"),
                cost_basis=Decimal("40.00"),
                currency="USD",
            ),
        ),
    )


def _state(policy: EconomicLedgerPolicyV2 | None = None):
    policy = policy or _policy()
    return create_simulation_economic_ledger_v2(policy, expected_policy_digest=policy.digest)


def _projection(
    application: CorporateActionApplication,
    *,
    instrument_id: UUID,
    session_date: date,
    action_id: UUID,
    revision: int = 1,
    action_digest: str | None = None,
    ratio: str = "2",
    cash_amount: str = "1.25",
    consideration: str = "12",
) -> CorporateActionProjection:
    phase = (
        EconomicPhase.PRE_OPEN
        if application
        in {
            CorporateActionApplication.SPLIT,
            CorporateActionApplication.DIVIDEND_ENTITLEMENT,
            CorporateActionApplication.DELISTING,
        }
        else EconomicPhase.POST_CLOSE
    )
    action_type = {
        CorporateActionApplication.SPLIT: CorporateActionType.SPLIT,
        CorporateActionApplication.DIVIDEND_ENTITLEMENT: CorporateActionType.CASH_DIVIDEND,
        CorporateActionApplication.DIVIDEND_PAYMENT: CorporateActionType.CASH_DIVIDEND,
        CorporateActionApplication.DELISTING: CorporateActionType.DELISTING,
        CorporateActionApplication.DELISTING_SETTLEMENT: CorporateActionType.DELISTING,
    }[application]
    application_at = datetime.combine(
        session_date,
        datetime.min.time().replace(hour=9)
        if phase is EconomicPhase.PRE_OPEN
        else datetime.min.time().replace(hour=16),
        tzinfo=UTC,
    )
    payload = {
        "projection_version": "corporate-action-projection/1",
        "dataset_id": DATASETS[instrument_id],
        "manifest_digest": MANIFESTS[instrument_id],
        "instrument_id": instrument_id,
        "session_date": session_date,
        "phase": phase,
        "application": application,
        "application_at": application_at,
        "action_id": action_id,
        "revision": revision,
        "action_type": action_type,
        "action_digest": action_digest
        or canonical_digest({"action_id": action_id, "action_type": action_type}),
        "source_digest": "f" * 64,
        "published_at": OPENED_AT,
        "available_at": OPENED_AT + timedelta(minutes=1),
        "effective_date": session_date
        if phase is EconomicPhase.PRE_OPEN
        else session_date - timedelta(days=1),
        "ex_date": session_date
        if application
        in {
            CorporateActionApplication.SPLIT,
            CorporateActionApplication.DIVIDEND_ENTITLEMENT,
        }
        else session_date - timedelta(days=1)
        if application is CorporateActionApplication.DIVIDEND_PAYMENT
        else None,
        "pay_date": session_date
        if phase is EconomicPhase.POST_CLOSE
        else session_date + timedelta(days=1)
        if action_type in {CorporateActionType.CASH_DIVIDEND, CorporateActionType.DELISTING}
        else None,
        "split_ratio": Decimal(ratio) if application is CorporateActionApplication.SPLIT else None,
        "split_ratio_convention": "new_shares_per_old_share"
        if application is CorporateActionApplication.SPLIT
        else None,
        "cash_amount": Decimal(cash_amount)
        if action_type is CorporateActionType.CASH_DIVIDEND
        else None,
        "cash_amount_basis": "net_cash_per_entitled_share"
        if action_type is CorporateActionType.CASH_DIVIDEND
        else None,
        "cash_consideration": Decimal(consideration)
        if action_type is CorporateActionType.DELISTING
        else None,
        "currency": None if action_type is CorporateActionType.SPLIT else "USD",
    }
    return CorporateActionProjection(**payload, projection_digest=_digest(payload))


def _view(
    instrument_id: UUID,
    session_date: date,
    phase: EconomicPhase,
    actions: tuple[CorporateActionProjection, ...] = (),
) -> AuthorizedEconomicEventView:
    as_of = datetime.combine(
        session_date,
        datetime.min.time().replace(hour=9)
        if phase is EconomicPhase.PRE_OPEN
        else datetime.min.time().replace(hour=16),
        tzinfo=UTC,
    )
    view_payload = {
        "view_version": "economic-event-view/1",
        "dataset_id": DATASETS[instrument_id],
        "tenant_id": TENANT_ID,
        "manifest_digest": MANIFESTS[instrument_id],
        "instrument_id": instrument_id,
        "session_date": session_date,
        "phase": phase,
        "as_of": as_of,
        "precedence_digest": ECONOMIC_APPLICATION_PRECEDENCE_DIGEST,
        "actions": actions,
    }
    view = EconomicEventView(**view_payload, view_digest=_digest(view_payload))
    authority_payload = {
        "authority_version": "authorized-economic-event-view/1",
        "tenant_id": TENANT_ID,
        "backtest_id": BACKTEST_ID,
        "ledger_id": LEDGER_ID,
        "backtest_spec_digest": SPEC_DIGEST,
        "ledger_policy_digest": TRADE_POLICY_DIGEST,
        "economics_policy_digest": ECONOMICS_POLICY_DIGEST,
        "dataset_id": DATASETS[instrument_id],
        "manifest_digest": MANIFESTS[instrument_id],
        "instrument_id": instrument_id,
        "quantitative_claims_status": "ineligible_unverified_coverage",
        "limitations": M5_UNVERIFIED_LIMITATIONS,
        "view": view,
    }
    return AuthorizedEconomicEventView(
        **authority_payload, authority_digest=_digest(authority_payload)
    )


def _fill(*, order_id: UUID = DEFAULT_ORDER_ID, executed_at: datetime) -> ExecutionFill:
    order = SimulationOrder(
        order_id=order_id,
        instrument_id=INSTRUMENT_A,
        side=SimulationSide.SELL,
        requested_quantity=Decimal("2"),
        raw_open=Decimal("13"),
        execution_at=executed_at,
    )
    return ExecutionFill(
        order_digest=order.digest,
        order_id=order.order_id,
        instrument_id=order.instrument_id,
        side=order.side,
        currency="USD",
        executed_at=order.execution_at,
        requested_quantity=Decimal("2"),
        executed_quantity=Decimal("2"),
        raw_open=Decimal("13"),
        fill_price=Decimal("12"),
        raw_notional=Decimal("26"),
        fill_notional=Decimal("24"),
        slippage_amount=Decimal("2"),
        commission_fee=Decimal("1"),
        tax=Decimal("1"),
        cost_basis_removed=Decimal("10"),
        realized_pnl=Decimal("14"),
        cash_delta=Decimal("22"),
    )


def _conversion(
    action: CorporateActionProjection,
    view: AuthorizedEconomicEventView,
    *,
    quantity: Decimal,
    prior_receipt: str,
) -> AuthorizedCorporateCashConversion:
    unit = (
        action.cash_amount
        if action.application is CorporateActionApplication.DIVIDEND_PAYMENT
        else action.cash_consideration
    )
    assert unit is not None and action.currency is not None
    source_amount = quantity * unit
    projection_payload = {
        "projection_version": "fx-rate-projection/1",
        "dataset_id": view.dataset_id,
        "manifest_digest": view.manifest_digest,
        "instrument_id": view.instrument_id,
        "functional_currency": "USD",
        "source_currency": "USD",
        "event_at": view.view.as_of,
        "max_staleness_seconds": 3600,
        "mode": FxConversionMode.IDENTITY,
        "rate": Decimal("1"),
        "observation_id": None,
        "observation_revision": None,
        "observed_at": None,
        "available_at": None,
        "observation_digest": None,
        "source_digest": None,
        "rate_convention": "quote_units_per_base_unit",
    }
    projection = FxRateProjection(
        **projection_payload, projection_digest=_digest(projection_payload)
    )
    conversion_payload = {
        "conversion_version": "fx-conversion/1",
        "fx_projection_digest": projection.projection_digest,
        "source_currency": "USD",
        "functional_currency": "USD",
        "source_amount": source_amount,
        "functional_amount": source_amount,
        "money_quantum": Decimal("0.01"),
        "rounding": "ROUND_HALF_EVEN",
    }
    conversion = FxConversion(**conversion_payload, conversion_digest=_digest(conversion_payload))
    checkpoint_id = UUID(int=901)
    checkpoint_digest = "8" * 64
    quantity_source_digest = "9" * 64
    ledger_receipt_id = "7" * 64
    quantity_basis = (
        "entitled_quantity"
        if action.application is CorporateActionApplication.DIVIDEND_PAYMENT
        else "position_quantity"
    )
    basis_payload = {
        "basis_version": "corporate-cash-ledger-basis/1",
        "tenant_id": TENANT_ID,
        "backtest_id": BACKTEST_ID,
        "ledger_id": LEDGER_ID,
        "backtest_spec_digest": SPEC_DIGEST,
        "ledger_policy_digest": TRADE_POLICY_DIGEST,
        "economics_policy_digest": ECONOMICS_POLICY_DIGEST,
        "checkpoint_id": checkpoint_id,
        "checkpoint_digest": checkpoint_digest,
        "checkpoint_generation": 1,
        "prior_session_receipt_digest": prior_receipt,
        "instrument_id": view.instrument_id,
        "action_id": action.action_id,
        "application": action.application,
        "session_date": action.session_date,
        "phase": EconomicPhase.POST_CLOSE,
        "quantity_basis": quantity_basis,
        "quantity": quantity,
        "quantity_source_digest": quantity_source_digest,
        "ledger_receipt_id": ledger_receipt_id,
    }
    authority_payload = {
        "authority_version": "authorized-corporate-cash-conversion/1",
        "tenant_id": TENANT_ID,
        "backtest_id": BACKTEST_ID,
        "ledger_id": LEDGER_ID,
        "backtest_spec_digest": SPEC_DIGEST,
        "ledger_policy_digest": TRADE_POLICY_DIGEST,
        "economics_policy_digest": ECONOMICS_POLICY_DIGEST,
        "dataset_id": view.dataset_id,
        "manifest_digest": view.manifest_digest,
        "instrument_id": view.instrument_id,
        "quantitative_claims_status": "ineligible_unverified_coverage",
        "limitations": M5_UNVERIFIED_LIMITATIONS,
        "purpose": "corporate_action_cash",
        "ledger_basis_digest": canonical_digest(basis_payload),
        "checkpoint_id": checkpoint_id,
        "checkpoint_digest": checkpoint_digest,
        "checkpoint_generation": 1,
        "prior_session_receipt_digest": prior_receipt,
        "quantity_source_digest": quantity_source_digest,
        "ledger_receipt_id": ledger_receipt_id,
        "event_authority_digest": view.authority_digest,
        "event_view_digest": view.view.view_digest,
        "action_id": action.action_id,
        "action_revision": action.revision,
        "application": action.application,
        "action_digest": action.action_digest,
        "action_projection_digest": action.projection_digest,
        "quantity_basis": quantity_basis,
        "expected_quantity": quantity,
        "unit_cash_amount": unit,
        "source_currency": action.currency,
        "session_date": action.session_date,
        "phase": EconomicPhase.POST_CLOSE,
        "event_at": view.view.as_of,
        "projection": projection,
        "conversion": conversion,
    }
    return AuthorizedCorporateCashConversion(
        **authority_payload, authority_digest=_digest(authority_payload)
    )


def _transition(
    *,
    session_id: UUID,
    session_date: date,
    pre_actions: dict[UUID, tuple[CorporateActionProjection, ...]] | None = None,
    fills: tuple[ExecutionFill, ...] = (),
    post_actions: dict[UUID, tuple[CorporateActionProjection, ...]] | None = None,
    conversions: tuple[AuthorizedCorporateCashConversion, ...] = (),
) -> EconomicSessionTransitionV2:
    pre_actions = pre_actions or {}
    post_actions = post_actions or {}
    payload = {
        "transition_version": "economic-session-transition/2",
        "session_id": session_id,
        "session_date": session_date,
        "pre_open_views": tuple(
            _view(item, session_date, EconomicPhase.PRE_OPEN, pre_actions.get(item, ()))
            for item in (INSTRUMENT_A, INSTRUMENT_B)
        ),
        "trade_fills": fills,
        "post_close_views": tuple(
            _view(item, session_date, EconomicPhase.POST_CLOSE, post_actions.get(item, ()))
            for item in (INSTRUMENT_A, INSTRUMENT_B)
        ),
        "cash_conversions": conversions,
    }
    return EconomicSessionTransitionV2(**payload, request_digest=_digest(payload))


def _commit(state, transition):
    return _commit_simulation_economic_session_v2(
        state,
        transition,
        expected_state_hash=state.state_hash,
        expected_policy_digest=state.policy.digest,
        expected_request_digest=transition.request_digest,
    )


def test_unified_session_orders_pre_fills_post_and_balances() -> None:
    state = _state()
    session_date = date(2024, 1, 2)
    split = _projection(
        CorporateActionApplication.SPLIT,
        instrument_id=INSTRUMENT_A,
        session_date=session_date,
        action_id=SPLIT_ID,
    )
    entitlement = _projection(
        CorporateActionApplication.DIVIDEND_ENTITLEMENT,
        instrument_id=INSTRUMENT_B,
        session_date=session_date,
        action_id=DIVIDEND_ID,
    )
    payment = _projection(
        CorporateActionApplication.DIVIDEND_PAYMENT,
        instrument_id=INSTRUMENT_B,
        session_date=session_date,
        action_id=DIVIDEND_ID,
        action_digest=entitlement.action_digest,
    )
    payment_view = _view(INSTRUMENT_B, session_date, EconomicPhase.POST_CLOSE, (payment,))
    conversion = _conversion(
        payment,
        payment_view,
        quantity=Decimal("4"),
        prior_receipt=state.last_session_receipt_hash,
    )
    transition = _transition(
        session_id=UUID(int=701),
        session_date=session_date,
        pre_actions={INSTRUMENT_A: (split,), INSTRUMENT_B: (entitlement,)},
        fills=(_fill(executed_at=datetime(2024, 1, 2, 12, tzinfo=UTC)),),
        post_actions={INSTRUMENT_B: (payment,)},
        conversions=(conversion,),
    )
    result = _commit(state, transition)

    assert tuple(event.stage for event in result.appended_events) == (
        EconomicStageV2.PRE_OPEN,
        EconomicStageV2.PRE_OPEN,
        EconomicStageV2.TRADE_FILL,
        EconomicStageV2.POST_CLOSE,
    )
    assert result.state.cash == Decimal("1027")
    assert result.state.realized_pnl == Decimal("14")
    assert result.state.position(INSTRUMENT_A).quantity == Decimal("18")
    assert result.state.position(INSTRUMENT_A).cost_basis == Decimal("90")
    assert result.state.entitlements[0].payment_status is DividendPaymentStatusV2.PAID
    assert result.state.quantitative_claims_status == "ineligible_unverified_coverage"
    assert result.state.limitations == M5_UNVERIFIED_LIMITATIONS
    for event in result.appended_events:
        if event.postings:
            debit = sum(
                posting.amount
                for posting in event.postings
                if posting.direction is PostingDirectionV2.DEBIT
            )
            credit = sum(
                posting.amount
                for posting in event.postings
                if posting.direction is PostingDirectionV2.CREDIT
            )
            assert debit == credit


@pytest.mark.parametrize("case", ["omission", "duplicate", "view_order", "fill_order"])
def test_transition_requires_complete_unique_canonical_inputs(case: str) -> None:
    session_date = date(2024, 1, 2)
    payload = _transition(session_id=UUID(int=710), session_date=session_date).model_dump(
        mode="python", exclude={"request_digest"}
    )
    if case == "omission":
        payload["post_close_views"] = payload["post_close_views"][:-1]
    elif case == "duplicate":
        payload["pre_open_views"] = (
            payload["pre_open_views"][0],
            payload["pre_open_views"][0],
        )
    elif case == "view_order":
        payload["pre_open_views"] = tuple(reversed(payload["pre_open_views"]))
        payload["post_close_views"] = tuple(reversed(payload["post_close_views"]))
    else:
        payload["trade_fills"] = (
            _fill(
                order_id=UUID(int=612),
                executed_at=datetime(2024, 1, 2, 13, tzinfo=UTC),
            ),
            _fill(
                order_id=UUID(int=611),
                executed_at=datetime(2024, 1, 2, 12, tzinfo=UTC),
            ),
        )
    with pytest.raises(ValidationError):
        EconomicSessionTransitionV2(**payload, request_digest=_digest(payload))


def test_pre_open_delisting_atomically_blocks_same_session_fill() -> None:
    state = _state()
    session_date = date(2024, 1, 2)
    block = _projection(
        CorporateActionApplication.DELISTING,
        instrument_id=INSTRUMENT_A,
        session_date=session_date,
        action_id=DELISTING_ID,
    )
    transition = _transition(
        session_id=UUID(int=720),
        session_date=session_date,
        pre_actions={INSTRUMENT_A: (block,)},
        fills=(_fill(executed_at=datetime(2024, 1, 2, 12, tzinfo=UTC)),),
    )
    state_before = state.model_dump(mode="python")
    transition_before = transition.model_dump(mode="python")
    with pytest.raises(SimulationEconomicLedgerError, match="delisted"):
        _commit(state, transition)
    assert state.model_dump(mode="python") == state_before
    assert transition.model_dump(mode="python") == transition_before


def test_delisting_settlement_closes_position_and_realizes_pnl() -> None:
    state = _state()
    block_date = date(2024, 1, 2)
    block = _projection(
        CorporateActionApplication.DELISTING,
        instrument_id=INSTRUMENT_A,
        session_date=block_date,
        action_id=DELISTING_ID,
    )
    state = _commit(
        state,
        _transition(
            session_id=UUID(int=730),
            session_date=block_date,
            pre_actions={INSTRUMENT_A: (block,)},
        ),
    ).state
    settle_date = date(2024, 1, 3)
    settlement = _projection(
        CorporateActionApplication.DELISTING_SETTLEMENT,
        instrument_id=INSTRUMENT_A,
        session_date=settle_date,
        action_id=DELISTING_ID,
        action_digest=block.action_digest,
    )
    settlement_view = _view(INSTRUMENT_A, settle_date, EconomicPhase.POST_CLOSE, (settlement,))
    conversion = _conversion(
        settlement,
        settlement_view,
        quantity=Decimal("10"),
        prior_receipt=state.last_session_receipt_hash,
    )
    result = _commit(
        state,
        _transition(
            session_id=UUID(int=731),
            session_date=settle_date,
            post_actions={INSTRUMENT_A: (settlement,)},
            conversions=(conversion,),
        ),
    )
    position = result.state.position(INSTRUMENT_A)
    assert (position.quantity, position.cost_basis) == (Decimal("0"), Decimal("0"))
    assert position.fill_eligibility is FillEligibilityV2.BLOCKED_DELISTED
    assert result.state.cash == Decimal("1120")
    assert result.state.realized_pnl == Decimal("20")
    assert result.appended_events[0].kind is EconomicEventKindV2.DELISTING_SETTLEMENT


@pytest.mark.parametrize(
    ("block_revision", "settlement_revision", "settlement_digest", "accepted"),
    [
        (2, 1, "5" * 64, False),
        (2, 2, "6" * 64, False),
        (2, 3, "7" * 64, True),
    ],
)
def test_delisting_settlement_enforces_revision_lineage(
    block_revision: int,
    settlement_revision: int,
    settlement_digest: str,
    accepted: bool,
) -> None:
    state = _state()
    block_date = date(2024, 1, 2)
    block = _projection(
        CorporateActionApplication.DELISTING,
        instrument_id=INSTRUMENT_A,
        session_date=block_date,
        action_id=DELISTING_ID,
        revision=block_revision,
        action_digest="5" * 64,
    )
    state = _commit(
        state,
        _transition(
            session_id=UUID(int=732),
            session_date=block_date,
            pre_actions={INSTRUMENT_A: (block,)},
        ),
    ).state
    settle_date = date(2024, 1, 3)
    settlement = _projection(
        CorporateActionApplication.DELISTING_SETTLEMENT,
        instrument_id=INSTRUMENT_A,
        session_date=settle_date,
        action_id=DELISTING_ID,
        revision=settlement_revision,
        action_digest=settlement_digest,
    )
    settlement_view = _view(INSTRUMENT_A, settle_date, EconomicPhase.POST_CLOSE, (settlement,))
    conversion = _conversion(
        settlement,
        settlement_view,
        quantity=Decimal("10"),
        prior_receipt=state.last_session_receipt_hash,
    )
    transition = _transition(
        session_id=UUID(int=733),
        session_date=settle_date,
        post_actions={INSTRUMENT_A: (settlement,)},
        conversions=(conversion,),
    )
    if accepted:
        assert _commit(state, transition).state.position(INSTRUMENT_A).quantity == 0
    else:
        with pytest.raises(SimulationEconomicLedgerError, match="precedes block"):
            _commit(state, transition)


def test_zero_entitlement_and_zero_position_are_recorded_without_fake_cash() -> None:
    policy = _policy().model_copy(
        update={
            "initial_positions": (
                EconomicPositionV2(
                    instrument_id=INSTRUMENT_A,
                    quantity=Decimal("0"),
                    cost_basis=Decimal("0"),
                    currency="USD",
                ),
                EconomicPositionV2(
                    instrument_id=INSTRUMENT_B,
                    quantity=Decimal("0"),
                    cost_basis=Decimal("0"),
                    currency="USD",
                ),
            )
        }
    )
    state = _state(policy)
    first_date = date(2024, 1, 2)
    entitlement = _projection(
        CorporateActionApplication.DIVIDEND_ENTITLEMENT,
        instrument_id=INSTRUMENT_A,
        session_date=first_date,
        action_id=DIVIDEND_ID,
    )
    block = _projection(
        CorporateActionApplication.DELISTING,
        instrument_id=INSTRUMENT_B,
        session_date=first_date,
        action_id=DELISTING_ID,
    )
    state = _commit(
        state,
        _transition(
            session_id=UUID(int=735),
            session_date=first_date,
            pre_actions={INSTRUMENT_A: (entitlement,), INSTRUMENT_B: (block,)},
        ),
    ).state

    second_date = date(2024, 1, 3)
    payment = _projection(
        CorporateActionApplication.DIVIDEND_PAYMENT,
        instrument_id=INSTRUMENT_A,
        session_date=second_date,
        action_id=DIVIDEND_ID,
        revision=2,
        action_digest="1" * 64,
    )
    settlement = _projection(
        CorporateActionApplication.DELISTING_SETTLEMENT,
        instrument_id=INSTRUMENT_B,
        session_date=second_date,
        action_id=DELISTING_ID,
        revision=2,
        action_digest="2" * 64,
    )
    result = _commit(
        state,
        _transition(
            session_id=UUID(int=736),
            session_date=second_date,
            post_actions={INSTRUMENT_A: (payment,), INSTRUMENT_B: (settlement,)},
        ),
    )
    assert tuple(event.kind for event in result.appended_events) == (
        EconomicEventKindV2.DIVIDEND_PAYMENT_ZERO,
        EconomicEventKindV2.DELISTING_SETTLEMENT_ZERO,
    )
    assert all(not event.postings and event.conversion is None for event in result.appended_events)
    assert result.state.cash == policy.initial_cash
    assert result.state.realized_pnl == Decimal("0")
    assert result.state.entitlements[0].payment_status is DividendPaymentStatusV2.PAID


def test_dividend_payment_accepts_later_revision_only_when_terms_are_unchanged() -> None:
    state = _state()
    entitlement_date = date(2024, 1, 2)
    entitlement = _projection(
        CorporateActionApplication.DIVIDEND_ENTITLEMENT,
        instrument_id=INSTRUMENT_B,
        session_date=entitlement_date,
        action_id=DIVIDEND_ID,
        revision=1,
    )
    state = _commit(
        state,
        _transition(
            session_id=UUID(int=737),
            session_date=entitlement_date,
            pre_actions={INSTRUMENT_B: (entitlement,)},
        ),
    ).state
    payment_date = date(2024, 1, 3)
    payment = _projection(
        CorporateActionApplication.DIVIDEND_PAYMENT,
        instrument_id=INSTRUMENT_B,
        session_date=payment_date,
        action_id=DIVIDEND_ID,
        revision=2,
        action_digest="3" * 64,
    )
    payment_view = _view(INSTRUMENT_B, payment_date, EconomicPhase.POST_CLOSE, (payment,))
    conversion = _conversion(
        payment,
        payment_view,
        quantity=Decimal("4"),
        prior_receipt=state.last_session_receipt_hash,
    )
    result = _commit(
        state,
        _transition(
            session_id=UUID(int=738),
            session_date=payment_date,
            post_actions={INSTRUMENT_B: (payment,)},
            conversions=(conversion,),
        ),
    )
    assert result.state.cash == Decimal("1005.00")

    bad_payment = _projection(
        CorporateActionApplication.DIVIDEND_PAYMENT,
        instrument_id=INSTRUMENT_B,
        session_date=payment_date,
        action_id=DIVIDEND_ID,
        revision=2,
        action_digest="4" * 64,
        cash_amount="9.99",
    )
    with pytest.raises(SimulationEconomicLedgerError, match="matching unpaid entitlement"):
        _commit(
            state,
            _transition(
                session_id=UUID(int=739),
                session_date=payment_date,
                post_actions={INSTRUMENT_B: (bad_payment,)},
            ),
        )


def test_fill_money_is_rejected_early_when_not_quantized() -> None:
    state = _state()
    fill_payload = _fill(
        executed_at=datetime(2024, 1, 2, 12, tzinfo=UTC)
    ).model_dump(mode="python")
    fill_payload["commission_fee"] = Decimal("1.001")
    fill_payload["cash_delta"] = Decimal("21.999")
    fill = ExecutionFill.model_validate(fill_payload)
    with pytest.raises(SimulationEconomicLedgerError, match="not quantized"):
        _commit(
            state,
            _transition(
                session_id=UUID(int=7391),
                session_date=date(2024, 1, 2),
                fills=(fill,),
            ),
        )


def test_sell_of_zero_cost_position_has_no_zero_posting() -> None:
    policy = _policy().model_copy(
        update={
            "initial_positions": (
                EconomicPositionV2(
                    instrument_id=INSTRUMENT_A,
                    quantity=Decimal("10"),
                    cost_basis=Decimal("0"),
                    currency="USD",
                ),
                _policy().initial_positions[1],
            )
        }
    )
    state = _state(policy)
    fill_payload = _fill(
        executed_at=datetime(2024, 1, 2, 12, tzinfo=UTC)
    ).model_dump(mode="python")
    fill_payload["cost_basis_removed"] = Decimal("0")
    fill_payload["realized_pnl"] = Decimal("24")
    fill = ExecutionFill.model_validate(fill_payload)
    result = _commit(
        state,
        _transition(
            session_id=UUID(int=7392),
            session_date=date(2024, 1, 2),
            fills=(fill,),
        ),
    )
    assert all(posting.amount > 0 for posting in result.appended_events[0].postings)


def test_conversion_quantum_must_equal_ledger_policy() -> None:
    state = _state()
    session_date = date(2024, 1, 2)
    entitlement = _projection(
        CorporateActionApplication.DIVIDEND_ENTITLEMENT,
        instrument_id=INSTRUMENT_B,
        session_date=session_date,
        action_id=DIVIDEND_ID,
    )
    payment = _projection(
        CorporateActionApplication.DIVIDEND_PAYMENT,
        instrument_id=INSTRUMENT_B,
        session_date=session_date,
        action_id=DIVIDEND_ID,
        action_digest=entitlement.action_digest,
    )
    payment_view = _view(INSTRUMENT_B, session_date, EconomicPhase.POST_CLOSE, (payment,))
    conversion = _conversion(
        payment,
        payment_view,
        quantity=Decimal("4"),
        prior_receipt=state.last_session_receipt_hash,
    )
    payload = conversion.model_dump(mode="python", exclude={"authority_digest"})
    fx = dict(payload["conversion"])
    fx["money_quantum"] = Decimal("0.10")
    fx["conversion_digest"] = _digest(
        {key: value for key, value in fx.items() if key != "conversion_digest"}
    )
    payload["conversion"] = fx
    wrong_quantum = AuthorizedCorporateCashConversion(
        **payload, authority_digest=_digest(payload)
    )
    with pytest.raises(SimulationEconomicLedgerError, match="not bound"):
        _commit(
            state,
            _transition(
                session_id=UUID(int=7393),
                session_date=session_date,
                pre_actions={INSTRUMENT_B: (entitlement,)},
                post_actions={INSTRUMENT_B: (payment,)},
                conversions=(wrong_quantum,),
            ),
        )


def test_replay_conflicts_and_full_logical_action_key() -> None:
    state = _state()
    session_date = date(2024, 1, 2)
    split = _projection(
        CorporateActionApplication.SPLIT,
        instrument_id=INSTRUMENT_A,
        session_date=session_date,
        action_id=SPLIT_ID,
    )
    transition = _transition(
        session_id=UUID(int=740),
        session_date=session_date,
        pre_actions={INSTRUMENT_A: (split,)},
    )
    state = _commit(state, transition).state
    replay = _commit(state, transition)
    assert replay.replay_status == "replayed"
    assert replay.appended_events == ()
    assert replay.state == state

    with pytest.raises(EconomicSessionConflictError, match="session ID"):
        _commit(
            state,
            _transition(session_id=UUID(int=740), session_date=date(2024, 1, 3)),
        )
    with pytest.raises(EconomicSessionConflictError, match="session date"):
        _commit(
            state,
            _transition(session_id=UUID(int=741), session_date=session_date),
        )
    repeated = _projection(
        CorporateActionApplication.SPLIT,
        instrument_id=INSTRUMENT_A,
        session_date=date(2024, 1, 3),
        action_id=SPLIT_ID,
        revision=2,
    )
    with pytest.raises(EconomicSessionConflictError, match="logical corporate-action key"):
        _commit(
            state,
            _transition(
                session_id=UUID(int=742),
                session_date=date(2024, 1, 3),
                pre_actions={INSTRUMENT_A: (repeated,)},
            ),
        )


def test_external_anchors_and_history_defeat_self_consistent_forgery() -> None:
    state = _state()
    transition = _transition(session_id=UUID(int=750), session_date=date(2024, 1, 2))
    with pytest.raises(EconomicSessionConflictError, match="request precondition"):
        _commit_simulation_economic_session_v2(
            state,
            transition,
            expected_state_hash=state.state_hash,
            expected_policy_digest=state.policy.digest,
            expected_request_digest="9" * 64,
        )
    result = _commit(state, transition)

    forged = result.state.model_dump(mode="python")
    forged["cash"] = Decimal("9999")
    forged["state_hash"] = _digest(
        {key: value for key, value in forged.items() if key != "state_hash"}
    )
    with pytest.raises(ValidationError, match="full history"):
        SimulationEconomicLedgerStateV2.model_validate(forged)

    tampered = result.state.model_copy(deep=True)
    object.__setattr__(tampered.session_receipts[0].transition, "session_date", date(2030, 1, 1))
    with pytest.raises(SimulationEconomicLedgerError, match="state is invalid"):
        _commit(
            tampered,
            _transition(session_id=UUID(int=751), session_date=date(2024, 1, 3)),
        )


def test_actual_conversion_contract_private_reducer_and_claims_taint() -> None:
    assert ledger_module.AuthorizedCorporateCashConversion is AuthorizedCorporateCashConversion
    assert not hasattr(ledger_module, "AuthorizedCorporateCashConversionV2")
    assert not hasattr(ledger_module, "reduce_economic_session_v2")
    assert "_derive_transition" not in ledger_module.__all__
    assert ledger_module._derive_transition.__name__.startswith("_")

    state = _state()
    assert state.quantitative_claims_status == "ineligible_unverified_coverage"
    assert state.limitations == M5_UNVERIFIED_LIMITATIONS


@pytest.mark.parametrize("anchor", [None, True, "A" * 64, "0" * 63])
def test_hash_anchors_are_exact_lowercase_sha256(anchor) -> None:
    policy = _policy()
    with pytest.raises(SimulationEconomicLedgerError, match="lowercase SHA-256"):
        create_simulation_economic_ledger_v2(policy, expected_policy_digest=anchor)

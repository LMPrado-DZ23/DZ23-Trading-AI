from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal, Inexact, Rounded, localcontext
from pathlib import Path
from time import perf_counter
from uuid import UUID

import pytest
from hypothesis import given, settings, strategies as st
from pydantic import ValidationError

import tradingagents.platform.backtest.simulation_ledger as simulation_ledger_module
from tradingagents.platform.backtest.contracts import canonical_digest, canonical_json_bytes
from tradingagents.platform.backtest.simulation_ledger import (
    LEDGER_GENESIS_HASH,
    M5_2B1_SCOPE,
    IdempotencyConflictError,
    InstrumentSimulationRule,
    InsufficientCashError,
    LedgerAccount,
    PostingDirection,
    ShortSaleError,
    SimulationBatch,
    SimulationBatchResult,
    SimulationLedgerPolicy,
    SimulationLedgerState,
    SimulationOrder,
    SimulationRunResult,
    SimulationSide,
    create_simulation_ledger,
    execute_simulation_batch as _production_execute_simulation_batch,
    execute_simulation_run,
)

LEDGER_ID = UUID("10000000-0000-0000-0000-000000000001")
STOCK_A = UUID("20000000-0000-0000-0000-000000000001")
STOCK_B = UUID("20000000-0000-0000-0000-000000000002")
OPENED_AT = datetime(2026, 1, 2, 12, tzinfo=UTC)
EXECUTED_AT = OPENED_AT + timedelta(days=1)


def _policy(**overrides) -> SimulationLedgerPolicy:
    values = {
        "ledger_id": LEDGER_ID,
        "opened_at": OPENED_AT,
        "base_currency": "USD",
        "initial_cash": "1000.00",
        "money_quantum": "0.01",
        "commission_bps": "10",
        "tax_bps": "5",
        "slippage_bps": "100",
        "instrument_rules": (
            InstrumentSimulationRule(
                instrument_id=STOCK_A,
                currency="USD",
                lot_size="0.1",
                price_tick="0.05",
            ),
            InstrumentSimulationRule(
                instrument_id=STOCK_B,
                currency="USD",
                lot_size="1",
                price_tick="0.01",
            ),
        ),
    }
    return SimulationLedgerPolicy(**(values | overrides))


def _order(
    number: int,
    *,
    instrument_id: UUID = STOCK_A,
    side: SimulationSide = SimulationSide.BUY,
    quantity: str = "2.34",
    raw_open: str = "10",
    execution_at: datetime = EXECUTED_AT,
) -> SimulationOrder:
    return SimulationOrder(
        order_id=UUID(f"30000000-0000-0000-0000-{number:012d}"),
        instrument_id=instrument_id,
        side=side,
        requested_quantity=quantity,
        raw_open=raw_open,
        execution_at=execution_at,
    )


def _batch(number: int, *orders: SimulationOrder) -> SimulationBatch:
    return SimulationBatch(
        batch_id=UUID(f"40000000-0000-0000-0000-{number:012d}"),
        session_id=UUID(f"50000000-0000-0000-0000-{number:012d}"),
        execution_at=orders[0].execution_at,
        orders=orders,
    )


def execute_simulation_batch(
    state: SimulationLedgerState,
    policy: SimulationLedgerPolicy,
    batch: SimulationBatch,
    *,
    expected_last_event_hash: str | None = None,
    expected_request_digest: str | None = None,
):
    return _production_execute_simulation_batch(
        state,
        policy,
        batch,
        expected_last_event_hash=(
            state.last_event_hash if expected_last_event_hash is None else expected_last_event_hash
        ),
        expected_request_digest=(
            batch.request_digest if expected_request_digest is None else expected_request_digest
        ),
    )


def _assert_balanced(state: SimulationLedgerState) -> None:
    for event in state.events:
        debit = sum(
            (
                posting.amount
                for posting in event.postings
                if posting.direction is PostingDirection.DEBIT
            ),
            Decimal("0"),
        )
        credit = sum(
            (
                posting.amount
                for posting in event.postings
                if posting.direction is PostingDirection.CREDIT
            ),
            Decimal("0"),
        )
        assert debit == credit


def test_contracts_are_versioned_frozen_exact_and_explicitly_m5_2b1() -> None:
    policy = _policy()
    assert policy.policy_version == "simulation-ledger-policy/1"
    assert policy.milestone_scope == M5_2B1_SCOPE
    assert policy.digest == _policy().digest
    with pytest.raises(ValidationError):
        policy.initial_cash = Decimal("1")
    with pytest.raises(ValidationError):
        SimulationOrder(
            order_id=UUID("30000000-0000-0000-0000-000000000099"),
            instrument_id=STOCK_A,
            side="buy",
            requested_quantity=1.0,
            raw_open="10",
            execution_at=EXECUTED_AT,
            unknown="rejected",
        )


def test_policy_rejects_ambiguous_quantization_and_cross_currency() -> None:
    with pytest.raises(ValidationError, match="quantized"):
        _policy(initial_cash="1000.003")
    reversed_rules = tuple(reversed(_policy().instrument_rules))
    with pytest.raises(ValidationError, match="sorted"):
        _policy(instrument_rules=reversed_rules)
    foreign = _policy().instrument_rules[0].model_copy(update={"currency": "BRL"})
    with pytest.raises(ValidationError, match="cross-currency"):
        _policy(instrument_rules=(foreign, _policy().instrument_rules[1]))


def test_opening_ledger_is_balanced_hash_chained_and_reconstructable() -> None:
    state = create_simulation_ledger(_policy())
    assert state.cash == Decimal("1000.00")
    assert state.realized_pnl == 0
    assert state.positions == ()
    assert state.events[0].previous_hash == LEDGER_GENESIS_HASH
    assert state.last_event_hash == state.events[0].event_hash
    assert state.events[0].postings[0].account is LedgerAccount.CASH
    assert state.events[0].postings[1].account is LedgerAccount.OPENING_EQUITY
    _assert_balanced(state)
    assert SimulationLedgerState.model_validate(state.model_dump(mode="python")) == state


def test_buy_applies_lot_tick_money_slippage_fee_and_tax() -> None:
    policy = _policy()
    initial = create_simulation_ledger(policy)
    result = execute_simulation_batch(initial, policy, _batch(1, _order(1)))
    fill = result.appended_events[0].fill
    assert fill is not None
    assert fill.executed_quantity == Decimal("2.3")
    assert fill.fill_price == Decimal("10.10")
    assert fill.raw_notional == Decimal("23.00")
    assert fill.fill_notional == Decimal("23.23")
    assert fill.slippage_amount == Decimal("0.23")
    assert fill.commission_fee == Decimal("0.03")
    assert fill.tax == Decimal("0.02")
    assert fill.cash_delta == Decimal("-23.28")
    assert result.state.cash == Decimal("976.72")
    assert result.state.position(STOCK_A) is not None
    assert result.state.position(STOCK_A).quantity == Decimal("2.3")
    assert result.state.position(STOCK_A).cost_basis == Decimal("23.23")
    assert result.state.position(STOCK_A).average_cost == Decimal("10.1")
    _assert_balanced(result.state)


def test_sell_uses_average_cost_and_records_gross_realized_pnl_separately() -> None:
    policy = _policy()
    bought = execute_simulation_batch(
        create_simulation_ledger(policy), policy, _batch(1, _order(1))
    ).state
    sold = execute_simulation_batch(
        bought,
        policy,
        _batch(
            2,
            _order(
                2,
                side=SimulationSide.SELL,
                quantity="1.1",
                raw_open="12",
                execution_at=EXECUTED_AT + timedelta(days=1),
            ),
        ),
    )
    fill = sold.appended_events[0].fill
    assert fill is not None
    assert fill.fill_price == Decimal("11.85")
    assert fill.raw_notional == Decimal("13.20")
    assert fill.fill_notional == Decimal("13.04")
    assert fill.slippage_amount == Decimal("0.16")
    assert fill.commission_fee == Decimal("0.02")
    assert fill.tax == Decimal("0.01")
    assert fill.cost_basis_removed == Decimal("11.11")
    assert fill.realized_pnl == Decimal("1.93")
    assert sold.state.cash == Decimal("989.73")
    assert sold.state.realized_pnl == Decimal("1.93")
    assert sold.state.position(STOCK_A).quantity == Decimal("1.2")
    assert sold.state.position(STOCK_A).cost_basis == Decimal("12.12")
    accounts = {posting.account for posting in sold.appended_events[0].postings}
    assert accounts == {
        LedgerAccount.CASH,
        LedgerAccount.COMMISSION_EXPENSE,
        LedgerAccount.TAX_EXPENSE,
        LedgerAccount.POSITION_COST,
        LedgerAccount.REALIZED_PNL,
    }
    _assert_balanced(sold.state)


def test_full_disposal_removes_rounding_residue_from_cost_basis() -> None:
    policy = _policy()
    bought = execute_simulation_batch(
        create_simulation_ledger(policy), policy, _batch(1, _order(1))
    ).state
    sold = execute_simulation_batch(
        bought,
        policy,
        _batch(
            2,
            _order(
                2,
                side=SimulationSide.SELL,
                quantity="2.3",
                raw_open="10",
                execution_at=EXECUTED_AT + timedelta(days=1),
            ),
        ),
    )
    assert sold.state.position(STOCK_A) is None
    assert sold.appended_events[0].fill.cost_basis_removed == Decimal("23.23")


def test_no_short_nonnegative_cash_and_sub_lot_orders_fail_closed() -> None:
    policy = _policy(initial_cash="10.00")
    initial = create_simulation_ledger(policy)
    with pytest.raises(InsufficientCashError):
        execute_simulation_batch(initial, policy, _batch(1, _order(1, quantity="2")))
    with pytest.raises(ShortSaleError):
        execute_simulation_batch(
            initial,
            policy,
            _batch(2, _order(2, side=SimulationSide.SELL, quantity="0.1")),
        )
    with pytest.raises(ValueError, match="below one executable lot"):
        execute_simulation_batch(initial, policy, _batch(3, _order(3, quantity="0.09")))
    assert initial.cash == Decimal("10.00")
    assert len(initial.events) == 1


def test_execution_cannot_precede_ledger_opening() -> None:
    policy = _policy()
    state = create_simulation_ledger(policy)
    for invalid_time in (OPENED_AT - timedelta(microseconds=1), OPENED_AT):
        invalid_order = _order(1, execution_at=invalid_time)
        with pytest.raises(ValueError, match="follow ledger opening"):
            execute_simulation_batch(state, policy, _batch(1, invalid_order))


def test_new_batch_cannot_reorder_an_already_committed_timestamp() -> None:
    policy = _policy()
    first = execute_simulation_batch(create_simulation_ledger(policy), policy, _batch(1, _order(1)))
    same_time = _order(2, execution_at=EXECUTED_AT)
    with pytest.raises(ValueError, match="new batch must execute after"):
        execute_simulation_batch(first.state, policy, _batch(2, same_time))


def test_batch_binds_one_complete_execution_session() -> None:
    first = _order(1)
    second = _order(2, execution_at=EXECUTED_AT + timedelta(seconds=1))
    with pytest.raises(ValidationError, match="one complete execution session"):
        _batch(1, first, second)

    original = _batch(1, first)
    other_session = original.model_copy(
        update={"session_id": UUID("50000000-0000-0000-0000-000000000099")}
    )
    other_session = SimulationBatch.model_validate(other_session)
    assert original.request_digest != other_session.request_digest


def test_external_request_digest_rejects_a_partial_session_subset() -> None:
    policy = _policy(
        initial_cash="20.00",
        commission_bps="0",
        tax_bps="0",
        slippage_bps="0",
    )
    seeded = execute_simulation_batch(
        create_simulation_ledger(policy),
        policy,
        _batch(1, _order(1, quantity="2", raw_open="10")),
    )
    execution_at = EXECUTED_AT + timedelta(days=1)
    sell = _order(
        2,
        side=SimulationSide.SELL,
        quantity="1",
        raw_open="10",
        execution_at=execution_at,
    )
    buy = _order(3, quantity="1", raw_open="10", execution_at=execution_at)
    complete = _batch(2, sell, buy)
    partial = SimulationBatch(
        batch_id=complete.batch_id,
        session_id=complete.session_id,
        execution_at=complete.execution_at,
        orders=(sell,),
    )
    with pytest.raises(ValueError, match="externally anchored request digest"):
        execute_simulation_batch(
            seeded.state,
            policy,
            partial,
            expected_request_digest=complete.request_digest,
        )
    full_result = execute_simulation_batch(
        seeded.state,
        policy,
        complete,
        expected_request_digest=complete.request_digest,
    )
    assert full_result.state.cash == 0
    assert full_result.state.position(STOCK_A).quantity == 2


def test_external_request_digest_is_mandatory_well_formed_and_exact() -> None:
    policy = _policy()
    state = create_simulation_ledger(policy)
    batch = _batch(1, _order(1))
    for malformed in (None, "F" * 64, "0" * 63, 7):
        with pytest.raises(ValueError, match="expected_request_digest|lowercase SHA-256"):
            _production_execute_simulation_batch(
                state,
                policy,
                batch,
                expected_last_event_hash=state.last_event_hash,
                expected_request_digest=malformed,
            )
    with pytest.raises(ValueError, match="externally anchored request digest"):
        _production_execute_simulation_batch(
            state,
            policy,
            batch,
            expected_last_event_hash=state.last_event_hash,
            expected_request_digest="f" * 64,
        )

    with pytest.raises(ValueError, match="bind every batch ID exactly once"):
        execute_simulation_run(
            state,
            policy,
            (batch,),
            expected_last_event_hash=state.last_event_hash,
            expected_request_digests={},
        )


def test_later_batch_cannot_backdate_the_hash_chain() -> None:
    policy = _policy()
    first = execute_simulation_batch(
        create_simulation_ledger(policy),
        policy,
        _batch(1, _order(1, execution_at=EXECUTED_AT + timedelta(days=1))),
    )
    backdated = _order(2, execution_at=EXECUTED_AT)
    with pytest.raises(ValueError, match="committed ledger events"):
        execute_simulation_batch(first.state, policy, _batch(2, backdated))


def test_decimal_calculation_ignores_ambient_precision_rounding_and_traps() -> None:
    policy = _policy()
    state = create_simulation_ledger(policy)
    batch = _batch(1, _order(1))
    baseline = execute_simulation_batch(state, policy, batch)
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        repeated = execute_simulation_batch(state, policy, batch)
    assert canonical_json_bytes(repeated) == canonical_json_bytes(baseline)

    payload = baseline.state.model_dump(mode="python")
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        rehydrated = SimulationLedgerState.model_validate(payload)
    assert rehydrated == baseline.state


def test_accumulated_decimal_overflow_is_rejected_before_result_escape() -> None:
    policy = _policy(
        initial_cash="9e37",
        money_quantum="1",
        commission_bps="0",
        tax_bps="0",
        slippage_bps="0",
        instrument_rules=(
            InstrumentSimulationRule(
                instrument_id=STOCK_A,
                currency="USD",
                lot_size="1",
                price_tick="1",
            ),
        ),
    )
    bought = execute_simulation_batch(
        create_simulation_ledger(policy),
        policy,
        _batch(1, _order(1, quantity="1", raw_open="1")),
    )
    sell = _batch(
        2,
        _order(
            2,
            side=SimulationSide.SELL,
            quantity="1",
            raw_open="9e37",
            execution_at=EXECUTED_AT + timedelta(days=1),
        ),
    )
    with pytest.raises(ValueError, match="38 significant integer digits"):
        execute_simulation_batch(bought.state, policy, sell)


def test_batch_permutation_has_identical_digest_events_and_state() -> None:
    policy = _policy()
    state = create_simulation_ledger(policy)
    first = _order(20, instrument_id=STOCK_B, quantity="3", raw_open="4.321")
    second = _order(10, instrument_id=STOCK_A, quantity="1.0", raw_open="8.02")
    normal = _batch(7, first, second)
    permuted = _batch(7, second, first)
    assert normal.request_digest == permuted.request_digest
    assert normal.digest == permuted.digest == normal.request_digest
    normal_result = execute_simulation_batch(state, policy, normal)
    permuted_result = execute_simulation_batch(state, policy, permuted)
    assert normal_result.state == permuted_result.state
    assert normal_result.appended_events == permuted_result.appended_events
    assert normal_result.state.last_event_hash == permuted_result.state.last_event_hash


def test_exact_replay_is_idempotent_and_payload_mismatch_is_rejected() -> None:
    policy = _policy()
    batch = _batch(1, _order(1))
    applied = execute_simulation_batch(create_simulation_ledger(policy), policy, batch)
    replay = execute_simulation_batch(applied.state, policy, batch)
    assert applied.replay_status == "applied"
    assert replay.replay_status == "replayed"
    assert replay.appended_events == ()
    assert replay.state == applied.state
    conflicting = _batch(1, _order(1, raw_open="10.01"))
    with pytest.raises(IdempotencyConflictError, match="different payload"):
        execute_simulation_batch(applied.state, policy, conflicting)


def test_order_id_cannot_be_replayed_under_another_batch() -> None:
    policy = _policy()
    order = _order(1)
    applied = execute_simulation_batch(create_simulation_ledger(policy), policy, _batch(1, order))
    with pytest.raises(IdempotencyConflictError, match="order ID"):
        execute_simulation_batch(applied.state, policy, _batch(2, order))

    reused_session = _batch(
        2,
        _order(2, execution_at=EXECUTED_AT + timedelta(days=1)),
    ).model_copy(update={"session_id": _batch(1, order).session_id})
    with pytest.raises(IdempotencyConflictError, match="session ID"):
        execute_simulation_batch(applied.state, policy, reused_session)


def test_batch_is_atomic_when_a_later_canonical_order_fails() -> None:
    policy = _policy()
    initial = create_simulation_ledger(policy)
    valid_buy = _order(1, quantity="1")
    invalid_sell = _order(2, side=SimulationSide.SELL, quantity="2")
    with pytest.raises(ShortSaleError):
        execute_simulation_batch(initial, policy, _batch(1, valid_buy, invalid_sell))
    assert initial == create_simulation_ledger(policy)
    assert initial.processed_batches == ()


def test_hash_chain_detects_payload_and_link_tampering() -> None:
    policy = _policy()
    applied = execute_simulation_batch(
        create_simulation_ledger(policy), policy, _batch(1, _order(1))
    )
    payload = applied.state.model_dump(mode="python")
    payload["events"][1]["previous_hash"] = "f" * 64
    with pytest.raises(ValidationError, match="event hash|hash chain"):
        SimulationLedgerState.model_validate(payload)
    payload = applied.state.model_dump(mode="python")
    payload["cash"] = Decimal("999.99")
    with pytest.raises(ValidationError, match="reconcile"):
        SimulationLedgerState.model_validate(payload)


def test_fill_digest_and_event_postings_are_semantically_bound() -> None:
    policy = _policy()
    applied = execute_simulation_batch(
        create_simulation_ledger(policy), policy, _batch(1, _order(1))
    )
    fill_payload = applied.appended_events[0].fill.model_dump(mode="python")
    fill_payload["order_digest"] = "f" * 64
    with pytest.raises(ValidationError, match="order digest"):
        type(applied.appended_events[0].fill).model_validate(fill_payload)

    event_payload = applied.appended_events[0].model_dump(mode="python")
    event_payload["postings"] = tuple(reversed(event_payload["postings"]))
    event_payload["event_hash"] = canonical_digest(
        {key: value for key, value in event_payload.items() if key != "event_hash"}
    )
    with pytest.raises(ValidationError, match="postings do not match"):
        type(applied.appended_events[0]).model_validate(event_payload)


def test_result_contracts_reject_impossible_digest_event_and_state_bindings() -> None:
    policy = _policy()
    initial = create_simulation_ledger(policy)
    applied = execute_simulation_batch(initial, policy, _batch(1, _order(1)))
    event = applied.appended_events[0]

    with pytest.raises(ValidationError, match="replayed batch cannot append"):
        SimulationBatchResult(
            request_digest=applied.request_digest,
            replay_status="replayed",
            appended_events=(event,),
            state=applied.state,
        )
    with pytest.raises(ValidationError, match="identify one committed receipt"):
        SimulationBatchResult(
            request_digest="f" * 64,
            replay_status="replayed",
            appended_events=(),
            state=applied.state,
        )
    with pytest.raises(ValidationError, match="committed receipts|exact state suffix"):
        SimulationRunResult(
            request_digests=(applied.request_digest,),
            appended_events=(event,),
            state=initial,
        )


def test_self_consistent_rehashed_cost_basis_forgery_is_rejected_on_resume() -> None:
    policy = _policy(commission_bps="0", tax_bps="0", slippage_bps="0")
    bought = execute_simulation_batch(
        create_simulation_ledger(policy),
        policy,
        _batch(1, _order(1, quantity="2", raw_open="10")),
    )
    sold = execute_simulation_batch(
        bought.state,
        policy,
        _batch(
            2,
            _order(
                2,
                side=SimulationSide.SELL,
                quantity="1",
                raw_open="12",
                execution_at=EXECUTED_AT + timedelta(days=1),
            ),
        ),
    )
    payload = sold.state.model_dump(mode="python")
    forged_event = payload["events"][-1]
    forged_event["fill"]["cost_basis_removed"] = Decimal("1.00")
    forged_event["fill"]["realized_pnl"] = Decimal("11.00")
    forged_event["postings"][-2]["amount"] = Decimal("1.00")
    forged_event["postings"][-1]["amount"] = Decimal("11.00")
    forged_event["event_hash"] = canonical_digest(
        {key: value for key, value in forged_event.items() if key != "event_hash"}
    )
    payload["positions"][0]["cost_basis"] = Decimal("19.00")
    payload["realized_pnl"] = Decimal("11.00")
    payload["processed_batches"][-1]["result_event_hash"] = forged_event["event_hash"]
    payload["last_event_hash"] = forged_event["event_hash"]
    self_consistent_forgery = SimulationLedgerState.model_validate(payload)

    with pytest.raises(ValueError, match="externally anchored hash"):
        execute_simulation_batch(
            self_consistent_forgery,
            policy,
            _batch(
                3,
                _order(
                    3,
                    quantity="1",
                    raw_open="10",
                    execution_at=EXECUTED_AT + timedelta(days=2),
                ),
            ),
            expected_last_event_hash=sold.state.last_event_hash,
        )
    with pytest.raises(ValueError, match="cost basis differs from sequential history"):
        execute_simulation_batch(
            self_consistent_forgery,
            policy,
            _batch(
                3,
                _order(
                    3,
                    quantity="1",
                    raw_open="10",
                    execution_at=EXECUTED_AT + timedelta(days=2),
                ),
            ),
            expected_last_event_hash=self_consistent_forgery.last_event_hash,
        )


def test_reducer_revalidates_instances_and_policy_state_binding() -> None:
    policy = _policy()
    state = create_simulation_ledger(policy)
    poisoned_policy = policy.model_copy(update={"initial_cash": Decimal("-1")})
    with pytest.raises(ValidationError, match="positive"):
        create_simulation_ledger(poisoned_policy)

    poisoned_batch = _batch(1, _order(1)).model_copy(update={"orders": ()})
    with pytest.raises(ValidationError):
        execute_simulation_batch(state, policy, poisoned_batch)

    self_consistent_but_wrong_quantum = state.model_copy(update={"money_quantum": Decimal("0.10")})
    with pytest.raises(ValueError, match="differs from policy"):
        execute_simulation_batch(
            self_consistent_but_wrong_quantum,
            policy,
            _batch(1, _order(1)),
            expected_last_event_hash=self_consistent_but_wrong_quantum.last_event_hash,
        )

    cash_mutated = create_simulation_ledger(policy)
    cash_mutated.__dict__["cash"] = Decimal("1000000.00")
    anchored_hash = cash_mutated.last_event_hash
    batch = _batch(1, _order(1, quantity="50", raw_open="10"))
    with pytest.raises(ValidationError, match="reconcile"):
        _production_execute_simulation_batch(
            cash_mutated,
            policy,
            batch,
            expected_last_event_hash=anchored_hash,
            expected_request_digest=batch.request_digest,
        )


def test_external_anchor_rejects_rollback_and_malformed_digests() -> None:
    policy = _policy()
    first = execute_simulation_batch(create_simulation_ledger(policy), policy, _batch(1, _order(1)))
    second = execute_simulation_batch(
        first.state,
        policy,
        _batch(
            2,
            _order(
                2,
                quantity="1",
                execution_at=EXECUTED_AT + timedelta(days=1),
            ),
        ),
    )
    resumed_prefix = SimulationLedgerState.model_validate(first.state.model_dump(mode="python"))
    next_batch = _batch(
        3,
        _order(3, execution_at=EXECUTED_AT + timedelta(days=2)),
    )
    with pytest.raises(ValueError, match="externally anchored hash"):
        execute_simulation_batch(
            resumed_prefix,
            policy,
            next_batch,
            expected_last_event_hash=second.state.last_event_hash,
        )
    for malformed in ("F" * 64, "0" * 63, 7):
        with pytest.raises(ValueError, match="lowercase SHA-256"):
            execute_simulation_batch(
                resumed_prefix,
                policy,
                next_batch,
                expected_last_event_hash=malformed,
            )


def test_contract_subclasses_are_not_accepted_at_public_boundaries() -> None:
    class PolicySubclass(SimulationLedgerPolicy):
        pass

    policy_subclass = PolicySubclass.model_validate(_policy().model_dump(mode="python"))
    with pytest.raises(ValueError, match="exact contract type"):
        create_simulation_ledger(policy_subclass)

    class BatchSubclass(SimulationBatch):
        pass

    policy = _policy()
    batch_subclass = BatchSubclass.model_validate(_batch(1, _order(1)).model_dump(mode="python"))
    with pytest.raises(ValueError, match="exact contract type"):
        execute_simulation_batch(create_simulation_ledger(policy), policy, batch_subclass)


@pytest.mark.parametrize(
    "field,value",
    [
        ("initial_cash", "NaN"),
        ("initial_cash", "Infinity"),
        ("money_quantum", "1e-19"),
        ("commission_bps", "1000.000000000000000001"),
    ],
)
def test_decimal_limits_fail_closed(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        _policy(**{field: value})


def test_receipts_must_exactly_partition_trade_events_by_batch() -> None:
    policy = _policy()
    first = execute_simulation_batch(create_simulation_ledger(policy), policy, _batch(1, _order(1)))
    second = execute_simulation_batch(
        first.state,
        policy,
        _batch(
            2,
            _order(
                2,
                instrument_id=STOCK_B,
                quantity="1",
                raw_open="5",
                execution_at=EXECUTED_AT + timedelta(days=1),
            ),
        ),
    )
    payload = second.state.model_dump(mode="python")
    payload["processed_batches"] = tuple(reversed(payload["processed_batches"]))
    with pytest.raises(ValidationError, match="contiguous|range"):
        SimulationLedgerState.model_validate(payload)
    payload = second.state.model_dump(mode="python")
    payload["processed_batches"] = payload["processed_batches"][:-1]
    with pytest.raises(ValidationError, match="cover|omit"):
        SimulationLedgerState.model_validate(payload)
    payload = second.state.model_dump(mode="python")
    payload["processed_batches"][0]["request_digest"] = "f" * 64
    with pytest.raises(ValidationError, match="digest"):
        SimulationLedgerState.model_validate(payload)


def test_fill_is_always_adverse_after_tick_rounding() -> None:
    zero_cost_policy = _policy(commission_bps="0", tax_bps="0", slippage_bps="0")
    state = create_simulation_ledger(zero_cost_policy)
    bought = execute_simulation_batch(
        state,
        zero_cost_policy,
        _batch(1, _order(1, quantity="1", raw_open="10.011")),
    )
    buy_fill = bought.appended_events[0].fill
    assert buy_fill.fill_price == Decimal("10.05")
    sold = execute_simulation_batch(
        bought.state,
        zero_cost_policy,
        _batch(
            2,
            _order(
                2,
                side=SimulationSide.SELL,
                quantity="1",
                raw_open="10.011",
                execution_at=EXECUTED_AT + timedelta(days=1),
            ),
        ),
    )
    sell_fill = sold.appended_events[0].fill
    assert sell_fill.fill_price == Decimal("10.00")
    assert buy_fill.fill_price >= buy_fill.raw_open
    assert sell_fill.fill_price <= sell_fill.raw_open


def test_zero_cost_basis_residue_can_be_closed_without_zero_posting() -> None:
    policy = _policy(
        initial_cash="1.00",
        commission_bps="0",
        tax_bps="0",
        slippage_bps="0",
        instrument_rules=(
            InstrumentSimulationRule(
                instrument_id=STOCK_A,
                currency="USD",
                lot_size="0.1",
                price_tick="0.01",
            ),
        ),
    )
    bought = execute_simulation_batch(
        create_simulation_ledger(policy),
        policy,
        _batch(1, _order(1, quantity="1", raw_open="0.01")),
    )
    partial = execute_simulation_batch(
        bought.state,
        policy,
        _batch(
            2,
            _order(
                2,
                side=SimulationSide.SELL,
                quantity="0.9",
                raw_open="0.01",
                execution_at=EXECUTED_AT + timedelta(days=1),
            ),
        ),
    )
    assert partial.state.position(STOCK_A).cost_basis == 0
    closed = execute_simulation_batch(
        partial.state,
        policy,
        _batch(
            3,
            _order(
                3,
                side=SimulationSide.SELL,
                quantity="0.1",
                raw_open="0.10",
                execution_at=EXECUTED_AT + timedelta(days=2),
            ),
        ),
    )
    assert closed.state.position(STOCK_A) is None
    assert closed.appended_events[0].fill.cost_basis_removed == 0
    assert all(
        posting.account is not LedgerAccount.POSITION_COST
        for posting in closed.appended_events[0].postings
    )


def test_same_timestamp_sells_precede_buys_deterministically() -> None:
    policy = _policy(commission_bps="0", tax_bps="0", slippage_bps="0")
    seeded = execute_simulation_batch(
        create_simulation_ledger(policy),
        policy,
        _batch(1, _order(1, quantity="2", raw_open="10")),
    )
    execution_at = EXECUTED_AT + timedelta(days=1)
    buy = _order(2, quantity="1", raw_open="10", execution_at=execution_at)
    sell = _order(
        3,
        side=SimulationSide.SELL,
        quantity="1",
        raw_open="10",
        execution_at=execution_at,
    )
    result = execute_simulation_batch(seeded.state, policy, _batch(2, buy, sell))
    assert [event.fill.side for event in result.appended_events] == [
        SimulationSide.SELL,
        SimulationSide.BUY,
    ]


def _round_half_even_fraction(numerator: int, denominator: int) -> int:
    quotient, remainder = divmod(numerator, denominator)
    doubled = remainder * 2
    if doubled < denominator:
        return quotient
    if doubled > denominator:
        return quotient + 1
    return quotient + (quotient % 2)


@settings(max_examples=60, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.sampled_from((SimulationSide.BUY, SimulationSide.SELL)),
            st.integers(min_value=1, max_value=5),
            st.integers(min_value=1, max_value=50),
        ),
        min_size=1,
        max_size=20,
    )
)
def test_integer_market_sequences_match_an_independent_fraction_oracle(
    operations: list[tuple[SimulationSide, int, int]],
) -> None:
    policy = _policy(
        initial_cash="1000.00",
        commission_bps="0",
        tax_bps="0",
        slippage_bps="0",
        instrument_rules=(
            InstrumentSimulationRule(
                instrument_id=STOCK_A,
                currency="USD",
                lot_size="1",
                price_tick="0.01",
            ),
        ),
    )
    state = create_simulation_ledger(policy)
    cash_cents = 100_000
    quantity = 0
    basis_cents = 0
    realized_cents = 0
    for index, (side, units, dollars) in enumerate(operations, start=1):
        before = state
        notional_cents = units * dollars * 100
        order = _order(
            index,
            side=side,
            quantity=str(units),
            raw_open=str(dollars),
            execution_at=EXECUTED_AT + timedelta(days=index),
        )
        batch = _batch(index, order)
        if side is SimulationSide.BUY and notional_cents > cash_cents:
            with pytest.raises(InsufficientCashError):
                execute_simulation_batch(state, policy, batch)
            assert state == before
            continue
        if side is SimulationSide.SELL and units > quantity:
            with pytest.raises(ShortSaleError):
                execute_simulation_batch(state, policy, batch)
            assert state == before
            continue
        state = execute_simulation_batch(state, policy, batch).state
        if side is SimulationSide.BUY:
            cash_cents -= notional_cents
            quantity += units
            basis_cents += notional_cents
        else:
            removed = (
                basis_cents
                if units == quantity
                else _round_half_even_fraction(basis_cents * units, quantity)
            )
            cash_cents += notional_cents
            quantity -= units
            basis_cents -= removed
            realized_cents += notional_cents - removed
        assert state.cash == Decimal(cash_cents) / 100
        assert state.realized_pnl == Decimal(realized_cents) / 100
        position = state.position(STOCK_A)
        if quantity == 0:
            assert position is None
            assert basis_cents == 0
        else:
            assert position is not None
            assert position.quantity == quantity
            assert position.cost_basis == Decimal(basis_cents) / 100
        _assert_balanced(state)


def test_500_session_run_fold_and_anchored_resume_are_bounded() -> None:
    policy = _policy(
        initial_cash="10000.00",
        commission_bps="0",
        tax_bps="0",
        slippage_bps="0",
        instrument_rules=(
            InstrumentSimulationRule(
                instrument_id=STOCK_A,
                currency="USD",
                lot_size="1",
                price_tick="0.01",
            ),
        ),
    )
    initial = create_simulation_ledger(policy)
    batches = tuple(
        _batch(
            index,
            _order(
                index,
                quantity="1",
                raw_open="1",
                execution_at=EXECUTED_AT + timedelta(days=index),
            ),
        )
        for index in range(1, 501)
    )
    started = perf_counter()
    run_result = execute_simulation_run(
        initial,
        policy,
        tuple(reversed(batches)),
        expected_last_event_hash=initial.last_event_hash,
        expected_request_digests={batch.batch_id: batch.request_digest for batch in batches},
    )
    assert perf_counter() - started < 20
    state = run_result.state
    assert len(run_result.request_digests) == 500
    assert len(run_result.appended_events) == 500
    assert len(state.events) == 501
    assert state.cash == Decimal("9500.00")

    serialized = state.model_dump(mode="python")
    resumed = SimulationLedgerState.model_validate(serialized)
    with pytest.raises(ValueError, match="expected_last_event_hash is required"):
        _production_execute_simulation_batch(
            resumed,
            policy,
            _batch(
                501,
                _order(
                    501,
                    quantity="1",
                    raw_open="1",
                    execution_at=EXECUTED_AT + timedelta(days=501),
                ),
            ),
        )
    resumed_result = execute_simulation_batch(
        resumed,
        policy,
        _batch(
            501,
            _order(
                501,
                quantity="1",
                raw_open="1",
                execution_at=EXECUTED_AT + timedelta(days=501),
            ),
        ),
        expected_last_event_hash=state.last_event_hash,
    )
    assert resumed_result.state.cash == Decimal("9499.00")


def test_run_replays_a_maximum_size_committed_batch_without_capacity_failure() -> None:
    policy = _policy(
        initial_cash="9999",
        money_quantum="1",
        commission_bps="0",
        tax_bps="0",
        slippage_bps="0",
        instrument_rules=(
            InstrumentSimulationRule(
                instrument_id=STOCK_A,
                currency="USD",
                lot_size="1",
                price_tick="1",
            ),
        ),
    )
    orders = tuple(_order(index, quantity="1", raw_open="1") for index in range(1, 10_000))
    batch = _batch(1, *orders)
    initial = create_simulation_ledger(policy)
    applied = execute_simulation_batch(initial, policy, batch)
    assert len(applied.state.events) == 10_000

    replay = execute_simulation_run(
        applied.state,
        policy,
        (batch,),
        expected_last_event_hash=applied.state.last_event_hash,
        expected_request_digests={batch.batch_id: batch.request_digest},
    )
    assert replay.appended_events == ()
    assert replay.state == applied.state


def test_run_rejects_aggregate_event_overflow_before_batch_revalidation() -> None:
    policy = _policy()
    initial = create_simulation_ledger(policy)
    repeated = _order(1)
    first = _batch(1, repeated).model_copy(update={"orders": (repeated,) * 5_000})
    second_order = _order(2, execution_at=EXECUTED_AT + timedelta(days=1))
    second = _batch(2, second_order).model_copy(update={"orders": (second_order,) * 5_000})
    started = perf_counter()
    with pytest.raises(ValueError, match="event resource limit"):
        execute_simulation_run(
            initial,
            policy,
            (first, second),
            expected_last_event_hash=initial.last_event_hash,
            expected_request_digests={},
        )
    assert perf_counter() - started < 2


def test_module_imports_are_pure_and_scope_exclusions_are_explicit() -> None:
    path = (
        Path(__file__).parents[1]
        / "tradingagents"
        / "platform"
        / "backtest"
        / "simulation_ledger.py"
    )
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots |= {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert imported_roots <= {
        "__future__",
        "datetime",
        "decimal",
        "enum",
        "hmac",
        "pydantic",
        "tradingagents",
        "typing",
        "uuid",
    }
    assert "M5.2b1" in source
    assert "full M5.2b" in source
    for forbidden in ("sqlalchemy", "requests", "httpx", "socket", "broker", "openai"):
        assert forbidden not in imported_roots
    assert not hasattr(simulation_ledger_module, "_apply_validated_batch")
    with pytest.raises(AttributeError):
        getattr(simulation_ledger_module, "_apply_" + "validated_batch")
    forbidden_private_boundaries = (
        "_apply_validated_batch",
        "_assert_state_matches_policy",
        "_validate_state_and_policy",
    )
    repository_root = Path(__file__).parents[1]
    production_files = list((repository_root / "tradingagents").rglob("*.py"))
    production_files += list((repository_root / "service").rglob("*.py"))
    production_files += list((repository_root / "cli").rglob("*.py"))
    production_files.append(repository_root / "main.py")
    for production_file in production_files:
        if production_file == path or not production_file.exists():
            continue
        candidate_source = production_file.read_text(encoding="utf-8")
        for private_name in forbidden_private_boundaries:
            assert private_name not in candidate_source, (
                f"{production_file} bypasses the public simulation-ledger boundary"
            )

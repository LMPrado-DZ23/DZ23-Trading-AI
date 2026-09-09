"""Pure deterministic simulation ledger for the M5.2b1 accounting slice.

This module intentionally has no repository, broker, database, network, FX,
corporate-action, or LLM integration.  ``raw_open`` is a typed economic input;
the full M5.2b milestone must bind it to the frozen price authority before
calling this module.

The reducer exposes no unchecked transition as a module attribute.  This is a
safe application boundary, not a Python sandbox: untrusted Python or plugins
must never execute in the ledger process.
"""

from __future__ import annotations

import hmac
from datetime import UTC, datetime
from decimal import (
    ROUND_CEILING,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    localcontext,
)
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tradingagents.platform.backtest.contracts import canonical_digest

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]

LEDGER_GENESIS_HASH = "0" * 64
M5_2B1_SCOPE = "m5.2b1-pure-simulation-ledger"
_BPS_DENOMINATOR = Decimal("10000")
_DECIMAL_CONTEXT = Context(prec=76, rounding=ROUND_HALF_EVEN)


class SimulationLedgerError(ValueError):
    """Base error for a rejected atomic ledger operation."""


class IdempotencyConflictError(SimulationLedgerError):
    """A batch identifier was reused with different economic inputs."""


class InsufficientCashError(SimulationLedgerError):
    """A buy would make cash negative."""


class ShortSaleError(SimulationLedgerError):
    """A sell exceeds the settled simulated position."""


class SimulationSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class PostingDirection(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"


class LedgerAccount(StrEnum):
    CASH = "cash"
    POSITION_COST = "position_cost"
    OPENING_EQUITY = "opening_equity"
    COMMISSION_EXPENSE = "commission_expense"
    TAX_EXPENSE = "tax_expense"
    REALIZED_PNL = "realized_pnl"


class LedgerEventKind(StrEnum):
    OPENING_BALANCE = "opening_balance"
    TRADE_FILL = "trade_fill"


class SimulationContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        revalidate_instances="always",
    )

    @property
    def digest(self) -> str:
        return canonical_digest(self)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError("timestamp must use UTC")
    return value.astimezone(UTC)


def _exact_decimal_input(value: Any) -> Any:
    if isinstance(value, (bool, float)):
        raise ValueError("decimal values must not use booleans or binary floats")
    if isinstance(value, str) and len(value) > 80:
        raise ValueError("decimal text exceeds the resource limit")
    if isinstance(value, int) and value.bit_length() > 256:
        raise ValueError("decimal integer exceeds the resource limit")
    return value


def _bounded_decimal(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("decimal value must be finite")
    _, digits, exponent = value.as_tuple()
    if len(digits) > 38 or value.adjusted() >= 38:
        raise ValueError("decimal value exceeds 38 significant integer digits")
    if exponent < -18:
        raise ValueError("decimal value exceeds 18 fractional digits")
    return value


def _positive(value: Decimal) -> Decimal:
    _bounded_decimal(value)
    if value <= 0:
        raise ValueError("value must be positive")
    return value


def _non_negative(value: Decimal) -> Decimal:
    _bounded_decimal(value)
    if value < 0:
        raise ValueError("value must be non-negative")
    return value


def _step_quantize(value: Decimal, step: Decimal, *, rounding: str) -> Decimal:
    """Quantize to any positive step without consulting the ambient context."""

    with localcontext(_DECIMAL_CONTEXT):
        units = (value / step).to_integral_value(rounding=rounding)
        return units * step


def _is_step_quantized(value: Decimal, step: Decimal) -> bool:
    with localcontext(_DECIMAL_CONTEXT):
        return value == _step_quantize(value, step, rounding=ROUND_HALF_EVEN)


def _money(value: Decimal, quantum: Decimal) -> Decimal:
    return _step_quantize(value, quantum, rounding=ROUND_HALF_EVEN)


def _decimal_add(left: Decimal, right: Decimal) -> Decimal:
    with localcontext(_DECIMAL_CONTEXT):
        return left + right


class InstrumentSimulationRule(SimulationContract):
    rule_version: Literal["simulation-instrument-rule/1"] = "simulation-instrument-rule/1"
    instrument_id: UUID
    currency: Currency
    lot_size: Decimal
    price_tick: Decimal

    _decimal_input_is_exact = field_validator("lot_size", "price_tick", mode="before")(
        _exact_decimal_input
    )
    _decimal_is_positive = field_validator("lot_size", "price_tick")(_positive)


class SimulationLedgerPolicy(SimulationContract):
    policy_version: Literal["simulation-ledger-policy/1"] = "simulation-ledger-policy/1"
    milestone_scope: Literal["m5.2b1-pure-simulation-ledger"] = M5_2B1_SCOPE
    ledger_id: UUID
    opened_at: datetime
    base_currency: Currency
    initial_cash: Decimal
    money_quantum: Decimal
    commission_bps: Decimal = Decimal("0")
    tax_bps: Decimal = Decimal("0")
    slippage_bps: Decimal = Decimal("0")
    instrument_rules: tuple[InstrumentSimulationRule, ...] = Field(min_length=1, max_length=1_000)

    _opened_at_is_utc = field_validator("opened_at")(_utc)
    _decimal_input_is_exact = field_validator(
        "initial_cash",
        "money_quantum",
        "commission_bps",
        "tax_bps",
        "slippage_bps",
        mode="before",
    )(_exact_decimal_input)

    @field_validator("initial_cash", "money_quantum")
    @classmethod
    def validate_positive_money(cls, value: Decimal) -> Decimal:
        return _positive(value)

    @field_validator("commission_bps", "tax_bps", "slippage_bps")
    @classmethod
    def validate_bps(cls, value: Decimal) -> Decimal:
        value = _non_negative(value)
        if value > Decimal("1000"):
            raise ValueError("simulation costs cannot exceed 1000 basis points")
        return value

    @model_validator(mode="after")
    def validate_policy(self):
        if any(type(rule) is not InstrumentSimulationRule for rule in self.instrument_rules):
            raise ValueError("instrument rules must use the exact contract type")
        if not _is_step_quantized(self.initial_cash, self.money_quantum):
            raise ValueError("initial cash must be quantized to the money quantum")
        instrument_ids = tuple(rule.instrument_id for rule in self.instrument_rules)
        if len(set(instrument_ids)) != len(instrument_ids):
            raise ValueError("instrument rules must have unique instrument IDs")
        if instrument_ids != tuple(sorted(instrument_ids, key=str)):
            raise ValueError("instrument rules must be sorted by instrument ID")
        if any(rule.currency != self.base_currency for rule in self.instrument_rules):
            raise ValueError("M5.2b1 does not support cross-currency instruments")
        return self


class SimulationOrder(SimulationContract):
    order_version: Literal["simulation-order/1"] = "simulation-order/1"
    order_id: UUID
    instrument_id: UUID
    side: SimulationSide
    requested_quantity: Decimal
    raw_open: Decimal
    execution_at: datetime

    _execution_at_is_utc = field_validator("execution_at")(_utc)
    _decimal_input_is_exact = field_validator("requested_quantity", "raw_open", mode="before")(
        _exact_decimal_input
    )
    _decimal_is_positive = field_validator("requested_quantity", "raw_open")(_positive)


class SimulationBatch(SimulationContract):
    batch_version: Literal["simulation-batch/1"] = "simulation-batch/1"
    batch_id: UUID
    session_id: UUID
    execution_at: datetime
    # One slot is reserved for the balanced opening event because the shared
    # canonical serializer bounds every sequence to 10,000 items.
    orders: tuple[SimulationOrder, ...] = Field(min_length=1, max_length=9_999)

    _execution_at_is_utc = field_validator("execution_at")(_utc)

    @model_validator(mode="after")
    def validate_unique_orders(self):
        if any(type(order) is not SimulationOrder for order in self.orders):
            raise ValueError("orders must use the exact contract type")
        order_ids = [order.order_id for order in self.orders]
        if len(set(order_ids)) != len(order_ids):
            raise ValueError("a simulation batch cannot repeat an order ID")
        if any(order.execution_at != self.execution_at for order in self.orders):
            raise ValueError("a simulation batch must contain one complete execution session")
        return self

    @property
    def request_digest(self) -> str:
        ordered = sorted(self.orders, key=_order_sort_key)
        return canonical_digest(
            {
                "batch_version": self.batch_version,
                "batch_id": self.batch_id,
                "session_id": self.session_id,
                "execution_at": self.execution_at,
                "orders": [order.model_dump(mode="python") for order in ordered],
            }
        )

    @property
    def digest(self) -> str:
        """Return the canonical, order-insensitive economic request digest."""

        return self.request_digest


class PositionBalance(SimulationContract):
    position_version: Literal["simulation-position/1"] = "simulation-position/1"
    instrument_id: UUID
    quantity: Decimal
    cost_basis: Decimal
    currency: Currency

    _decimal_input_is_exact = field_validator("quantity", "cost_basis", mode="before")(
        _exact_decimal_input
    )

    @field_validator("quantity", "cost_basis")
    @classmethod
    def validate_non_negative(cls, value: Decimal) -> Decimal:
        return _non_negative(value)

    @model_validator(mode="after")
    def validate_position(self):
        if self.quantity == 0 and self.cost_basis != 0:
            raise ValueError("an empty position cannot retain cost basis")
        return self

    @property
    def average_cost(self) -> Decimal:
        if self.quantity == 0:
            return Decimal("0")
        with localcontext(_DECIMAL_CONTEXT):
            return self.cost_basis / self.quantity


class JournalPosting(SimulationContract):
    posting_version: Literal["simulation-posting/1"] = "simulation-posting/1"
    account: LedgerAccount
    direction: PostingDirection
    amount: Decimal
    currency: Currency
    instrument_id: UUID | None = None

    _amount_input_is_exact = field_validator("amount", mode="before")(_exact_decimal_input)
    _amount_is_positive = field_validator("amount")(_positive)

    @model_validator(mode="after")
    def validate_account_dimension(self):
        if (self.account is LedgerAccount.POSITION_COST) != (self.instrument_id is not None):
            raise ValueError("only position-cost postings require an instrument ID")
        return self


class ExecutionFill(SimulationContract):
    """Deterministic fill economics; realized PnL is gross of fee and tax."""

    fill_version: Literal["simulation-fill/1"] = "simulation-fill/1"
    order_digest: Digest
    order_id: UUID
    instrument_id: UUID
    side: SimulationSide
    currency: Currency
    executed_at: datetime
    requested_quantity: Decimal
    executed_quantity: Decimal
    raw_open: Decimal
    fill_price: Decimal
    raw_notional: Decimal
    fill_notional: Decimal
    slippage_amount: Decimal
    commission_fee: Decimal
    tax: Decimal
    cost_basis_removed: Decimal
    realized_pnl: Decimal
    cash_delta: Decimal

    _executed_at_is_utc = field_validator("executed_at")(_utc)
    _decimal_input_is_exact = field_validator(
        "requested_quantity",
        "executed_quantity",
        "raw_open",
        "fill_price",
        "raw_notional",
        "fill_notional",
        "slippage_amount",
        "commission_fee",
        "tax",
        "cost_basis_removed",
        "realized_pnl",
        "cash_delta",
        mode="before",
    )(_exact_decimal_input)

    @field_validator(
        "requested_quantity",
        "executed_quantity",
        "raw_open",
        "fill_price",
        "raw_notional",
        "fill_notional",
    )
    @classmethod
    def validate_positive(cls, value: Decimal) -> Decimal:
        return _positive(value)

    @field_validator("slippage_amount", "commission_fee", "tax", "cost_basis_removed")
    @classmethod
    def validate_non_negative(cls, value: Decimal) -> Decimal:
        return _non_negative(value)

    @field_validator("realized_pnl", "cash_delta")
    @classmethod
    def validate_signed_money(cls, value: Decimal) -> Decimal:
        return _bounded_decimal(value)

    @model_validator(mode="after")
    def validate_side_semantics(self):
        with localcontext(_DECIMAL_CONTEXT):
            if self.executed_quantity > self.requested_quantity:
                raise ValueError("executed quantity cannot exceed requested quantity")
            if self.side is SimulationSide.BUY:
                if self.fill_price < self.raw_open or self.cash_delta >= 0:
                    raise ValueError("buy fills and cash movement must be adverse")
                if self.cost_basis_removed != 0 or self.realized_pnl != 0:
                    raise ValueError("buy fills cannot remove cost basis or realize PnL")
            else:
                if self.fill_price > self.raw_open or self.cash_delta <= 0:
                    raise ValueError("sell fills and cash movement must be adverse")
                if self.realized_pnl != self.fill_notional - self.cost_basis_removed:
                    raise ValueError("sell realized PnL is inconsistent with the fill")
            expected_slippage = (
                self.fill_notional - self.raw_notional
                if self.side is SimulationSide.BUY
                else self.raw_notional - self.fill_notional
            )
        if self.slippage_amount != expected_slippage:
            raise ValueError("fill slippage is inconsistent with its notionals")
        reconstructed_order = SimulationOrder(
            order_id=self.order_id,
            instrument_id=self.instrument_id,
            side=self.side,
            requested_quantity=self.requested_quantity,
            raw_open=self.raw_open,
            execution_at=self.executed_at,
        )
        if not hmac.compare_digest(self.order_digest, reconstructed_order.digest):
            raise ValueError("fill order digest is inconsistent with its economic input")
        return self


class LedgerEvent(SimulationContract):
    event_version: Literal["simulation-ledger-event/1"] = "simulation-ledger-event/1"
    sequence: int = Field(ge=1)
    kind: LedgerEventKind
    ledger_id: UUID
    policy_digest: Digest
    occurred_at: datetime
    batch_id: UUID | None = None
    session_id: UUID | None = None
    batch_request_digest: Digest | None = None
    order_id: UUID | None = None
    fill: ExecutionFill | None = None
    postings: tuple[JournalPosting, ...] = Field(min_length=2, max_length=8)
    previous_hash: Digest
    event_hash: Digest

    _occurred_at_is_utc = field_validator("occurred_at")(_utc)

    @model_validator(mode="after")
    def validate_event(self):
        if any(type(posting) is not JournalPosting for posting in self.postings):
            raise ValueError("postings must use the exact contract type")
        if self.fill is not None and type(self.fill) is not ExecutionFill:
            raise ValueError("fill must use the exact contract type")
        is_opening = self.kind is LedgerEventKind.OPENING_BALANCE
        opening_fields_are_empty = (
            self.batch_id is None
            and self.session_id is None
            and self.batch_request_digest is None
            and self.order_id is None
            and self.fill is None
        )
        if is_opening != opening_fields_are_empty:
            raise ValueError("opening and trade event fields are inconsistent")
        if not is_opening and (
            self.batch_id is None
            or self.session_id is None
            or self.batch_request_digest is None
            or self.order_id is None
            or self.fill is None
            or self.fill.order_id != self.order_id
        ):
            raise ValueError("trade events require one matching fill and order")
        if is_opening:
            first, second, *extra = self.postings
            if (
                extra
                or first.account is not LedgerAccount.CASH
                or first.direction is not PostingDirection.DEBIT
                or second.account is not LedgerAccount.OPENING_EQUITY
                or second.direction is not PostingDirection.CREDIT
                or first.amount != second.amount
                or first.currency != second.currency
            ):
                raise ValueError("opening event postings are not canonical")
        else:
            assert self.fill is not None
            if self.occurred_at != self.fill.executed_at:
                raise ValueError("trade event timestamp does not match its fill")
            if tuple(self.postings) != _expected_postings_for_fill(self.fill):
                raise ValueError("trade event postings do not match its fill")
        currencies = {posting.currency for posting in self.postings}
        if len(currencies) != 1:
            raise ValueError("all postings in an event must use one currency")
        with localcontext(_DECIMAL_CONTEXT):
            debits = sum(
                (
                    posting.amount
                    for posting in self.postings
                    if posting.direction is PostingDirection.DEBIT
                ),
                Decimal("0"),
            )
            credits = sum(
                (
                    posting.amount
                    for posting in self.postings
                    if posting.direction is PostingDirection.CREDIT
                ),
                Decimal("0"),
            )
        if debits != credits:
            raise ValueError("ledger event postings must balance exactly")
        expected_hash = canonical_digest(self.model_dump(mode="python", exclude={"event_hash"}))
        if not hmac.compare_digest(self.event_hash, expected_hash):
            raise ValueError("ledger event hash does not match its payload")
        return self


class BatchReceipt(SimulationContract):
    receipt_version: Literal["simulation-batch-receipt/1"] = "simulation-batch-receipt/1"
    batch_id: UUID
    session_id: UUID
    execution_at: datetime
    request_digest: Digest
    first_event_sequence: int = Field(ge=2)
    last_event_sequence: int = Field(ge=2)
    result_event_hash: Digest

    _execution_at_is_utc = field_validator("execution_at")(_utc)

    @model_validator(mode="after")
    def validate_range(self):
        if self.last_event_sequence < self.first_event_sequence:
            raise ValueError("batch receipt event range is invalid")
        return self


class SimulationLedgerState(SimulationContract):
    state_version: Literal["simulation-ledger-state/1"] = "simulation-ledger-state/1"
    milestone_scope: Literal["m5.2b1-pure-simulation-ledger"] = M5_2B1_SCOPE
    ledger_id: UUID
    policy_digest: Digest
    base_currency: Currency
    money_quantum: Decimal
    cash: Decimal
    realized_pnl: Decimal
    positions: tuple[PositionBalance, ...] = Field(max_length=1_000)
    events: tuple[LedgerEvent, ...] = Field(min_length=1, max_length=10_000)
    processed_batches: tuple[BatchReceipt, ...] = Field(max_length=9_999)
    last_event_hash: Digest
    next_sequence: int = Field(ge=2)

    _decimal_input_is_exact = field_validator(
        "money_quantum", "cash", "realized_pnl", mode="before"
    )(_exact_decimal_input)
    _money_quantum_is_positive = field_validator("money_quantum")(_positive)
    _cash_is_non_negative = field_validator("cash")(_non_negative)
    _realized_pnl_is_bounded = field_validator("realized_pnl")(_bounded_decimal)

    @model_validator(mode="after")
    def validate_state(self):
        if any(type(position) is not PositionBalance for position in self.positions):
            raise ValueError("positions must use the exact contract type")
        if any(type(event) is not LedgerEvent for event in self.events):
            raise ValueError("events must use the exact contract type")
        if any(type(receipt) is not BatchReceipt for receipt in self.processed_batches):
            raise ValueError("receipts must use the exact contract type")
        if not _is_step_quantized(self.cash, self.money_quantum) or not _is_step_quantized(
            self.realized_pnl, self.money_quantum
        ):
            raise ValueError("state money must be quantized")
        position_ids = tuple(position.instrument_id for position in self.positions)
        if position_ids != tuple(sorted(position_ids, key=str)) or len(set(position_ids)) != len(
            position_ids
        ):
            raise ValueError("positions must be unique and sorted by instrument ID")
        if any(
            position.currency != self.base_currency
            or not _is_step_quantized(position.cost_basis, self.money_quantum)
            for position in self.positions
        ):
            raise ValueError("position currency or cost-basis quantization is invalid")
        if self.next_sequence != len(self.events) + 1:
            raise ValueError("next ledger sequence does not follow the event count")
        previous_hash = LEDGER_GENESIS_HASH
        previous_occurred_at: datetime | None = None
        cash_balance = Decimal("0")
        realized_balance = Decimal("0")
        position_costs: dict[UUID, Decimal] = {}
        position_quantities: dict[UUID, Decimal] = {}
        for expected_sequence, event in enumerate(self.events, start=1):
            if event.sequence != expected_sequence or event.previous_hash != previous_hash:
                raise ValueError("ledger event sequence or hash chain is invalid")
            if previous_occurred_at is not None and event.occurred_at < previous_occurred_at:
                raise ValueError("ledger events must be chronological")
            if event.ledger_id != self.ledger_id or event.policy_digest != self.policy_digest:
                raise ValueError("ledger event belongs to another ledger or policy")
            previous_hash = event.event_hash
            previous_occurred_at = event.occurred_at
            for posting in event.postings:
                if posting.currency != self.base_currency:
                    raise ValueError("posting currency differs from ledger currency")
                signed = (
                    posting.amount
                    if posting.direction is PostingDirection.DEBIT
                    else posting.amount.copy_negate()
                )
                if posting.account is LedgerAccount.CASH:
                    cash_balance = _decimal_add(cash_balance, signed)
                elif posting.account is LedgerAccount.POSITION_COST:
                    assert posting.instrument_id is not None
                    position_costs[posting.instrument_id] = _decimal_add(
                        position_costs.get(posting.instrument_id, Decimal("0")), signed
                    )
                elif posting.account is LedgerAccount.REALIZED_PNL:
                    realized_balance = _decimal_add(realized_balance, signed.copy_negate())
            if event.fill is not None:
                quantity_delta = (
                    event.fill.executed_quantity
                    if event.fill.side is SimulationSide.BUY
                    else event.fill.executed_quantity.copy_negate()
                )
                position_quantities[event.fill.instrument_id] = _decimal_add(
                    position_quantities.get(event.fill.instrument_id, Decimal("0")),
                    quantity_delta,
                )
        if self.last_event_hash != previous_hash:
            raise ValueError("last event hash does not match the hash chain")
        if cash_balance != self.cash or realized_balance != self.realized_pnl:
            raise ValueError("state balances do not reconcile to ledger postings")
        expected_positions = {
            position.instrument_id: (position.quantity, position.cost_basis)
            for position in self.positions
        }
        derived_positions = {
            instrument_id: (
                position_quantities.get(instrument_id, Decimal("0")),
                cost_basis,
            )
            for instrument_id, cost_basis in position_costs.items()
            if position_quantities.get(instrument_id, Decimal("0")) != 0 or cost_basis != 0
        }
        if derived_positions != expected_positions:
            raise ValueError("positions do not reconcile to ledger events")
        batch_ids = [receipt.batch_id for receipt in self.processed_batches]
        session_ids = [receipt.session_id for receipt in self.processed_batches]
        if len(set(batch_ids)) != len(batch_ids):
            raise ValueError("processed batch identifiers must be unique")
        if len(set(session_ids)) != len(session_ids):
            raise ValueError("processed session identifiers must be unique")
        trade_sequences: dict[UUID, list[int]] = {}
        trade_order_ids: list[UUID] = []
        for event in self.events:
            if event.kind is LedgerEventKind.TRADE_FILL:
                assert event.batch_id is not None
                assert event.order_id is not None
                trade_sequences.setdefault(event.batch_id, []).append(event.sequence)
                trade_order_ids.append(event.order_id)
        if len(set(trade_order_ids)) != len(trade_order_ids):
            raise ValueError("committed order identifiers must be unique")
        if set(trade_sequences) != set(batch_ids):
            raise ValueError("batch receipts must cover every trade event exactly once")
        expected_first_sequence = 2
        previous_batch_time = self.events[0].occurred_at
        for receipt in self.processed_batches:
            if receipt.first_event_sequence != expected_first_sequence:
                raise ValueError("batch receipt ranges must be contiguous and non-overlapping")
            expected_sequences = list(
                range(receipt.first_event_sequence, receipt.last_event_sequence + 1)
            )
            if trade_sequences[receipt.batch_id] != expected_sequences:
                raise ValueError("batch receipt range does not match its trade events")
            if any(
                self.events[sequence - 1].batch_request_digest != receipt.request_digest
                or self.events[sequence - 1].session_id != receipt.session_id
                for sequence in expected_sequences
            ):
                raise ValueError(
                    "batch receipt digest or session binding does not match its trade events"
                )
            spanned_events = tuple(self.events[sequence - 1] for sequence in expected_sequences)
            if any(event.occurred_at != receipt.execution_at for event in spanned_events):
                raise ValueError("batch receipt timestamp does not match its trade events")
            if spanned_events[0].occurred_at <= previous_batch_time:
                raise ValueError("batch execution times must increase strictly")
            spanned_orders = tuple(_order_from_fill(event.fill) for event in spanned_events)
            reconstructed_batch = SimulationBatch(
                batch_id=receipt.batch_id,
                session_id=receipt.session_id,
                execution_at=receipt.execution_at,
                orders=spanned_orders,
            )
            if not hmac.compare_digest(receipt.request_digest, reconstructed_batch.request_digest):
                raise ValueError("batch receipt digest is not derivable from its fills")
            if spanned_orders != tuple(sorted(spanned_orders, key=_order_sort_key)):
                raise ValueError("trade events do not follow the canonical order")
            event = self.events[receipt.last_event_sequence - 1]
            if event.event_hash != receipt.result_event_hash:
                raise ValueError("batch receipt hash does not match its final event")
            previous_batch_time = spanned_events[-1].occurred_at
            expected_first_sequence = receipt.last_event_sequence + 1
        if expected_first_sequence != self.next_sequence:
            raise ValueError("batch receipt ranges omit committed trade events")
        return self

    def position(self, instrument_id: UUID) -> PositionBalance | None:
        return next((item for item in self.positions if item.instrument_id == instrument_id), None)


class SimulationBatchResult(SimulationContract):
    result_version: Literal["simulation-batch-result/1"] = "simulation-batch-result/1"
    request_digest: Digest
    replay_status: Literal["applied", "replayed"]
    appended_events: tuple[LedgerEvent, ...] = Field(max_length=9_999)
    state: SimulationLedgerState

    @model_validator(mode="after")
    def validate_result(self):
        if type(self.state) is not SimulationLedgerState:
            raise ValueError("batch result state must use the exact contract type")
        if any(type(event) is not LedgerEvent for event in self.appended_events):
            raise ValueError("batch result events must use the exact contract type")
        matching_receipts = tuple(
            receipt
            for receipt in self.state.processed_batches
            if hmac.compare_digest(receipt.request_digest, self.request_digest)
        )
        if len(matching_receipts) != 1:
            raise ValueError("batch result digest must identify one committed receipt")
        receipt = matching_receipts[0]
        if self.replay_status == "replayed":
            if self.appended_events:
                raise ValueError("a replayed batch cannot append ledger events")
            return self
        if not self.appended_events:
            raise ValueError("an applied batch must append ledger events")
        if tuple(self.state.events[-len(self.appended_events) :]) != self.appended_events:
            raise ValueError("batch result events must be the exact state suffix")
        if (
            receipt.first_event_sequence != self.appended_events[0].sequence
            or receipt.last_event_sequence != self.appended_events[-1].sequence
            or receipt.result_event_hash != self.appended_events[-1].event_hash
            or any(
                event.batch_id != receipt.batch_id
                or event.session_id != receipt.session_id
                or event.batch_request_digest != self.request_digest
                for event in self.appended_events
            )
        ):
            raise ValueError("batch result events do not match their committed receipt")
        return self


class SimulationRunResult(SimulationContract):
    result_version: Literal["simulation-run-result/1"] = "simulation-run-result/1"
    request_digests: tuple[Digest, ...] = Field(min_length=1, max_length=9_999)
    appended_events: tuple[LedgerEvent, ...] = Field(max_length=9_999)
    state: SimulationLedgerState

    @model_validator(mode="after")
    def validate_result(self):
        if type(self.state) is not SimulationLedgerState:
            raise ValueError("run result state must use the exact contract type")
        if any(type(event) is not LedgerEvent for event in self.appended_events):
            raise ValueError("run result events must use the exact contract type")
        if len(set(self.request_digests)) != len(self.request_digests):
            raise ValueError("run result request digests must be unique")
        receipts_by_digest = {
            receipt.request_digest: receipt for receipt in self.state.processed_batches
        }
        if len(receipts_by_digest) != len(self.state.processed_batches) or any(
            digest not in receipts_by_digest for digest in self.request_digests
        ):
            raise ValueError("run result digests must identify committed receipts")
        if not self.appended_events:
            return self
        if tuple(self.state.events[-len(self.appended_events) :]) != self.appended_events:
            raise ValueError("run result events must be the exact state suffix")
        first_sequence = self.appended_events[0].sequence
        appended_receipts = tuple(
            receipt
            for receipt in self.state.processed_batches
            if receipt.first_event_sequence >= first_sequence
        )
        if not appended_receipts:
            raise ValueError("run result event suffix has no matching receipt")
        expected_digests = tuple(
            digest
            for digest in self.request_digests
            if receipts_by_digest[digest].first_event_sequence >= first_sequence
        )
        if tuple(receipt.request_digest for receipt in appended_receipts) != expected_digests:
            raise ValueError("run result events do not match the requested new batches")
        if any(
            event.batch_request_digest not in expected_digests for event in self.appended_events
        ):
            raise ValueError("run result event digest is outside the requested batches")
        if (
            appended_receipts[0].first_event_sequence != first_sequence
            or appended_receipts[-1].last_event_sequence != self.appended_events[-1].sequence
            or appended_receipts[-1].result_event_hash != self.appended_events[-1].event_hash
        ):
            raise ValueError("run result receipt range does not cover its event suffix")
        return self


def _order_sort_key(order: SimulationOrder) -> tuple[datetime, int, bytes, bytes]:
    side_priority = 0 if order.side is SimulationSide.SELL else 1
    return order.execution_at, side_priority, order.instrument_id.bytes, order.order_id.bytes


def _posting(
    account: LedgerAccount,
    direction: PostingDirection,
    amount: Decimal,
    currency: str,
    *,
    instrument_id: UUID | None = None,
) -> JournalPosting:
    return JournalPosting(
        account=account,
        direction=direction,
        amount=amount,
        currency=currency,
        instrument_id=instrument_id,
    )


def _order_from_fill(fill: ExecutionFill | None) -> SimulationOrder:
    if type(fill) is not ExecutionFill:
        raise ValueError("trade event is missing an exact fill contract")
    return SimulationOrder(
        order_id=fill.order_id,
        instrument_id=fill.instrument_id,
        side=fill.side,
        requested_quantity=fill.requested_quantity,
        raw_open=fill.raw_open,
        execution_at=fill.executed_at,
    )


def _expected_postings_for_fill(fill: ExecutionFill) -> tuple[JournalPosting, ...]:
    postings: list[JournalPosting]
    if fill.side is SimulationSide.BUY:
        postings = [
            _posting(
                LedgerAccount.POSITION_COST,
                PostingDirection.DEBIT,
                fill.fill_notional,
                fill.currency,
                instrument_id=fill.instrument_id,
            )
        ]
        if fill.commission_fee:
            postings.append(
                _posting(
                    LedgerAccount.COMMISSION_EXPENSE,
                    PostingDirection.DEBIT,
                    fill.commission_fee,
                    fill.currency,
                )
            )
        if fill.tax:
            postings.append(
                _posting(
                    LedgerAccount.TAX_EXPENSE,
                    PostingDirection.DEBIT,
                    fill.tax,
                    fill.currency,
                )
            )
        postings.append(
            _posting(
                LedgerAccount.CASH,
                PostingDirection.CREDIT,
                fill.cash_delta.copy_negate(),
                fill.currency,
            )
        )
    else:
        postings = [
            _posting(
                LedgerAccount.CASH,
                PostingDirection.DEBIT,
                fill.cash_delta,
                fill.currency,
            )
        ]
        if fill.commission_fee:
            postings.append(
                _posting(
                    LedgerAccount.COMMISSION_EXPENSE,
                    PostingDirection.DEBIT,
                    fill.commission_fee,
                    fill.currency,
                )
            )
        if fill.tax:
            postings.append(
                _posting(
                    LedgerAccount.TAX_EXPENSE,
                    PostingDirection.DEBIT,
                    fill.tax,
                    fill.currency,
                )
            )
        if fill.cost_basis_removed:
            postings.append(
                _posting(
                    LedgerAccount.POSITION_COST,
                    PostingDirection.CREDIT,
                    fill.cost_basis_removed,
                    fill.currency,
                    instrument_id=fill.instrument_id,
                )
            )
        if fill.realized_pnl > 0:
            postings.append(
                _posting(
                    LedgerAccount.REALIZED_PNL,
                    PostingDirection.CREDIT,
                    fill.realized_pnl,
                    fill.currency,
                )
            )
        elif fill.realized_pnl < 0:
            postings.append(
                _posting(
                    LedgerAccount.REALIZED_PNL,
                    PostingDirection.DEBIT,
                    fill.realized_pnl.copy_negate(),
                    fill.currency,
                )
            )
    return tuple(postings)


def _event(
    *,
    sequence: int,
    kind: LedgerEventKind,
    ledger_id: UUID,
    policy_digest: str,
    occurred_at: datetime,
    postings: tuple[JournalPosting, ...],
    previous_hash: str,
    batch_id: UUID | None = None,
    session_id: UUID | None = None,
    batch_request_digest: str | None = None,
    order_id: UUID | None = None,
    fill: ExecutionFill | None = None,
) -> LedgerEvent:
    payload = {
        "event_version": "simulation-ledger-event/1",
        "sequence": sequence,
        "kind": kind,
        "ledger_id": ledger_id,
        "policy_digest": policy_digest,
        "occurred_at": occurred_at,
        "batch_id": batch_id,
        "session_id": session_id,
        "batch_request_digest": batch_request_digest,
        "order_id": order_id,
        "fill": None if fill is None else fill.model_dump(mode="python"),
        "postings": [posting.model_dump(mode="python") for posting in postings],
        "previous_hash": previous_hash,
    }
    return LedgerEvent(**payload, event_hash=canonical_digest(payload))


def _create_simulation_ledger(policy: SimulationLedgerPolicy) -> SimulationLedgerState:
    """Create a deterministic ledger with a balanced opening transaction."""

    policy = SimulationLedgerPolicy.model_validate(policy)
    postings = (
        _posting(
            LedgerAccount.CASH,
            PostingDirection.DEBIT,
            policy.initial_cash,
            policy.base_currency,
        ),
        _posting(
            LedgerAccount.OPENING_EQUITY,
            PostingDirection.CREDIT,
            policy.initial_cash,
            policy.base_currency,
        ),
    )
    opening = _event(
        sequence=1,
        kind=LedgerEventKind.OPENING_BALANCE,
        ledger_id=policy.ledger_id,
        policy_digest=policy.digest,
        occurred_at=policy.opened_at,
        postings=postings,
        previous_hash=LEDGER_GENESIS_HASH,
    )
    return SimulationLedgerState(
        ledger_id=policy.ledger_id,
        policy_digest=policy.digest,
        base_currency=policy.base_currency,
        money_quantum=policy.money_quantum,
        cash=policy.initial_cash,
        realized_pnl=Decimal("0"),
        positions=(),
        events=(opening,),
        processed_batches=(),
        last_event_hash=opening.event_hash,
        next_sequence=2,
    )


def create_simulation_ledger(policy: SimulationLedgerPolicy) -> SimulationLedgerState:
    """Create a deterministic ledger under its private Decimal context."""

    with localcontext(_DECIMAL_CONTEXT):
        if type(policy) is not SimulationLedgerPolicy:
            raise SimulationLedgerError("policy must use the exact contract type")
        return _create_simulation_ledger(SimulationLedgerPolicy.model_validate(policy))


def _assert_state_matches_policy(
    state: SimulationLedgerState,
    policy: SimulationLedgerPolicy,
) -> None:
    if state.base_currency != policy.base_currency or state.money_quantum != policy.money_quantum:
        raise SimulationLedgerError("ledger state currency or quantum differs from policy")
    opening = state.events[0]
    if (
        opening.kind is not LedgerEventKind.OPENING_BALANCE
        or opening.occurred_at != policy.opened_at
        or opening.postings[0].amount != policy.initial_cash
        or opening.postings[0].currency != policy.base_currency
    ):
        raise SimulationLedgerError("ledger opening event differs from policy")

    rules = {rule.instrument_id: rule for rule in policy.instrument_rules}
    cash = policy.initial_cash
    realized_pnl = Decimal("0")
    positions: dict[UUID, tuple[Decimal, Decimal]] = {}
    for event in state.events[1:]:
        fill = event.fill
        if type(fill) is not ExecutionFill:
            raise SimulationLedgerError("committed trade event has no exact fill")
        rule = rules.get(fill.instrument_id)
        if rule is None or fill.currency != rule.currency:
            raise SimulationLedgerError("committed fill has no matching instrument rule")
        expected_quantity = _step_quantize(
            fill.requested_quantity, rule.lot_size, rounding=ROUND_FLOOR
        )
        if fill.executed_quantity != expected_quantity:
            raise SimulationLedgerError("committed fill quantity differs from lot policy")
        slip_rate = policy.slippage_bps / _BPS_DENOMINATOR
        unrounded_fill = (
            fill.raw_open * (Decimal("1") + slip_rate)
            if fill.side is SimulationSide.BUY
            else fill.raw_open * (Decimal("1") - slip_rate)
        )
        expected_price = _step_quantize(
            unrounded_fill,
            rule.price_tick,
            rounding=(ROUND_CEILING if fill.side is SimulationSide.BUY else ROUND_FLOOR),
        )
        if fill.fill_price != expected_price:
            raise SimulationLedgerError("committed fill price differs from slippage policy")
        expected_raw_notional = _money(fill.raw_open * fill.executed_quantity, policy.money_quantum)
        expected_fill_notional = _money(
            fill.fill_price * fill.executed_quantity, policy.money_quantum
        )
        expected_commission = _step_quantize(
            expected_fill_notional * policy.commission_bps / _BPS_DENOMINATOR,
            policy.money_quantum,
            rounding=ROUND_CEILING,
        )
        expected_tax = _step_quantize(
            expected_fill_notional * policy.tax_bps / _BPS_DENOMINATOR,
            policy.money_quantum,
            rounding=ROUND_CEILING,
        )
        if (
            fill.raw_notional != expected_raw_notional
            or fill.fill_notional != expected_fill_notional
            or fill.commission_fee != expected_commission
            or fill.tax != expected_tax
        ):
            raise SimulationLedgerError("committed fill costs differ from policy")
        expected_cash_delta = (
            -(expected_fill_notional + expected_commission + expected_tax)
            if fill.side is SimulationSide.BUY
            else expected_fill_notional - expected_commission - expected_tax
        )
        if fill.cash_delta != expected_cash_delta:
            raise SimulationLedgerError("committed fill cash movement differs from policy")
        prior_quantity, prior_basis = positions.get(
            fill.instrument_id, (Decimal("0"), Decimal("0"))
        )
        if fill.side is SimulationSide.BUY:
            if -fill.cash_delta > cash:
                raise SimulationLedgerError("committed history makes cash negative")
            cash = _decimal_add(cash, fill.cash_delta)
            positions[fill.instrument_id] = (
                _decimal_add(prior_quantity, fill.executed_quantity),
                _decimal_add(prior_basis, fill.fill_notional),
            )
        else:
            if fill.executed_quantity > prior_quantity:
                raise SimulationLedgerError("committed history contains a short sale")
            expected_basis_removed = (
                prior_basis
                if fill.executed_quantity == prior_quantity
                else _money(
                    prior_basis * fill.executed_quantity / prior_quantity,
                    policy.money_quantum,
                )
            )
            if fill.cost_basis_removed != expected_basis_removed:
                raise SimulationLedgerError(
                    "committed fill cost basis differs from sequential history"
                )
            cash = _decimal_add(cash, fill.cash_delta)
            realized_pnl = _decimal_add(realized_pnl, fill.realized_pnl)
            remaining_quantity = prior_quantity - fill.executed_quantity
            remaining_basis = prior_basis - expected_basis_removed
            if remaining_quantity:
                positions[fill.instrument_id] = (remaining_quantity, remaining_basis)
            else:
                positions.pop(fill.instrument_id, None)
    expected_positions = {
        position.instrument_id: (position.quantity, position.cost_basis)
        for position in state.positions
    }
    if cash != state.cash or realized_pnl != state.realized_pnl:
        raise SimulationLedgerError("ledger totals differ from sequential history")
    if positions != expected_positions:
        raise SimulationLedgerError("positions differ from sequential history")


def _validate_external_anchor(
    state: SimulationLedgerState,
    expected_last_event_hash: str | None,
) -> None:
    if expected_last_event_hash is None:
        raise SimulationLedgerError("expected_last_event_hash is required")
    if type(expected_last_event_hash) is not str or (
        len(expected_last_event_hash) != 64
        or any(character not in "0123456789abcdef" for character in expected_last_event_hash)
    ):
        raise SimulationLedgerError("expected_last_event_hash must be a lowercase SHA-256")
    if not hmac.compare_digest(state.last_event_hash, expected_last_event_hash):
        raise SimulationLedgerError("ledger state does not match the externally anchored hash")


def _validate_request_anchor(
    batch: SimulationBatch,
    expected_request_digest: str | None,
) -> str:
    if expected_request_digest is None:
        raise SimulationLedgerError("expected_request_digest is required")
    if type(expected_request_digest) is not str or (
        len(expected_request_digest) != 64
        or any(character not in "0123456789abcdef" for character in expected_request_digest)
    ):
        raise SimulationLedgerError("expected_request_digest must be a lowercase SHA-256")
    request_digest = batch.request_digest
    if not hmac.compare_digest(request_digest, expected_request_digest):
        raise SimulationLedgerError(
            "simulation batch does not match the externally anchored request digest"
        )
    return request_digest


def _validate_state_and_policy(
    state: SimulationLedgerState,
    policy: SimulationLedgerPolicy,
    expected_last_event_hash: str | None,
) -> tuple[SimulationLedgerState, SimulationLedgerPolicy]:
    if type(state) is not SimulationLedgerState:
        raise SimulationLedgerError("state must use the exact contract type")
    if type(policy) is not SimulationLedgerPolicy:
        raise SimulationLedgerError("policy must use the exact contract type")
    _validate_external_anchor(state, expected_last_event_hash)
    state = SimulationLedgerState.model_validate(state)
    policy = SimulationLedgerPolicy.model_validate(policy)
    if state.ledger_id != policy.ledger_id or not hmac.compare_digest(
        state.policy_digest, policy.digest
    ):
        raise SimulationLedgerError("ledger state and policy do not match")
    _assert_state_matches_policy(state, policy)
    return state, policy


def execute_simulation_run(
    state: SimulationLedgerState,
    policy: SimulationLedgerPolicy,
    batches: tuple[SimulationBatch, ...],
    *,
    expected_last_event_hash: str | None = None,
    expected_request_digests: dict[UUID, str] | None = None,
) -> SimulationRunResult:
    """Validate once and fold a bounded set of complete sessions deterministically."""

    def _apply_new_batch(batch: SimulationBatch, request_digest: str) -> tuple[LedgerEvent, ...]:
        """Mutate only this call's transient accumulator after all anchors validate."""

        nonlocal cash, last_event_time, previous_hash, realized_pnl, sequence
        appended: list[LedgerEvent] = []

        for order in sorted(batch.orders, key=_order_sort_key):
            if order.execution_at <= policy.opened_at:
                raise SimulationLedgerError("order execution must follow ledger opening")
            if last_event_time is not None and order.execution_at <= last_event_time:
                raise SimulationLedgerError(
                    "a new batch must execute after all committed ledger events"
                )
            rule = rules.get(order.instrument_id)
            if rule is None:
                raise SimulationLedgerError("order instrument has no simulation rule")
            quantity = _step_quantize(order.requested_quantity, rule.lot_size, rounding=ROUND_FLOOR)
            if quantity <= 0:
                raise SimulationLedgerError("order quantity is below one executable lot")
            with localcontext(_DECIMAL_CONTEXT):
                slip_rate = policy.slippage_bps / _BPS_DENOMINATOR
                unrounded_fill = (
                    order.raw_open * (Decimal("1") + slip_rate)
                    if order.side is SimulationSide.BUY
                    else order.raw_open * (Decimal("1") - slip_rate)
                )
            price_rounding = ROUND_CEILING if order.side is SimulationSide.BUY else ROUND_FLOOR
            fill_price = _step_quantize(unrounded_fill, rule.price_tick, rounding=price_rounding)
            if fill_price <= 0:
                raise SimulationLedgerError("adverse sell slippage produced a non-positive price")
            with localcontext(_DECIMAL_CONTEXT):
                raw_notional = _money(order.raw_open * quantity, policy.money_quantum)
                fill_notional = _money(fill_price * quantity, policy.money_quantum)
                commission_fee = _step_quantize(
                    fill_notional * policy.commission_bps / _BPS_DENOMINATOR,
                    policy.money_quantum,
                    rounding=ROUND_CEILING,
                )
                tax = _step_quantize(
                    fill_notional * policy.tax_bps / _BPS_DENOMINATOR,
                    policy.money_quantum,
                    rounding=ROUND_CEILING,
                )
            if raw_notional <= 0 or fill_notional <= 0:
                raise SimulationLedgerError("order notional is below the money quantum")
            slippage_amount = (
                fill_notional - raw_notional
                if order.side is SimulationSide.BUY
                else raw_notional - fill_notional
            )
            if slippage_amount < 0:
                raise SimulationLedgerError("quantized fill is favorable instead of adverse")

            position = positions.get(order.instrument_id)
            cost_basis_removed = Decimal("0")
            trade_realized_pnl = Decimal("0")
            if order.side is SimulationSide.BUY:
                cash_outflow = _bounded_decimal(fill_notional + commission_fee + tax)
                if cash_outflow > cash:
                    raise InsufficientCashError("buy would make simulated cash negative")
                cash = _non_negative(_decimal_add(cash, -cash_outflow))
                new_quantity = _positive(
                    _decimal_add(quantity, Decimal("0") if position is None else position.quantity)
                )
                new_cost_basis = _positive(
                    _decimal_add(
                        fill_notional,
                        Decimal("0") if position is None else position.cost_basis,
                    )
                )
                positions[order.instrument_id] = PositionBalance(
                    instrument_id=order.instrument_id,
                    quantity=new_quantity,
                    cost_basis=new_cost_basis,
                    currency=policy.base_currency,
                )
                postings_list = [
                    _posting(
                        LedgerAccount.POSITION_COST,
                        PostingDirection.DEBIT,
                        fill_notional,
                        policy.base_currency,
                        instrument_id=order.instrument_id,
                    )
                ]
                if commission_fee:
                    postings_list.append(
                        _posting(
                            LedgerAccount.COMMISSION_EXPENSE,
                            PostingDirection.DEBIT,
                            commission_fee,
                            policy.base_currency,
                        )
                    )
                if tax:
                    postings_list.append(
                        _posting(
                            LedgerAccount.TAX_EXPENSE,
                            PostingDirection.DEBIT,
                            tax,
                            policy.base_currency,
                        )
                    )
                postings_list.append(
                    _posting(
                        LedgerAccount.CASH,
                        PostingDirection.CREDIT,
                        cash_outflow,
                        policy.base_currency,
                    )
                )
                cash_delta = -cash_outflow
            else:
                if position is None or quantity > position.quantity:
                    raise ShortSaleError("sell exceeds the settled simulated position")
                if quantity == position.quantity:
                    cost_basis_removed = position.cost_basis
                else:
                    with localcontext(_DECIMAL_CONTEXT):
                        cost_basis_removed = _money(
                            position.cost_basis * quantity / position.quantity,
                            policy.money_quantum,
                        )
                net_proceeds = _bounded_decimal(fill_notional - commission_fee - tax)
                if net_proceeds <= 0:
                    raise SimulationLedgerError("sell costs consume all simulated proceeds")
                trade_realized_pnl = _bounded_decimal(fill_notional - cost_basis_removed)
                cash = _non_negative(_decimal_add(cash, net_proceeds))
                realized_pnl = _bounded_decimal(_decimal_add(realized_pnl, trade_realized_pnl))
                remaining_quantity = _non_negative(position.quantity - quantity)
                remaining_cost = _non_negative(position.cost_basis - cost_basis_removed)
                if remaining_quantity == 0:
                    positions.pop(order.instrument_id)
                else:
                    positions[order.instrument_id] = PositionBalance(
                        instrument_id=order.instrument_id,
                        quantity=remaining_quantity,
                        cost_basis=remaining_cost,
                        currency=policy.base_currency,
                    )
                postings_list = [
                    _posting(
                        LedgerAccount.CASH,
                        PostingDirection.DEBIT,
                        net_proceeds,
                        policy.base_currency,
                    )
                ]
                if commission_fee:
                    postings_list.append(
                        _posting(
                            LedgerAccount.COMMISSION_EXPENSE,
                            PostingDirection.DEBIT,
                            commission_fee,
                            policy.base_currency,
                        )
                    )
                if tax:
                    postings_list.append(
                        _posting(
                            LedgerAccount.TAX_EXPENSE,
                            PostingDirection.DEBIT,
                            tax,
                            policy.base_currency,
                        )
                    )
                if cost_basis_removed:
                    postings_list.append(
                        _posting(
                            LedgerAccount.POSITION_COST,
                            PostingDirection.CREDIT,
                            cost_basis_removed,
                            policy.base_currency,
                            instrument_id=order.instrument_id,
                        )
                    )
                if trade_realized_pnl > 0:
                    postings_list.append(
                        _posting(
                            LedgerAccount.REALIZED_PNL,
                            PostingDirection.CREDIT,
                            trade_realized_pnl,
                            policy.base_currency,
                        )
                    )
                elif trade_realized_pnl < 0:
                    postings_list.append(
                        _posting(
                            LedgerAccount.REALIZED_PNL,
                            PostingDirection.DEBIT,
                            -trade_realized_pnl,
                            policy.base_currency,
                        )
                    )
                cash_delta = net_proceeds

            fill = ExecutionFill(
                order_digest=order.digest,
                order_id=order.order_id,
                instrument_id=order.instrument_id,
                side=order.side,
                currency=policy.base_currency,
                executed_at=order.execution_at,
                requested_quantity=order.requested_quantity,
                executed_quantity=quantity,
                raw_open=order.raw_open,
                fill_price=fill_price,
                raw_notional=raw_notional,
                fill_notional=fill_notional,
                slippage_amount=slippage_amount,
                commission_fee=commission_fee,
                tax=tax,
                cost_basis_removed=cost_basis_removed,
                realized_pnl=trade_realized_pnl,
                cash_delta=cash_delta,
            )
            event = _event(
                sequence=sequence,
                kind=LedgerEventKind.TRADE_FILL,
                ledger_id=state.ledger_id,
                policy_digest=state.policy_digest,
                occurred_at=order.execution_at,
                batch_id=batch.batch_id,
                session_id=batch.session_id,
                batch_request_digest=request_digest,
                order_id=order.order_id,
                fill=fill,
                postings=tuple(postings_list),
                previous_hash=previous_hash,
            )
            appended.append(event)
            previous_hash = event.event_hash
            sequence += 1

        receipt = BatchReceipt(
            batch_id=batch.batch_id,
            session_id=batch.session_id,
            execution_at=batch.execution_at,
            request_digest=request_digest,
            first_event_sequence=appended[0].sequence,
            last_event_sequence=appended[-1].sequence,
            result_event_hash=appended[-1].event_hash,
        )
        events_buffer.extend(appended)
        receipts_buffer.append(receipt)
        last_event_time = appended[-1].occurred_at
        if len(events_buffer) > 10_000 or len(receipts_buffer) > 9_999:
            raise SimulationLedgerError("simulation ledger history exceeds its resource limit")
        return tuple(appended)

    with localcontext(_DECIMAL_CONTEXT):
        state, policy = _validate_state_and_policy(state, policy, expected_last_event_hash)
        if type(batches) is not tuple or not 1 <= len(batches) <= 9_999:
            raise SimulationLedgerError("batches must be a bounded non-empty tuple")
        if any(type(batch) is not SimulationBatch for batch in batches):
            raise SimulationLedgerError("batches must use the exact contract type")
        receipts_by_batch = {receipt.batch_id: receipt for receipt in state.processed_batches}
        committed_session_ids = {receipt.session_id for receipt in state.processed_batches}
        committed_order_ids = {
            event.order_id for event in state.events if event.order_id is not None
        }
        remaining_events = 10_000 - len(state.events)
        total_new_orders = 0
        total_submitted_orders = 0
        for batch in batches:
            if type(batch.orders) is not tuple:
                raise SimulationLedgerError("batch orders must use a bounded tuple")
            total_submitted_orders += len(batch.orders)
            if total_submitted_orders > 9_999:
                raise SimulationLedgerError(
                    "simulation run input exceeds the order/event resource limit"
                )
            if batch.batch_id not in receipts_by_batch:
                total_new_orders += len(batch.orders)
            if total_new_orders > remaining_events:
                raise SimulationLedgerError("simulation run exceeds the event resource limit")
        raw_batch_ids = tuple(batch.batch_id for batch in batches)
        if (
            type(expected_request_digests) is not dict
            or len(expected_request_digests) != len(raw_batch_ids)
            or set(expected_request_digests) != set(raw_batch_ids)
        ):
            raise SimulationLedgerError(
                "expected_request_digests must bind every batch ID exactly once"
            )
        validated_batches = tuple(SimulationBatch.model_validate(batch) for batch in batches)
        ordered_batches = tuple(
            sorted(
                validated_batches,
                key=lambda item: (
                    item.execution_at,
                    item.session_id.bytes,
                    item.batch_id.bytes,
                ),
            )
        )
        batch_ids = tuple(batch.batch_id for batch in ordered_batches)
        session_ids = tuple(batch.session_id for batch in ordered_batches)
        if len(set(batch_ids)) != len(batch_ids):
            raise IdempotencyConflictError("a simulation run cannot repeat a batch ID")
        if len(set(session_ids)) != len(session_ids):
            raise IdempotencyConflictError("a simulation run cannot repeat a session ID")
        seen_order_ids: set[UUID] = set()
        anchored_request_digests: dict[UUID, str] = {}
        for batch in ordered_batches:
            request_digest = _validate_request_anchor(
                batch, expected_request_digests[batch.batch_id]
            )
            anchored_request_digests[batch.batch_id] = request_digest
            existing_receipt = receipts_by_batch.get(batch.batch_id)
            if existing_receipt is not None:
                if not hmac.compare_digest(existing_receipt.request_digest, request_digest):
                    raise IdempotencyConflictError("batch ID was reused with a different payload")
                continue
            if batch.session_id in committed_session_ids:
                raise IdempotencyConflictError(
                    "session ID was already committed under another batch"
                )
            batch_order_ids = {order.order_id for order in batch.orders}
            if batch_order_ids & (committed_order_ids | seen_order_ids):
                raise IdempotencyConflictError("order ID was already committed in another batch")
            seen_order_ids.update(batch_order_ids)

        rules = {rule.instrument_id: rule for rule in policy.instrument_rules}
        positions = {position.instrument_id: position for position in state.positions}
        cash = state.cash
        realized_pnl = state.realized_pnl
        events_buffer = list(state.events)
        receipts_buffer = list(state.processed_batches)
        previous_hash = state.last_event_hash
        sequence = state.next_sequence
        last_event_time = state.events[-1].occurred_at if len(state.events) > 1 else None
        request_digests: list[str] = []
        appended_events: list[LedgerEvent] = []
        for batch in ordered_batches:
            request_digest = anchored_request_digests[batch.batch_id]
            request_digests.append(request_digest)
            if batch.batch_id in receipts_by_batch:
                continue
            appended_events.extend(_apply_new_batch(batch, request_digest))
        next_positions = tuple(
            sorted(positions.values(), key=lambda item: item.instrument_id.bytes)
        )
        if len(next_positions) > 1_000:
            raise SimulationLedgerError("simulation positions exceed their resource limit")
        current = SimulationLedgerState.model_construct(
            ledger_id=state.ledger_id,
            policy_digest=state.policy_digest,
            base_currency=state.base_currency,
            money_quantum=state.money_quantum,
            cash=cash,
            realized_pnl=realized_pnl,
            positions=next_positions,
            events=tuple(events_buffer),
            processed_batches=tuple(receipts_buffer),
            last_event_hash=previous_hash,
            next_sequence=sequence,
        )
        return SimulationRunResult(
            request_digests=tuple(request_digests),
            appended_events=tuple(appended_events),
            state=current,
        )


def execute_simulation_batch(
    state: SimulationLedgerState,
    policy: SimulationLedgerPolicy,
    batch: SimulationBatch,
    *,
    expected_last_event_hash: str | None = None,
    expected_request_digest: str | None = None,
) -> SimulationBatchResult:
    """Validate both external anchors, then apply one batch atomically."""

    if type(batch) is not SimulationBatch:
        raise SimulationLedgerError("batch must use the exact contract type")
    run_result = execute_simulation_run(
        state,
        policy,
        (batch,),
        expected_last_event_hash=expected_last_event_hash,
        expected_request_digests={batch.batch_id: expected_request_digest},
    )
    appended_events = run_result.appended_events
    return SimulationBatchResult(
        request_digest=run_result.request_digests[0],
        replay_status="applied" if appended_events else "replayed",
        appended_events=appended_events,
        state=run_result.state,
    )


__all__ = [
    "LEDGER_GENESIS_HASH",
    "M5_2B1_SCOPE",
    "BatchReceipt",
    "ExecutionFill",
    "IdempotencyConflictError",
    "InsufficientCashError",
    "InstrumentSimulationRule",
    "JournalPosting",
    "LedgerAccount",
    "LedgerEvent",
    "LedgerEventKind",
    "PositionBalance",
    "PostingDirection",
    "ShortSaleError",
    "SimulationBatch",
    "SimulationBatchResult",
    "SimulationLedgerError",
    "SimulationLedgerPolicy",
    "SimulationLedgerState",
    "SimulationRunResult",
    "SimulationOrder",
    "SimulationSide",
    "create_simulation_ledger",
    "execute_simulation_batch",
    "execute_simulation_run",
]

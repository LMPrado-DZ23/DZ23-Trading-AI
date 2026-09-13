"""Private, deterministic whole-session economic ledger reducer for M5.2c2.

This module validates and rederives complete PRE_OPEN -> fills -> POST_CLOSE
transitions.  It does not mint authority: the private reducer is intentionally
called only by the integrated session authority, which obtains every input
from sealed PIT and execution authorities.
"""

from __future__ import annotations

import hmac
import re
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tradingagents.platform.backtest.contracts import (
    M5_UNVERIFIED_LIMITATIONS,
    Currency,
    DatasetLimitation,
    canonical_digest,
)
from tradingagents.platform.backtest.simulation_economics import (
    AuthorizedCorporateCashConversion,
    AuthorizedEconomicEventView,
    CorporateActionApplication,
    CorporateActionProjection,
    EconomicPhase,
)
from tradingagents.platform.backtest.simulation_ledger import ExecutionFill, SimulationSide

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ECONOMIC_LEDGER_GENESIS_HASH = "0" * 64
_CTX = Context(prec=76, rounding=ROUND_HALF_EVEN)


class SimulationEconomicLedgerError(ValueError):
    pass


class EconomicSessionConflictError(SimulationEconomicLedgerError):
    pass


class FillEligibilityV2(StrEnum):
    ACTIVE = "active"
    BLOCKED_DELISTED = "blocked_delisted"


class DividendPaymentStatusV2(StrEnum):
    UNPAID = "unpaid"
    PAID = "paid"


class EconomicStageV2(StrEnum):
    PRE_OPEN = "pre_open"
    TRADE_FILL = "trade_fill"
    POST_CLOSE = "post_close"


class EconomicEventKindV2(StrEnum):
    TRADE_FILL = "trade_fill"
    DIVIDEND_CASH = "dividend_cash"
    DELISTING_SETTLEMENT = "delisting_settlement"
    SPLIT = "split"
    DIVIDEND_ENTITLEMENT = "dividend_entitlement"
    DELISTING_BLOCK = "delisting_block"
    DIVIDEND_PAYMENT_ZERO = "dividend_payment_zero"
    DELISTING_SETTLEMENT_ZERO = "delisting_settlement_zero"


class PostingDirectionV2(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"


class EconomicLedgerAccountV2(StrEnum):
    CASH = "cash"
    POSITION_COST = "position_cost"
    COMMISSION_EXPENSE = "commission_expense"
    TAX_EXPENSE = "tax_expense"
    REALIZED_PNL = "realized_pnl"
    CORPORATE_ACTION_INCOME = "corporate_action_income"


class Contract(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", str_strip_whitespace=True, revalidate_instances="always"
    )

    @property
    def digest(self) -> str:
        return canonical_digest(self)


def _utc(v: datetime) -> datetime:
    if v.tzinfo is None or v.utcoffset() is None or v.utcoffset().total_seconds() != 0:
        raise ValueError("timestamp must use UTC")
    return v.astimezone(UTC)


def _exact(v: Any) -> Any:
    if (
        isinstance(v, (bool, float))
        or (isinstance(v, str) and len(v) > 80)
        or (isinstance(v, int) and v.bit_length() > 256)
    ):
        raise ValueError("decimal input is not exact and bounded")
    return v


def _bound(v: Decimal) -> Decimal:
    if not v.is_finite():
        raise ValueError("decimal must be finite")
    _, ds, exponent = v.as_tuple()
    if len(ds) > 38 or v.adjusted() >= 38 or exponent < -18:
        raise ValueError("decimal exceeds precision or scale")
    return v


def _nonneg(v: Decimal) -> Decimal:
    _bound(v)
    if v < 0:
        raise ValueError("value must be non-negative")
    return v


def _positive(v: Decimal) -> Decimal:
    _bound(v)
    if v <= 0:
        raise ValueError("value must be positive")
    return v


def _add(a: Decimal, b: Decimal) -> Decimal:
    with localcontext(_CTX):
        return _bound(a + b)


def _mul(a: Decimal, b: Decimal) -> Decimal:
    with localcontext(_CTX):
        return _bound(a * b)


def _div(a: Decimal, b: Decimal) -> Decimal:
    if b == 0:
        raise ValueError("division by zero")
    with localcontext(_CTX):
        return _bound(a / b)


def _money(v: Decimal, q: Decimal) -> Decimal:
    with localcontext(_CTX):
        return _bound((v / q).to_integral_value(rounding=ROUND_HALF_EVEN) * q)


def _digest(payload: dict[str, Any]) -> str:
    def norm(v: Any) -> Any:
        if isinstance(v, BaseModel):
            return v.model_dump(mode="python")
        if isinstance(v, dict):
            return {k: norm(x) for k, x in v.items()}
        if isinstance(v, (tuple, list)):
            return tuple(norm(x) for x in v)
        return v

    return canonical_digest({k: norm(v) for k, v in payload.items()})


class EconomicPositionV2(Contract):
    position_version: Literal["economic-position/2"] = "economic-position/2"
    instrument_id: UUID
    quantity: Decimal
    cost_basis: Decimal
    currency: Currency
    fill_eligibility: FillEligibilityV2 = FillEligibilityV2.ACTIVE
    _e = field_validator("quantity", "cost_basis", mode="before")(_exact)
    _n = field_validator("quantity", "cost_basis")(_nonneg)

    @model_validator(mode="after")
    def valid(self):
        if self.quantity == 0 and self.cost_basis != 0:
            raise ValueError("empty position retains cost basis")
        return self


class DividendEntitlementV2(Contract):
    entitlement_version: Literal["dividend-entitlement/2"] = "dividend-entitlement/2"
    instrument_id: UUID
    action_id: UUID
    action_digest: Digest
    action_revision: int = Field(ge=1)
    entitled_quantity: Decimal
    cash_amount_per_share: Decimal
    source_currency: Currency
    payment_status: DividendPaymentStatusV2 = DividendPaymentStatusV2.UNPAID
    _e = field_validator("entitled_quantity", "cash_amount_per_share", mode="before")(_exact)
    _n = field_validator("entitled_quantity")(_nonneg)
    _p = field_validator("cash_amount_per_share")(_positive)


class EconomicActionKeyV2(Contract):
    key_version: Literal["economic-action-key/2"] = "economic-action-key/2"
    instrument_id: UUID
    action_id: UUID
    application: CorporateActionApplication


class EconomicPostingV2(Contract):
    posting_version: Literal["economic-posting/2"] = "economic-posting/2"
    account: EconomicLedgerAccountV2
    direction: PostingDirectionV2
    amount: Decimal
    currency: Currency
    instrument_id: UUID | None = None
    _e = field_validator("amount", mode="before")(_exact)
    _p = field_validator("amount")(_positive)

    @model_validator(mode="after")
    def valid(self):
        if (self.account is EconomicLedgerAccountV2.POSITION_COST) != (
            self.instrument_id is not None
        ):
            raise ValueError("invalid posting dimension")
        return self


class EconomicNonMonetaryTransitionV2(Contract):
    transition_version: Literal["economic-nonmonetary-transition/2"] = (
        "economic-nonmonetary-transition/2"
    )
    kind: Literal[
        EconomicEventKindV2.SPLIT,
        EconomicEventKindV2.DIVIDEND_ENTITLEMENT,
        EconomicEventKindV2.DELISTING_BLOCK,
        EconomicEventKindV2.DIVIDEND_PAYMENT_ZERO,
        EconomicEventKindV2.DELISTING_SETTLEMENT_ZERO,
    ]
    action_key: EconomicActionKeyV2

    @model_validator(mode="after")
    def valid(self):
        expected = {
            EconomicEventKindV2.SPLIT: CorporateActionApplication.SPLIT,
            EconomicEventKindV2.DIVIDEND_ENTITLEMENT: CorporateActionApplication.DIVIDEND_ENTITLEMENT,
            EconomicEventKindV2.DELISTING_BLOCK: CorporateActionApplication.DELISTING,
            EconomicEventKindV2.DIVIDEND_PAYMENT_ZERO: CorporateActionApplication.DIVIDEND_PAYMENT,
            EconomicEventKindV2.DELISTING_SETTLEMENT_ZERO: CorporateActionApplication.DELISTING_SETTLEMENT,
        }[self.kind]
        if self.action_key.application is not expected:
            raise ValueError("nonmonetary kind differs from action application")
        return self


class EconomicLedgerPolicyV2(Contract):
    policy_version: Literal["simulation-economic-ledger-policy/2"] = (
        "simulation-economic-ledger-policy/2"
    )
    tenant_id: UUID
    backtest_id: UUID
    ledger_id: UUID
    backtest_spec_digest: Digest
    trade_ledger_policy_digest: Digest
    economics_policy_digest: Digest
    opened_at: datetime
    functional_currency: Currency
    money_quantum: Decimal
    initial_cash: Decimal
    instrument_ids: tuple[UUID, ...] = Field(min_length=1, max_length=1_000)
    initial_positions: tuple[EconomicPositionV2, ...] = Field(min_length=1, max_length=1_000)
    quantitative_claims_status: Literal["ineligible_unverified_coverage"] = (
        "ineligible_unverified_coverage"
    )
    limitations: tuple[DatasetLimitation, ...] = M5_UNVERIFIED_LIMITATIONS
    _u = field_validator("opened_at")(_utc)
    _e = field_validator("money_quantum", "initial_cash", mode="before")(_exact)
    _q = field_validator("money_quantum")(_positive)
    _c = field_validator("initial_cash")(_nonneg)

    @model_validator(mode="after")
    def valid(self):
        ids = tuple(sorted(self.instrument_ids, key=str))
        positions = tuple(p.instrument_id for p in self.initial_positions)
        if (
            ids != self.instrument_ids
            or len(set(ids)) != len(ids)
            or positions != ids
            or any(type(item) is not EconomicPositionV2 for item in self.initial_positions)
        ):
            raise ValueError("policy instruments and positions must be complete, unique, sorted")
        if self.initial_cash != _money(self.initial_cash, self.money_quantum) or any(
            p.currency != self.functional_currency
            or p.cost_basis != _money(p.cost_basis, self.money_quantum)
            for p in self.initial_positions
        ):
            raise ValueError("initial money is invalid")
        if self.limitations != M5_UNVERIFIED_LIMITATIONS:
            raise ValueError("claims limitations are not canonical")
        return self


def _fill_key(f: ExecutionFill):
    return (f.executed_at, str(f.instrument_id), str(f.order_id))


def _conv_key(c: AuthorizedCorporateCashConversion):
    return (str(c.instrument_id), str(c.action_id), c.application.value)


class EconomicSessionTransitionV2(Contract):
    transition_version: Literal["economic-session-transition/2"] = "economic-session-transition/2"
    session_id: UUID
    session_date: date
    pre_open_views: tuple[AuthorizedEconomicEventView, ...] = Field(min_length=1, max_length=1_000)
    trade_fills: tuple[ExecutionFill, ...] = Field(max_length=9_000)
    post_close_views: tuple[AuthorizedEconomicEventView, ...] = Field(
        min_length=1, max_length=1_000
    )
    cash_conversions: tuple[AuthorizedCorporateCashConversion, ...] = Field(max_length=1_000)
    request_digest: Digest

    @model_validator(mode="after")
    def valid(self):
        if (
            any(
                type(v) is not AuthorizedEconomicEventView
                for v in self.pre_open_views + self.post_close_views
            )
            or any(type(f) is not ExecutionFill for f in self.trade_fills)
            or any(type(c) is not AuthorizedCorporateCashConversion for c in self.cash_conversions)
        ):
            raise ValueError("transition contains another contract type")
        pre = tuple(v.instrument_id for v in self.pre_open_views)
        post = tuple(v.instrument_id for v in self.post_close_views)
        if pre != tuple(sorted(pre, key=str)) or pre != post or len(set(pre)) != len(pre):
            raise ValueError("views must be complete, unique, canonical")
        if any(
            v.view.session_date != self.session_date or v.view.phase is not EconomicPhase.PRE_OPEN
            for v in self.pre_open_views
        ) or any(
            v.view.session_date != self.session_date or v.view.phase is not EconomicPhase.POST_CLOSE
            for v in self.post_close_views
        ):
            raise ValueError("view date or phase is invalid")
        if any(
            pre_view.view.as_of >= post_view.view.as_of
            for pre_view, post_view in zip(self.pre_open_views, self.post_close_views, strict=True)
        ):
            raise ValueError("PRE_OPEN must precede POST_CLOSE")
        keys = tuple(_fill_key(f) for f in self.trade_fills)
        if keys != tuple(sorted(keys)) or len({f.order_id for f in self.trade_fills}) != len(
            self.trade_fills
        ):
            raise ValueError("fills must be unique and canonical")
        windows = {
            a.instrument_id: (a.view.as_of, b.view.as_of)
            for a, b in zip(self.pre_open_views, self.post_close_views, strict=True)
        }
        if any(
            f.instrument_id not in windows
            or not windows[f.instrument_id][0] <= f.executed_at <= windows[f.instrument_id][1]
            for f in self.trade_fills
        ):
            raise ValueError("fill is outside its authority window")
        ckeys = tuple(_conv_key(c) for c in self.cash_conversions)
        if ckeys != tuple(sorted(ckeys)) or len(set(ckeys)) != len(ckeys):
            raise ValueError("conversions must be unique and canonical")
        akeys = [
            (a.instrument_id, a.action_id, a.application)
            for v in self.pre_open_views + self.post_close_views
            for a in v.view.actions
        ]
        if len(set(akeys)) != len(akeys):
            raise ValueError("transition repeats a logical action key")
        if len(akeys) + len(self.trade_fills) > 10_000:
            raise ValueError("transition exceeds the atomic event bound")
        expected = _digest(self.model_dump(mode="python", exclude={"request_digest"}))
        if not hmac.compare_digest(expected, self.request_digest):
            raise ValueError("transition digest is invalid")
        return self


class EconomicLedgerEventV2(Contract):
    event_version: Literal["simulation-economic-ledger-event/2"] = (
        "simulation-economic-ledger-event/2"
    )
    sequence: int = Field(ge=1)
    ledger_id: UUID
    policy_digest: Digest
    session_id: UUID
    session_date: date
    request_digest: Digest
    stage: EconomicStageV2
    kind: EconomicEventKindV2
    occurred_at: datetime
    fill: ExecutionFill | None = None
    action: CorporateActionProjection | None = None
    conversion: AuthorizedCorporateCashConversion | None = None
    nonmonetary: EconomicNonMonetaryTransitionV2 | None = None
    postings: tuple[EconomicPostingV2, ...] = Field(max_length=8)
    cash_before: Decimal
    cash_after: Decimal
    realized_pnl_before: Decimal
    realized_pnl_after: Decimal
    position_before: EconomicPositionV2
    position_after: EconomicPositionV2
    entitlement_before: DividendEntitlementV2 | None = None
    entitlement_after: DividendEntitlementV2 | None = None
    previous_hash: Digest
    event_hash: Digest
    _u = field_validator("occurred_at")(_utc)
    _e = field_validator(
        "cash_before", "cash_after", "realized_pnl_before", "realized_pnl_after", mode="before"
    )(_exact)

    @model_validator(mode="after")
    def valid(self):
        nonmoney = self.kind in {
            EconomicEventKindV2.SPLIT,
            EconomicEventKindV2.DIVIDEND_ENTITLEMENT,
            EconomicEventKindV2.DELISTING_BLOCK,
            EconomicEventKindV2.DIVIDEND_PAYMENT_ZERO,
            EconomicEventKindV2.DELISTING_SETTLEMENT_ZERO,
        }
        if (
            type(self.position_before) is not EconomicPositionV2
            or type(self.position_after) is not EconomicPositionV2
            or any(type(item) is not EconomicPostingV2 for item in self.postings)
            or (self.fill is not None and type(self.fill) is not ExecutionFill)
            or (self.action is not None and type(self.action) is not CorporateActionProjection)
            or (
                self.conversion is not None
                and type(self.conversion) is not AuthorizedCorporateCashConversion
            )
            or (
                self.nonmonetary is not None
                and type(self.nonmonetary) is not EconomicNonMonetaryTransitionV2
            )
            or (
                self.entitlement_before is not None
                and type(self.entitlement_before) is not DividendEntitlementV2
            )
            or (
                self.entitlement_after is not None
                and type(self.entitlement_after) is not DividendEntitlementV2
            )
        ):
            raise ValueError("event contains another contract type")
        if (
            (self.kind is EconomicEventKindV2.TRADE_FILL) != (self.fill is not None)
            or (self.action is not None) == (self.fill is not None)
            or nonmoney != (self.nonmonetary is not None)
        ):
            raise ValueError("event sources are inconsistent")
        if nonmoney and self.postings:
            raise ValueError("nonmonetary event has postings")
        if not nonmoney:
            debit = sum(
                (p.amount for p in self.postings if p.direction is PostingDirectionV2.DEBIT),
                Decimal("0"),
            )
            credit = sum(
                (p.amount for p in self.postings if p.direction is PostingDirectionV2.CREDIT),
                Decimal("0"),
            )
            if len(self.postings) < 2 or debit != credit:
                raise ValueError("monetary event is not balanced")
        if not hmac.compare_digest(
            self.event_hash, _digest(self.model_dump(mode="python", exclude={"event_hash"}))
        ):
            raise ValueError("event hash is invalid")
        return self


class EconomicSessionReceiptV2(Contract):
    receipt_version: Literal["economic-session-receipt/2"] = "economic-session-receipt/2"
    transition: EconomicSessionTransitionV2
    first_event_sequence: int | None = Field(default=None, ge=1)
    last_event_sequence: int | None = Field(default=None, ge=1)
    event_count: int = Field(ge=0, le=10_000)
    result_event_hash: Digest
    previous_receipt_hash: Digest
    receipt_hash: Digest
    quantitative_claims_status: Literal["ineligible_unverified_coverage"] = (
        "ineligible_unverified_coverage"
    )
    limitations: tuple[DatasetLimitation, ...] = M5_UNVERIFIED_LIMITATIONS

    @model_validator(mode="after")
    def valid(self):
        if type(self.transition) is not EconomicSessionTransitionV2:
            raise ValueError("receipt contains another transition contract type")
        if (
            self.event_count == 0
            and (self.first_event_sequence is not None or self.last_event_sequence is not None)
        ) or (
            self.event_count
            and (
                self.first_event_sequence is None
                or self.last_event_sequence is None
                or self.last_event_sequence - self.first_event_sequence + 1 != self.event_count
            )
        ):
            raise ValueError("receipt range is invalid")
        if self.limitations != M5_UNVERIFIED_LIMITATIONS or not hmac.compare_digest(
            self.receipt_hash, _digest(self.model_dump(mode="python", exclude={"receipt_hash"}))
        ):
            raise ValueError("receipt hash or claims taint is invalid")
        return self


class SimulationEconomicLedgerStateV2(Contract):
    state_version: Literal["simulation-economic-ledger-state/2"] = (
        "simulation-economic-ledger-state/2"
    )
    policy: EconomicLedgerPolicyV2
    cash: Decimal
    realized_pnl: Decimal
    positions: tuple[EconomicPositionV2, ...]
    entitlements: tuple[DividendEntitlementV2, ...] = Field(max_length=10_000)
    events: tuple[EconomicLedgerEventV2, ...] = Field(max_length=10_000)
    session_receipts: tuple[EconomicSessionReceiptV2, ...] = Field(max_length=10_000)
    last_event_hash: Digest
    last_session_receipt_hash: Digest
    next_sequence: int = Field(ge=1)
    quantitative_claims_status: Literal["ineligible_unverified_coverage"] = (
        "ineligible_unverified_coverage"
    )
    limitations: tuple[DatasetLimitation, ...] = M5_UNVERIFIED_LIMITATIONS
    state_hash: Digest
    _e = field_validator("cash", "realized_pnl", mode="before")(_exact)

    @model_validator(mode="after")
    def valid(self):
        if (
            self.quantitative_claims_status != self.policy.quantitative_claims_status
            or self.limitations != self.policy.limitations
        ):
            raise ValueError("state lost claims taint")
        if (
            self.cash != _money(self.cash, self.policy.money_quantum)
            or self.realized_pnl != _money(self.realized_pnl, self.policy.money_quantum)
            or any(
                item.cost_basis != _money(item.cost_basis, self.policy.money_quantum)
                for item in self.positions
            )
        ):
            raise ValueError("state monetary balances are not quantized")
        if not hmac.compare_digest(
            self.state_hash, _digest(self.model_dump(mode="python", exclude={"state_hash"}))
        ):
            raise ValueError("state hash is invalid")
        _validate_state_history(self)
        return self

    def position(self, instrument_id: UUID) -> EconomicPositionV2:
        return next(p for p in self.positions if p.instrument_id == instrument_id)


class EconomicSessionResultV2(Contract):
    result_version: Literal["economic-session-result/2"] = "economic-session-result/2"
    request_digest: Digest
    replay_status: Literal["applied", "replayed"]
    appended_events: tuple[EconomicLedgerEventV2, ...]
    receipt: EconomicSessionReceiptV2
    state: SimulationEconomicLedgerStateV2

    @model_validator(mode="after")
    def valid(self):
        if (
            type(self.receipt) is not EconomicSessionReceiptV2
            or type(self.state) is not SimulationEconomicLedgerStateV2
            or any(type(item) is not EconomicLedgerEventV2 for item in self.appended_events)
            or self.request_digest != self.receipt.transition.request_digest
        ):
            raise ValueError("economic session result bindings are invalid")
        if self.replay_status == "replayed":
            if self.appended_events or self.receipt not in self.state.session_receipts:
                raise ValueError("replayed result cannot append events")
        else:
            expected = self.state.events[-self.receipt.event_count :] if self.receipt.event_count else ()
            if tuple(expected) != self.appended_events:
                raise ValueError("applied result does not contain the receipted events")
            if (
                not self.state.session_receipts
                or self.state.session_receipts[-1] != self.receipt
                or self.state.last_session_receipt_hash != self.receipt.receipt_hash
                or self.state.last_event_hash != self.receipt.result_event_hash
            ):
                raise ValueError("economic session result does not match state heads")
        return self


def _post(account, direction, amount, currency, instrument_id=None):
    return EconomicPostingV2(
        account=account,
        direction=direction,
        amount=amount,
        currency=currency,
        instrument_id=instrument_id,
    )


def _fill_postings(fill: ExecutionFill):
    if fill.side is SimulationSide.BUY:
        result = [
            _post(
                EconomicLedgerAccountV2.POSITION_COST,
                PostingDirectionV2.DEBIT,
                fill.fill_notional,
                fill.currency,
                fill.instrument_id,
            )
        ]
        for account, amount in (
            (EconomicLedgerAccountV2.COMMISSION_EXPENSE, fill.commission_fee),
            (EconomicLedgerAccountV2.TAX_EXPENSE, fill.tax),
        ):
            if amount:
                result.append(_post(account, PostingDirectionV2.DEBIT, amount, fill.currency))
        result.append(
            _post(
                EconomicLedgerAccountV2.CASH,
                PostingDirectionV2.CREDIT,
                fill.cash_delta.copy_abs(),
                fill.currency,
            )
        )
        return tuple(result)
    result = [
        _post(
            EconomicLedgerAccountV2.CASH, PostingDirectionV2.DEBIT, fill.cash_delta, fill.currency
        )
    ]
    for account, amount in (
        (EconomicLedgerAccountV2.COMMISSION_EXPENSE, fill.commission_fee),
        (EconomicLedgerAccountV2.TAX_EXPENSE, fill.tax),
    ):
        if amount:
            result.append(_post(account, PostingDirectionV2.DEBIT, amount, fill.currency))
    if fill.cost_basis_removed:
        result.append(
            _post(
                EconomicLedgerAccountV2.POSITION_COST,
                PostingDirectionV2.CREDIT,
                fill.cost_basis_removed,
                fill.currency,
                fill.instrument_id,
            )
        )
    if fill.realized_pnl > 0:
        result.append(
            _post(
                EconomicLedgerAccountV2.REALIZED_PNL,
                PostingDirectionV2.CREDIT,
                fill.realized_pnl,
                fill.currency,
            )
        )
    elif fill.realized_pnl < 0:
        result.append(
            _post(
                EconomicLedgerAccountV2.REALIZED_PNL,
                PostingDirectionV2.DEBIT,
                fill.realized_pnl.copy_abs(),
                fill.currency,
            )
        )
    return tuple(result)


def _akey(action):
    return EconomicActionKeyV2(
        instrument_id=action.instrument_id,
        action_id=action.action_id,
        application=action.application,
    )


def _bound_view(view, policy):
    return (
        view.tenant_id == policy.tenant_id
        and view.backtest_id == policy.backtest_id
        and view.ledger_id == policy.ledger_id
        and view.backtest_spec_digest == policy.backtest_spec_digest
        and view.ledger_policy_digest == policy.trade_ledger_policy_digest
        and view.economics_policy_digest == policy.economics_policy_digest
        and view.quantitative_claims_status == policy.quantitative_claims_status
        and view.limitations == policy.limitations
    )


def _cash_amount(conversion, action, view, quantity, prior_receipt, policy):
    if type(conversion) is not AuthorizedCorporateCashConversion:
        raise SimulationEconomicLedgerError(
            "corporate cash conversion uses another contract type"
        )
    unit = (
        action.cash_amount
        if action.application is CorporateActionApplication.DIVIDEND_PAYMENT
        else action.cash_consideration
    )
    basis_payload = {
        "basis_version": "corporate-cash-ledger-basis/1",
        "tenant_id": conversion.tenant_id,
        "backtest_id": conversion.backtest_id,
        "ledger_id": conversion.ledger_id,
        "backtest_spec_digest": conversion.backtest_spec_digest,
        "ledger_policy_digest": conversion.ledger_policy_digest,
        "economics_policy_digest": conversion.economics_policy_digest,
        "checkpoint_id": conversion.checkpoint_id,
        "checkpoint_digest": conversion.checkpoint_digest,
        "checkpoint_generation": conversion.checkpoint_generation,
        "prior_session_receipt_digest": conversion.prior_session_receipt_digest,
        "instrument_id": conversion.instrument_id,
        "action_id": conversion.action_id,
        "application": conversion.application,
        "session_date": conversion.session_date,
        "phase": conversion.phase,
        "quantity_basis": conversion.quantity_basis,
        "quantity": conversion.expected_quantity,
        "quantity_source_digest": conversion.quantity_source_digest,
        "ledger_receipt_id": conversion.ledger_receipt_id,
    }
    if (
        conversion.tenant_id != policy.tenant_id
        or conversion.backtest_id != policy.backtest_id
        or conversion.ledger_id != policy.ledger_id
        or conversion.backtest_spec_digest != policy.backtest_spec_digest
        or conversion.ledger_policy_digest != policy.trade_ledger_policy_digest
        or conversion.economics_policy_digest != policy.economics_policy_digest
        or conversion.dataset_id != view.dataset_id
        or conversion.manifest_digest != view.manifest_digest
        or conversion.instrument_id != action.instrument_id
        or conversion.event_authority_digest != view.authority_digest
        or conversion.event_view_digest != view.view.view_digest
        or conversion.action_id != action.action_id
        or conversion.action_revision != action.revision
        or conversion.application is not action.application
        or conversion.action_digest != action.action_digest
        or conversion.action_projection_digest != action.projection_digest
        or conversion.expected_quantity != quantity
        or conversion.unit_cash_amount != unit
        or conversion.source_currency != action.currency
        or conversion.session_date != action.session_date
        or conversion.event_at != view.view.as_of
        or conversion.prior_session_receipt_digest != prior_receipt
        or conversion.conversion.functional_currency != policy.functional_currency
        or conversion.conversion.functional_amount
        != _money(conversion.conversion.functional_amount, policy.money_quantum)
        or conversion.conversion.money_quantum != policy.money_quantum
        or conversion.ledger_basis_digest != _digest(basis_payload)
        or conversion.quantitative_claims_status != policy.quantitative_claims_status
        or conversion.limitations != policy.limitations
    ):
        raise SimulationEconomicLedgerError(
            "corporate cash conversion is not bound to this transition"
        )
    return conversion.conversion.functional_amount


def _make_event(
    *,
    seq,
    policy,
    transition,
    stage,
    kind,
    occurred,
    before_cash,
    cash,
    before_pnl,
    pnl,
    before,
    after,
    previous,
    fill=None,
    action=None,
    conversion=None,
    nonmonetary=None,
    postings=(),
    entitlement_before=None,
    entitlement_after=None,
):
    payload = {
        "event_version": "simulation-economic-ledger-event/2",
        "sequence": seq,
        "ledger_id": policy.ledger_id,
        "policy_digest": policy.digest,
        "session_id": transition.session_id,
        "session_date": transition.session_date,
        "request_digest": transition.request_digest,
        "stage": stage,
        "kind": kind,
        "occurred_at": occurred,
        "fill": fill,
        "action": action,
        "conversion": conversion,
        "nonmonetary": nonmonetary,
        "postings": postings,
        "cash_before": before_cash,
        "cash_after": cash,
        "realized_pnl_before": before_pnl,
        "realized_pnl_after": pnl,
        "position_before": before,
        "position_after": after,
        "entitlement_before": entitlement_before,
        "entitlement_after": entitlement_after,
        "previous_hash": previous,
    }
    return EconomicLedgerEventV2(**payload, event_hash=_digest(payload))


def _derive_transition(
    *,
    policy,
    transition,
    cash,
    pnl,
    positions,
    entitlements,
    previous_event,
    previous_receipt,
    sequence,
    action_evidence,
    order_ids,
):
    if transition.session_date < policy.opened_at.date():
        raise SimulationEconomicLedgerError("session predates the economic ledger")
    ids = tuple(v.instrument_id for v in transition.pre_open_views)
    if (
        ids != policy.instrument_ids
        or tuple(v.instrument_id for v in transition.post_close_views) != policy.instrument_ids
    ):
        raise SimulationEconomicLedgerError("session views omit or add policy instruments")
    if any(
        not _bound_view(v, policy) for v in transition.pre_open_views + transition.post_close_views
    ):
        raise SimulationEconomicLedgerError("authorized view differs from ledger policy")
    conversions = {_conv_key(c): c for c in transition.cash_conversions}
    used = set()
    events = []

    def action_event(action, view, stage):
        nonlocal cash, pnl, previous_event, sequence
        key = (action.instrument_id, action.action_id, action.application)
        if key in action_evidence:
            raise EconomicSessionConflictError("logical corporate-action key already committed")
        before = positions[action.instrument_id]
        after = before
        ekey = (action.instrument_id, action.action_id)
        ebefore = entitlements.get(ekey)
        eafter = ebefore
        before_cash, before_pnl = cash, pnl
        conversion = None
        postings = ()
        nonmoney = None
        if action.application is CorporateActionApplication.SPLIT:
            if action.split_ratio is None:
                raise SimulationEconomicLedgerError("split lacks ratio")
            after = before.model_copy(
                update={"quantity": _mul(before.quantity, action.split_ratio)}
            )
            kind = EconomicEventKindV2.SPLIT
            nonmoney = EconomicNonMonetaryTransitionV2(kind=kind, action_key=_akey(action))
        elif action.application is CorporateActionApplication.DIVIDEND_ENTITLEMENT:
            if ebefore is not None or action.cash_amount is None or action.currency is None:
                raise SimulationEconomicLedgerError("invalid dividend entitlement")
            eafter = DividendEntitlementV2(
                instrument_id=action.instrument_id,
                action_id=action.action_id,
                action_digest=action.action_digest,
                action_revision=action.revision,
                entitled_quantity=before.quantity,
                cash_amount_per_share=action.cash_amount,
                source_currency=action.currency,
            )
            entitlements[ekey] = eafter
            kind = EconomicEventKindV2.DIVIDEND_ENTITLEMENT
            nonmoney = EconomicNonMonetaryTransitionV2(kind=kind, action_key=_akey(action))
        elif action.application is CorporateActionApplication.DELISTING:
            after = before.model_copy(
                update={"fill_eligibility": FillEligibilityV2.BLOCKED_DELISTED}
            )
            kind = EconomicEventKindV2.DELISTING_BLOCK
            nonmoney = EconomicNonMonetaryTransitionV2(kind=kind, action_key=_akey(action))
        elif action.application is CorporateActionApplication.DIVIDEND_PAYMENT:
            if (
                ebefore is None
                or ebefore.payment_status is not DividendPaymentStatusV2.UNPAID
                or action.revision < ebefore.action_revision
                or (
                    action.revision == ebefore.action_revision
                    and action.action_digest != ebefore.action_digest
                )
                or action.cash_amount != ebefore.cash_amount_per_share
                or action.currency != ebefore.source_currency
            ):
                raise SimulationEconomicLedgerError("dividend has no matching unpaid entitlement")
            eafter = ebefore.model_copy(update={"payment_status": DividendPaymentStatusV2.PAID})
            entitlements[ekey] = eafter
            if ebefore.entitled_quantity == 0:
                kind = EconomicEventKindV2.DIVIDEND_PAYMENT_ZERO
                nonmoney = EconomicNonMonetaryTransitionV2(kind=kind, action_key=_akey(action))
            else:
                conversion = conversions.get(
                    (str(action.instrument_id), str(action.action_id), action.application.value)
                )
                if conversion is None:
                    raise SimulationEconomicLedgerError("dividend lacks conversion")
                amount = _cash_amount(
                    conversion, action, view, ebefore.entitled_quantity, previous_receipt, policy
                )
                if amount <= 0:
                    raise SimulationEconomicLedgerError("dividend conversion has no monetary amount")
                used.add(_conv_key(conversion))
                cash = _add(cash, amount)
                postings = (
                    _post(
                        EconomicLedgerAccountV2.CASH,
                        PostingDirectionV2.DEBIT,
                        amount,
                        policy.functional_currency,
                    ),
                    _post(
                        EconomicLedgerAccountV2.CORPORATE_ACTION_INCOME,
                        PostingDirectionV2.CREDIT,
                        amount,
                        policy.functional_currency,
                    ),
                )
                kind = EconomicEventKindV2.DIVIDEND_CASH
        else:
            block_action = action_evidence.get(
                (
                    action.instrument_id,
                    action.action_id,
                    CorporateActionApplication.DELISTING,
                )
            )
            if (
                before.fill_eligibility is not FillEligibilityV2.BLOCKED_DELISTED
                or block_action is None
                or action.cash_consideration is None
                or action.revision < block_action.revision
                or (
                    action.revision == block_action.revision
                    and action.action_digest != block_action.action_digest
                )
                or action.cash_consideration != block_action.cash_consideration
                or action.currency != block_action.currency
            ):
                raise SimulationEconomicLedgerError("delisting settlement precedes block")
            if before.quantity == 0:
                after = before.model_copy(update={"quantity": Decimal("0"), "cost_basis": Decimal("0")})
                kind = EconomicEventKindV2.DELISTING_SETTLEMENT_ZERO
                nonmoney = EconomicNonMonetaryTransitionV2(kind=kind, action_key=_akey(action))
            else:
                conversion = conversions.get(
                    (str(action.instrument_id), str(action.action_id), action.application.value)
                )
                if conversion is None:
                    raise SimulationEconomicLedgerError("settlement lacks conversion")
                amount = _cash_amount(
                    conversion, action, view, before.quantity, previous_receipt, policy
                )
                if amount <= 0:
                    raise SimulationEconomicLedgerError("settlement conversion has no monetary amount")
                used.add(_conv_key(conversion))
                cash = _add(cash, amount)
                gain = _add(amount, before.cost_basis.copy_negate())
                pnl = _add(pnl, gain)
                ps = [
                    _post(
                        EconomicLedgerAccountV2.CASH,
                        PostingDirectionV2.DEBIT,
                        amount,
                        policy.functional_currency,
                    )
                ]
                if before.cost_basis:
                    ps.append(
                        _post(
                            EconomicLedgerAccountV2.POSITION_COST,
                            PostingDirectionV2.CREDIT,
                            before.cost_basis,
                            policy.functional_currency,
                            before.instrument_id,
                        )
                    )
                if gain > 0:
                    ps.append(
                        _post(
                            EconomicLedgerAccountV2.REALIZED_PNL,
                            PostingDirectionV2.CREDIT,
                            gain,
                            policy.functional_currency,
                        )
                    )
                elif gain < 0:
                    ps.append(
                        _post(
                            EconomicLedgerAccountV2.REALIZED_PNL,
                            PostingDirectionV2.DEBIT,
                            gain.copy_abs(),
                            policy.functional_currency,
                        )
                    )
                postings = tuple(ps)
                after = before.model_copy(
                    update={"quantity": Decimal("0"), "cost_basis": Decimal("0")}
                )
                kind = EconomicEventKindV2.DELISTING_SETTLEMENT
        positions[action.instrument_id] = after
        event = _make_event(
            seq=sequence,
            policy=policy,
            transition=transition,
            stage=stage,
            kind=kind,
            occurred=action.application_at,
            before_cash=before_cash,
            cash=cash,
            before_pnl=before_pnl,
            pnl=pnl,
            before=before,
            after=after,
            previous=previous_event,
            action=action,
            conversion=conversion,
            nonmonetary=nonmoney,
            postings=postings,
            entitlement_before=ebefore,
            entitlement_after=eafter,
        )
        events.append(event)
        previous_event = event.event_hash
        sequence += 1
        action_evidence[key] = action

    for view in transition.pre_open_views:
        for action in view.view.actions:
            action_event(action, view, EconomicStageV2.PRE_OPEN)
    for fill in transition.trade_fills:
        if fill.order_id in order_ids:
            raise EconomicSessionConflictError("fill order ID already committed")
        if fill.instrument_id not in positions or fill.currency != policy.functional_currency:
            raise SimulationEconomicLedgerError("fill instrument or currency is invalid")
        monetary_values = (
            fill.raw_notional,
            fill.fill_notional,
            fill.slippage_amount,
            fill.commission_fee,
            fill.tax,
            fill.cost_basis_removed,
            fill.realized_pnl,
            fill.cash_delta,
        )
        if any(value != _money(value, policy.money_quantum) for value in monetary_values):
            raise SimulationEconomicLedgerError("fill money is not quantized to ledger policy")
        before = positions[fill.instrument_id]
        if before.fill_eligibility is not FillEligibilityV2.ACTIVE:
            raise SimulationEconomicLedgerError("fill targets delisted instrument")
        before_cash, before_pnl = cash, pnl
        if fill.side is SimulationSide.BUY:
            if (
                fill.cash_delta
                != (fill.fill_notional + fill.commission_fee + fill.tax).copy_negate()
            ):
                raise SimulationEconomicLedgerError("buy fill cash is inconsistent")
            cash = _add(cash, fill.cash_delta)
            if cash < 0:
                raise SimulationEconomicLedgerError("fill makes cash negative")
            after = before.model_copy(
                update={
                    "quantity": _add(before.quantity, fill.executed_quantity),
                    "cost_basis": _add(before.cost_basis, fill.fill_notional),
                }
            )
        else:
            if fill.executed_quantity > before.quantity:
                raise SimulationEconomicLedgerError("fill creates short position")
            removed = (
                before.cost_basis
                if fill.executed_quantity == before.quantity
                else _money(
                    _div(_mul(before.cost_basis, fill.executed_quantity), before.quantity),
                    policy.money_quantum,
                )
            )
            if (
                fill.cost_basis_removed != removed
                or fill.realized_pnl != fill.fill_notional - removed
                or fill.cash_delta != fill.fill_notional - fill.commission_fee - fill.tax
            ):
                raise SimulationEconomicLedgerError("sell fill differs from current state")
            cash = _add(cash, fill.cash_delta)
            pnl = _add(pnl, fill.realized_pnl)
            qty = _add(before.quantity, fill.executed_quantity.copy_negate())
            after = before.model_copy(
                update={
                    "quantity": qty,
                    "cost_basis": Decimal("0")
                    if qty == 0
                    else _add(before.cost_basis, removed.copy_negate()),
                }
            )
        positions[fill.instrument_id] = after
        postings = _fill_postings(fill)
        event = _make_event(
            seq=sequence,
            policy=policy,
            transition=transition,
            stage=EconomicStageV2.TRADE_FILL,
            kind=EconomicEventKindV2.TRADE_FILL,
            occurred=fill.executed_at,
            before_cash=before_cash,
            cash=cash,
            before_pnl=before_pnl,
            pnl=pnl,
            before=before,
            after=after,
            previous=previous_event,
            fill=fill,
            postings=postings,
        )
        events.append(event)
        previous_event = event.event_hash
        sequence += 1
        order_ids.add(fill.order_id)
    for view in transition.post_close_views:
        for action in view.view.actions:
            action_event(action, view, EconomicStageV2.POST_CLOSE)
    if used != set(conversions):
        raise SimulationEconomicLedgerError("missing or unused cash conversion")
    return cash, pnl, positions, entitlements, tuple(events)


def _external_digest(value: BaseModel) -> str:
    return canonical_digest(value.model_dump(mode="python"))


def _checked(value: Any, exact_type: type[BaseModel], label: str):
    if type(value) is not exact_type:
        raise SimulationEconomicLedgerError(f"{label} must use the exact contract type")
    try:
        return exact_type.model_validate(value.model_dump(mode="python"))
    except (ValueError, ArithmeticError) as exc:
        raise SimulationEconomicLedgerError(f"{label} is invalid") from exc


def _anchor(value: Any, label: str) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise SimulationEconomicLedgerError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _validate_state_history(state: SimulationEconomicLedgerStateV2) -> None:
    """Rebuild every committed session; hashes alone never authorize state."""

    policy = state.policy
    if type(policy) is not EconomicLedgerPolicyV2:
        raise ValueError("state policy uses another contract type")
    if tuple(item.instrument_id for item in state.positions) != policy.instrument_ids:
        raise ValueError("state positions are not complete and canonical")
    if (
        any(type(item) is not EconomicPositionV2 for item in state.positions)
        or any(type(item) is not DividendEntitlementV2 for item in state.entitlements)
        or any(type(item) is not EconomicLedgerEventV2 for item in state.events)
    ):
        raise ValueError("state contains another contract type")
    if (
        tuple(
            sorted(
                state.entitlements, key=lambda item: (str(item.instrument_id), str(item.action_id))
            )
        )
        != state.entitlements
    ):
        raise ValueError("state entitlements are not canonical")

    cash = policy.initial_cash
    pnl = Decimal("0")
    positions = {item.instrument_id: item for item in policy.initial_positions}
    entitlements: dict[tuple[UUID, UUID], DividendEntitlementV2] = {}
    previous_event = ECONOMIC_LEDGER_GENESIS_HASH
    previous_receipt = ECONOMIC_LEDGER_GENESIS_HASH
    sequence = 1
    action_evidence: dict[
        tuple[UUID, UUID, CorporateActionApplication], CorporateActionProjection
    ] = {}
    order_ids: set[UUID] = set()
    event_offset = 0
    session_ids: set[UUID] = set()
    session_dates: set[date] = set()
    prior_date: date | None = None

    for receipt in state.session_receipts:
        if type(receipt) is not EconomicSessionReceiptV2:
            raise ValueError("state receipt uses another contract type")
        transition = receipt.transition
        if transition.session_id in session_ids or transition.session_date in session_dates:
            raise ValueError("state repeats a committed session")
        if prior_date is not None and transition.session_date <= prior_date:
            raise ValueError("state sessions are not strictly chronological")
        if receipt.previous_receipt_hash != previous_receipt:
            raise ValueError("receipt chain is broken")
        first = sequence if receipt.event_count else None
        try:
            cash, pnl, positions, entitlements, derived = _derive_transition(
                policy=policy,
                transition=transition,
                cash=cash,
                pnl=pnl,
                positions=dict(positions),
                entitlements=dict(entitlements),
                previous_event=previous_event,
                previous_receipt=previous_receipt,
                sequence=sequence,
                action_evidence=dict(action_evidence),
                order_ids=set(order_ids),
            )
        except SimulationEconomicLedgerError as exc:
            raise ValueError("state history cannot be rederived") from exc
        actual = state.events[event_offset : event_offset + len(derived)]
        if actual != derived or receipt.event_count != len(derived):
            raise ValueError("state events differ from session rederivation")
        last = sequence + len(derived) - 1 if derived else None
        result_hash = derived[-1].event_hash if derived else previous_event
        if (
            receipt.first_event_sequence != first
            or receipt.last_event_sequence != last
            or receipt.result_event_hash != result_hash
        ):
            raise ValueError("receipt result differs from session rederivation")

        for event in derived:
            if event.action is not None:
                action_evidence[
                    (event.action.instrument_id, event.action.action_id, event.action.application)
                ] = event.action
            if event.fill is not None:
                order_ids.add(event.fill.order_id)
        event_offset += len(derived)
        sequence += len(derived)
        previous_event = result_hash
        previous_receipt = receipt.receipt_hash
        session_ids.add(transition.session_id)
        session_dates.add(transition.session_date)
        prior_date = transition.session_date

    expected_positions = tuple(positions[instrument_id] for instrument_id in policy.instrument_ids)
    expected_entitlements = tuple(
        sorted(
            entitlements.values(), key=lambda item: (str(item.instrument_id), str(item.action_id))
        )
    )
    if (
        event_offset != len(state.events)
        or state.events
        and tuple(event.sequence for event in state.events)
        != tuple(range(1, len(state.events) + 1))
    ):
        raise ValueError("state contains unreceipted or noncanonical events")
    if (
        state.cash != cash
        or state.realized_pnl != pnl
        or state.positions != expected_positions
        or state.entitlements != expected_entitlements
    ):
        raise ValueError("state balances differ from full history")
    if (
        state.last_event_hash != previous_event
        or state.last_session_receipt_hash != previous_receipt
        or state.next_sequence != sequence
    ):
        raise ValueError("state heads differ from full history")


def create_simulation_economic_ledger_v2(
    policy: EconomicLedgerPolicyV2,
    *,
    expected_policy_digest: Digest,
) -> SimulationEconomicLedgerStateV2:
    policy = _checked(policy, EconomicLedgerPolicyV2, "economic ledger policy")
    expected_policy_digest = _anchor(expected_policy_digest, "expected policy digest")
    if not hmac.compare_digest(_external_digest(policy), expected_policy_digest):
        raise EconomicSessionConflictError("economic ledger policy precondition failed")
    positions = tuple(policy.initial_positions)
    payload = {
        "state_version": "simulation-economic-ledger-state/2",
        "policy": policy,
        "cash": policy.initial_cash,
        "realized_pnl": Decimal("0"),
        "positions": positions,
        "entitlements": (),
        "events": (),
        "session_receipts": (),
        "last_event_hash": ECONOMIC_LEDGER_GENESIS_HASH,
        "last_session_receipt_hash": ECONOMIC_LEDGER_GENESIS_HASH,
        "next_sequence": 1,
        "quantitative_claims_status": policy.quantitative_claims_status,
        "limitations": policy.limitations,
    }
    return SimulationEconomicLedgerStateV2(**payload, state_hash=_digest(payload))


def _commit_simulation_economic_session_v2(
    state: SimulationEconomicLedgerStateV2,
    transition: EconomicSessionTransitionV2,
    *,
    expected_state_hash: Digest,
    expected_policy_digest: Digest,
    expected_request_digest: Digest,
) -> EconomicSessionResultV2:
    """Atomically validate and commit one complete session transition."""

    state = _checked(state, SimulationEconomicLedgerStateV2, "economic ledger state")
    transition = _checked(transition, EconomicSessionTransitionV2, "economic session transition")
    expected_state_hash = _anchor(expected_state_hash, "expected state hash")
    expected_policy_digest = _anchor(expected_policy_digest, "expected policy digest")
    expected_request_digest = _anchor(expected_request_digest, "expected request digest")
    if not hmac.compare_digest(state.state_hash, expected_state_hash):
        raise EconomicSessionConflictError("economic ledger state precondition failed")
    if not hmac.compare_digest(_external_digest(state.policy), expected_policy_digest):
        raise EconomicSessionConflictError("economic ledger policy precondition failed")
    if not hmac.compare_digest(transition.request_digest, expected_request_digest):
        raise EconomicSessionConflictError("economic session request precondition failed")

    prior_by_id = {item.transition.session_id: item for item in state.session_receipts}
    if transition.session_id in prior_by_id:
        receipt = prior_by_id[transition.session_id]
        if (
            receipt.transition.request_digest != transition.request_digest
            or receipt.transition != transition
        ):
            raise EconomicSessionConflictError("session ID was committed with another request")
        return EconomicSessionResultV2(
            request_digest=transition.request_digest,
            replay_status="replayed",
            appended_events=(),
            receipt=receipt,
            state=state,
        )
    if any(
        item.transition.session_date == transition.session_date for item in state.session_receipts
    ):
        raise EconomicSessionConflictError("session date was already committed")
    if (
        state.session_receipts
        and transition.session_date <= state.session_receipts[-1].transition.session_date
    ):
        raise EconomicSessionConflictError("session date is not strictly increasing")

    action_evidence = {
        (event.action.instrument_id, event.action.action_id, event.action.application): event.action
        for event in state.events
        if event.action is not None
    }
    order_ids = {event.fill.order_id for event in state.events if event.fill is not None}
    try:
        cash, pnl, positions, entitlements, appended = _derive_transition(
            policy=state.policy,
            transition=transition,
            cash=state.cash,
            pnl=state.realized_pnl,
            positions={item.instrument_id: item for item in state.positions},
            entitlements={
                (item.instrument_id, item.action_id): item for item in state.entitlements
            },
            previous_event=state.last_event_hash,
            previous_receipt=state.last_session_receipt_hash,
            sequence=state.next_sequence,
            action_evidence=action_evidence,
            order_ids=order_ids,
        )
    except SimulationEconomicLedgerError:
        raise
    except (ArithmeticError, ValueError) as exc:
        raise SimulationEconomicLedgerError(
            "economic session arithmetic is outside the supported domain"
        ) from exc
    result_event_hash = appended[-1].event_hash if appended else state.last_event_hash
    receipt_payload = {
        "receipt_version": "economic-session-receipt/2",
        "transition": transition,
        "first_event_sequence": appended[0].sequence if appended else None,
        "last_event_sequence": appended[-1].sequence if appended else None,
        "event_count": len(appended),
        "result_event_hash": result_event_hash,
        "previous_receipt_hash": state.last_session_receipt_hash,
        "quantitative_claims_status": state.quantitative_claims_status,
        "limitations": state.limitations,
    }
    receipt = EconomicSessionReceiptV2(**receipt_payload, receipt_hash=_digest(receipt_payload))
    state_payload = {
        "state_version": "simulation-economic-ledger-state/2",
        "policy": state.policy,
        "cash": cash,
        "realized_pnl": pnl,
        "positions": tuple(
            positions[instrument_id] for instrument_id in state.policy.instrument_ids
        ),
        "entitlements": tuple(
            sorted(
                entitlements.values(),
                key=lambda item: (str(item.instrument_id), str(item.action_id)),
            )
        ),
        "events": state.events + appended,
        "session_receipts": state.session_receipts + (receipt,),
        "last_event_hash": result_event_hash,
        "last_session_receipt_hash": receipt.receipt_hash,
        "next_sequence": state.next_sequence + len(appended),
        "quantitative_claims_status": state.quantitative_claims_status,
        "limitations": state.limitations,
    }
    next_state = SimulationEconomicLedgerStateV2(**state_payload, state_hash=_digest(state_payload))
    return EconomicSessionResultV2(
        request_digest=transition.request_digest,
        replay_status="applied",
        appended_events=appended,
        receipt=receipt,
        state=next_state,
    )


__all__ = [
    "ECONOMIC_LEDGER_GENESIS_HASH",
    "DividendEntitlementV2",
    "DividendPaymentStatusV2",
    "EconomicActionKeyV2",
    "EconomicEventKindV2",
    "EconomicLedgerAccountV2",
    "EconomicLedgerEventV2",
    "EconomicLedgerPolicyV2",
    "EconomicNonMonetaryTransitionV2",
    "EconomicPositionV2",
    "EconomicSessionConflictError",
    "EconomicSessionReceiptV2",
    "EconomicSessionResultV2",
    "EconomicSessionTransitionV2",
    "EconomicStageV2",
    "FillEligibilityV2",
    "PostingDirectionV2",
    "SimulationEconomicLedgerError",
    "SimulationEconomicLedgerStateV2",
    "create_simulation_economic_ledger_v2",
]

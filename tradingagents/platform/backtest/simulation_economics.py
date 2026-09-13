"""Pure point-in-time corporate-action and FX projections for M5.2c.

This module contains no repository, broker, database, network, clock or LLM
access.  Repository authorities construct these projections from frozen bytes;
later accounting reducers may consume only rederived, typed bundles.
"""

from __future__ import annotations

import hmac
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tradingagents.platform.backtest.contracts import (
    CorporateActionType,
    Currency,
    DatasetLimitation,
    FxObservation,
    canonical_digest,
)

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_DECIMAL_CONTEXT = Context(prec=76, rounding=ROUND_HALF_EVEN)


class SimulationEconomicsError(ValueError):
    """A PIT economic event or conversion was missing, ambiguous or invalid."""


class EconomicPhase(StrEnum):
    PRE_OPEN = "pre_open"
    POST_CLOSE = "post_close"


class CorporateActionApplication(StrEnum):
    SPLIT = "split"
    DIVIDEND_ENTITLEMENT = "dividend_entitlement"
    DIVIDEND_PAYMENT = "dividend_payment"
    DELISTING = "delisting"
    DELISTING_SETTLEMENT = "delisting_settlement"


class FxConversionMode(StrEnum):
    IDENTITY = "identity"
    DIRECT = "direct_functional_to_source"


ECONOMIC_APPLICATION_PRECEDENCE = {
    CorporateActionApplication.SPLIT: 10,
    CorporateActionApplication.DIVIDEND_ENTITLEMENT: 20,
    CorporateActionApplication.DELISTING: 30,
    CorporateActionApplication.DIVIDEND_PAYMENT: 40,
    CorporateActionApplication.DELISTING_SETTLEMENT: 50,
}
ECONOMIC_APPLICATION_PRECEDENCE_DIGEST = canonical_digest(
    {
        "version": "economic-application-precedence/1",
        "entries": {
            application.value: rank
            for application, rank in ECONOMIC_APPLICATION_PRECEDENCE.items()
        },
    }
)


def fx_authority_book_digest(observations: tuple[FxObservation, ...]) -> str:
    """Bind a run policy to one canonical FX book shared by every instrument."""

    if type(observations) is not tuple or any(
        type(item) is not FxObservation for item in observations
    ):
        raise SimulationEconomicsError("FX authority book uses invalid records")
    ordered = tuple(
        sorted(
            observations,
            key=lambda item: (
                item.base_currency,
                item.quote_currency,
                item.observed_at,
                item.available_at,
                str(item.observation_id),
                item.revision,
            ),
        )
    )
    return canonical_digest(
        {
            "version": "fx-authority-book/1",
            "observations": tuple(item.model_dump(mode="python") for item in ordered),
        }
    )


class EconomicsContract(BaseModel):
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


def _exact_decimal(value: Any) -> Any:
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
    if len(digits) > 38 or value.adjusted() >= 38 or exponent < -18:
        raise ValueError("decimal value exceeds the precision or scale limit")
    return value


class InstrumentEconomicsBinding(EconomicsContract):
    binding_version: Literal["instrument-economics-binding/1"] = (
        "instrument-economics-binding/1"
    )
    instrument_id: UUID
    dataset_id: UUID
    manifest_digest: Digest
    quote_currency: Currency


class SimulationEconomicsPolicy(EconomicsContract):
    policy_version: Literal["simulation-economics-policy/1"] = (
        "simulation-economics-policy/1"
    )
    tenant_id: UUID
    backtest_id: UUID
    ledger_id: UUID
    functional_currency: Currency
    fx_max_staleness_seconds: int = Field(ge=1, le=7 * 24 * 60 * 60)
    fx_book_digest: Digest
    money_quantum: Decimal
    precedence_digest: Digest = ECONOMIC_APPLICATION_PRECEDENCE_DIGEST
    instrument_bindings: tuple[InstrumentEconomicsBinding, ...] = Field(
        min_length=1, max_length=1_000
    )

    _money_quantum_is_exact = field_validator("money_quantum", mode="before")(
        _exact_decimal
    )

    @model_validator(mode="after")
    def validate_policy(self):
        _bounded_decimal(self.money_quantum)
        if self.money_quantum <= 0:
            raise ValueError("economics money quantum must be positive")
        if not hmac.compare_digest(
            self.precedence_digest, ECONOMIC_APPLICATION_PRECEDENCE_DIGEST
        ):
            raise ValueError("economics policy uses another event precedence")
        if any(type(item) is not InstrumentEconomicsBinding for item in self.instrument_bindings):
            raise ValueError("economics instrument bindings use another contract type")
        keys = tuple(item.instrument_id for item in self.instrument_bindings)
        if len(set(keys)) != len(keys) or keys != tuple(sorted(keys, key=str)):
            raise ValueError("economics instrument bindings must be unique and sorted")
        return self


class CorporateActionProjection(EconomicsContract):
    projection_version: Literal["corporate-action-projection/1"] = (
        "corporate-action-projection/1"
    )
    dataset_id: UUID
    manifest_digest: Digest
    instrument_id: UUID
    session_date: date
    phase: EconomicPhase
    application: CorporateActionApplication
    application_at: datetime
    action_id: UUID
    revision: int = Field(ge=1)
    action_type: CorporateActionType
    action_digest: Digest
    source_digest: Digest
    published_at: datetime
    available_at: datetime
    effective_date: date
    ex_date: date | None = None
    pay_date: date | None = None
    split_ratio: Decimal | None = None
    split_ratio_convention: Literal["new_shares_per_old_share"] | None = None
    cash_amount: Decimal | None = None
    cash_amount_basis: Literal["net_cash_per_entitled_share"] | None = None
    cash_consideration: Decimal | None = None
    currency: Currency | None = None
    projection_digest: Digest

    _timestamps_are_utc = field_validator(
        "application_at", "published_at", "available_at"
    )(_utc)
    _decimal_inputs_are_exact = field_validator(
        "split_ratio", "cash_amount", "cash_consideration", mode="before"
    )(_exact_decimal)

    @model_validator(mode="after")
    def validate_projection(self):
        if self.available_at > self.application_at:
            raise ValueError("corporate action was unavailable at application time")
        expected_phase = {
            CorporateActionApplication.SPLIT: EconomicPhase.PRE_OPEN,
            CorporateActionApplication.DIVIDEND_ENTITLEMENT: EconomicPhase.PRE_OPEN,
            CorporateActionApplication.DIVIDEND_PAYMENT: EconomicPhase.POST_CLOSE,
            CorporateActionApplication.DELISTING: EconomicPhase.PRE_OPEN,
            CorporateActionApplication.DELISTING_SETTLEMENT: EconomicPhase.POST_CLOSE,
        }[self.application]
        expected_type = {
            CorporateActionApplication.SPLIT: CorporateActionType.SPLIT,
            CorporateActionApplication.DIVIDEND_ENTITLEMENT: CorporateActionType.CASH_DIVIDEND,
            CorporateActionApplication.DIVIDEND_PAYMENT: CorporateActionType.CASH_DIVIDEND,
            CorporateActionApplication.DELISTING: CorporateActionType.DELISTING,
            CorporateActionApplication.DELISTING_SETTLEMENT: CorporateActionType.DELISTING,
        }[self.application]
        if self.phase is not expected_phase or self.action_type is not expected_type:
            raise ValueError("corporate-action phase and type are inconsistent")
        if self.phase is EconomicPhase.PRE_OPEN and self.effective_date != self.session_date:
            raise ValueError("pre-open corporate action belongs to another session")
        if self.phase is EconomicPhase.POST_CLOSE and (
            self.pay_date is None or self.pay_date > self.session_date
        ):
            raise ValueError("settlement is not payable in this session")
        if self.application is CorporateActionApplication.SPLIT and (
            self.split_ratio is None
            or self.split_ratio_convention != "new_shares_per_old_share"
        ):
            raise ValueError("split projection is missing its explicit ratio convention")
        if self.application in {
            CorporateActionApplication.DIVIDEND_ENTITLEMENT,
            CorporateActionApplication.DIVIDEND_PAYMENT,
        } and (
            self.cash_amount is None
            or self.cash_amount_basis != "net_cash_per_entitled_share"
            or self.currency is None
        ):
            raise ValueError("dividend projection is missing its explicit cash basis")
        for value in (self.split_ratio, self.cash_amount, self.cash_consideration):
            if value is not None:
                _bounded_decimal(value)
                if value <= 0:
                    raise ValueError("corporate-action economic values must be positive")
        expected = canonical_digest(
            self.model_dump(mode="python", exclude={"projection_digest"})
        )
        if not hmac.compare_digest(self.projection_digest, expected):
            raise ValueError("corporate-action projection digest is invalid")
        return self


class EconomicEventView(EconomicsContract):
    view_version: Literal["economic-event-view/1"] = "economic-event-view/1"
    dataset_id: UUID
    tenant_id: UUID
    manifest_digest: Digest
    instrument_id: UUID
    session_date: date
    phase: EconomicPhase
    as_of: datetime
    precedence_digest: Digest
    actions: tuple[CorporateActionProjection, ...] = Field(max_length=1_000)
    view_digest: Digest

    _as_of_is_utc = field_validator("as_of")(_utc)

    @model_validator(mode="after")
    def validate_view(self):
        keys = tuple(
            (
                item.application_at,
                ECONOMIC_APPLICATION_PRECEDENCE[item.application],
                str(item.action_id),
            )
            for item in self.actions
        )
        if keys != tuple(sorted(keys)) or len({(item.action_id, item.application) for item in self.actions}) != len(self.actions):
            raise ValueError("economic events must be unique and canonically ordered")
        if any(
            type(item) is not CorporateActionProjection
            or item.dataset_id != self.dataset_id
            or item.manifest_digest != self.manifest_digest
            or item.instrument_id != self.instrument_id
            or item.session_date != self.session_date
            or item.phase is not self.phase
            or item.application_at != self.as_of
            for item in self.actions
        ):
            raise ValueError("economic event view contains a foreign action")
        if not hmac.compare_digest(
            self.precedence_digest, ECONOMIC_APPLICATION_PRECEDENCE_DIGEST
        ):
            raise ValueError("economic event view uses another precedence policy")
        expected = canonical_digest(self.model_dump(mode="python", exclude={"view_digest"}))
        if not hmac.compare_digest(self.view_digest, expected):
            raise ValueError("economic event view digest is invalid")
        return self


class FxRateProjection(EconomicsContract):
    projection_version: Literal["fx-rate-projection/1"] = "fx-rate-projection/1"
    dataset_id: UUID
    manifest_digest: Digest
    instrument_id: UUID
    functional_currency: Currency
    source_currency: Currency
    event_at: datetime
    max_staleness_seconds: int = Field(ge=1, le=7 * 24 * 60 * 60)
    mode: FxConversionMode
    rate: Decimal
    observation_id: UUID | None = None
    observation_revision: int | None = Field(default=None, ge=1)
    observed_at: datetime | None = None
    available_at: datetime | None = None
    observation_digest: Digest | None = None
    source_digest: Digest | None = None
    rate_convention: Literal["quote_units_per_base_unit"] = (
        "quote_units_per_base_unit"
    )
    projection_digest: Digest

    _event_at_is_utc = field_validator("event_at")(_utc)
    _optional_timestamps_are_utc = field_validator("observed_at", "available_at")(
        lambda value: None if value is None else _utc(value)
    )
    _rate_input_is_exact = field_validator("rate", mode="before")(_exact_decimal)

    @model_validator(mode="after")
    def validate_projection(self):
        _bounded_decimal(self.rate)
        evidence = (
            self.observation_id,
            self.observation_revision,
            self.observed_at,
            self.available_at,
            self.observation_digest,
            self.source_digest,
        )
        if self.mode is FxConversionMode.IDENTITY:
            if self.functional_currency != self.source_currency or self.rate != 1 or any(
                item is not None for item in evidence
            ):
                raise ValueError("identity FX requires same currency, rate one, and no observation")
        else:
            if self.functional_currency == self.source_currency or self.rate <= 0 or any(
                item is None for item in evidence
            ):
                raise ValueError("direct FX requires different currencies and complete evidence")
            assert self.observed_at is not None and self.available_at is not None
            if self.observed_at > self.event_at or self.available_at > self.event_at:
                raise ValueError("FX observation was unavailable at the economic event")
            if self.event_at - self.observed_at > timedelta(
                seconds=self.max_staleness_seconds
            ):
                raise ValueError("FX observation exceeds the staleness policy")
        expected = canonical_digest(
            self.model_dump(mode="python", exclude={"projection_digest"})
        )
        if not hmac.compare_digest(self.projection_digest, expected):
            raise ValueError("FX projection digest is invalid")
        return self


class FxConversion(EconomicsContract):
    conversion_version: Literal["fx-conversion/1"] = "fx-conversion/1"
    fx_projection_digest: Digest
    source_currency: Currency
    functional_currency: Currency
    source_amount: Decimal
    functional_amount: Decimal
    money_quantum: Decimal
    rounding: Literal["ROUND_HALF_EVEN"] = "ROUND_HALF_EVEN"
    conversion_digest: Digest

    _decimal_inputs_are_exact = field_validator(
        "source_amount", "functional_amount", "money_quantum", mode="before"
    )(_exact_decimal)

    @model_validator(mode="after")
    def validate_conversion(self):
        for value in (self.source_amount, self.functional_amount, self.money_quantum):
            _bounded_decimal(value)
        if self.source_amount < 0 or self.functional_amount < 0 or self.money_quantum <= 0:
            raise ValueError("FX conversion amounts must be non-negative and quantum positive")
        expected = canonical_digest(
            self.model_dump(mode="python", exclude={"conversion_digest"})
        )
        if not hmac.compare_digest(self.conversion_digest, expected):
            raise ValueError("FX conversion digest is invalid")
        return self


class AuthorizedEconomicEventView(EconomicsContract):
    authority_version: Literal["authorized-economic-event-view/1"] = (
        "authorized-economic-event-view/1"
    )
    tenant_id: UUID
    backtest_id: UUID
    ledger_id: UUID
    backtest_spec_digest: Digest
    ledger_policy_digest: Digest
    economics_policy_digest: Digest
    dataset_id: UUID
    manifest_digest: Digest
    instrument_id: UUID
    quantitative_claims_status: Literal["ineligible_unverified_coverage"] = (
        "ineligible_unverified_coverage"
    )
    limitations: tuple[DatasetLimitation, ...] = Field(min_length=1, max_length=64)
    view: EconomicEventView
    authority_digest: Digest

    @model_validator(mode="after")
    def validate_authority(self):
        if type(self.view) is not EconomicEventView or (
            self.view.tenant_id != self.tenant_id
            or self.view.dataset_id != self.dataset_id
            or self.view.manifest_digest != self.manifest_digest
            or self.view.instrument_id != self.instrument_id
        ):
            raise ValueError("authorized event view contains foreign evidence")
        expected = canonical_digest(
            self.model_dump(mode="python", exclude={"authority_digest"})
        )
        if not hmac.compare_digest(self.authority_digest, expected):
            raise ValueError("authorized event-view digest is invalid")
        return self


class AuthorizedFxConversion(EconomicsContract):
    authority_version: Literal["authorized-fx-conversion/1"] = (
        "authorized-fx-conversion/1"
    )
    tenant_id: UUID
    backtest_id: UUID
    ledger_id: UUID
    backtest_spec_digest: Digest
    ledger_policy_digest: Digest
    economics_policy_digest: Digest
    dataset_id: UUID
    manifest_digest: Digest
    instrument_id: UUID
    quantitative_claims_status: Literal["ineligible_unverified_coverage"] = (
        "ineligible_unverified_coverage"
    )
    limitations: tuple[DatasetLimitation, ...] = Field(min_length=1, max_length=64)
    purpose: Literal["market_valuation"] = "market_valuation"
    source_reference_digest: Digest
    session_date: date
    phase: EconomicPhase
    event_at: datetime
    projection: FxRateProjection
    conversion: FxConversion
    authority_digest: Digest

    _event_at_is_utc = field_validator("event_at")(_utc)

    @model_validator(mode="after")
    def validate_authority(self):
        if type(self.projection) is not FxRateProjection or type(
            self.conversion
        ) is not FxConversion:
            raise ValueError("authorized FX result contains foreign contract types")
        if (
            self.projection.dataset_id != self.dataset_id
            or self.projection.manifest_digest != self.manifest_digest
            or self.projection.instrument_id != self.instrument_id
            or self.projection.event_at != self.event_at
            or self.projection.source_currency != self.conversion.source_currency
            or self.projection.functional_currency
            != self.conversion.functional_currency
            or not hmac.compare_digest(
                self.conversion.fx_projection_digest,
                self.projection.projection_digest,
            )
        ):
            raise ValueError("authorized FX result contains foreign evidence")
        expected = canonical_digest(
            self.model_dump(mode="python", exclude={"authority_digest"})
        )
        if not hmac.compare_digest(self.authority_digest, expected):
            raise ValueError("authorized FX digest is invalid")
        return self


def _convert_quote_to_functional(
    amount: Decimal,
    projection: FxRateProjection,
    *,
    money_quantum: Decimal,
) -> FxConversion:
    """Convert once at the ledger boundary under a private Decimal context."""

    if type(projection) is not FxRateProjection:
        raise SimulationEconomicsError("FX conversion requires the exact projection type")
    try:
        checked = FxRateProjection.model_validate(projection.model_dump(mode="python"))
        _exact_decimal(amount)
        _exact_decimal(money_quantum)
        amount = _bounded_decimal(Decimal(amount))
        money_quantum = _bounded_decimal(Decimal(money_quantum))
    except (ValueError, ArithmeticError) as exc:
        raise SimulationEconomicsError("FX conversion input is invalid") from exc
    if amount < 0 or money_quantum <= 0:
        raise SimulationEconomicsError("FX amount must be non-negative and quantum positive")
    with localcontext(_DECIMAL_CONTEXT):
        unrounded = amount if checked.mode is FxConversionMode.IDENTITY else amount / checked.rate
        converted = (unrounded / money_quantum).to_integral_value(
            rounding=ROUND_HALF_EVEN
        ) * money_quantum
    if amount > 0 and converted == 0:
        raise SimulationEconomicsError(
            "FX conversion would discard a positive amount; explicit dust handling is required"
        )
    payload = {
        "conversion_version": "fx-conversion/1",
        "fx_projection_digest": checked.projection_digest,
        "source_currency": checked.source_currency,
        "functional_currency": checked.functional_currency,
        "source_amount": amount,
        "functional_amount": converted,
        "money_quantum": money_quantum,
        "rounding": "ROUND_HALF_EVEN",
    }
    return FxConversion(**payload, conversion_digest=canonical_digest(payload))


__all__ = [
    "CorporateActionApplication",
    "CorporateActionProjection",
    "AuthorizedEconomicEventView",
    "AuthorizedFxConversion",
    "EconomicEventView",
    "EconomicPhase",
    "ECONOMIC_APPLICATION_PRECEDENCE",
    "ECONOMIC_APPLICATION_PRECEDENCE_DIGEST",
    "FxConversion",
    "FxConversionMode",
    "FxRateProjection",
    "fx_authority_book_digest",
    "InstrumentEconomicsBinding",
    "SimulationEconomicsError",
    "SimulationEconomicsPolicy",
]

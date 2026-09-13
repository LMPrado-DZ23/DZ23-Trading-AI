"""Immutable, point-in-time contracts for deterministic backtests.

The contracts in this module describe trusted inputs to a backtest.  They do
not fetch market data, generate signals, simulate fills, or place orders.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class AssetType(StrEnum):
    STOCK = "stock"
    CRYPTO = "crypto"


class MarketCalendar(StrEnum):
    CONTINUOUS = "24/7"
    NEW_YORK = "XNYS"
    BRAZIL = "BVMF"


class CorporateActionType(StrEnum):
    SPLIT = "split"
    CASH_DIVIDEND = "cash_dividend"
    DELISTING = "delisting"


class AvailabilityBasis(StrEnum):
    SOURCE_PUBLISHED = "source_published"
    EXCHANGE_SETTLEMENT_POLICY = "exchange_settlement_policy"
    UNKNOWN = "unknown"


class DatasetLimitation(StrEnum):
    BAR_AVAILABILITY_UNVERIFIED = "bar_availability_unverified"
    SURVIVORSHIP_UNVERIFIED = "survivorship_unverified"
    CORPORATE_ACTION_VINTAGE_UNVERIFIED = "corporate_action_vintage_unverified"
    DELISTING_COVERAGE_UNVERIFIED = "delisting_coverage_unverified"
    FX_VINTAGE_UNVERIFIED = "fx_vintage_unverified"
    VENDOR_ADJUSTMENT_STATUS_UNVERIFIED = "vendor_adjustment_status_unverified"


M5_UNVERIFIED_LIMITATIONS = tuple(DatasetLimitation)


class DecisionPolicy(StrEnum):
    SETTLED_SESSION_CLOSE = "settled_session_close"


class FillPolicy(StrEnum):
    FIRST_SESSION_RAW_OPEN_AFTER_DECISION = "first_session_raw_open_after_decision"


class DatasetRole(StrEnum):
    TRADEABLE = "tradeable"
    BENCHMARK = "benchmark"


class MissingDataPolicy(StrEnum):
    FAIL_CLOSED = "fail_closed"


def _decimal_text(value: Decimal) -> str:
    _bounded_decimal(value)
    if not value.is_finite():
        raise ValueError("canonical decimals must be finite")
    if value == 0:
        return "0"
    sign, raw_digits, exponent = value.as_tuple()
    digits = list(raw_digits)
    while digits and digits[-1] == 0:
        digits.pop()
        exponent += 1
    if not digits:
        return "0"
    rendered_digits = "".join(str(digit) for digit in digits)
    if exponent >= 0:
        rendered = rendered_digits + ("0" * exponent)
    else:
        point = len(rendered_digits) + exponent
        if point > 0:
            rendered = f"{rendered_digits[:point]}.{rendered_digits[point:]}"
        else:
            rendered = f"0.{('0' * -point)}{rendered_digits}"
    return f"-{rendered}" if sign else rendered


def _canonical_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 32:
        raise ValueError("canonical payload exceeds the nesting limit")
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, datetime):
        _utc(value)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, (bool, float)):
        raise ValueError("canonical payload cannot contain booleans or binary floats")
    if isinstance(value, int):
        if value.bit_length() > 256:
            raise ValueError("canonical integer exceeds the resource limit")
        return value
    if isinstance(value, str):
        if len(value) > 4_096:
            raise ValueError("canonical string exceeds the resource limit")
        return value
    if isinstance(value, dict):
        if len(value) > 10_000 or any(not isinstance(key, str) for key in value):
            raise ValueError("canonical mappings require bounded string keys")
        return {
            key: _canonical_value(item, depth=depth + 1) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        if len(value) > 10_000:
            raise ValueError("canonical sequence exceeds the resource limit")
        return [_canonical_value(item, depth=depth + 1) for item in value]
    if value is None:
        return None
    raise ValueError("canonical payload contains an unsupported scalar type")


def canonical_json_bytes(value: BaseModel | dict[str, Any]) -> bytes:
    """Serialize a bounded contract payload without ambient Decimal context."""

    payload = value.model_dump(mode="python") if isinstance(value, BaseModel) else value
    canonical = json.dumps(
        _canonical_value(payload),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return canonical.encode("utf-8")


def canonical_digest(value: BaseModel | dict[str, Any]) -> str:
    """Return a stable SHA-256 digest without binary-float serialization."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def market_dataset_binding_digest(
    *,
    tenant_id: UUID,
    dataset_id: UUID,
    instrument_digest: str,
    content_bytes: int,
    content_sha256: str,
    schedule_digest: str,
    source_vintage: str,
    calendar_engine_version: str,
    timezone_database_version: str,
    settlement_policy_version: str,
    schema_version: Literal[2] | None = None,
) -> str:
    """Bind frozen dataset bytes to their tenant, identity, and source vintage."""

    payload = {
            "calendar_engine_version": calendar_engine_version,
            "content_bytes": content_bytes,
            "content_sha256": content_sha256,
            "dataset_id": dataset_id,
            "instrument_digest": instrument_digest,
            "schedule_digest": schedule_digest,
            "settlement_policy_version": settlement_policy_version,
            "source_vintage": source_vintage,
            "tenant_id": tenant_id,
            "timezone_database_version": timezone_database_version,
    }
    if schema_version is not None:
        payload["schema_version"] = schema_version
    return canonical_digest(payload)


class BacktestContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    @property
    def digest(self) -> str:
        return canonical_digest(self)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError("timestamp must use UTC")
    return value.astimezone(UTC)


def _reject_binary_float(value: Any) -> Any:
    if isinstance(value, (bool, float)):
        raise ValueError("decimal values must not be supplied as binary floats or booleans")
    if isinstance(value, str) and len(value) > 80:
        raise ValueError("decimal text exceeds the resource limit")
    if isinstance(value, int) and value.bit_length() > 256:
        raise ValueError("decimal integer exceeds the resource limit")
    if isinstance(value, Decimal):
        _bounded_decimal(value)
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
        raise ValueError("value must be finite and positive")
    return value


def _non_negative(value: Decimal) -> Decimal:
    _bounded_decimal(value)
    if value < 0:
        raise ValueError("value must be finite and non-negative")
    return value


class InstrumentSpec(BacktestContract):
    instrument_id: UUID
    symbol: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9][A-Z0-9./_=-]*$")
    asset_type: AssetType
    venue: str = Field(min_length=1, max_length=32, pattern=r"^[a-z0-9_.-]+$")
    venue_instrument_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9./:_=-]*$",
    )
    base_currency: Currency | None = None
    quote_currency: Currency
    market_calendar: MarketCalendar
    published_at: datetime
    available_at: datetime
    listing_date: date
    delisting_date: date | None = None
    source_digest: Digest

    _timestamps_are_utc = field_validator("published_at", "available_at")(_utc)

    @model_validator(mode="after")
    def validate_point_in_time_window(self):
        if self.available_at < self.published_at:
            raise ValueError("instrument availability cannot precede publication")
        if self.delisting_date is not None and self.delisting_date < self.listing_date:
            raise ValueError("instrument validity end cannot precede its start")
        if self.asset_type is AssetType.CRYPTO and self.market_calendar is not MarketCalendar.CONTINUOUS:
            raise ValueError("crypto instruments require the 24/7 market calendar")
        if self.asset_type is AssetType.STOCK and self.market_calendar is MarketCalendar.CONTINUOUS:
            raise ValueError("stock instruments require an exchange session calendar")
        if self.asset_type is AssetType.CRYPTO and self.base_currency is None:
            raise ValueError("crypto instruments require a base currency")
        if self.asset_type is AssetType.STOCK and self.base_currency is not None:
            raise ValueError("stock instruments cannot declare a base currency")
        return self

    @property
    def price_currency(self) -> str:
        return self.quote_currency

    @property
    def valid_from(self) -> date:
        return self.listing_date

    @property
    def valid_to(self) -> date | None:
        return self.delisting_date


class DailyBar(BacktestContract):
    instrument_id: UUID
    session_date: date
    session_open_at: datetime
    session_close_at: datetime
    source_published_at: datetime | None = None
    fetched_at: datetime
    available_at: datetime | None
    availability_basis: AvailabilityBasis
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    price_basis: Literal["raw"] = "raw"
    source_digest: Digest

    _timestamps_are_utc = field_validator("session_open_at", "session_close_at", "fetched_at")(
        _utc
    )
    _optional_timestamps_are_utc = field_validator("source_published_at", "available_at")(
        lambda value: None if value is None else _utc(value)
    )
    _decimal_input_is_exact = field_validator(
        "open", "high", "low", "close", "volume", mode="before"
    )(_reject_binary_float)

    @field_validator("open", "high", "low", "close")
    @classmethod
    def validate_price(cls, value: Decimal) -> Decimal:
        return _positive(value)

    @field_validator("volume")
    @classmethod
    def validate_volume(cls, value: Decimal) -> Decimal:
        return _non_negative(value)

    @model_validator(mode="after")
    def validate_bar(self):
        if not self.session_open_at < self.session_close_at <= self.fetched_at:
            raise ValueError("bar timestamps must satisfy open < close <= fetched")
        if self.availability_basis is AvailabilityBasis.UNKNOWN:
            if self.available_at is not None or self.source_published_at is not None:
                raise ValueError("unknown bar availability cannot carry availability timestamps")
        else:
            if self.available_at is None or self.available_at <= self.session_close_at:
                raise ValueError("known bar availability must follow the session close")
            if self.available_at > self.fetched_at:
                raise ValueError("bar cannot be available after it was fetched")
            if self.availability_basis is AvailabilityBasis.SOURCE_PUBLISHED:
                if (
                    self.source_published_at is None
                    or self.available_at < self.source_published_at
                ):
                    raise ValueError("source-published availability requires its publication time")
            elif self.source_published_at is not None:
                raise ValueError("settlement-policy availability cannot claim vendor publication")
        if self.low > self.high:
            raise ValueError("bar low cannot exceed high")
        if not self.low <= self.open <= self.high:
            raise ValueError("bar open must be within low and high")
        if not self.low <= self.close <= self.high:
            raise ValueError("bar close must be within low and high")
        return self


class ExecutionOpen(BacktestContract):
    dataset_id: UUID
    manifest_digest: Digest
    instrument_id: UUID
    quote_currency: Currency
    decision_session_date: date
    decision_at: datetime
    eligible_view_digest: Digest
    session_date: date
    execution_at: datetime
    raw_open: Decimal
    decision_volume: Decimal
    parent_bar_digest: Digest
    source_digest: Digest
    decision_bar_digest: Digest
    decision_bar_source_digest: Digest

    _timestamps_are_utc = field_validator("decision_at", "execution_at")(_utc)
    _economic_inputs_are_exact = field_validator(
        "raw_open", "decision_volume", mode="before"
    )(_reject_binary_float)

    @field_validator("raw_open")
    @classmethod
    def validate_raw_open(cls, value: Decimal) -> Decimal:
        return _positive(value)

    @field_validator("decision_volume")
    @classmethod
    def validate_decision_volume(cls, value: Decimal) -> Decimal:
        return _non_negative(value)

    @model_validator(mode="after")
    def validate_execution_follows_decision(self):
        if self.execution_at <= self.decision_at:
            raise ValueError("execution open must occur strictly after the decision")
        if self.session_date <= self.decision_session_date:
            raise ValueError("execution session must follow the decision session")
        return self


class ExecutionPriceProjection(BacktestContract):
    """PIT fill inputs: D+1 raw open and only D-known liquidity."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        revalidate_instances="always",
    )

    projection_version: Literal["execution-price/2"] = "execution-price/2"
    instrument_id: UUID
    quote_currency: Currency
    decision_session_date: date
    decision_at: datetime
    session_date: date
    execution_at: datetime
    raw_open: Decimal
    decision_volume: Decimal
    price_basis: Literal["raw"] = "raw"
    liquidity_basis: Literal["decision-session-raw-volume"] = (
        "decision-session-raw-volume"
    )
    projection_digest: Digest

    _timestamps_are_utc = field_validator("decision_at", "execution_at")(_utc)
    _economic_inputs_are_exact = field_validator(
        "raw_open", "decision_volume", mode="before"
    )(_reject_binary_float)

    @field_validator("raw_open")
    @classmethod
    def validate_raw_open(cls, value: Decimal) -> Decimal:
        return _positive(value)

    @field_validator("decision_volume")
    @classmethod
    def validate_decision_volume(cls, value: Decimal) -> Decimal:
        return _non_negative(value)

    @model_validator(mode="after")
    def validate_projection(self):
        if self.execution_at <= self.decision_at:
            raise ValueError("execution price must follow the decision")
        if self.session_date <= self.decision_session_date:
            raise ValueError("execution price session must follow the decision session")
        expected = canonical_digest(self.model_dump(mode="python", exclude={"projection_digest"}))
        if self.projection_digest != expected:
            raise ValueError("execution-price digest does not match its economic fields")
        return self


class ExecutionPriceProvenance(BacktestContract):
    """Full custody binding kept outside the economic fill digest."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        revalidate_instances="always",
    )

    provenance_version: Literal["execution-price-provenance/2"] = "execution-price-provenance/2"
    dataset_id: UUID
    manifest_digest: Digest
    eligible_view_digest: Digest
    projection_digest: Digest
    parent_bar_digest: Digest
    source_digest: Digest
    decision_bar_digest: Digest
    decision_bar_source_digest: Digest
    provenance_digest: Digest

    @model_validator(mode="after")
    def validate_provenance(self):
        expected = canonical_digest(self.model_dump(mode="python", exclude={"provenance_digest"}))
        if self.provenance_digest != expected:
            raise ValueError("execution-price provenance digest does not match its fields")
        return self


class ExecutionPriceBundle(BacktestContract):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        revalidate_instances="always",
    )

    price: ExecutionPriceProjection
    provenance: ExecutionPriceProvenance
    bundle_digest: Digest

    @model_validator(mode="after")
    def validate_bundle(self):
        if self.price.projection_digest != self.provenance.projection_digest:
            raise ValueError("execution-price provenance references another projection")
        expected = canonical_digest(
            {
                "bundle_version": "execution-price-bundle/2",
                "price_digest": self.price.projection_digest,
                "provenance_digest": self.provenance.provenance_digest,
            }
        )
        if self.bundle_digest != expected:
            raise ValueError("execution-price bundle digest does not match its children")
        return self


def _revalidate_execution_price_bundle(value: object) -> ExecutionPriceBundle:
    """Rebuild a bundle for internal structural validation only.

    This does not prove origin. Authoritative consumers must rederive and compare
    against the frozen repository through ``verify_execution_price_authority``.
    """

    if type(value) is not ExecutionPriceBundle:
        raise ValueError("execution-price bundle must use the exact typed contract")
    if type(value.price) is not ExecutionPriceProjection:
        raise ValueError("execution-price projection must use the exact typed contract")
    if type(value.provenance) is not ExecutionPriceProvenance:
        raise ValueError("execution-price provenance must use the exact typed contract")
    return ExecutionPriceBundle.model_validate(value.model_dump(mode="python"))


class DatasetReference(BacktestContract):
    instrument_id: UUID
    dataset_id: UUID
    manifest_digest: Digest
    role: DatasetRole


class CorporateActionV1(BacktestContract):
    """Legacy schema-v1 action retained only for byte-identical replay."""

    action_id: UUID
    instrument_id: UUID
    revision: int = Field(default=1, ge=1)
    action_type: CorporateActionType
    announced_at: datetime | None = None
    published_at: datetime
    available_at: datetime
    ex_date: date | None = None
    effective_date: date
    pay_date: date | None = None
    split_ratio: Decimal | None = None
    cash_amount: Decimal | None = None
    cash_consideration: Decimal | None = None
    currency: Currency | None = None
    source_digest: Digest

    _timestamps_are_utc = field_validator("announced_at", "published_at", "available_at")(
        lambda value: None if value is None else _utc(value)
    )
    _decimal_input_is_exact = field_validator(
        "split_ratio", "cash_amount", "cash_consideration", mode="before"
    )(_reject_binary_float)

    @field_validator("split_ratio", "cash_amount", "cash_consideration")
    @classmethod
    def validate_optional_positive_value(cls, value: Decimal | None) -> Decimal | None:
        return None if value is None else _positive(value)

    @model_validator(mode="after")
    def validate_action(self):
        if self.available_at < self.published_at:
            raise ValueError("corporate-action availability cannot precede publication")
        if self.announced_at is not None and self.announced_at > self.published_at:
            raise ValueError("corporate-action announcement cannot follow publication")
        if self.pay_date is not None and self.pay_date < self.effective_date:
            raise ValueError("corporate-action pay date cannot precede its effective date")
        if self.action_type is CorporateActionType.SPLIT:
            if (
                self.split_ratio is None
                or self.ex_date is None
                or self.cash_amount is not None
                or self.cash_consideration is not None
                or self.currency is not None
            ):
                raise ValueError("split actions require an ex-date and only a split ratio")
        elif self.action_type is CorporateActionType.CASH_DIVIDEND:
            if (
                self.cash_amount is None
                or self.currency is None
                or self.ex_date is None
                or self.pay_date is None
                or self.split_ratio is not None
                or self.cash_consideration is not None
            ):
                raise ValueError(
                    "cash dividends require an ex-date, pay date, amount, and currency"
                )
        elif (
            self.ex_date is not None
            or self.split_ratio is not None
            or (self.cash_consideration is None) != (self.currency is None)
            or (self.cash_consideration is None) != (self.pay_date is None)
        ):
            raise ValueError(
                "delistings allow only an optional cash consideration, currency, and pay date"
            )
        return self


class FxObservationV1(BacktestContract):
    """Legacy schema-v1 FX row retained only for byte-identical replay."""

    base_currency: Currency
    quote_currency: Currency
    observed_at: datetime
    available_at: datetime
    rate: Decimal
    source_digest: Digest

    _timestamps_are_utc = field_validator("observed_at", "available_at")(_utc)
    _decimal_input_is_exact = field_validator("rate", mode="before")(_reject_binary_float)

    @field_validator("rate")
    @classmethod
    def validate_rate(cls, value: Decimal) -> Decimal:
        return _positive(value)

    @model_validator(mode="after")
    def validate_observation(self):
        if self.base_currency == self.quote_currency:
            raise ValueError("FX currencies must be different")
        if self.available_at < self.observed_at:
            raise ValueError("FX availability cannot precede observation")
        return self


class CorporateAction(BacktestContract):
    action_id: UUID
    instrument_id: UUID
    revision: int = Field(default=1, ge=1)
    action_type: CorporateActionType
    announced_at: datetime | None = None
    published_at: datetime
    available_at: datetime
    ex_date: date | None = None
    effective_date: date
    pay_date: date | None = None
    split_ratio: Decimal | None = None
    split_ratio_convention: Literal["new_shares_per_old_share"] | None = None
    cash_amount: Decimal | None = None
    cash_amount_basis: Literal["net_cash_per_entitled_share"] | None = None
    cash_consideration: Decimal | None = None
    currency: Currency | None = None
    source_digest: Digest

    _timestamps_are_utc = field_validator("announced_at", "published_at", "available_at")(
        lambda value: None if value is None else _utc(value)
    )
    _decimal_input_is_exact = field_validator(
        "split_ratio", "cash_amount", "cash_consideration", mode="before"
    )(_reject_binary_float)

    @field_validator("split_ratio", "cash_amount", "cash_consideration")
    @classmethod
    def validate_optional_positive_value(cls, value: Decimal | None) -> Decimal | None:
        return None if value is None else _positive(value)

    @model_validator(mode="after")
    def validate_action(self):
        if self.available_at < self.published_at:
            raise ValueError("corporate-action availability cannot precede publication")
        if self.announced_at is not None and self.announced_at > self.published_at:
            raise ValueError("corporate-action announcement cannot follow publication")
        if self.pay_date is not None and self.pay_date < self.effective_date:
            raise ValueError("corporate-action pay date cannot precede its effective date")
        if self.action_type is CorporateActionType.SPLIT:
            if (
                self.split_ratio is None
                or self.ex_date is None
                or self.ex_date != self.effective_date
                or self.pay_date is not None
                or self.split_ratio_convention != "new_shares_per_old_share"
                or self.cash_amount is not None
                or self.cash_amount_basis is not None
                or self.cash_consideration is not None
                or self.currency is not None
            ):
                raise ValueError(
                    "split actions require one effective ex-date, a ratio convention, "
                    "and only a split ratio"
                )
        elif self.action_type is CorporateActionType.CASH_DIVIDEND:
            if (
                self.cash_amount is None
                or self.currency is None
                or self.ex_date is None
                or self.effective_date != self.ex_date
                or self.pay_date is None
                or self.split_ratio is not None
                or self.split_ratio_convention is not None
                or self.cash_amount_basis != "net_cash_per_entitled_share"
                or self.cash_consideration is not None
            ):
                raise ValueError(
                    "cash dividends require one effective ex-date, pay date, net amount basis, "
                    "amount, and currency"
                )
        elif (
            self.ex_date is not None
            or self.split_ratio is not None
            or self.split_ratio_convention is not None
            or self.cash_amount is not None
            or self.cash_amount_basis is not None
            or (self.cash_consideration is None) != (self.currency is None)
            or (self.cash_consideration is None) != (self.pay_date is None)
        ):
            raise ValueError(
                "delistings allow only an optional cash consideration, currency, and pay date"
            )
        return self


class FxObservation(BacktestContract):
    observation_id: UUID
    revision: int = Field(default=1, ge=1)
    base_currency: Currency
    quote_currency: Currency
    observed_at: datetime
    available_at: datetime
    rate: Decimal
    rate_convention: Literal["quote_units_per_base_unit"] = (
        "quote_units_per_base_unit"
    )
    source_digest: Digest

    _timestamps_are_utc = field_validator("observed_at", "available_at")(_utc)
    _decimal_input_is_exact = field_validator("rate", mode="before")(_reject_binary_float)

    @field_validator("rate")
    @classmethod
    def validate_rate(cls, value: Decimal) -> Decimal:
        return _positive(value)

    @model_validator(mode="after")
    def validate_observation(self):
        if self.base_currency == self.quote_currency:
            raise ValueError("FX currencies must be different")
        if self.available_at < self.observed_at:
            raise ValueError("FX availability cannot precede observation")
        return self


class MarketDatasetManifest(BacktestContract):
    dataset_id: UUID
    tenant_id: UUID
    schema_version: Literal[1, 2] = 2
    created_at: datetime
    source: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_.-]+$")
    source_vintage: str = Field(min_length=1, max_length=160)
    instrument_digest: Digest
    content_bytes: int = Field(ge=1, le=16 * 1024 * 1024)
    content_sha256: Digest
    row_count: int = Field(ge=1)
    first_session: date
    last_session: date
    schedule_digest: Digest
    calendar_engine_version: str = Field(min_length=1, max_length=80)
    timezone_database_version: str = Field(min_length=1, max_length=80)
    settlement_policy_version: str = Field(min_length=1, max_length=80)
    binding_digest: Digest
    coverage_status: Literal["unverified"] = "unverified"
    limitations: tuple[DatasetLimitation, ...] = Field(min_length=1, max_length=64)

    _created_at_is_utc = field_validator("created_at")(_utc)

    @field_validator("limitations")
    @classmethod
    def validate_limitations(
        cls, value: tuple[DatasetLimitation, ...]
    ) -> tuple[DatasetLimitation, ...]:
        if len(set(value)) != len(value):
            raise ValueError("dataset limitations must be unique")
        if value != M5_UNVERIFIED_LIMITATIONS:
            raise ValueError("M5.0 requires canonical unverified coverage limitations")
        return value

    @model_validator(mode="after")
    def validate_session_window(self):
        if self.last_session < self.first_session:
            raise ValueError("dataset last session cannot precede first session")
        expected_binding = market_dataset_binding_digest(
            tenant_id=self.tenant_id,
            dataset_id=self.dataset_id,
            instrument_digest=self.instrument_digest,
            content_bytes=self.content_bytes,
            content_sha256=self.content_sha256,
            schedule_digest=self.schedule_digest,
            source_vintage=self.source_vintage,
            calendar_engine_version=self.calendar_engine_version,
            timezone_database_version=self.timezone_database_version,
            settlement_policy_version=self.settlement_policy_version,
            schema_version=2 if self.schema_version == 2 else None,
        )
        if self.binding_digest != expected_binding:
            raise ValueError("dataset binding digest does not match the frozen authority fields")
        return self

    @property
    def quantitative_claims_eligible(self) -> bool:
        """False when source coverage or vintage remains explicitly unverified."""

        return False


class BacktestSpec(BacktestContract):
    backtest_id: UUID
    tenant_id: UUID
    schema_version: Literal[2] = 2
    created_at: datetime
    dataset_references: tuple[DatasetReference, ...] = Field(min_length=1, max_length=1_001)
    instrument_ids: tuple[UUID, ...] = Field(min_length=1, max_length=1_000)
    benchmark_instrument_id: UUID
    start_session: date
    end_session: date
    base_currency: Currency
    initial_cash: Decimal
    simulation_policy_digest: Digest
    commission_bps: Decimal = Decimal("0")
    slippage_bps: Decimal = Decimal("0")
    max_volume_participation: Decimal = Decimal("0.10")
    decision_policy: Literal[DecisionPolicy.SETTLED_SESSION_CLOSE] = (
        DecisionPolicy.SETTLED_SESSION_CLOSE
    )
    fill_policy: Literal[FillPolicy.FIRST_SESSION_RAW_OPEN_AFTER_DECISION] = (
        FillPolicy.FIRST_SESSION_RAW_OPEN_AFTER_DECISION
    )
    price_basis: Literal["raw"] = "raw"
    missing_data_policy: Literal[MissingDataPolicy.FAIL_CLOSED] = MissingDataPolicy.FAIL_CLOSED
    random_seed: int = Field(default=0, ge=0, le=2**63 - 1)

    _created_at_is_utc = field_validator("created_at")(_utc)
    _decimal_input_is_exact = field_validator(
        "initial_cash",
        "commission_bps",
        "slippage_bps",
        "max_volume_participation",
        mode="before",
    )(_reject_binary_float)

    @field_validator("initial_cash")
    @classmethod
    def validate_initial_cash(cls, value: Decimal) -> Decimal:
        return _positive(value)

    @field_validator("commission_bps", "slippage_bps")
    @classmethod
    def validate_cost(cls, value: Decimal) -> Decimal:
        value = _non_negative(value)
        if value > Decimal("1000"):
            raise ValueError("backtest costs cannot exceed 1000 basis points")
        return value

    @field_validator("max_volume_participation")
    @classmethod
    def validate_participation(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0 or value > 1:
            raise ValueError("volume participation must be finite and in (0, 1]")
        return value

    @model_validator(mode="after")
    def validate_backtest_window(self):
        if self.end_session < self.start_session:
            raise ValueError("backtest end session cannot precede start session")
        if len(set(self.instrument_ids)) != len(self.instrument_ids):
            raise ValueError("backtest instrument IDs must be unique")
        if self.benchmark_instrument_id in self.instrument_ids:
            raise ValueError("benchmark instrument must be distinct from tradeable instruments")
        reference_ids = [item.instrument_id for item in self.dataset_references]
        if len(set(reference_ids)) != len(reference_ids):
            raise ValueError("each instrument requires exactly one dataset reference")
        dataset_ids = [item.dataset_id for item in self.dataset_references]
        if len(set(dataset_ids)) != len(dataset_ids):
            raise ValueError("each dataset may bind exactly one instrument reference")
        tradeable_ids = {
            item.instrument_id
            for item in self.dataset_references
            if item.role is DatasetRole.TRADEABLE
        }
        benchmark_ids = [
            item.instrument_id
            for item in self.dataset_references
            if item.role is DatasetRole.BENCHMARK
        ]
        if tradeable_ids != set(self.instrument_ids):
            raise ValueError("tradeable dataset references must match every instrument")
        if benchmark_ids != [self.benchmark_instrument_id]:
            raise ValueError("exactly one dataset reference must bind the benchmark")
        canonical_reference_order = self.instrument_ids + (self.benchmark_instrument_id,)
        if tuple(item.instrument_id for item in self.dataset_references) != canonical_reference_order:
            raise ValueError(
                "dataset references must follow instrument order with benchmark last"
            )
        return self


class BacktestSpecV3(BacktestSpec):
    """Economics-capable spec; v2 remains immutable for prior replay."""

    schema_version: Literal[3] = 3
    economics_policy_digest: Digest

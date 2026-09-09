"""Fail-closed errors for frozen market datasets and calendar evaluation."""

from __future__ import annotations

from enum import StrEnum


class BacktestDataCode(StrEnum):
    DATASET_INVALID = "DATASET_INVALID"
    DATASET_ALREADY_EXISTS = "DATASET_ALREADY_EXISTS"
    DATASET_TAMPERED = "DATASET_TAMPERED"
    DATASET_BINDING_MISMATCH = "DATASET_BINDING_MISMATCH"
    TENANT_SCOPE_MISMATCH = "TENANT_SCOPE_MISMATCH"
    PIT_AVAILABILITY_UNKNOWN = "PIT_AVAILABILITY_UNKNOWN"
    PIT_CUTOFF_MISMATCH = "PIT_CUTOFF_MISMATCH"
    CALENDAR_SNAPSHOT_MISMATCH = "CALENDAR_SNAPSHOT_MISMATCH"


class BacktestDataError(ValueError):
    """Base class for market data that is not admissible for a backtest."""

    default_code = BacktestDataCode.DATASET_INVALID

    def __init__(
        self,
        message: str,
        *,
        code: BacktestDataCode | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code or self.default_code


class DatasetIntegrityError(BacktestDataError):
    """Raised when frozen bytes or their manifest fail integrity checks."""

    default_code = BacktestDataCode.DATASET_TAMPERED


class DatasetAlreadyExistsError(BacktestDataError):
    """Raised when an immutable dataset identifier has already been claimed."""

    default_code = BacktestDataCode.DATASET_ALREADY_EXISTS


class MarketCalendarError(BacktestDataError):
    """Raised when a bar cannot be reconciled with its exchange calendar."""

    default_code = BacktestDataCode.CALENDAR_SNAPSHOT_MISMATCH

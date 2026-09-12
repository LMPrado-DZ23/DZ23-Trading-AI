"""Immutable, content-addressed storage for frozen market data snapshots."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import hmac
import io
import json
import os
import secrets
import shutil
import stat
import tempfile
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from .candlesticks import CandlestickEvidenceBundle
else:
    CandlestickEvidenceBundle = Any

from .calendar import BacktestCalendar, calendar_provenance, schedule_digest
from .contracts import (
    M5_UNVERIFIED_LIMITATIONS,
    CorporateAction,
    DailyBar,
    DatasetLimitation,
    ExecutionOpen,
    ExecutionPriceBundle,
    ExecutionPriceProjection,
    ExecutionPriceProvenance,
    FxObservation,
    InstrumentSpec,
    MarketDatasetManifest,
    _revalidate_execution_price_bundle,
    canonical_json_bytes,
    market_dataset_binding_digest,
)
from .errors import BacktestDataCode, DatasetAlreadyExistsError, DatasetIntegrityError

DATA_FILENAME = "market-data.jsonl"
MANIFEST_FILENAME = "manifest.json"
MANIFEST_SEAL_FILENAME = "manifest.sha256"
MAX_DATASET_BYTES = 16 * 1024 * 1024
MAX_DATASET_ROWS = 25_000
MAX_JSON_RECORD_BYTES = 256 * 1024
MAX_JSON_NESTING = 64


@dataclass(frozen=True, slots=True)
class _FrozenMarketDataset:
    manifest: MarketDatasetManifest
    instrument: InstrumentSpec
    bars: tuple[DailyBar, ...]
    corporate_actions: tuple[CorporateAction, ...]
    fx_observations: tuple[FxObservation, ...]

    def eligible_at(
        self,
        decision_at: datetime,
        *,
        decision_session_date: date,
    ) -> EligibleMarketView:
        """Project the full snapshot into a decision-time-safe immutable view."""

        if decision_at.tzinfo is None or decision_at.utcoffset() is None:
            raise DatasetIntegrityError("decision timestamp must include a timezone")
        decision_cutoff = decision_at.astimezone(UTC)
        decision_bars = tuple(
            item for item in self.bars if item.session_date == decision_session_date
        )
        if len(decision_bars) != 1:
            raise DatasetIntegrityError("decision session has no unique frozen bar")
        decision_bar = decision_bars[0]
        if decision_bar.available_at is None:
            raise DatasetIntegrityError(
                "decision session bar has unknown availability",
                code=BacktestDataCode.PIT_AVAILABILITY_UNKNOWN,
            )
        data_cutoff = decision_bar.available_at
        if data_cutoff > decision_cutoff:
            raise DatasetIntegrityError(
                "decision timestamp precedes the frozen session cutoff",
                code=BacktestDataCode.PIT_CUTOFF_MISMATCH,
            )
        if self.instrument.available_at > data_cutoff:
            raise DatasetIntegrityError(
                "instrument was not available at the frozen data cutoff",
                code=BacktestDataCode.PIT_AVAILABILITY_UNKNOWN,
            )
        indexed_bars = tuple(
            (ordinal, item)
            for ordinal, item in enumerate(self.bars)
            if (
                item.session_date <= decision_session_date
                and item.available_at is not None
                and item.available_at <= data_cutoff
            )
        )
        bar_ordinals = tuple(ordinal for ordinal, _item in indexed_bars)
        bars = tuple(item for _ordinal, item in indexed_bars)
        eligible_actions = tuple(
            item for item in self.corporate_actions if item.available_at <= data_cutoff
        )
        latest_actions: dict[UUID, CorporateAction] = {}
        for item in eligible_actions:
            current = latest_actions.get(item.action_id)
            if current is None or item.revision > current.revision:
                latest_actions[item.action_id] = item
        actions = tuple(
            sorted(
                latest_actions.values(),
                key=lambda item: (item.effective_date, str(item.action_id)),
            )
        )
        visible_delisting_date = (
            self.instrument.delisting_date
            if any(action.action_type.value == "delisting" for action in actions)
            else None
        )
        eligible_instrument = self.instrument.model_copy(
            update={"delisting_date": visible_delisting_date}
        )
        fx = tuple(item for item in self.fx_observations if item.available_at <= data_cutoff)
        eligible_data_digest = _eligible_data_sha256(
            data_cutoff_at=data_cutoff,
            decision_at=decision_cutoff,
            decision_session_date=decision_session_date,
            instrument=eligible_instrument,
            bars=bars,
            corporate_actions=actions,
            fx_observations=fx,
        )
        authoritative_payload = {
            "binding_digest": self.manifest.binding_digest,
            "dataset_id": self.manifest.dataset_id,
            "eligible_data_sha256": eligible_data_digest,
            "limitations": self.manifest.limitations,
            "manifest_digest": self.manifest.digest,
            "source_vintage": self.manifest.source_vintage,
            "tenant_id": self.manifest.tenant_id,
        }
        eligible_digest = hashlib.sha256(_canonical_json_bytes(authoritative_payload)).hexdigest()
        return EligibleMarketView(
            decision_at=decision_cutoff,
            data_cutoff_at=data_cutoff,
            decision_session_date=decision_bar.session_date,
            full_content_sha256=self.manifest.content_sha256,
            eligible_data_sha256=eligible_data_digest,
            eligible_view_sha256=eligible_digest,
            tenant_id=self.manifest.tenant_id,
            dataset_id=self.manifest.dataset_id,
            manifest_digest=self.manifest.digest,
            binding_digest=self.manifest.binding_digest,
            source_vintage=self.manifest.source_vintage,
            limitations=self.manifest.limitations,
            quantitative_claims_eligible=self.manifest.quantitative_claims_eligible,
            instrument=eligible_instrument,
            bar_ordinals=bar_ordinals,
            bars=bars,
            corporate_actions=actions,
            fx_observations=fx,
        )

    def execution_open_after(self, view: EligibleMarketView) -> ExecutionOpen:
        """Expose only the first frozen raw open strictly after a decision."""

        cutoff = view.decision_at
        decision_bars = tuple(
            bar for bar in view.bars if bar.session_date == view.decision_session_date
        )
        if len(decision_bars) != 1:
            raise DatasetIntegrityError("eligible view has no unique decision-session bar")
        decision_bar = decision_bars[0]
        future = tuple(bar for bar in self.bars if bar.session_open_at > cutoff)
        if not future:
            raise DatasetIntegrityError(
                "dataset has no frozen execution open after decision",
                code=BacktestDataCode.NEXT_OPEN_UNAVAILABLE,
            )
        bar = min(future, key=lambda item: item.session_open_at)
        return ExecutionOpen(
            dataset_id=self.manifest.dataset_id,
            manifest_digest=self.manifest.digest,
            instrument_id=bar.instrument_id,
            quote_currency=view.instrument.quote_currency,
            decision_session_date=view.decision_session_date,
            decision_at=view.decision_at,
            eligible_view_digest=view.eligible_view_sha256,
            session_date=bar.session_date,
            execution_at=bar.session_open_at,
            raw_open=bar.open,
            decision_volume=decision_bar.volume,
            parent_bar_digest=bar.digest,
            source_digest=bar.source_digest,
            decision_bar_digest=decision_bar.digest,
            decision_bar_source_digest=decision_bar.source_digest,
        )


@dataclass(frozen=True, slots=True)
class EligibleMarketView:
    """Only the records whose provenance was available by one decision time."""

    decision_at: datetime
    data_cutoff_at: datetime
    decision_session_date: date
    full_content_sha256: str
    eligible_data_sha256: str
    eligible_view_sha256: str
    tenant_id: UUID
    dataset_id: UUID
    manifest_digest: str
    binding_digest: str
    source_vintage: str
    limitations: tuple[DatasetLimitation, ...]
    quantitative_claims_eligible: bool
    instrument: InstrumentSpec
    bar_ordinals: tuple[int, ...]
    bars: tuple[DailyBar, ...]
    corporate_actions: tuple[CorporateAction, ...]
    fx_observations: tuple[FxObservation, ...]


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise DatasetIntegrityError("dataset contains a non-canonical value") from exc


def _record_bytes(kind: str, model: Any) -> bytes:
    return _canonical_json_bytes(
        {"kind": kind, "payload": model.model_dump(mode="python")}
    ) + b"\n"


def _eligible_data_sha256(
    *,
    data_cutoff_at: datetime,
    decision_at: datetime,
    decision_session_date: date,
    instrument: InstrumentSpec,
    bars: tuple[DailyBar, ...],
    corporate_actions: tuple[CorporateAction, ...],
    fx_observations: tuple[FxObservation, ...],
) -> str:
    """Hash the canonical eligible-view preimage without an unbounded list.

    The emitted bytes are identical to ``canonical_json_bytes`` for views below
    its generic 10,000-item sequence limit.  Dataset-specific row and byte caps
    are enforced before this internal helper is reached.
    """

    if 1 + len(bars) + len(corporate_actions) + len(fx_observations) > MAX_DATASET_ROWS:
        raise DatasetIntegrityError("eligible view exceeds the dataset row limit")
    digest = hashlib.sha256()
    digest.update(b'{"corporate_actions":[')
    for index, item in enumerate(corporate_actions):
        if index:
            digest.update(b",")
        digest.update(_canonical_json_bytes(item.model_dump(mode="python")))
    digest.update(b'],"daily_bars":[')
    for index, item in enumerate(bars):
        if index:
            digest.update(b",")
        digest.update(_canonical_json_bytes(item.model_dump(mode="python")))
    digest.update(b'],"data_cutoff_at":')
    digest.update(_canonical_json_bytes(data_cutoff_at))
    digest.update(b',"decision_at":')
    digest.update(_canonical_json_bytes(decision_at))
    digest.update(b',"decision_session_date":')
    digest.update(_canonical_json_bytes(decision_session_date))
    digest.update(b',"fx_observations":[')
    for index, item in enumerate(fx_observations):
        if index:
            digest.update(b",")
        digest.update(_canonical_json_bytes(item.model_dump(mode="python")))
    digest.update(b'],"instrument":')
    digest.update(_canonical_json_bytes(instrument.model_dump(mode="python")))
    digest.update(b"}")
    return digest.hexdigest()


def _timestamp_sort_key(value: datetime) -> str:
    return value.isoformat()


class FrozenDatasetRepository:
    """Publish once and replay only verified local bytes.

    This class deliberately has no vendor callback, clock, HTTP client, or cache
    fallback. Missing or corrupt frozen evidence stops the replay.
    """

    __slots__ = ("root", "_root_fd")

    def __init__(self, root: str | os.PathLike[str]) -> None:
        requested_root = Path(root)
        if requested_root.exists() and (
            requested_root.is_symlink() or self._is_reparse(requested_root.lstat())
        ):
            raise DatasetIntegrityError("dataset root cannot be a link or reparse point")
        self.root = requested_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._root_fd: int | None = None
        if os.name == "posix":
            before = self.root.lstat()
            if (
                not stat.S_ISDIR(before.st_mode)
                or self._is_reparse(before)
                or before.st_uid != os.geteuid()
            ):
                raise DatasetIntegrityError("dataset root must be an owned real directory")
            flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
            try:
                root_fd = os.open(self.root, flags)
            except OSError as exc:
                raise DatasetIntegrityError("dataset root could not be pinned safely") from exc
            after = os.fstat(root_fd)
            if (
                not stat.S_ISDIR(after.st_mode)
                or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
                or after.st_uid != os.geteuid()
            ):
                os.close(root_fd)
                raise DatasetIntegrityError("dataset root identity changed while it was pinned")
            os.fchmod(root_fd, stat.S_IRWXU)
            if stat.S_IMODE(os.fstat(root_fd).st_mode) != stat.S_IRWXU:
                os.close(root_fd)
                raise DatasetIntegrityError("dataset root permissions could not be hardened")
            self._root_fd = root_fd
        elif not self.root.is_dir() or self.root.is_symlink():
            raise DatasetIntegrityError("dataset root must be a real directory")

    def close(self) -> None:
        if self._root_fd is not None:
            os.close(self._root_fd)
            self._root_fd = None

    def __enter__(self) -> FrozenDatasetRepository:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __del__(self) -> None:
        with suppress(OSError):
            self.close()

    @staticmethod
    def _require_uuid(value: object, label: str) -> UUID:
        if type(value) is not UUID:
            raise DatasetIntegrityError(
                f"{label} must be a UUID",
                code=BacktestDataCode.DATASET_INVALID,
            )
        return value

    @staticmethod
    def _require_digest(value: object, label: str) -> str:
        if (
            type(value) is not str
            or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)
        ):
            raise DatasetIntegrityError(
                f"{label} is not a lowercase SHA-256",
                code=BacktestDataCode.DATASET_INVALID,
            )
        return value

    @staticmethod
    def _require_datetime(value: object, label: str) -> datetime:
        if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
            raise DatasetIntegrityError(
                f"{label} must be a timezone-aware datetime",
                code=BacktestDataCode.DATASET_INVALID,
            )
        return value

    @staticmethod
    def _require_date(value: object, label: str) -> date:
        if type(value) is not date:
            raise DatasetIntegrityError(
                f"{label} must be a date",
                code=BacktestDataCode.DATASET_INVALID,
            )
        return value

    @staticmethod
    def _bounded_tuple(values: Sequence[Any], *, limit: int, label: str) -> tuple[Any, ...]:
        result: list[Any] = []
        for item in values:
            if len(result) >= limit:
                raise DatasetIntegrityError(f"dataset exceeds the {label} row limit")
            result.append(item)
        return tuple(result)

    @staticmethod
    def _revalidate_contract(value: object, model_class: type[Any], label: str) -> Any:
        if type(value) is not model_class:
            raise DatasetIntegrityError(f"{label} must use the exact typed contract")
        try:
            return model_class.model_validate(value.model_dump(mode="python"))
        except ValueError as exc:
            raise DatasetIntegrityError(f"{label} failed contract revalidation") from exc

    @classmethod
    def _bounded_contract_tuple(
        cls,
        values: Sequence[Any],
        *,
        model_class: type[Any],
        limit: int,
        label: str,
    ) -> tuple[Any, ...]:
        result: list[Any] = []
        for item in values:
            if len(result) >= limit:
                raise DatasetIntegrityError(f"dataset exceeds the {label} row limit")
            result.append(cls._revalidate_contract(item, model_class, label))
        return tuple(result)

    def ingest(
        self,
        *,
        dataset_id: UUID,
        tenant_id: UUID,
        created_at: datetime,
        source: str,
        source_vintage: str,
        instrument: InstrumentSpec,
        bars: Sequence[DailyBar],
        corporate_actions: Sequence[CorporateAction] = (),
        fx_observations: Sequence[FxObservation] = (),
        limitations: Sequence[DatasetLimitation] = M5_UNVERIFIED_LIMITATIONS,
    ) -> MarketDatasetManifest:
        """Validate and atomically publish a new immutable snapshot."""

        dataset_id = self._require_uuid(dataset_id, "dataset_id")
        tenant_id = self._require_uuid(tenant_id, "tenant_id")
        instrument = self._revalidate_contract(instrument, InstrumentSpec, "instrument")
        frozen_bars = self._bounded_contract_tuple(
            bars,
            model_class=DailyBar,
            limit=MAX_DATASET_ROWS - 1,
            label="bar",
        )
        remaining = MAX_DATASET_ROWS - 1 - len(frozen_bars)
        frozen_actions = self._bounded_contract_tuple(
            corporate_actions,
            model_class=CorporateAction,
            limit=remaining,
            label="corporate-action",
        )
        remaining -= len(frozen_actions)
        frozen_fx = self._bounded_contract_tuple(
            fx_observations,
            model_class=FxObservation,
            limit=remaining,
            label="FX",
        )
        frozen_limitations = self._bounded_tuple(
            limitations, limit=64, label="limitation"
        )
        if not frozen_limitations:
            raise DatasetIntegrityError("dataset requires at least one explicit limitation")
        if any(type(item) is not DatasetLimitation for item in frozen_limitations):
            raise DatasetIntegrityError("dataset limitations must use the typed contract")
        self._validate_records(
            instrument,
            frozen_bars,
            frozen_actions,
            frozen_fx,
            validate_calendar=True,
        )
        latest_record_availability = max(
            [instrument.available_at]
            + [bar.fetched_at for bar in frozen_bars]
            + [action.available_at for action in frozen_actions]
            + [item.available_at for item in frozen_fx]
        )
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise DatasetIntegrityError("dataset creation timestamp must include a timezone")
        if created_at.astimezone(UTC) < latest_record_availability:
            raise DatasetIntegrityError("dataset cannot be created before its latest source record")

        action_ids = {(action.action_id, action.revision) for action in frozen_actions}
        if len(action_ids) != len(frozen_actions):
            raise DatasetIntegrityError("dataset contains duplicate corporate-action identifiers")
        fx_keys = {
            (item.base_currency, item.quote_currency, item.observed_at, item.available_at)
            for item in frozen_fx
        }
        if len(fx_keys) != len(frozen_fx):
            raise DatasetIntegrityError("dataset contains duplicate FX observations")

        ordered_actions = tuple(
            sorted(
                frozen_actions,
                key=lambda item: (
                    _timestamp_sort_key(item.available_at),
                    item.effective_date.isoformat(),
                    str(item.action_id),
                    item.revision,
                ),
            )
        )
        ordered_fx = tuple(
            sorted(
                frozen_fx,
                key=lambda item: (
                    _timestamp_sort_key(item.observed_at),
                    item.base_currency,
                    item.quote_currency,
                    _timestamp_sort_key(item.available_at),
                ),
            )
        )
        row_count = 1 + len(frozen_bars) + len(ordered_actions) + len(ordered_fx)
        if row_count > MAX_DATASET_ROWS:
            raise DatasetIntegrityError("dataset exceeds the row limit")
        content_buffer = io.BytesIO()
        for kind, items in (
            ("instrument", (instrument,)),
            ("daily_bar", frozen_bars),
            ("corporate_action", ordered_actions),
            ("fx_observation", ordered_fx),
        ):
            for item in items:
                record = _record_bytes(kind, item)
                if len(record) > MAX_JSON_RECORD_BYTES:
                    raise DatasetIntegrityError("dataset record exceeds its byte limit")
                if content_buffer.tell() + len(record) > MAX_DATASET_BYTES:
                    raise DatasetIntegrityError("dataset exceeds the byte limit")
                content_buffer.write(record)
        content = content_buffer.getvalue()

        calendar_engine_version, timezone_database_version, settlement_policy_version = (
            calendar_provenance()
        )
        frozen_schedule_digest = schedule_digest(
            instrument,
            frozen_bars,
            calendar_engine_version=calendar_engine_version,
            timezone_database_version=timezone_database_version,
            settlement_policy_version=settlement_policy_version,
        )
        content_sha256 = hashlib.sha256(content).hexdigest()
        manifest = MarketDatasetManifest(
            dataset_id=dataset_id,
            tenant_id=tenant_id,
            schema_version=1,
            created_at=created_at,
            source=source,
            source_vintage=source_vintage,
            instrument_digest=instrument.digest,
            content_bytes=len(content),
            content_sha256=content_sha256,
            row_count=row_count,
            first_session=frozen_bars[0].session_date,
            last_session=frozen_bars[-1].session_date,
            schedule_digest=frozen_schedule_digest,
            calendar_engine_version=calendar_engine_version,
            timezone_database_version=timezone_database_version,
            settlement_policy_version=settlement_policy_version,
            binding_digest=market_dataset_binding_digest(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                instrument_digest=instrument.digest,
                content_bytes=len(content),
                content_sha256=content_sha256,
                schedule_digest=frozen_schedule_digest,
                source_vintage=source_vintage,
                calendar_engine_version=calendar_engine_version,
                timezone_database_version=timezone_database_version,
                settlement_policy_version=settlement_policy_version,
            ),
            limitations=frozen_limitations,
        )
        manifest_bytes = _canonical_json_bytes(manifest.model_dump(mode="python")) + b"\n"
        manifest_seal = hashlib.sha256(manifest_bytes).hexdigest().encode("ascii") + b"\n"
        self._publish(dataset_id, content, manifest_bytes, manifest_seal)
        return manifest

    def _load_verified(
        self,
        dataset_id: UUID,
        *,
        expected_tenant_id: UUID,
        expected_manifest_digest: str,
    ) -> _FrozenMarketDataset:
        """Load a snapshot only after all byte and semantic checks pass."""

        dataset_id = self._require_uuid(dataset_id, "dataset_id")
        expected_tenant_id = self._require_uuid(expected_tenant_id, "expected_tenant_id")
        expected_manifest_digest = self._require_digest(
            expected_manifest_digest, "expected manifest digest"
        )
        manifest_bytes, seal_bytes, content = self._read_dataset_files(dataset_id)
        expected_seal = hashlib.sha256(manifest_bytes).hexdigest().encode("ascii") + b"\n"
        if seal_bytes != expected_seal:
            raise DatasetIntegrityError("manifest seal does not match its frozen bytes")
        manifest = self._parse_manifest(manifest_bytes)
        if not hmac.compare_digest(manifest.digest, expected_manifest_digest):
            raise DatasetIntegrityError(
                "manifest does not match the external trusted binding",
                code=BacktestDataCode.DATASET_BINDING_MISMATCH,
            )
        if manifest.tenant_id != expected_tenant_id:
            raise DatasetIntegrityError(
                "dataset belongs to a different tenant",
                code=BacktestDataCode.TENANT_SCOPE_MISMATCH,
            )
        if manifest.dataset_id != dataset_id:
            raise DatasetIntegrityError("manifest dataset identifier does not match its path")

        if hashlib.sha256(content).hexdigest() != manifest.content_sha256:
            raise DatasetIntegrityError("dataset content hash does not match the manifest")
        if len(content) != manifest.content_bytes:
            raise DatasetIntegrityError("dataset content length does not match the manifest")
        records = self._parse_records(content, manifest.row_count)
        instrument, bars, actions, fx = records
        # Replay trusts the materialized, hashed schedule rather than consulting
        # today's exchange-calendar database. This keeps historical results
        # reproducible after dependency upgrades and makes load strictly local.
        self._validate_records(instrument, bars, actions, fx, validate_calendar=False)
        if instrument.digest != manifest.instrument_digest:
            raise DatasetIntegrityError("instrument digest does not match the manifest")
        if bars[0].session_date != manifest.first_session or bars[-1].session_date != manifest.last_session:
            raise DatasetIntegrityError("dataset session range does not match the manifest")
        if schedule_digest(
            instrument,
            bars,
            calendar_engine_version=manifest.calendar_engine_version,
            timezone_database_version=manifest.timezone_database_version,
            settlement_policy_version=manifest.settlement_policy_version,
        ) != manifest.schedule_digest:
            raise DatasetIntegrityError("dataset schedule does not match the manifest")
        return _FrozenMarketDataset(manifest, instrument, bars, actions, fx)

    def verify(
        self,
        dataset_id: UUID,
        *,
        expected_tenant_id: UUID,
        expected_manifest_digest: str,
    ) -> MarketDatasetManifest:
        """Verify a snapshot without exposing future market records."""

        return self._load_verified(
            dataset_id,
            expected_tenant_id=expected_tenant_id,
            expected_manifest_digest=expected_manifest_digest,
        ).manifest

    def load_eligible(
        self,
        dataset_id: UUID,
        *,
        expected_tenant_id: UUID,
        expected_manifest_digest: str,
        decision_at: datetime,
        decision_session_date: date,
    ) -> EligibleMarketView:
        decision_at = self._require_datetime(decision_at, "decision_at")
        decision_session_date = self._require_date(
            decision_session_date, "decision_session_date"
        )
        return self._load_verified(
            dataset_id,
            expected_tenant_id=expected_tenant_id,
            expected_manifest_digest=expected_manifest_digest,
        ).eligible_at(decision_at, decision_session_date=decision_session_date)

    def derive_candlestick_bundle(
        self,
        dataset_id: UUID,
        *,
        expected_tenant_id: UUID,
        expected_manifest_digest: str,
        decision_at: datetime,
        decision_session_date: date,
    ) -> CandlestickEvidenceBundle:
        """Derive M5.1 evidence only after this repository verifies authority.

        The pure detector is intentionally private: accepting a caller-built
        ``EligibleMarketView`` would let that caller forge frozen bar ordinals.
        """

        from .candlesticks import _derive_candlestick_bundle_from_view

        if type(self) is not FrozenDatasetRepository:
            raise DatasetIntegrityError("candlestick derivation requires the repository authority")
        decision_at = FrozenDatasetRepository._require_datetime(decision_at, "decision_at")
        decision_session_date = FrozenDatasetRepository._require_date(
            decision_session_date, "decision_session_date"
        )
        view = FrozenDatasetRepository._load_verified(
            self,
            dataset_id,
            expected_tenant_id=expected_tenant_id,
            expected_manifest_digest=expected_manifest_digest,
        ).eligible_at(
            decision_at,
            decision_session_date=decision_session_date,
        )
        return _derive_candlestick_bundle_from_view(view)

    def load_execution_open(
        self,
        dataset_id: UUID,
        *,
        expected_tenant_id: UUID,
        expected_manifest_digest: str,
        decision_at: datetime,
        decision_session_date: date,
        expected_eligible_view_digest: str,
    ) -> ExecutionOpen:
        decision_at = self._require_datetime(decision_at, "decision_at")
        decision_session_date = self._require_date(
            decision_session_date, "decision_session_date"
        )
        expected_eligible_view_digest = self._require_digest(
            expected_eligible_view_digest, "expected eligible-view digest"
        )
        dataset = self._load_verified(
            dataset_id,
            expected_tenant_id=expected_tenant_id,
            expected_manifest_digest=expected_manifest_digest,
        )
        view = dataset.eligible_at(
            decision_at,
            decision_session_date=decision_session_date,
        )
        if not hmac.compare_digest(
            view.eligible_view_sha256, expected_eligible_view_digest
        ):
            raise DatasetIntegrityError(
                "execution open does not match the authorized eligible view",
                code=BacktestDataCode.DATASET_BINDING_MISMATCH,
            )
        return dataset.execution_open_after(view)

    def load_execution_price(
        self,
        dataset_id: UUID,
        *,
        expected_tenant_id: UUID,
        expected_instrument_id: UUID,
        expected_manifest_digest: str,
        decision_at: datetime,
        decision_session_date: date,
        expected_eligible_view_digest: str,
    ) -> ExecutionPriceBundle:
        """Return a narrow raw-open projection and separate full provenance."""

        if type(self) is not FrozenDatasetRepository:
            raise DatasetIntegrityError("execution pricing requires the repository authority")
        expected_instrument_id = FrozenDatasetRepository._require_uuid(
            expected_instrument_id, "expected_instrument_id"
        )
        decision_at = FrozenDatasetRepository._require_datetime(decision_at, "decision_at")
        decision_session_date = FrozenDatasetRepository._require_date(
            decision_session_date, "decision_session_date"
        )
        expected_eligible_view_digest = FrozenDatasetRepository._require_digest(
            expected_eligible_view_digest, "expected eligible-view digest"
        )
        dataset = FrozenDatasetRepository._load_verified(
            self,
            dataset_id,
            expected_tenant_id=expected_tenant_id,
            expected_manifest_digest=expected_manifest_digest,
        )
        view = dataset.eligible_at(
            decision_at,
            decision_session_date=decision_session_date,
        )
        if not hmac.compare_digest(
            view.eligible_view_sha256, expected_eligible_view_digest
        ):
            raise DatasetIntegrityError(
                "execution price does not match the authorized eligible view",
                code=BacktestDataCode.DATASET_BINDING_MISMATCH,
            )
        execution = dataset.execution_open_after(view)
        if execution.instrument_id != expected_instrument_id:
            raise DatasetIntegrityError(
                "execution price does not match the authorized instrument",
                code=BacktestDataCode.DATASET_BINDING_MISMATCH,
            )
        price_payload = {
            "projection_version": "execution-price/2",
            "instrument_id": execution.instrument_id,
            "quote_currency": execution.quote_currency,
            "decision_session_date": execution.decision_session_date,
            "decision_at": execution.decision_at,
            "session_date": execution.session_date,
            "execution_at": execution.execution_at,
            "raw_open": execution.raw_open,
            "decision_volume": execution.decision_volume,
            "price_basis": "raw",
            "liquidity_basis": "decision-session-raw-volume",
        }
        price = ExecutionPriceProjection(
            **price_payload,
            projection_digest=hashlib.sha256(_canonical_json_bytes(price_payload)).hexdigest(),
        )
        provenance_payload = {
            "provenance_version": "execution-price-provenance/2",
            "dataset_id": execution.dataset_id,
            "manifest_digest": execution.manifest_digest,
            "eligible_view_digest": execution.eligible_view_digest,
            "projection_digest": price.projection_digest,
            "parent_bar_digest": execution.parent_bar_digest,
            "source_digest": execution.source_digest,
            "decision_bar_digest": execution.decision_bar_digest,
            "decision_bar_source_digest": execution.decision_bar_source_digest,
        }
        provenance = ExecutionPriceProvenance(
            **provenance_payload,
            provenance_digest=hashlib.sha256(_canonical_json_bytes(provenance_payload)).hexdigest(),
        )
        bundle_payload = {
            "bundle_version": "execution-price-bundle/2",
            "price_digest": price.projection_digest,
            "provenance_digest": provenance.provenance_digest,
        }
        return _revalidate_execution_price_bundle(
            ExecutionPriceBundle(
                price=price,
                provenance=provenance,
                bundle_digest=hashlib.sha256(_canonical_json_bytes(bundle_payload)).hexdigest(),
            )
        )

    def _verify_execution_price(
        self,
        candidate: ExecutionPriceBundle,
        dataset_id: UUID,
        *,
        expected_tenant_id: UUID,
        expected_instrument_id: UUID,
        expected_manifest_digest: str,
        decision_at: datetime,
        decision_session_date: date,
        expected_eligible_view_digest: str,
    ) -> ExecutionPriceBundle:
        """Rederive and compare a candidate before authoritative consumption.

        Self-digests prove internal consistency, not origin. A simulator must
        source every expected value here from its authenticated run and order
        state, then consume only the rederived return value.
        """

        if type(self) is not FrozenDatasetRepository:
            raise DatasetIntegrityError("execution pricing requires the repository authority")
        try:
            checked = _revalidate_execution_price_bundle(candidate)
        except ValueError as exc:
            raise DatasetIntegrityError(
                "execution-price candidate failed deep contract revalidation",
                code=BacktestDataCode.DATASET_BINDING_MISMATCH,
            ) from exc
        authoritative = FrozenDatasetRepository.load_execution_price(
            self,
            dataset_id,
            expected_tenant_id=expected_tenant_id,
            expected_instrument_id=expected_instrument_id,
            expected_manifest_digest=expected_manifest_digest,
            decision_at=decision_at,
            decision_session_date=decision_session_date,
            expected_eligible_view_digest=expected_eligible_view_digest,
        )
        if not hmac.compare_digest(
            _canonical_json_bytes(checked.model_dump(mode="python")),
            _canonical_json_bytes(authoritative.model_dump(mode="python")),
        ):
            raise DatasetIntegrityError(
                "execution-price candidate does not match frozen market data",
                code=BacktestDataCode.DATASET_BINDING_MISMATCH,
            )
        return authoritative

    def _dataset_dir(self, dataset_id: UUID) -> Path:
        dataset_id = self._require_uuid(dataset_id, "dataset_id")
        candidate = self.root / str(dataset_id)
        if candidate.parent != self.root:
            raise DatasetIntegrityError("dataset path escaped its repository")
        return candidate

    def _publish(
        self,
        dataset_id: UUID,
        content: bytes,
        manifest: bytes,
        seal: bytes,
    ) -> None:
        if os.name == "posix":
            self._publish_at(dataset_id, content, manifest, seal)
            return
        destination = self._dataset_dir(dataset_id)
        claim = self.root / f".{dataset_id}.claim"
        try:
            claim_fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o400)
        except FileExistsError as exc:
            raise DatasetAlreadyExistsError("dataset identifier has already been claimed") from exc
        os.close(claim_fd)
        temporary = Path(tempfile.mkdtemp(prefix=f".{dataset_id}.", dir=self.root))
        try:
            if destination.exists():
                raise DatasetAlreadyExistsError("immutable dataset already exists")
            self._write_file(temporary / DATA_FILENAME, content)
            self._write_file(temporary / MANIFEST_FILENAME, manifest)
            self._write_file(temporary / MANIFEST_SEAL_FILENAME, seal)
            self._fsync_directory(temporary)
            os.rename(temporary, destination)
            self._fsync_directory(self.root)
        except Exception:
            if temporary.exists() and temporary.parent == self.root:
                shutil.rmtree(temporary)
            claim.unlink(missing_ok=True)
            raise

    @staticmethod
    def _write_file(path: Path, content: bytes) -> None:
        with path.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        path.chmod(stat.S_IRUSR)

    def _publish_at(
        self,
        dataset_id: UUID,
        content: bytes,
        manifest: bytes,
        seal: bytes,
    ) -> None:
        if self._root_fd is None:
            raise DatasetIntegrityError("dataset root descriptor is closed")
        root_fd = os.dup(self._root_fd)
        leaf = str(self._require_uuid(dataset_id, "dataset_id"))
        claim = f".{leaf}.claim"
        temporary = f".{leaf}.{secrets.token_hex(16)}"
        temp_fd: int | None = None
        claimed = False
        published = False
        try:
            try:
                claim_fd = os.open(
                    claim,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                    0o400,
                    dir_fd=root_fd,
                )
            except FileExistsError as exc:
                raise DatasetAlreadyExistsError(
                    "dataset identifier has already been claimed"
                ) from exc
            else:
                os.close(claim_fd)
                claimed = True
            try:
                os.stat(leaf, dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise DatasetAlreadyExistsError("immutable dataset already exists")
            os.mkdir(temporary, mode=0o700, dir_fd=root_fd)
            flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
            temp_fd = os.open(temporary, flags, dir_fd=root_fd)
            self._write_file_at(temp_fd, DATA_FILENAME, content)
            self._write_file_at(temp_fd, MANIFEST_FILENAME, manifest)
            self._write_file_at(temp_fd, MANIFEST_SEAL_FILENAME, seal)
            os.fsync(temp_fd)
            self._rename_directory_noreplace(root_fd, temporary, leaf)
            published = True
            os.fsync(root_fd)
        except Exception:
            if temp_fd is not None and not published:
                for name in (DATA_FILENAME, MANIFEST_FILENAME, MANIFEST_SEAL_FILENAME):
                    with suppress(FileNotFoundError):
                        os.unlink(name, dir_fd=temp_fd)
                os.close(temp_fd)
                temp_fd = None
            if not published:
                with suppress(FileNotFoundError):
                    os.rmdir(temporary, dir_fd=root_fd)
                if claimed:
                    with suppress(FileNotFoundError):
                        os.unlink(claim, dir_fd=root_fd)
            raise
        finally:
            if temp_fd is not None:
                os.close(temp_fd)
            os.close(root_fd)

    @staticmethod
    def _rename_directory_noreplace(directory_fd: int, source: str, destination: str) -> None:
        if os.name != "posix":
            raise DatasetIntegrityError("no-replace directory publication requires POSIX")
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise DatasetIntegrityError("atomic no-replace publication is unavailable")
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(
            directory_fd,
            os.fsencode(source),
            directory_fd,
            os.fsencode(destination),
            1,
        )
        if result == 0:
            return
        error_number = ctypes.get_errno()
        if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
            raise DatasetAlreadyExistsError("immutable dataset already exists")
        raise DatasetIntegrityError("atomic no-replace publication failed") from OSError(
            error_number, os.strerror(error_number)
        )

    @staticmethod
    def _write_file_at(directory_fd: int, name: str, content: bytes) -> None:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(name, flags, 0o400, dir_fd=directory_fd)
        try:
            view = memoryview(content)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)

    def _read_dataset_files(self, dataset_id: UUID) -> tuple[bytes, bytes, bytes]:
        """Read all components beneath one pinned directory handle on POSIX."""

        if os.name == "posix":
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
            if self._root_fd is None:
                raise DatasetIntegrityError("dataset root descriptor is closed")
            try:
                root_fd = os.dup(self._root_fd)
                try:
                    leaf = str(self._require_uuid(dataset_id, "dataset_id"))
                    dataset_fd = os.open(leaf, directory_flags, dir_fd=root_fd)
                finally:
                    os.close(root_fd)
            except OSError as exc:
                raise DatasetIntegrityError("frozen dataset directory could not be opened safely") from exc
            try:
                return (
                    self._read_regular_at(dataset_fd, MANIFEST_FILENAME, 2 * 1024 * 1024),
                    self._read_regular_at(dataset_fd, MANIFEST_SEAL_FILENAME, 256),
                    self._read_regular_at(dataset_fd, DATA_FILENAME, MAX_DATASET_BYTES),
                )
            finally:
                os.close(dataset_fd)

        # Windows deployments are not supported for the capital-facing runtime.
        # This fallback still rejects reparse points and verifies containment
        # before and after opening, but Linux dirfd pinning is the production gate.
        dataset_dir = self._dataset_dir(dataset_id)
        try:
            resolved = dataset_dir.resolve(strict=True)
            before = dataset_dir.lstat()
        except (FileNotFoundError, OSError) as exc:
            raise DatasetIntegrityError("frozen dataset does not exist") from exc
        if (
            resolved != dataset_dir
            or resolved.parent != self.root
            or not stat.S_ISDIR(before.st_mode)
            or self._is_reparse(before)
        ):
            raise DatasetIntegrityError("frozen dataset directory is not a real contained directory")
        result = (
            self._read_regular(dataset_dir / MANIFEST_FILENAME, 2 * 1024 * 1024),
            self._read_regular(dataset_dir / MANIFEST_SEAL_FILENAME, 256),
            self._read_regular(dataset_dir / DATA_FILENAME, MAX_DATASET_BYTES),
        )
        after = dataset_dir.lstat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise DatasetIntegrityError("frozen dataset directory changed during read")
        return result

    @staticmethod
    def _read_regular_at(directory_fd: int, name: str, max_bytes: int) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(name, flags, dir_fd=directory_fd)
        except OSError as exc:
            raise DatasetIntegrityError("frozen dataset file could not be opened safely") from exc
        try:
            file_stat = os.fstat(fd)
            if not stat.S_ISREG(file_stat.st_mode) or FrozenDatasetRepository._is_reparse(file_stat):
                raise DatasetIntegrityError("frozen dataset contains a non-regular file")
            if file_stat.st_size > max_bytes:
                raise DatasetIntegrityError("frozen dataset file exceeds its byte limit")
            with os.fdopen(fd, "rb", closefd=False) as handle:
                content = handle.read(max_bytes + 1)
        finally:
            os.close(fd)
        if len(content) > max_bytes:
            raise DatasetIntegrityError("frozen dataset file exceeds its byte limit")
        return content

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name != "posix":
            return
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def _read_regular(path: Path, max_bytes: int) -> bytes:
        try:
            before = path.lstat()
        except FileNotFoundError as exc:
            raise DatasetIntegrityError("frozen dataset is incomplete") from exc
        if not stat.S_ISREG(before.st_mode) or FrozenDatasetRepository._is_reparse(before):
            raise DatasetIntegrityError("frozen dataset contains a non-regular file")
        if before.st_size > max_bytes:
            raise DatasetIntegrityError("frozen dataset file exceeds its byte limit")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags)
        except OSError as exc:
            raise DatasetIntegrityError("frozen dataset file could not be opened safely") from exc
        try:
            after = os.fstat(fd)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise DatasetIntegrityError("frozen dataset file changed during open")
            with os.fdopen(fd, "rb", closefd=False) as handle:
                content = handle.read(max_bytes + 1)
        finally:
            os.close(fd)
        if len(content) > max_bytes:
            raise DatasetIntegrityError("frozen dataset file exceeds its byte limit")
        return content

    @staticmethod
    def _parse_manifest(content: bytes) -> MarketDatasetManifest:
        try:
            raw = _strict_json_loads(content)
            manifest = MarketDatasetManifest.model_validate(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise DatasetIntegrityError("manifest is not valid canonical JSON") from exc
        if content != _canonical_json_bytes(manifest.model_dump(mode="python")) + b"\n":
            raise DatasetIntegrityError("manifest bytes are not canonical")
        return manifest

    @staticmethod
    def _parse_records(
        content: bytes,
        expected_count: int,
    ) -> tuple[
        InstrumentSpec,
        tuple[DailyBar, ...],
        tuple[CorporateAction, ...],
        tuple[FxObservation, ...],
    ]:
        if expected_count < 1 or expected_count > MAX_DATASET_ROWS:
            raise DatasetIntegrityError("dataset row count exceeds the operational limit")
        if not content.endswith(b"\n"):
            raise DatasetIntegrityError("dataset is not canonical JSONL")
        classes = {
            "instrument": InstrumentSpec,
            "daily_bar": DailyBar,
            "corporate_action": CorporateAction,
            "fx_observation": FxObservation,
        }
        order = {"instrument": 0, "daily_bar": 1, "corporate_action": 2, "fx_observation": 3}
        instrument: InstrumentSpec | None = None
        bars: list[DailyBar] = []
        actions: list[CorporateAction] = []
        fx: list[FxObservation] = []
        previous_group = -1
        count = 0
        stream = io.BytesIO(content)
        while True:
            line_with_newline = stream.readline(MAX_JSON_RECORD_BYTES + 2)
            if not line_with_newline:
                break
            count += 1
            if count > expected_count or count > MAX_DATASET_ROWS:
                raise DatasetIntegrityError("dataset row count does not match the manifest")
            if len(line_with_newline) > MAX_JSON_RECORD_BYTES + 1:
                raise DatasetIntegrityError("dataset record exceeds its byte limit")
            if not line_with_newline.endswith(b"\n") or line_with_newline.endswith(b"\r\n"):
                raise DatasetIntegrityError("dataset is not canonical JSONL")
            line = line_with_newline[:-1]
            try:
                raw = _strict_json_loads(line)
                if not isinstance(raw, dict) or set(raw) != {"kind", "payload"}:
                    raise ValueError
                kind = raw["kind"]
                model_class = classes[kind]
                model = model_class.model_validate(raw["payload"])
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise DatasetIntegrityError("dataset contains an invalid typed record") from exc
            if line_with_newline != _record_bytes(kind, model):
                raise DatasetIntegrityError("dataset record bytes are not canonical")
            group = order[kind]
            if group < previous_group:
                raise DatasetIntegrityError("dataset record groups are not canonical")
            previous_group = group
            if kind == "instrument":
                if count != 1 or instrument is not None:
                    raise DatasetIntegrityError(
                        "dataset must contain exactly one leading instrument"
                    )
                instrument = model
            elif kind == "daily_bar":
                bars.append(model)
            elif kind == "corporate_action":
                actions.append(model)
            else:
                fx.append(model)
        if count != expected_count:
            raise DatasetIntegrityError("dataset row count does not match the manifest")
        if instrument is None:
            raise DatasetIntegrityError("dataset must contain exactly one leading instrument")
        return instrument, tuple(bars), tuple(actions), tuple(fx)

    @staticmethod
    def _is_reparse(file_stat: os.stat_result) -> bool:
        attributes = getattr(file_stat, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return bool(attributes & reparse_flag)

    @staticmethod
    def _validate_records(
        instrument: InstrumentSpec,
        bars: tuple[DailyBar, ...],
        actions: tuple[CorporateAction, ...],
        fx: tuple[FxObservation, ...],
        *,
        validate_calendar: bool,
    ) -> None:
        if not bars:
            raise DatasetIntegrityError("dataset requires at least one daily bar")
        calendar = BacktestCalendar(instrument.market_calendar) if validate_calendar else None
        previous: date | None = None
        seen: set[date] = set()
        for bar in bars:
            if bar.session_date in seen:
                raise DatasetIntegrityError("dataset contains duplicate daily bars")
            if previous is not None and bar.session_date <= previous:
                raise DatasetIntegrityError("daily bars are not strictly chronological")
            if bar.instrument_id != instrument.instrument_id:
                raise DatasetIntegrityError("bar belongs to a different instrument")
            if bar.session_date < instrument.valid_from:
                raise DatasetIntegrityError("bar predates the instrument listing")
            if instrument.valid_to is not None and bar.session_date > instrument.valid_to:
                raise DatasetIntegrityError("bar follows the instrument delisting")
            if calendar is not None:
                calendar.validate_bar(instrument, bar)
            previous = bar.session_date
            seen.add(bar.session_date)
        if calendar is not None:
            expected_sessions = calendar.session_dates_in_range(
                bars[0].session_date, bars[-1].session_date
            )
            if tuple(bar.session_date for bar in bars) != expected_sessions:
                raise DatasetIntegrityError("dataset has a missing exchange session")
        if any(action.instrument_id != instrument.instrument_id for action in actions):
            raise DatasetIntegrityError("corporate action belongs to a different instrument")
        revisions: dict[UUID, list[CorporateAction]] = {}
        for action in actions:
            revisions.setdefault(action.action_id, []).append(action)
        for chain in revisions.values():
            ordered = sorted(chain, key=lambda item: item.revision)
            if [item.revision for item in ordered] != list(range(1, len(ordered) + 1)):
                raise DatasetIntegrityError("corporate-action revisions must be contiguous from one")
            first = ordered[0]
            for previous_action, action in zip(ordered, ordered[1:], strict=False):
                if (
                    action.published_at <= previous_action.published_at
                    or action.available_at <= previous_action.available_at
                ):
                    raise DatasetIntegrityError(
                        "corporate-action revisions must advance publication and availability"
                    )
                if (
                    action.instrument_id != first.instrument_id
                    or action.action_type is not first.action_type
                    or action.effective_date != first.effective_date
                    or action.ex_date != first.ex_date
                ):
                    raise DatasetIntegrityError(
                        "corporate-action revisions cannot rewrite identity or effective dates"
                    )
        delistings = [
            action
            for action in actions
            if action.action_type.value == "delisting"
        ]
        if len({action.action_id for action in delistings}) > 1:
            raise DatasetIntegrityError("dataset contains multiple logical delisting actions")
        if instrument.delisting_date is None and delistings:
            raise DatasetIntegrityError(
                "delisting action requires a matching security-master delisting date"
            )
        if instrument.delisting_date is not None and (
            not delistings
            or any(action.effective_date != instrument.delisting_date for action in delistings)
        ):
            raise DatasetIntegrityError(
                "security-master delisting requires matching corporate-action evidence"
            )
        if any(item.available_at < item.observed_at for item in fx):
            raise DatasetIntegrityError("FX observation is available before it was observed")


def verify_execution_price_authority(
    repository: FrozenDatasetRepository,
    candidate: ExecutionPriceBundle,
    dataset_id: UUID,
    *,
    expected_tenant_id: UUID,
    expected_instrument_id: UUID,
    expected_manifest_digest: str,
    decision_at: datetime,
    decision_session_date: date,
    expected_eligible_view_digest: str,
) -> ExecutionPriceBundle:
    """Non-virtual execution-price authority boundary for future reducers."""

    if type(repository) is not FrozenDatasetRepository:
        raise DatasetIntegrityError("execution pricing requires the repository authority")
    return FrozenDatasetRepository._verify_execution_price(
        repository,
        candidate,
        dataset_id,
        expected_tenant_id=expected_tenant_id,
        expected_instrument_id=expected_instrument_id,
        expected_manifest_digest=expected_manifest_digest,
        decision_at=decision_at,
        decision_session_date=decision_session_date,
        expected_eligible_view_digest=expected_eligible_view_digest,
    )


def resolve_execution_price_authority(
    repository: FrozenDatasetRepository,
    dataset_id: UUID,
    *,
    expected_tenant_id: UUID,
    expected_instrument_id: UUID,
    expected_manifest_digest: str,
    decision_at: datetime,
    decision_session_date: date,
    expected_eligible_view_digest: str,
) -> ExecutionPriceBundle:
    """Load and independently rederive the only execution price a reducer may consume.

    Callers provide custody bindings, never an economic price object.  The
    candidate is loaded from the frozen repository and then passed through the
    non-virtual M5.2a verification boundary before it is returned.
    """

    if type(repository) is not FrozenDatasetRepository:
        raise DatasetIntegrityError("execution pricing requires the repository authority")
    candidate = FrozenDatasetRepository.load_execution_price(
        repository,
        dataset_id,
        expected_tenant_id=expected_tenant_id,
        expected_instrument_id=expected_instrument_id,
        expected_manifest_digest=expected_manifest_digest,
        decision_at=decision_at,
        decision_session_date=decision_session_date,
        expected_eligible_view_digest=expected_eligible_view_digest,
    )
    return verify_execution_price_authority(
        repository,
        candidate,
        dataset_id,
        expected_tenant_id=expected_tenant_id,
        expected_instrument_id=expected_instrument_id,
        expected_manifest_digest=expected_manifest_digest,
        decision_at=decision_at,
        decision_session_date=decision_session_date,
        expected_eligible_view_digest=expected_eligible_view_digest,
    )


def _strict_json_loads(content: bytes) -> Any:
    if len(content) > MAX_JSON_RECORD_BYTES:
        raise DatasetIntegrityError("JSON record exceeds its byte limit")
    expected_closings: list[int] = []
    in_string = False
    escaped = False
    for byte in content:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                in_string = False
            continue
        if byte == 0x22:
            in_string = True
        elif byte in (0x7B, 0x5B):
            expected_closings.append(0x7D if byte == 0x7B else 0x5D)
            if len(expected_closings) > MAX_JSON_NESTING:
                raise DatasetIntegrityError("JSON record exceeds its nesting limit")
        elif byte in (0x7D, 0x5D) and (
            not expected_closings or expected_closings.pop() != byte
        ):
            raise DatasetIntegrityError("JSON record has mismatched delimiters")
    if in_string or expected_closings:
        raise DatasetIntegrityError("JSON record is truncated or structurally invalid")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise DatasetIntegrityError("JSON record contains duplicate object keys")
            result[key] = value
        return result

    try:
        return json.loads(
            content,
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                DatasetIntegrityError("JSON record contains a non-finite number")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatasetIntegrityError("JSON record is invalid") from exc

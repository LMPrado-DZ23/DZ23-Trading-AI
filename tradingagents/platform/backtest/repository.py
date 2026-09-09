"""Immutable, content-addressed storage for frozen market data snapshots."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

from .calendar import BacktestCalendar, calendar_provenance, schedule_digest
from .contracts import (
    CorporateAction,
    DailyBar,
    DatasetLimitation,
    FxObservation,
    InstrumentSpec,
    MarketDatasetManifest,
    market_dataset_binding_digest,
)
from .errors import BacktestDataCode, DatasetAlreadyExistsError, DatasetIntegrityError

DATA_FILENAME = "market-data.jsonl"
MANIFEST_FILENAME = "manifest.json"
MANIFEST_SEAL_FILENAME = "manifest.sha256"
MAX_DATASET_BYTES = 512 * 1024 * 1024
MAX_DATASET_ROWS = 5_000_000
MAX_JSON_RECORD_BYTES = 1024 * 1024
MAX_JSON_NESTING = 64


@dataclass(frozen=True, slots=True)
class FrozenMarketDataset:
    manifest: MarketDatasetManifest
    instrument: InstrumentSpec
    bars: tuple[DailyBar, ...]
    corporate_actions: tuple[CorporateAction, ...]
    fx_observations: tuple[FxObservation, ...]

    def eligible_at(self, decision_at: datetime) -> EligibleMarketView:
        """Project the full snapshot into a decision-time-safe immutable view."""

        if decision_at.tzinfo is None or decision_at.utcoffset() is None:
            raise DatasetIntegrityError("decision timestamp must include a timezone")
        cutoff = decision_at.astimezone(UTC)
        if self.instrument.available_at > cutoff:
            raise DatasetIntegrityError(
                "instrument was not available at the decision timestamp",
                code=BacktestDataCode.PIT_AVAILABILITY_UNKNOWN,
            )
        decision_bars = tuple(item for item in self.bars if item.available_at == cutoff)
        if len(decision_bars) != 1:
            raise DatasetIntegrityError(
                "decision timestamp must equal one frozen session cutoff",
                code=BacktestDataCode.PIT_CUTOFF_MISMATCH,
            )
        bars = tuple(item for item in self.bars if item.available_at <= cutoff)
        eligible_actions = tuple(
            item for item in self.corporate_actions if item.available_at <= cutoff
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
        fx = tuple(item for item in self.fx_observations if item.available_at <= cutoff)
        digest_payload = {
            "decision_at": cutoff.isoformat(),
            "instrument": self.instrument.model_dump(mode="python"),
            "daily_bars": [item.model_dump(mode="python") for item in bars],
            "corporate_actions": [item.model_dump(mode="python") for item in actions],
            "fx_observations": [item.model_dump(mode="python") for item in fx],
        }
        eligible_digest = hashlib.sha256(_canonical_json_bytes(digest_payload)).hexdigest()
        return EligibleMarketView(
            decision_at=cutoff,
            decision_session_date=decision_bars[0].session_date,
            full_content_sha256=self.manifest.content_sha256,
            eligible_view_sha256=eligible_digest,
            instrument=self.instrument,
            bars=bars,
            corporate_actions=actions,
            fx_observations=fx,
        )


@dataclass(frozen=True, slots=True)
class EligibleMarketView:
    """Only the records whose provenance was available by one decision time."""

    decision_at: datetime
    decision_session_date: date
    full_content_sha256: str
    eligible_view_sha256: str
    instrument: InstrumentSpec
    bars: tuple[DailyBar, ...]
    corporate_actions: tuple[CorporateAction, ...]
    fx_observations: tuple[FxObservation, ...]


def _canonical_json_bytes(value: Any) -> bytes:
    normalized = _canonical_value(value)
    try:
        rendered = json.dumps(
            normalized,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise DatasetIntegrityError("dataset contains a non-canonical value") from exc
    return rendered.encode("utf-8")


def _record_bytes(kind: str, model: Any) -> bytes:
    return _canonical_json_bytes(
        {"kind": kind, "payload": model.model_dump(mode="python")}
    ) + b"\n"


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise DatasetIntegrityError("dataset contains a non-finite decimal")
        return "0" if value == 0 else format(value.normalize(), "f")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise DatasetIntegrityError("dataset contains a timezone-naive timestamp")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, dict):
        return {key: _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


def _timestamp_sort_key(value: datetime) -> str:
    return value.isoformat()


class FrozenDatasetRepository:
    """Publish once and replay only verified local bytes.

    This class deliberately has no vendor callback, clock, HTTP client, or cache
    fallback. Missing or corrupt frozen evidence stops the replay.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        requested_root = Path(root)
        if requested_root.exists() and (
            requested_root.is_symlink() or self._is_reparse(requested_root.lstat())
        ):
            raise DatasetIntegrityError("dataset root cannot be a link or reparse point")
        self.root = requested_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir() or self.root.is_symlink():
            raise DatasetIntegrityError("dataset root must be a real directory")

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
        limitations: Sequence[DatasetLimitation] = (),
    ) -> MarketDatasetManifest:
        """Validate and atomically publish a new immutable snapshot."""

        frozen_bars = tuple(bars)
        frozen_actions = tuple(corporate_actions)
        frozen_fx = tuple(fx_observations)
        self._validate_records(
            instrument,
            frozen_bars,
            frozen_actions,
            frozen_fx,
            validate_calendar=True,
        )
        latest_record_availability = max(
            [instrument.available_at]
            + [bar.available_at for bar in frozen_bars]
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
        content = b"".join(
            [
                _record_bytes("instrument", instrument),
                *(_record_bytes("daily_bar", bar) for bar in frozen_bars),
                *(_record_bytes("corporate_action", action) for action in ordered_actions),
                *(_record_bytes("fx_observation", item) for item in ordered_fx),
            ]
        )
        if len(content) > MAX_DATASET_BYTES:
            raise DatasetIntegrityError("dataset exceeds the byte limit")
        row_count = 1 + len(frozen_bars) + len(ordered_actions) + len(ordered_fx)
        if row_count > MAX_DATASET_ROWS:
            raise DatasetIntegrityError("dataset exceeds the row limit")

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
                content_sha256=content_sha256,
                schedule_digest=frozen_schedule_digest,
                source_vintage=source_vintage,
                calendar_engine_version=calendar_engine_version,
                timezone_database_version=timezone_database_version,
                settlement_policy_version=settlement_policy_version,
            ),
            limitations=tuple(limitations),
        )
        manifest_bytes = _canonical_json_bytes(manifest.model_dump(mode="python")) + b"\n"
        manifest_seal = hashlib.sha256(manifest_bytes).hexdigest().encode("ascii") + b"\n"
        self._publish(dataset_id, content, manifest_bytes, manifest_seal)
        return manifest

    def load(
        self,
        dataset_id: UUID,
        *,
        expected_tenant_id: UUID,
        expected_manifest_digest: str,
    ) -> FrozenMarketDataset:
        """Load a snapshot only after all byte and semantic checks pass."""

        if len(expected_manifest_digest) != 64 or any(
            char not in "0123456789abcdef" for char in expected_manifest_digest
        ):
            raise DatasetIntegrityError("expected manifest digest is not a lowercase SHA-256")
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
        return FrozenMarketDataset(manifest, instrument, bars, actions, fx)

    def load_eligible(
        self,
        dataset_id: UUID,
        *,
        expected_tenant_id: UUID,
        expected_manifest_digest: str,
        decision_at: datetime,
    ) -> EligibleMarketView:
        return self.load(
            dataset_id,
            expected_tenant_id=expected_tenant_id,
            expected_manifest_digest=expected_manifest_digest,
        ).eligible_at(decision_at)

    def _dataset_dir(self, dataset_id: UUID) -> Path:
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
        path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

    def _read_dataset_files(self, dataset_id: UUID) -> tuple[bytes, bytes, bytes]:
        """Read all components beneath one pinned directory handle on POSIX."""

        if os.name == "posix":
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
            try:
                root_fd = os.open(self.root, directory_flags)
                try:
                    dataset_fd = os.open(str(dataset_id), directory_flags, dir_fd=root_fd)
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
        if not content.endswith(b"\n") or b"\r" in content:
            raise DatasetIntegrityError("dataset is not canonical JSONL")
        lines = content.splitlines()
        if len(lines) != expected_count or len(lines) > MAX_DATASET_ROWS:
            raise DatasetIntegrityError("dataset row count does not match the manifest")
        models: list[tuple[str, Any]] = []
        classes = {
            "instrument": InstrumentSpec,
            "daily_bar": DailyBar,
            "corporate_action": CorporateAction,
            "fx_observation": FxObservation,
        }
        for line in lines:
            try:
                raw = _strict_json_loads(line)
                if not isinstance(raw, dict) or set(raw) != {"kind", "payload"}:
                    raise ValueError
                kind = raw["kind"]
                model_class = classes[kind]
                model = model_class.model_validate(raw["payload"])
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise DatasetIntegrityError("dataset contains an invalid typed record") from exc
            if line + b"\n" != _record_bytes(kind, model):
                raise DatasetIntegrityError("dataset record bytes are not canonical")
            models.append((kind, model))
        if not models or models[0][0] != "instrument" or sum(k == "instrument" for k, _ in models) != 1:
            raise DatasetIntegrityError("dataset must contain exactly one leading instrument")
        order = {"instrument": 0, "daily_bar": 1, "corporate_action": 2, "fx_observation": 3}
        if [order[kind] for kind, _ in models] != sorted(order[kind] for kind, _ in models):
            raise DatasetIntegrityError("dataset record groups are not canonical")
        instrument = models[0][1]
        bars = tuple(model for kind, model in models if kind == "daily_bar")
        actions = tuple(model for kind, model in models if kind == "corporate_action")
        fx = tuple(model for kind, model in models if kind == "fx_observation")
        return instrument, bars, actions, fx

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
        if any(action.instrument_id != instrument.instrument_id for action in actions):
            raise DatasetIntegrityError("corporate action belongs to a different instrument")
        if any(item.available_at < item.observed_at for item in fx):
            raise DatasetIntegrityError("FX observation is available before it was observed")


def _strict_json_loads(content: bytes) -> Any:
    if len(content) > MAX_JSON_RECORD_BYTES:
        raise DatasetIntegrityError("JSON record exceeds its byte limit")
    depth = 0
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
            depth += 1
            if depth > MAX_JSON_NESTING:
                raise DatasetIntegrityError("JSON record exceeds its nesting limit")
        elif byte in (0x7D, 0x5D):
            depth -= 1
            if depth < 0:
                raise DatasetIntegrityError("JSON record has mismatched delimiters")
    if in_string or depth != 0:
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

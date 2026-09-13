"""Append-only compare-and-swap custody for simulation checkpoint heads.

The ledger checkpoint bytes remain owned by the authenticated simulation
authority.  This module stores only their trusted identity and the current
head for one tenant/backtest/ledger/spec scope.  It deliberately exposes a
small storage protocol so the required production PostgreSQL implementation
can provide the same transactional contract later.

The local implementation is restricted to tests and development. It assumes
its process and storage root are trusted, does not load executable objects,
pickle data, or caller-selected paths, and does not claim power-loss durability
on Windows. Production custody must use a transactional database adapter.
"""

from __future__ import annotations

import hmac
import json
import os
import stat
import tempfile
import threading
import time
import weakref
from contextlib import suppress
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tradingagents.platform.backtest.contracts import canonical_digest, canonical_json_bytes

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
MAX_GENERATION = (1 << 63) - 1
MAX_RECORD_BYTES = 32 * 1024
DEFAULT_LOCK_TIMEOUT_SECONDS = 5.0


def _custody_payload(value: BaseModel | dict[str, Any]) -> dict[str, Any]:
    """Keep persisted trade-v1 bytes/digests compatible with the pre-stream schema."""

    payload = value.model_dump(mode="python") if isinstance(value, BaseModel) else value

    def normalize(item):
        if isinstance(item, dict):
            normalized = {key: normalize(child) for key, child in item.items()}
            if (
                normalized.get("scope_version") == "simulation-checkpoint-scope/1"
                and normalized.get("stream_kind") == "trade-v1"
            ):
                normalized.pop("stream_kind")
            return normalized
        if isinstance(item, (list, tuple)):
            return tuple(normalize(child) for child in item)
        return item

    return normalize(payload)


class SimulationCheckpointHeadError(ValueError):
    """Base error for checkpoint-head custody failures."""


class SimulationCheckpointHeadConflictError(SimulationCheckpointHeadError):
    """The expected head is stale or the proposed append would fork history."""


class SimulationCheckpointHeadIntegrityError(SimulationCheckpointHeadError):
    """Trusted local checkpoint-head storage is corrupt or inconsistent."""


class SimulationCheckpointHeadCapacityError(SimulationCheckpointHeadError):
    """A bounded checkpoint-head resource limit was exceeded."""


class _Contract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        revalidate_instances="always",
    )


class SimulationCheckpointScope(_Contract):
    """Complete namespace for exactly one linear simulation ledger history."""

    scope_version: Literal["simulation-checkpoint-scope/1"] = "simulation-checkpoint-scope/1"
    stream_kind: Literal["trade-v1", "economic-v2"] = "trade-v1"
    tenant_id: UUID
    backtest_id: UUID
    ledger_id: UUID
    backtest_spec_digest: Digest

    @property
    def digest(self) -> str:
        return canonical_digest(_custody_payload(self))


class SimulationCheckpointExpectation(_Contract):
    """The exact head a caller observed before requesting an append.

    Generation zero and absent checkpoint identity describe an empty history.
    Every non-empty expectation binds all three CAS coordinates.
    """

    expectation_version: Literal["simulation-checkpoint-expectation/1"] = (
        "simulation-checkpoint-expectation/1"
    )
    checkpoint_id: UUID | None = None
    checkpoint_digest: Digest | None = None
    generation: int = Field(strict=True, ge=0, le=MAX_GENERATION)

    @model_validator(mode="after")
    def validate_identity(self):
        identity_is_absent = self.checkpoint_id is None and self.checkpoint_digest is None
        identity_is_complete = self.checkpoint_id is not None and self.checkpoint_digest is not None
        if not (identity_is_absent or identity_is_complete):
            raise ValueError("checkpoint expectation identity must be complete or absent")
        if identity_is_absent and self.generation != 0:
            raise ValueError("an empty checkpoint expectation must use generation zero")
        if identity_is_complete and self.generation == 0:
            raise ValueError("a populated checkpoint expectation must use a positive generation")
        return self

    @classmethod
    def empty(cls) -> SimulationCheckpointExpectation:
        return cls(generation=0)


class AuthenticatedSimulationCheckpointReference(_Contract):
    """Authority-issued identity of one validated ledger checkpoint.

    ``authority_record_digest`` is the opaque digest of the authenticated
    authority record verified before this trusted storage API is called. The
    CAS store preserves and binds that proof; it does not mint or authenticate
    checkpoint references itself.
    """

    reference_version: Literal["authenticated-simulation-checkpoint-reference/1"] = (
        "authenticated-simulation-checkpoint-reference/1"
    )
    scope: SimulationCheckpointScope
    checkpoint_id: UUID
    checkpoint_digest: Digest
    previous_checkpoint_digest: Digest | None = None
    authority_record_digest: Digest

    @model_validator(mode="after")
    def validate_scope_type(self):
        if type(self.scope) is not SimulationCheckpointScope:
            raise ValueError("authenticated reference requires the exact scope contract")
        return self

    @property
    def digest(self) -> str:
        return canonical_digest(_custody_payload(self))


class SimulationCheckpointCandidate(_Contract):
    """Exact authenticated reference proposed as the next linear head."""

    candidate_version: Literal["simulation-checkpoint-candidate/2"] = (
        "simulation-checkpoint-candidate/2"
    )
    reference: AuthenticatedSimulationCheckpointReference

    @model_validator(mode="after")
    def validate_reference_type(self):
        if type(self.reference) is not AuthenticatedSimulationCheckpointReference:
            raise ValueError("candidate requires the exact authenticated reference contract")
        return self

    @property
    def checkpoint_id(self) -> UUID:
        return self.reference.checkpoint_id

    @property
    def checkpoint_digest(self) -> str:
        return self.reference.checkpoint_digest


class SimulationCheckpointHead(_Contract):
    """One immutable append record; the newest record is also the current head."""

    head_version: Literal["simulation-checkpoint-head/1"] = "simulation-checkpoint-head/1"
    scope: SimulationCheckpointScope
    checkpoint_id: UUID
    checkpoint_digest: Digest
    authenticated_reference_digest: Digest
    generation: int = Field(strict=True, ge=1, le=MAX_GENERATION)
    previous_checkpoint_id: UUID | None = None
    previous_checkpoint_digest: Digest | None = None

    @model_validator(mode="after")
    def validate_lineage(self):
        if type(self.scope) is not SimulationCheckpointScope:
            raise ValueError("checkpoint head requires the exact scope contract")
        previous_is_absent = (
            self.previous_checkpoint_id is None and self.previous_checkpoint_digest is None
        )
        previous_is_complete = (
            self.previous_checkpoint_id is not None and self.previous_checkpoint_digest is not None
        )
        if not (previous_is_absent or previous_is_complete):
            raise ValueError("checkpoint predecessor identity must be complete or absent")
        if self.generation == 1 and not previous_is_absent:
            raise ValueError("the first checkpoint head cannot have a predecessor")
        if self.generation > 1 and not previous_is_complete:
            raise ValueError("a later checkpoint head must bind its predecessor")
        return self

    @property
    def record_digest(self) -> str:
        return canonical_digest(_custody_payload(self))

    @property
    def expectation(self) -> SimulationCheckpointExpectation:
        return SimulationCheckpointExpectation(
            checkpoint_id=self.checkpoint_id,
            checkpoint_digest=self.checkpoint_digest,
            generation=self.generation,
        )


class SimulationCheckpointHeadStore(Protocol):
    """Trusted storage API implemented locally now and transactionally later."""

    def read_head(self, scope: SimulationCheckpointScope) -> SimulationCheckpointHead | None:
        """Return the validated current head for ``scope`` or ``None``."""

    def compare_and_append(
        self,
        scope: SimulationCheckpointScope,
        expected: SimulationCheckpointExpectation,
        candidate: SimulationCheckpointCandidate,
    ) -> SimulationCheckpointHead:
        """Append exactly one checkpoint if all expected CAS coordinates match."""


_THREAD_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: weakref.WeakValueDictionary[str, threading.Lock] = weakref.WeakValueDictionary()


def _thread_lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.Lock())


class _InterprocessLock:
    def __init__(self, path: Path, timeout_seconds: float) -> None:
        self._path = path
        self._timeout_seconds = timeout_seconds
        self._fd: int | None = None

    def __enter__(self):
        thread_lock = _thread_lock_for(self._path)
        if not thread_lock.acquire(timeout=self._timeout_seconds):
            raise SimulationCheckpointHeadConflictError("checkpoint head lock timed out")
        self._thread_lock = thread_lock
        try:
            try:
                self._fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600)
            except OSError as exc:
                raise SimulationCheckpointHeadIntegrityError(
                    "checkpoint lock file cannot be opened safely"
                ) from exc
            if os.fstat(self._fd).st_size == 0:
                os.write(self._fd, b"\0")
                os.fsync(self._fd)
            deadline = time.monotonic() + self._timeout_seconds
            while True:
                try:
                    self._lock_fd()
                    return self
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise SimulationCheckpointHeadConflictError(
                            "checkpoint head lock timed out"
                        ) from exc
                    time.sleep(0.01)
        except BaseException:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            thread_lock.release()
            raise

    def _lock_fd(self) -> None:
        assert self._fd is not None
        if os.name == "nt":
            import msvcrt

            os.lseek(self._fd, 0, os.SEEK_SET)
            msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def __exit__(self, exc_type, exc, traceback) -> None:
        assert self._fd is not None
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None
            self._thread_lock.release()


class LocalSimulationCheckpointHeadStore:
    """Filesystem-safe local CAS store restricted to tests and development.

    Each scope has an advisory cross-process lock. Immutable append records are
    installed with a no-replace hard link; ``head.json`` is then published with
    an atomic replacement. A crash between those operations leaves an orphaned
    valid record that the exact retry completes without forking history.

    On POSIX, directory entries are fsynced after publication. Windows provides
    process-crash atomicity here, not a power-loss durability guarantee. Real
    deployments must implement :class:`SimulationCheckpointHeadStore` with a
    transactional database CAS and durable append table.
    """

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    ) -> None:
        if isinstance(lock_timeout_seconds, bool) or not isinstance(
            lock_timeout_seconds, (int, float)
        ):
            raise ValueError("lock timeout must be a finite positive number")
        timeout = float(lock_timeout_seconds)
        if (
            timeout <= 0
            or timeout > 60
            or timeout != timeout
            or timeout in {float("inf"), -float("inf")}
        ):
            raise ValueError("lock timeout must be finite, positive and at most 60 seconds")
        self._root = Path(root).resolve()
        try:
            self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise SimulationCheckpointHeadIntegrityError(
                "checkpoint head root is not a directory"
            ) from exc
        if not self._root.is_dir():
            raise SimulationCheckpointHeadIntegrityError("checkpoint head root is not a directory")
        self._lock_timeout_seconds = timeout

    @staticmethod
    def _validate_scope(value: object) -> SimulationCheckpointScope:
        if type(value) is not SimulationCheckpointScope:
            raise SimulationCheckpointHeadError("scope must use the exact typed contract")
        try:
            return SimulationCheckpointScope.model_validate(value.model_dump(mode="python"))
        except Exception as exc:
            raise SimulationCheckpointHeadError("checkpoint scope failed deep validation") from exc

    @staticmethod
    def _validate_expectation(value: object) -> SimulationCheckpointExpectation:
        if type(value) is not SimulationCheckpointExpectation:
            raise SimulationCheckpointHeadError("expectation must use the exact typed contract")
        try:
            return SimulationCheckpointExpectation.model_validate(value.model_dump(mode="python"))
        except Exception as exc:
            raise SimulationCheckpointHeadError(
                "checkpoint expectation failed deep validation"
            ) from exc

    @staticmethod
    def _validate_candidate(value: object) -> SimulationCheckpointCandidate:
        if type(value) is not SimulationCheckpointCandidate:
            raise SimulationCheckpointHeadError("candidate must use the exact typed contract")
        try:
            return SimulationCheckpointCandidate.model_validate(value.model_dump(mode="python"))
        except Exception as exc:
            raise SimulationCheckpointHeadError(
                "checkpoint candidate failed deep validation"
            ) from exc

    def _scope_directory(self, scope: SimulationCheckpointScope) -> Path:
        return self._root / scope.digest

    @staticmethod
    def _record_path(directory: Path, generation: int) -> Path:
        return directory / "records" / f"{generation:020d}.json"

    @staticmethod
    def _identity_claim_path(directory: Path, checkpoint_id: UUID) -> Path:
        return directory / "checkpoint-identities" / f"{checkpoint_id}.json"

    @staticmethod
    def _canonical_bytes(value: BaseModel) -> bytes:
        content = canonical_json_bytes(_custody_payload(value)) + b"\n"
        if len(content) > MAX_RECORD_BYTES:
            raise SimulationCheckpointHeadCapacityError("checkpoint head record is too large")
        return content

    @staticmethod
    def _read_bounded(path: Path) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise SimulationCheckpointHeadIntegrityError(
                "checkpoint head file cannot be opened safely"
            ) from exc
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise SimulationCheckpointHeadIntegrityError(
                    "checkpoint head storage contains a non-regular file"
                )
            if metadata.st_size > MAX_RECORD_BYTES:
                raise SimulationCheckpointHeadCapacityError(
                    "checkpoint head file exceeds its byte limit"
                )
            content = os.read(fd, MAX_RECORD_BYTES + 1)
            if len(content) > MAX_RECORD_BYTES or os.read(fd, 1):
                raise SimulationCheckpointHeadCapacityError(
                    "checkpoint head file exceeds its byte limit"
                )
            return content
        finally:
            os.close(fd)

    @classmethod
    def _load_model(cls, path: Path, model_type):
        content = cls._read_bounded(path)
        try:
            raw = json.loads(content)
            value = model_type.model_validate(raw)
        except Exception as exc:
            raise SimulationCheckpointHeadIntegrityError(
                "checkpoint head file is not valid bounded JSON"
            ) from exc
        canonical = cls._canonical_bytes(value)
        if not hmac.compare_digest(content, canonical):
            raise SimulationCheckpointHeadIntegrityError(
                "checkpoint head file does not use canonical bytes"
            )
        return value

    @staticmethod
    def _write_temporary(directory: Path, content: bytes) -> Path:
        fd, name = tempfile.mkstemp(prefix=".pending-", suffix=".json", dir=directory)
        path = Path(name)
        try:
            os.chmod(path, 0o600)
            with os.fdopen(fd, "wb", closefd=True) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            return path
        except BaseException:
            with suppress(OSError):
                os.close(fd)
            with suppress(OSError):
                path.unlink()
            raise

    @classmethod
    def _publish_no_replace(cls, path: Path, content: bytes) -> bool:
        temporary = cls._write_temporary(path.parent, content)
        try:
            try:
                os.link(temporary, path)
            except FileExistsError:
                return False
            cls._fsync_directory(path.parent)
            return True
        finally:
            with suppress(OSError):
                temporary.unlink()

    @classmethod
    def _atomic_replace(cls, path: Path, content: bytes) -> None:
        temporary = cls._write_temporary(path.parent, content)
        try:
            os.replace(temporary, path)
            cls._fsync_directory(path.parent)
        except BaseException:
            with suppress(OSError):
                temporary.unlink()
            raise

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        """Persist POSIX directory entries; Windows has no equivalent contract here."""

        if os.name == "nt":
            return
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            fd = os.open(directory, flags)
        except OSError as exc:
            raise SimulationCheckpointHeadIntegrityError(
                "checkpoint directory cannot be opened for durability"
            ) from exc
        try:
            os.fsync(fd)
        except OSError as exc:
            raise SimulationCheckpointHeadIntegrityError(
                "checkpoint directory metadata could not be persisted"
            ) from exc
        finally:
            os.close(fd)

    def _prepare_scope_directory(self, scope: SimulationCheckpointScope) -> Path:
        directory = self._scope_directory(scope)
        records = directory / "records"
        identities = directory / "checkpoint-identities"
        try:
            records.mkdir(mode=0o700, parents=True, exist_ok=True)
            identities.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise SimulationCheckpointHeadIntegrityError(
                "checkpoint scope directory cannot be prepared safely"
            ) from exc
        self._fsync_directory(directory.parent)
        self._fsync_directory(records.parent)
        return directory

    def _bind_scope(self, directory: Path, scope: SimulationCheckpointScope) -> None:
        scope_path = directory / "scope.json"
        content = self._canonical_bytes(scope)
        if not self._publish_no_replace(scope_path, content):
            stored = self._load_model(scope_path, SimulationCheckpointScope)
            if stored != scope:
                raise SimulationCheckpointHeadIntegrityError(
                    "checkpoint scope digest resolved to different stored bindings"
                )

    def _load_head(self, directory: Path, scope: SimulationCheckpointScope):
        try:
            head = self._load_model(directory / "head.json", SimulationCheckpointHead)
        except FileNotFoundError:
            return None
        if head.scope != scope:
            raise SimulationCheckpointHeadIntegrityError("checkpoint head scope mismatch")
        try:
            record = self._load_model(
                self._record_path(directory, head.generation), SimulationCheckpointHead
            )
        except FileNotFoundError as exc:
            raise SimulationCheckpointHeadIntegrityError(
                "checkpoint head is missing its immutable append record"
            ) from exc
        if record != head:
            raise SimulationCheckpointHeadIntegrityError(
                "checkpoint head does not match its immutable append record"
            )
        try:
            identity_claim = self._load_model(
                self._identity_claim_path(directory, head.checkpoint_id),
                SimulationCheckpointHead,
            )
        except FileNotFoundError as exc:
            raise SimulationCheckpointHeadIntegrityError(
                "checkpoint head is missing its immutable identity claim"
            ) from exc
        if identity_claim != head:
            raise SimulationCheckpointHeadIntegrityError(
                "checkpoint head does not match its immutable identity claim"
            )
        return head

    @staticmethod
    def _matches_expectation(
        head: SimulationCheckpointHead | None,
        expected: SimulationCheckpointExpectation,
    ) -> bool:
        if head is None:
            return expected.generation == 0
        return (
            head.generation == expected.generation
            and head.checkpoint_id == expected.checkpoint_id
            and expected.checkpoint_digest is not None
            and hmac.compare_digest(head.checkpoint_digest, expected.checkpoint_digest)
        )

    @staticmethod
    def _record_matches_request(
        record: SimulationCheckpointHead,
        scope: SimulationCheckpointScope,
        expected: SimulationCheckpointExpectation,
        candidate: SimulationCheckpointCandidate,
    ) -> bool:
        return (
            record.scope == scope
            and record.generation == expected.generation + 1
            and record.checkpoint_id == candidate.checkpoint_id
            and hmac.compare_digest(record.checkpoint_digest, candidate.checkpoint_digest)
            and hmac.compare_digest(
                record.authenticated_reference_digest, candidate.reference.digest
            )
            and record.previous_checkpoint_id == expected.checkpoint_id
            and (
                record.previous_checkpoint_digest is None
                if expected.checkpoint_digest is None
                else record.previous_checkpoint_digest is not None
                and hmac.compare_digest(
                    record.previous_checkpoint_digest, expected.checkpoint_digest
                )
            )
        )

    def read_head(self, scope: SimulationCheckpointScope) -> SimulationCheckpointHead | None:
        checked_scope = self._validate_scope(scope)
        directory = self._scope_directory(checked_scope)
        if not directory.exists():
            return None
        with _InterprocessLock(directory / ".cas.lock", self._lock_timeout_seconds):
            try:
                stored_scope = self._load_model(directory / "scope.json", SimulationCheckpointScope)
            except FileNotFoundError:
                records = directory / "records"
                identities = directory / "checkpoint-identities"
                has_record = records.is_dir() and next(records.glob("*.json"), None) is not None
                has_identity = (
                    identities.is_dir() and next(identities.glob("*.json"), None) is not None
                )
                if (directory / "head.json").exists() or has_record or has_identity:
                    raise SimulationCheckpointHeadIntegrityError(
                        "checkpoint scope binding is missing"
                    ) from None
                return None
            if stored_scope != checked_scope:
                raise SimulationCheckpointHeadIntegrityError("checkpoint scope binding mismatch")
            return self._load_head(directory, checked_scope)

    def compare_and_append(
        self,
        scope: SimulationCheckpointScope,
        expected: SimulationCheckpointExpectation,
        candidate: SimulationCheckpointCandidate,
    ) -> SimulationCheckpointHead:
        checked_scope = self._validate_scope(scope)
        checked_expected = self._validate_expectation(expected)
        checked_candidate = self._validate_candidate(candidate)
        if checked_candidate.reference.scope != checked_scope:
            raise SimulationCheckpointHeadConflictError(
                "checkpoint candidate belongs to another custody scope"
            )
        candidate_previous = checked_candidate.reference.previous_checkpoint_digest
        if checked_expected.generation == 0:
            if candidate_previous is not None:
                raise SimulationCheckpointHeadConflictError(
                    "a genesis append cannot claim a checkpoint predecessor"
                )
        elif (
            candidate_previous is None
            or checked_expected.checkpoint_digest is None
            or not hmac.compare_digest(candidate_previous, checked_expected.checkpoint_digest)
        ):
            raise SimulationCheckpointHeadConflictError(
                "checkpoint candidate does not extend the expected predecessor"
            )
        if checked_expected.generation >= MAX_GENERATION:
            raise SimulationCheckpointHeadCapacityError("checkpoint head generation is exhausted")

        directory = self._prepare_scope_directory(checked_scope)
        with _InterprocessLock(directory / ".cas.lock", self._lock_timeout_seconds):
            self._bind_scope(directory, checked_scope)
            current = self._load_head(directory, checked_scope)
            record_path = self._record_path(directory, checked_expected.generation + 1)
            identity_path = self._identity_claim_path(directory, checked_candidate.checkpoint_id)
            try:
                existing = self._load_model(record_path, SimulationCheckpointHead)
            except FileNotFoundError:
                existing = None

            if existing is not None:
                if not self._record_matches_request(
                    existing, checked_scope, checked_expected, checked_candidate
                ):
                    raise SimulationCheckpointHeadConflictError(
                        "checkpoint lineage slot is already bound to a sibling append"
                    )
                try:
                    identity_claim = self._load_model(identity_path, SimulationCheckpointHead)
                except FileNotFoundError:
                    identity_claim = None
                if identity_claim is None:
                    if not self._publish_no_replace(identity_path, self._canonical_bytes(existing)):
                        raise SimulationCheckpointHeadConflictError(
                            "checkpoint identity was claimed concurrently"
                        )
                elif identity_claim != existing:
                    raise SimulationCheckpointHeadConflictError(
                        "checkpoint identifier is already bound to another append"
                    )
                if current == existing or (
                    current is not None and current.generation > existing.generation
                ):
                    return existing
                if not self._matches_expectation(current, checked_expected):
                    raise SimulationCheckpointHeadIntegrityError(
                        "orphaned checkpoint record cannot extend the current head"
                    )
                self._atomic_replace(directory / "head.json", self._canonical_bytes(existing))
                return existing

            if not self._matches_expectation(current, checked_expected):
                raise SimulationCheckpointHeadConflictError(
                    "checkpoint head changed; stale append or fork rejected"
                )

            appended = SimulationCheckpointHead(
                scope=checked_scope,
                checkpoint_id=checked_candidate.checkpoint_id,
                checkpoint_digest=checked_candidate.checkpoint_digest,
                authenticated_reference_digest=checked_candidate.reference.digest,
                generation=checked_expected.generation + 1,
                previous_checkpoint_id=checked_expected.checkpoint_id,
                previous_checkpoint_digest=checked_expected.checkpoint_digest,
            )
            content = self._canonical_bytes(appended)
            try:
                identity_claim = self._load_model(identity_path, SimulationCheckpointHead)
            except FileNotFoundError:
                identity_claim = None
            if identity_claim is not None and identity_claim != appended:
                raise SimulationCheckpointHeadConflictError(
                    "checkpoint identifier is already bound to another append"
                )
            if not self._publish_no_replace(record_path, content):
                raise SimulationCheckpointHeadConflictError(
                    "checkpoint lineage slot was claimed concurrently"
                )
            if identity_claim is None and not self._publish_no_replace(identity_path, content):
                raise SimulationCheckpointHeadConflictError(
                    "checkpoint identity was claimed concurrently"
                )
            self._atomic_replace(directory / "head.json", content)
            return appended


__all__ = [
    "AuthenticatedSimulationCheckpointReference",
    "LocalSimulationCheckpointHeadStore",
    "MAX_GENERATION",
    "MAX_RECORD_BYTES",
    "SimulationCheckpointCandidate",
    "SimulationCheckpointExpectation",
    "SimulationCheckpointHead",
    "SimulationCheckpointHeadCapacityError",
    "SimulationCheckpointHeadConflictError",
    "SimulationCheckpointHeadError",
    "SimulationCheckpointHeadIntegrityError",
    "SimulationCheckpointHeadStore",
    "SimulationCheckpointScope",
]

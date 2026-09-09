"""Authenticated order authority and price-bound M5.2b simulation entrypoint.

The pure ledger deliberately accepts typed economic inputs.  This module is the
application boundary that prevents a caller from manufacturing those inputs:
orders are loaded from an immutable HMAC-authenticated record, and every raw
open is resolved through the frozen M5.2a market-data authority.

This is still a backtest component.  It has no broker, PAPER, LIVE, network or
LLM path and must never be used as a capital-execution shortcut.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import hmac
import json
import os
import secrets
import shutil
import stat
import tempfile
from contextlib import suppress
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tradingagents.platform.backtest.contracts import (
    BacktestSpec,
    DatasetRole,
    canonical_digest,
    canonical_json_bytes,
)
from tradingagents.platform.backtest.repository import (
    FrozenDatasetRepository,
    resolve_execution_price_authority,
)
from tradingagents.platform.backtest.simulation_ledger import (
    SimulationBatch,
    SimulationBatchResult,
    SimulationLedgerPolicy,
    SimulationLedgerState,
    SimulationOrder,
    SimulationSide,
    execute_simulation_batch,
)

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
REQUEST_FILENAME = "request.json"
SIGNATURE_FILENAME = "request.hmac"
MAX_AUTHORITY_BYTES = 16 * 1024 * 1024
_SIGNATURE_DOMAIN = b"tradingagents/simulation-order-authority/1\x00"


class SimulationAuthorityError(ValueError):
    """An authenticated simulation request was missing, invalid or tampered."""


class SimulationRequestAlreadyExistsError(SimulationAuthorityError):
    """A request identifier was already claimed by the append-only authority."""


class SimulationAuthorityContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        revalidate_instances="always",
    )


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


def _positive_decimal(value: Decimal) -> Decimal:
    if not value.is_finite() or value <= 0:
        raise ValueError("requested quantity must be finite and positive")
    _, digits, exponent = value.as_tuple()
    if len(digits) > 38 or value.adjusted() >= 38 or exponent < -18:
        raise ValueError("requested quantity exceeds the decimal resource limit")
    return value


class AuthorizedSimulationOrder(SimulationAuthorityContract):
    """Price-free order instruction frozen before the next execution session."""

    order_version: Literal["authorized-simulation-order/1"] = (
        "authorized-simulation-order/1"
    )
    order_id: UUID
    instrument_id: UUID
    side: SimulationSide
    requested_quantity: Decimal
    dataset_id: UUID
    manifest_digest: Digest
    decision_at: datetime
    decision_session_date: date
    eligible_view_digest: Digest

    _decision_at_is_utc = field_validator("decision_at")(_utc)
    _quantity_input_is_exact = field_validator("requested_quantity", mode="before")(
        _exact_decimal
    )
    _quantity_is_positive = field_validator("requested_quantity")(_positive_decimal)


class AuthorizedSimulationRequest(SimulationAuthorityContract):
    """Complete immutable authority for one simulation execution session."""

    request_version: Literal["authorized-simulation-request/1"] = (
        "authorized-simulation-request/1"
    )
    tenant_id: UUID
    backtest_id: UUID
    backtest_spec_digest: Digest
    ledger_id: UUID
    policy_digest: Digest
    batch_id: UUID
    session_id: UUID
    created_at: datetime
    orders: tuple[AuthorizedSimulationOrder, ...] = Field(min_length=1, max_length=9_999)

    _created_at_is_utc = field_validator("created_at")(_utc)

    @model_validator(mode="after")
    def validate_request(self):
        if any(type(order) is not AuthorizedSimulationOrder for order in self.orders):
            raise ValueError("orders must use the exact authorized contract type")
        order_ids = tuple(order.order_id for order in self.orders)
        if len(set(order_ids)) != len(order_ids):
            raise ValueError("an authorized request cannot repeat an order ID")
        if order_ids != tuple(sorted(order_ids, key=str)):
            raise ValueError("authorized orders must be sorted by order ID")
        return self

    @property
    def authority_digest(self) -> str:
        return canonical_digest(self)


class SimulationRequestReference(SimulationAuthorityContract):
    reference_version: Literal["simulation-request-reference/1"] = (
        "simulation-request-reference/1"
    )
    tenant_id: UUID
    backtest_id: UUID
    backtest_spec_digest: Digest
    ledger_id: UUID
    batch_id: UUID
    authority_digest: Digest


class SimulationLedgerCheckpoint(SimulationAuthorityContract):
    """Immutable ledger state and policy supplied by trusted storage."""

    checkpoint_version: Literal["simulation-ledger-checkpoint/1"] = (
        "simulation-ledger-checkpoint/1"
    )
    checkpoint_id: UUID
    tenant_id: UUID
    backtest_id: UUID
    backtest_spec_digest: Digest
    ledger_id: UUID
    created_at: datetime
    previous_checkpoint_digest: Digest | None = None
    policy: SimulationLedgerPolicy
    state: SimulationLedgerState

    _created_at_is_utc = field_validator("created_at")(_utc)

    @model_validator(mode="after")
    def validate_checkpoint(self):
        if type(self.policy) is not SimulationLedgerPolicy:
            raise ValueError("checkpoint policy must use the exact typed contract")
        if type(self.state) is not SimulationLedgerState:
            raise ValueError("checkpoint state must use the exact typed contract")
        if self.ledger_id != self.policy.ledger_id or self.ledger_id != self.state.ledger_id:
            raise ValueError("checkpoint ledger binding mismatch")
        if not hmac.compare_digest(self.state.policy_digest, self.policy.digest):
            raise ValueError("checkpoint policy digest mismatch")
        return self

    @property
    def checkpoint_digest(self) -> str:
        return canonical_digest(self)


class SimulationCheckpointReference(SimulationAuthorityContract):
    reference_version: Literal["simulation-checkpoint-reference/1"] = (
        "simulation-checkpoint-reference/1"
    )
    tenant_id: UUID
    backtest_id: UUID
    backtest_spec_digest: Digest
    ledger_id: UUID
    checkpoint_id: UUID
    checkpoint_digest: Digest


class AuthorizedOrderExecutionEvidence(SimulationAuthorityContract):
    evidence_version: Literal["authorized-order-execution-evidence/1"] = (
        "authorized-order-execution-evidence/1"
    )
    order_id: UUID
    authority_digest: Digest
    execution_price_bundle_digest: Digest
    execution_price_projection_digest: Digest
    execution_price_provenance_digest: Digest


class AuthorizedSimulationBatchResult(SimulationAuthorityContract):
    result_version: Literal["authorized-simulation-batch-result/1"] = (
        "authorized-simulation-batch-result/1"
    )
    reference: SimulationRequestReference
    prior_checkpoint: SimulationCheckpointReference
    execution_evidence: tuple[AuthorizedOrderExecutionEvidence, ...] = Field(
        min_length=1,
        max_length=9_999,
    )
    ledger_result: SimulationBatchResult
    next_checkpoint: SimulationLedgerCheckpoint | None
    result_digest: Digest

    @model_validator(mode="after")
    def validate_result(self):
        if type(self.reference) is not SimulationRequestReference:
            raise ValueError("reference must use the exact typed contract")
        if type(self.prior_checkpoint) is not SimulationCheckpointReference:
            raise ValueError("prior checkpoint must use the exact typed contract")
        if type(self.ledger_result) is not SimulationBatchResult:
            raise ValueError("ledger result must use the exact typed contract")
        if self.next_checkpoint is not None and type(self.next_checkpoint) is not SimulationLedgerCheckpoint:
            raise ValueError("next checkpoint must use the exact typed contract")
        if any(
            type(item) is not AuthorizedOrderExecutionEvidence
            for item in self.execution_evidence
        ):
            raise ValueError("execution evidence must use the exact typed contract")
        evidence_ids = tuple(item.order_id for item in self.execution_evidence)
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("execution evidence cannot repeat an order ID")
        fill_ids = tuple(
            sorted(
                (
                    event.order_id
                    for event in self.ledger_result.state.events
                    if event.batch_request_digest == self.ledger_result.request_digest
                    and event.order_id is not None
                ),
                key=str,
            )
        )
        if evidence_ids != fill_ids:
            raise ValueError("execution evidence must match every ledger fill")
        if any(
            item.authority_digest != self.reference.authority_digest
            for item in self.execution_evidence
        ):
            raise ValueError("execution evidence references another order authority")
        if self.ledger_result.replay_status == "replayed":
            if self.next_checkpoint is not None:
                raise ValueError("a replay must not create another ledger checkpoint")
        elif self.next_checkpoint is None:
            raise ValueError("an applied batch must produce a ledger checkpoint")
        elif self.next_checkpoint.state != self.ledger_result.state:
            raise ValueError("next checkpoint does not contain the resulting ledger state")
        elif self.next_checkpoint.previous_checkpoint_digest != self.prior_checkpoint.checkpoint_digest:
            raise ValueError("next checkpoint does not extend the prior checkpoint")
        expected = canonical_digest(
            self.model_dump(mode="python", exclude={"result_digest"})
        )
        if not hmac.compare_digest(self.result_digest, expected):
            raise ValueError("authorized simulation result digest mismatch")
        return self


class FrozenSimulationAuthorityRepository:
    """Append-only HMAC authority for requests and ledger checkpoints.

    The signing key is an application secret and is never persisted beside the
    records.  Filesystem immutability prevents accidental replacement; the HMAC
    makes replacement fail closed even for an attacker who can write the root.
    """

    __slots__ = ("root", "_root_fd", "_signing_key")

    def __init__(self, root: str | os.PathLike[str], *, signing_key: bytes) -> None:
        if type(signing_key) is not bytes or len(signing_key) < 32:
            raise ValueError("simulation authority signing key must contain at least 32 bytes")
        requested_root = Path(root)
        if requested_root.exists() and (
            requested_root.is_symlink() or self._is_reparse(requested_root.lstat())
        ):
            raise SimulationAuthorityError("authority root cannot be a link or reparse point")
        self.root = requested_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._signing_key = bytes(signing_key)
        self._root_fd: int | None = None
        if os.name == "posix":
            before = self.root.lstat()
            if (
                not stat.S_ISDIR(before.st_mode)
                or self._is_reparse(before)
                or before.st_uid != os.geteuid()
            ):
                raise SimulationAuthorityError("authority root must be an owned real directory")
            flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
            try:
                root_fd = os.open(self.root, flags)
            except OSError as exc:
                raise SimulationAuthorityError("authority root could not be pinned safely") from exc
            after = os.fstat(root_fd)
            if (
                not stat.S_ISDIR(after.st_mode)
                or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
                or after.st_uid != os.geteuid()
            ):
                os.close(root_fd)
                raise SimulationAuthorityError("authority root identity changed while pinned")
            os.fchmod(root_fd, stat.S_IRWXU)
            self._root_fd = root_fd
        elif not self.root.is_dir() or self.root.is_symlink():
            raise SimulationAuthorityError("authority root must be a real directory")

    def close(self) -> None:
        root_fd = getattr(self, "_root_fd", None)
        if root_fd is not None:
            os.close(root_fd)
            self._root_fd = None

    def __enter__(self) -> FrozenSimulationAuthorityRepository:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __del__(self) -> None:
        with suppress(OSError):
            self.close()

    @staticmethod
    def _is_reparse(file_stat: os.stat_result) -> bool:
        return bool(getattr(file_stat, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)

    @staticmethod
    def _revalidate_request(value: object) -> AuthorizedSimulationRequest:
        if type(value) is not AuthorizedSimulationRequest:
            raise SimulationAuthorityError("request must use the exact typed contract")
        try:
            return AuthorizedSimulationRequest.model_validate(value.model_dump(mode="python"))
        except ValueError as exc:
            raise SimulationAuthorityError("request failed deep contract revalidation") from exc

    @staticmethod
    def _revalidate_reference(value: object) -> SimulationRequestReference:
        if type(value) is not SimulationRequestReference:
            raise SimulationAuthorityError("reference must use the exact typed contract")
        try:
            return SimulationRequestReference.model_validate(value.model_dump(mode="python"))
        except ValueError as exc:
            raise SimulationAuthorityError("reference failed deep contract revalidation") from exc

    @staticmethod
    def _revalidate_checkpoint(value: object) -> SimulationLedgerCheckpoint:
        if type(value) is not SimulationLedgerCheckpoint:
            raise SimulationAuthorityError("checkpoint must use the exact typed contract")
        try:
            return SimulationLedgerCheckpoint.model_validate(value.model_dump(mode="python"))
        except ValueError as exc:
            raise SimulationAuthorityError("checkpoint failed deep contract revalidation") from exc

    @staticmethod
    def _revalidate_checkpoint_reference(value: object) -> SimulationCheckpointReference:
        if type(value) is not SimulationCheckpointReference:
            raise SimulationAuthorityError("checkpoint reference must use the exact typed contract")
        try:
            return SimulationCheckpointReference.model_validate(value.model_dump(mode="python"))
        except ValueError as exc:
            raise SimulationAuthorityError(
                "checkpoint reference failed deep contract revalidation"
            ) from exc

    def _signature(self, content: bytes) -> bytes:
        return hmac.new(
            self._signing_key,
            _SIGNATURE_DOMAIN + content,
            hashlib.sha256,
        ).hexdigest().encode("ascii") + b"\n"

    def publish_request(
        self, request: AuthorizedSimulationRequest
    ) -> SimulationRequestReference:
        if type(self) is not FrozenSimulationAuthorityRepository:
            raise SimulationAuthorityError("request publication requires the repository authority")
        checked = self._revalidate_request(request)
        content = canonical_json_bytes(checked.model_dump(mode="python")) + b"\n"
        if len(content) > MAX_AUTHORITY_BYTES:
            raise SimulationAuthorityError("authorized request exceeds its byte limit")
        signature = self._signature(content)
        self._publish(checked.batch_id, "orders", content, signature)
        return SimulationRequestReference(
            tenant_id=checked.tenant_id,
            backtest_id=checked.backtest_id,
            backtest_spec_digest=checked.backtest_spec_digest,
            ledger_id=checked.ledger_id,
            batch_id=checked.batch_id,
            authority_digest=checked.authority_digest,
        )

    def load_request(
        self,
        reference: SimulationRequestReference,
        *,
        expected_tenant_id: UUID,
        expected_backtest_id: UUID,
        expected_backtest_spec_digest: str,
        expected_ledger_id: UUID,
        expected_policy_digest: str,
    ) -> AuthorizedSimulationRequest:
        if type(self) is not FrozenSimulationAuthorityRepository:
            raise SimulationAuthorityError("request loading requires the repository authority")
        checked_ref = self._revalidate_reference(reference)
        content, signature = self._read_files(checked_ref.batch_id, "orders")
        if not hmac.compare_digest(signature, self._signature(content)):
            raise SimulationAuthorityError("authorized request signature mismatch")
        try:
            raw = json.loads(content)
            request = AuthorizedSimulationRequest.model_validate(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise SimulationAuthorityError("authorized request is not valid canonical JSON") from exc
        canonical = canonical_json_bytes(request.model_dump(mode="python")) + b"\n"
        if not hmac.compare_digest(content, canonical):
            raise SimulationAuthorityError("authorized request bytes are not canonical")
        bindings = (
            request.tenant_id == expected_tenant_id == checked_ref.tenant_id,
            request.backtest_id == expected_backtest_id == checked_ref.backtest_id,
            hmac.compare_digest(
                request.backtest_spec_digest,
                expected_backtest_spec_digest,
            )
            and hmac.compare_digest(
                request.backtest_spec_digest,
                checked_ref.backtest_spec_digest,
            ),
            request.ledger_id == expected_ledger_id == checked_ref.ledger_id,
            request.batch_id == checked_ref.batch_id,
            hmac.compare_digest(request.policy_digest, expected_policy_digest),
            hmac.compare_digest(request.authority_digest, checked_ref.authority_digest),
        )
        if not all(bindings):
            raise SimulationAuthorityError("authorized request does not match its trusted bindings")
        return request

    def publish_checkpoint(
        self, checkpoint: SimulationLedgerCheckpoint
    ) -> SimulationCheckpointReference:
        if type(self) is not FrozenSimulationAuthorityRepository:
            raise SimulationAuthorityError("checkpoint publication requires repository authority")
        checked = self._revalidate_checkpoint(checkpoint)
        content = canonical_json_bytes(checked.model_dump(mode="python")) + b"\n"
        if len(content) > MAX_AUTHORITY_BYTES:
            raise SimulationAuthorityError("ledger checkpoint exceeds its byte limit")
        self._publish(checked.checkpoint_id, "checkpoints", content, self._signature(content))
        return SimulationCheckpointReference(
            tenant_id=checked.tenant_id,
            backtest_id=checked.backtest_id,
            backtest_spec_digest=checked.backtest_spec_digest,
            ledger_id=checked.ledger_id,
            checkpoint_id=checked.checkpoint_id,
            checkpoint_digest=checked.checkpoint_digest,
        )

    def load_checkpoint(
        self,
        reference: SimulationCheckpointReference,
        *,
        expected_tenant_id: UUID,
        expected_backtest_id: UUID,
        expected_backtest_spec_digest: str,
    ) -> SimulationLedgerCheckpoint:
        if type(self) is not FrozenSimulationAuthorityRepository:
            raise SimulationAuthorityError("checkpoint loading requires repository authority")
        checked_ref = self._revalidate_checkpoint_reference(reference)
        content, signature = self._read_files(checked_ref.checkpoint_id, "checkpoints")
        if not hmac.compare_digest(signature, self._signature(content)):
            raise SimulationAuthorityError("ledger checkpoint signature mismatch")
        try:
            raw = json.loads(content)
            checkpoint = SimulationLedgerCheckpoint.model_validate(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise SimulationAuthorityError("ledger checkpoint is not valid canonical JSON") from exc
        canonical = canonical_json_bytes(checkpoint.model_dump(mode="python")) + b"\n"
        if not hmac.compare_digest(content, canonical):
            raise SimulationAuthorityError("ledger checkpoint bytes are not canonical")
        bindings = (
            checkpoint.tenant_id == expected_tenant_id == checked_ref.tenant_id,
            checkpoint.backtest_id == expected_backtest_id == checked_ref.backtest_id,
            hmac.compare_digest(
                checkpoint.backtest_spec_digest,
                checked_ref.backtest_spec_digest,
            )
            and hmac.compare_digest(
                checkpoint.backtest_spec_digest,
                expected_backtest_spec_digest,
            ),
            checkpoint.ledger_id == checked_ref.ledger_id,
            checkpoint.checkpoint_id == checked_ref.checkpoint_id,
            hmac.compare_digest(checkpoint.checkpoint_digest, checked_ref.checkpoint_digest),
        )
        if not all(bindings):
            raise SimulationAuthorityError("ledger checkpoint does not match trusted bindings")
        return checkpoint

    @staticmethod
    def _leaf(record_id: UUID, namespace: str) -> str:
        if type(record_id) is not UUID:
            raise SimulationAuthorityError("authority record ID must be a UUID")
        if namespace not in {"orders", "checkpoints"}:
            raise SimulationAuthorityError("authority namespace is invalid")
        return f"{namespace}-{record_id}"

    def _request_dir(self, record_id: UUID, namespace: str) -> Path:
        candidate = self.root / self._leaf(record_id, namespace)
        if candidate.parent != self.root:
            raise SimulationAuthorityError("authority path escaped its repository")
        return candidate

    def _publish(
        self,
        record_id: UUID,
        namespace: str,
        content: bytes,
        signature: bytes,
    ) -> None:
        if os.name == "posix":
            self._publish_at(record_id, namespace, content, signature)
            return
        leaf = self._leaf(record_id, namespace)
        destination = self._request_dir(record_id, namespace)
        claim = self.root / f".{leaf}.claim"
        try:
            claim_fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o400)
        except FileExistsError as exc:
            raise SimulationRequestAlreadyExistsError(
                "simulation request identifier has already been claimed"
            ) from exc
        os.close(claim_fd)
        temporary = Path(tempfile.mkdtemp(prefix=f".{leaf}.", dir=self.root))
        try:
            if destination.exists():
                raise SimulationRequestAlreadyExistsError("immutable request already exists")
            self._write_file(temporary / REQUEST_FILENAME, content)
            self._write_file(temporary / SIGNATURE_FILENAME, signature)
            os.rename(temporary, destination)
        except Exception:
            if temporary.exists() and temporary.parent == self.root:
                shutil.rmtree(temporary)
            claim.unlink(missing_ok=True)
            raise

    def _publish_at(
        self,
        record_id: UUID,
        namespace: str,
        content: bytes,
        signature: bytes,
    ) -> None:
        if self._root_fd is None:
            raise SimulationAuthorityError("authority root descriptor is closed")
        root_fd = os.dup(self._root_fd)
        leaf = self._leaf(record_id, namespace)
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
                raise SimulationRequestAlreadyExistsError(
                    "simulation request identifier has already been claimed"
                ) from exc
            os.close(claim_fd)
            claimed = True
            os.mkdir(temporary, mode=0o700, dir_fd=root_fd)
            flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
            temp_fd = os.open(temporary, flags, dir_fd=root_fd)
            self._write_file_at(temp_fd, REQUEST_FILENAME, content)
            self._write_file_at(temp_fd, SIGNATURE_FILENAME, signature)
            os.fsync(temp_fd)
            self._rename_directory_noreplace(root_fd, temporary, leaf)
            published = True
            os.fsync(root_fd)
        except Exception:
            if temp_fd is not None and not published:
                for name in (REQUEST_FILENAME, SIGNATURE_FILENAME):
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
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise SimulationAuthorityError("atomic no-replace publication is unavailable")
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
            raise SimulationRequestAlreadyExistsError("immutable request already exists")
        raise SimulationAuthorityError("atomic no-replace publication failed") from OSError(
            error_number,
            os.strerror(error_number),
        )

    @staticmethod
    def _write_file(path: Path, content: bytes) -> None:
        with path.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        path.chmod(stat.S_IRUSR)

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

    def _read_files(self, record_id: UUID, namespace: str) -> tuple[bytes, bytes]:
        leaf = self._leaf(record_id, namespace)
        if os.name == "posix":
            if self._root_fd is None:
                raise SimulationAuthorityError("authority root descriptor is closed")
            root_fd = os.dup(self._root_fd)
            flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
            try:
                request_fd = os.open(leaf, flags, dir_fd=root_fd)
            except OSError as exc:
                os.close(root_fd)
                raise SimulationAuthorityError("authorized request does not exist") from exc
            os.close(root_fd)
            try:
                return (
                    self._read_regular_at(request_fd, REQUEST_FILENAME, MAX_AUTHORITY_BYTES),
                    self._read_regular_at(request_fd, SIGNATURE_FILENAME, 256),
                )
            finally:
                os.close(request_fd)

        request_dir = self._request_dir(record_id, namespace)
        try:
            resolved = request_dir.resolve(strict=True)
            before = request_dir.lstat()
        except (FileNotFoundError, OSError) as exc:
            raise SimulationAuthorityError("authorized request does not exist") from exc
        if (
            resolved != request_dir
            or resolved.parent != self.root
            or not stat.S_ISDIR(before.st_mode)
            or self._is_reparse(before)
        ):
            raise SimulationAuthorityError("authorized request directory is unsafe")
        result = (
            self._read_regular(request_dir / REQUEST_FILENAME, MAX_AUTHORITY_BYTES),
            self._read_regular(request_dir / SIGNATURE_FILENAME, 256),
        )
        after = request_dir.lstat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise SimulationAuthorityError("authorized request directory changed during read")
        return result

    @staticmethod
    def _read_regular_at(directory_fd: int, name: str, max_bytes: int) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(name, flags, dir_fd=directory_fd)
        except OSError as exc:
            raise SimulationAuthorityError("authorized request is incomplete") from exc
        try:
            file_stat = os.fstat(fd)
            if not stat.S_ISREG(file_stat.st_mode):
                raise SimulationAuthorityError("authorized request contains a non-regular file")
            if file_stat.st_size > max_bytes:
                raise SimulationAuthorityError("authorized request file exceeds its byte limit")
            chunks: list[bytes] = []
            remaining = max_bytes + 1
            while remaining:
                chunk = os.read(fd, min(remaining, 1024 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
        finally:
            os.close(fd)
        if len(content) > max_bytes:
            raise SimulationAuthorityError("authorized request file exceeds its byte limit")
        return content

    @staticmethod
    def _read_regular(path: Path, max_bytes: int) -> bytes:
        try:
            before = path.lstat()
        except FileNotFoundError as exc:
            raise SimulationAuthorityError("authorized request is incomplete") from exc
        if not stat.S_ISREG(before.st_mode) or FrozenSimulationAuthorityRepository._is_reparse(before):
            raise SimulationAuthorityError("authorized request contains a non-regular file")
        if before.st_size > max_bytes:
            raise SimulationAuthorityError("authorized request file exceeds its byte limit")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags)
        except OSError as exc:
            raise SimulationAuthorityError("authorized request could not be opened safely") from exc
        try:
            after = os.fstat(fd)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise SimulationAuthorityError("authorized request changed during open")
            chunks: list[bytes] = []
            remaining = max_bytes + 1
            while remaining:
                chunk = os.read(fd, min(remaining, 1024 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
        finally:
            os.close(fd)
        if len(content) > max_bytes:
            raise SimulationAuthorityError("authorized request file exceeds its byte limit")
        return content


def execute_authorized_simulation_batch(
    authority_repository: FrozenSimulationAuthorityRepository,
    dataset_repository: FrozenDatasetRepository,
    reference: SimulationRequestReference,
    checkpoint_reference: SimulationCheckpointReference,
    backtest_spec: BacktestSpec,
    *,
    expected_tenant_id: UUID,
    expected_backtest_id: UUID,
    expected_backtest_spec_digest: str,
) -> AuthorizedSimulationBatchResult:
    """Resolve one authenticated batch and execute it atomically in the pure ledger."""

    if type(authority_repository) is not FrozenSimulationAuthorityRepository:
        raise SimulationAuthorityError("execution requires the exact simulation repository authority")
    if type(dataset_repository) is not FrozenDatasetRepository:
        raise SimulationAuthorityError("execution requires the exact dataset repository authority")
    if type(backtest_spec) is not BacktestSpec:
        raise SimulationAuthorityError("execution requires the exact backtest specification")
    try:
        checked_spec = BacktestSpec.model_validate(backtest_spec.model_dump(mode="python"))
    except ValueError as exc:
        raise SimulationAuthorityError("backtest specification failed deep revalidation") from exc
    checked_spec_digest = canonical_digest(checked_spec)
    if (
        checked_spec.tenant_id != expected_tenant_id
        or checked_spec.backtest_id != expected_backtest_id
        or not hmac.compare_digest(checked_spec_digest, expected_backtest_spec_digest)
    ):
        raise SimulationAuthorityError("backtest specification does not match trusted bindings")
    checkpoint = FrozenSimulationAuthorityRepository.load_checkpoint(
        authority_repository,
        checkpoint_reference,
        expected_tenant_id=expected_tenant_id,
        expected_backtest_id=expected_backtest_id,
        expected_backtest_spec_digest=expected_backtest_spec_digest,
    )
    request = FrozenSimulationAuthorityRepository.load_request(
        authority_repository,
        reference,
        expected_tenant_id=expected_tenant_id,
        expected_backtest_id=expected_backtest_id,
        expected_backtest_spec_digest=checkpoint.backtest_spec_digest,
        expected_ledger_id=checkpoint.ledger_id,
        expected_policy_digest=checkpoint.policy.digest,
    )
    if request.ledger_id != checkpoint.policy.ledger_id:
        raise SimulationAuthorityError("authorized request references another ledger policy")
    if not hmac.compare_digest(
        request.backtest_spec_digest,
        checkpoint.backtest_spec_digest,
    ):
        raise SimulationAuthorityError("authorized request references another backtest spec")
    if request.created_at < checkpoint.created_at:
        raise SimulationAuthorityError("authorized request predates its ledger checkpoint")
    references = {
        item.instrument_id: item
        for item in checked_spec.dataset_references
        if item.role is DatasetRole.TRADEABLE
    }
    for authorized in request.orders:
        dataset_reference = references.get(authorized.instrument_id)
        if (
            dataset_reference is None
            or dataset_reference.dataset_id != authorized.dataset_id
            or not hmac.compare_digest(
                dataset_reference.manifest_digest,
                authorized.manifest_digest,
            )
        ):
            raise SimulationAuthorityError(
                "authorized order is outside the frozen backtest specification"
            )

    orders: list[SimulationOrder] = []
    evidence: list[AuthorizedOrderExecutionEvidence] = []
    execution_at: datetime | None = None
    for authorized in request.orders:
        resolved = resolve_execution_price_authority(
            dataset_repository,
            authorized.dataset_id,
            expected_tenant_id=request.tenant_id,
            expected_instrument_id=authorized.instrument_id,
            expected_manifest_digest=authorized.manifest_digest,
            decision_at=authorized.decision_at,
            decision_session_date=authorized.decision_session_date,
            expected_eligible_view_digest=authorized.eligible_view_digest,
        )
        if execution_at is None:
            execution_at = resolved.price.execution_at
        elif execution_at != resolved.price.execution_at:
            raise SimulationAuthorityError("authorized orders do not share one execution session")
        orders.append(
            SimulationOrder(
                order_id=authorized.order_id,
                instrument_id=authorized.instrument_id,
                side=authorized.side,
                requested_quantity=authorized.requested_quantity,
                raw_open=resolved.price.raw_open,
                execution_at=resolved.price.execution_at,
            )
        )
        evidence.append(
            AuthorizedOrderExecutionEvidence(
                order_id=authorized.order_id,
                authority_digest=request.authority_digest,
                execution_price_bundle_digest=resolved.bundle_digest,
                execution_price_projection_digest=resolved.price.projection_digest,
                execution_price_provenance_digest=resolved.provenance.provenance_digest,
            )
        )
    if execution_at is None:
        raise SimulationAuthorityError("authorized request contains no executable orders")
    batch = SimulationBatch(
        batch_id=request.batch_id,
        session_id=request.session_id,
        execution_at=execution_at,
        orders=tuple(orders),
    )
    ledger_result = execute_simulation_batch(
        checkpoint.state,
        checkpoint.policy,
        batch,
        expected_last_event_hash=checkpoint.state.last_event_hash,
        expected_request_digest=batch.request_digest,
    )
    next_checkpoint = None
    if ledger_result.replay_status == "applied":
        next_checkpoint_payload = {
            "checkpoint_version": "simulation-ledger-checkpoint/1",
            "checkpoint_id": uuid5(
                NAMESPACE_URL,
                "tradingagents/simulation-checkpoint/1:"
                f"{checkpoint.checkpoint_digest}:{request.authority_digest}:"
                f"{ledger_result.state.last_event_hash}",
            ),
            "tenant_id": request.tenant_id,
            "backtest_id": request.backtest_id,
            "backtest_spec_digest": request.backtest_spec_digest,
            "ledger_id": request.ledger_id,
            "created_at": request.created_at,
            "previous_checkpoint_digest": checkpoint.checkpoint_digest,
            "policy": checkpoint.policy,
            "state": ledger_result.state,
        }
        next_checkpoint = SimulationLedgerCheckpoint(**next_checkpoint_payload)
    result_payload = {
        "result_version": "authorized-simulation-batch-result/1",
        "reference": reference,
        "prior_checkpoint": checkpoint_reference,
        "execution_evidence": tuple(evidence),
        "ledger_result": ledger_result,
        "next_checkpoint": next_checkpoint,
    }
    digest_payload = {
        "result_version": "authorized-simulation-batch-result/1",
        "reference": reference.model_dump(mode="python"),
        "prior_checkpoint": checkpoint_reference.model_dump(mode="python"),
        "execution_evidence": tuple(
            item.model_dump(mode="python") for item in evidence
        ),
        "ledger_result": ledger_result.model_dump(mode="python"),
        "next_checkpoint": (
            next_checkpoint.model_dump(mode="python")
            if next_checkpoint is not None
            else None
        ),
    }
    return AuthorizedSimulationBatchResult(
        **result_payload,
        result_digest=canonical_digest(digest_payload),
    )


__all__ = [
    "AuthorizedOrderExecutionEvidence",
    "AuthorizedSimulationBatchResult",
    "AuthorizedSimulationOrder",
    "AuthorizedSimulationRequest",
    "FrozenSimulationAuthorityRepository",
    "SimulationAuthorityError",
    "SimulationCheckpointReference",
    "SimulationLedgerCheckpoint",
    "SimulationRequestAlreadyExistsError",
    "SimulationRequestReference",
    "execute_authorized_simulation_batch",
]

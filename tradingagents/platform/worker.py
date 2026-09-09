"""Lease-based analysis worker with tenant credential and run isolation."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.exc import DBAPIError, OperationalError

from tradingagents.dataflows.redact import redact_secrets
from tradingagents.default_config import PLATFORM_DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.platform.evidence_ledger import capture_evidence, serialize_evidence
from tradingagents.platform.jobs import (
    ClaimedJob,
    JobError,
    JobService,
    effective_analysis_config,
)
from tradingagents.platform.llm_connections import (
    LlmConnectionError,
    LlmConnectionService,
    ResolvedLlmCredential,
)
from tradingagents.platform.usage import (
    JobUsage,
    LlmUsageCallback,
    PricingCatalog,
    UsagePolicyError,
)

logger = logging.getLogger(__name__)
_POSIX_SIGKILL = getattr(signal, "SIGKILL", 9)


class GraphExecutor(Protocol):
    def __call__(
        self,
        claim: ClaimedJob,
        credential: ResolvedLlmCredential,
        control: ExecutionControl,
    ) -> ExecutionOutcome: ...


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    result: dict[str, Any]
    manifest: dict[str, Any]
    usage: JobUsage = JobUsage()


@dataclass(frozen=True, slots=True)
class ExecutionControl:
    lease_lost: threading.Event
    deadline_monotonic: float

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_monotonic - time.monotonic())

    def assert_active(self) -> None:
        if self.lease_lost.is_set():
            raise GraphExecutionFailure(JobUsage(), "WORKER_LEASE_LOST", retryable=False)
        if time.monotonic() >= self.deadline_monotonic:
            raise GraphExecutionFailure(
                JobUsage(),
                "WORKER_DEADLINE_EXCEEDED",
                retryable=False,
            )


class GraphExecutionFailure(RuntimeError):
    def __init__(
        self,
        usage: JobUsage,
        code: str = "WORKER_EXECUTION_FAILED",
        *,
        retryable: bool = True,
        partial_manifest: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.usage = usage
        self.code = code
        self.retryable = retryable
        self.partial_manifest = partial_manifest


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    worker_id: str
    lease_seconds: int = 120
    maintenance_seconds: int = 300

    def __post_init__(self) -> None:
        if not 3 <= len(self.worker_id) <= 120:
            raise ValueError("worker_id is invalid")
        if not 30 <= self.lease_seconds <= 900:
            raise ValueError("lease_seconds must be between 30 and 900")
        if not 30 <= self.maintenance_seconds <= 86_400:
            raise ValueError("maintenance_seconds must be between 30 and 86400")


def _supervise_attempt_process(
    process: subprocess.Popen[str],
    control: ExecutionControl,
    *,
    attempt_root: Path | None = None,
    tenant_root: Path | None = None,
    max_attempt_bytes: int | None = None,
    max_tenant_bytes: int | None = None,
) -> str | None:
    """Wait for a child or terminate it physically at lease/deadline loss."""

    failure_code: str | None = None
    next_storage_check = time.monotonic()
    while process.poll() is None:
        if control.lease_lost.is_set():
            failure_code = "WORKER_LEASE_LOST"
            break
        if control.remaining_seconds() <= 0:
            failure_code = "WORKER_DEADLINE_EXCEEDED"
            break
        now = time.monotonic()
        if now >= next_storage_check:
            try:
                if (
                    attempt_root is not None
                    and max_attempt_bytes is not None
                    and _directory_size(attempt_root, stop_after=max_attempt_bytes)
                    > max_attempt_bytes
                ):
                    failure_code = "WORKER_ATTEMPT_STORAGE_EXCEEDED"
                    break
                if (
                    tenant_root is not None
                    and max_tenant_bytes is not None
                    and _directory_size(tenant_root, stop_after=max_tenant_bytes)
                    > max_tenant_bytes
                ):
                    failure_code = "WORKER_TENANT_STORAGE_EXCEEDED"
                    break
            except Exception:
                # A failed quota inspection cannot leave an uncontained child
                # running or enter the retry path after billable work may occur.
                failure_code = "WORKER_STORAGE_INSPECTION_FAILED"
                break
            next_storage_check = now + 0.5
        time.sleep(min(0.1, max(0.01, control.remaining_seconds())))
    if failure_code is not None:
        try:
            terminated = _terminate_process_tree(process)
        except Exception:
            # The terminator is a containment boundary: even an unforeseen OS
            # or subprocess failure must not enter the generic retry path.
            terminated = False
        if not terminated:
            # The caller converts this terminal protocol failure into the
            # conservative remaining-budget charge.
            failure_code = "WORKER_TERMINATION_UNCONFIRMED"
    return failure_code


def _directory_size(root: Path, *, stop_after: int) -> int:
    """Bounded, no-follow size walk for a private runtime directory."""

    total = 0
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            return stop_after + 1
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                            if total > stop_after:
                                return total
                    except FileNotFoundError:
                        continue
        except FileNotFoundError:
            continue
    return total


def _terminate_process_tree(process: subprocess.Popen[str]) -> bool:
    """Best-effort tree termination which never leaks a supervisor exception."""

    if process.poll() is not None:
        return True
    tree_signal_confirmed = False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=5,
            )
            tree_signal_confirmed = result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            tree_signal_confirmed = False
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            tree_signal_confirmed = True
        except ProcessLookupError:
            tree_signal_confirmed = process.poll() is not None
        except OSError:
            tree_signal_confirmed = False
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        try:
            process.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            return False
    except (OSError, subprocess.SubprocessError):
        return False
    leader_terminated = process.poll() is not None
    if os.name != "nt":
        return leader_terminated and _terminate_remaining_posix_group(process.pid)
    return tree_signal_confirmed and leader_terminated


def _terminate_remaining_posix_group(process_group_id: int) -> bool:
    """Confirm that no descendant remains after the process-group leader exits."""

    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    try:
        os.killpg(process_group_id, _POSIX_SIGKILL)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        time.sleep(0.05)
    return False


def _read_evidence_journal(path: Path) -> list[dict[str, Any]]:
    try:
        if not path.exists() or path.is_symlink():
            return []
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 2_000_000:
            return []
        records: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
            if not line or len(line.encode()) > 100_000:
                continue
            try:
                record = json.loads(redact_secrets(line))
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
        return records[:1000]
    except (OSError, UnicodeError):
        # Advisory evidence must never cancel conservative accounting.
        return []


def _conservative_attempt_usage(claim: ClaimedJob) -> JobUsage:
    remaining_tokens = max(0, claim.command.token_budget - claim.prior_usage.total_tokens)
    remaining_cost = None
    if claim.command.cost_budget_microusd is not None:
        remaining_cost = max(
            0,
            claim.command.cost_budget_microusd
            - (claim.prior_usage.estimated_cost_microusd or 0),
        )
    return JobUsage(
        output_tokens=remaining_tokens,
        total_tokens=remaining_tokens,
        llm_requests=1 if remaining_tokens or remaining_cost else 0,
        estimated_cost_microusd=remaining_cost,
        pricing_version=(
            claim.run_context.pricing_catalog_version
            if remaining_cost is not None
            else None
        ),
        accounting_basis="conservative_reservation",
    )


def _protocol_failure(
    claim: ClaimedJob,
    evidence_path: Path,
    code: str = "WORKER_ATTEMPT_PROTOCOL_FAILED",
) -> GraphExecutionFailure:
    usage = _conservative_attempt_usage(claim)
    return GraphExecutionFailure(
        usage,
        code,
        retryable=False,
        partial_manifest=_partial_attempt_manifest(
            claim,
            usage,
            _read_evidence_journal(evidence_path),
        ),
    )


def _load_attempt_result(
    claim: ClaimedJob,
    result_path: Path,
    evidence_path: Path,
) -> tuple[dict[str, Any], JobUsage]:
    """Consume one child result without allowing local I/O to escape fail-closed."""

    try:
        if not result_path.exists() or result_path.is_symlink():
            raise _protocol_failure(claim, evidence_path)
        metadata = result_path.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 4_000_000:
            raise _protocol_failure(claim, evidence_path)
        response = json.loads(result_path.read_text(encoding="utf-8", errors="strict"))
        usage = JobUsage(**response["usage"])
        return response, usage
    except GraphExecutionFailure:
        raise
    except (KeyError, TypeError, ValueError, OSError, UnicodeError, json.JSONDecodeError):
        raise _protocol_failure(
            claim,
            evidence_path,
            "WORKER_RESULT_INSPECTION_FAILED",
        ) from None
    finally:
        try:
            result_path.unlink(missing_ok=True)
        except OSError:
            logger.error("could not remove worker result file")


class ProductionGraphExecutor:
    """Construct one isolated graph from immutable job inputs and BYOK secret."""

    def __init__(
        self,
        *,
        runtime_root: Path,
        pricing: PricingCatalog | None = None,
        request_timeout_seconds: float = 30.0,
        isolate_process: bool = True,
        max_attempt_bytes: int = 256 * 1024 * 1024,
        max_tenant_bytes: int = 1024 * 1024 * 1024,
    ) -> None:
        lexical = runtime_root.expanduser().absolute()
        for candidate in (lexical, *lexical.parents):
            if candidate.exists() and candidate.is_symlink():
                raise RuntimeError("worker runtime root cannot traverse symlinks")
        requested = lexical.resolve(strict=False)
        forbidden = {
            Path(requested.anchor).resolve(),
            Path.home().resolve(),
            Path.cwd().resolve(),
        }
        if any(requested == boundary or requested in boundary.parents for boundary in forbidden):
            raise RuntimeError("worker runtime root must be a dedicated subdirectory")
        existed = requested.exists()
        if existed and not requested.is_dir():
            raise RuntimeError("worker runtime root must be a directory")
        if not existed:
            requested.mkdir(parents=True, mode=0o700)
        if requested.is_symlink():
            raise RuntimeError("worker runtime root cannot be a symlink")
        self._root = requested.resolve(strict=True)
        if os.name == "posix":
            metadata = self._root.stat()
            if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
                if existed:
                    raise RuntimeError("existing worker runtime root is not private and owned")
                self._root.chmod(0o700)
        self._pricing = pricing or PricingCatalog()
        if not 1 <= request_timeout_seconds <= 300:
            raise ValueError("LLM request timeout is invalid")
        self._request_timeout_seconds = request_timeout_seconds
        self._isolate_process = isolate_process
        if not 16 * 1024 * 1024 <= max_attempt_bytes <= 1024 * 1024 * 1024:
            raise ValueError("attempt storage quota is invalid")
        if not max_attempt_bytes <= max_tenant_bytes <= 16 * 1024 * 1024 * 1024:
            raise ValueError("tenant storage quota is invalid")
        self._max_attempt_bytes = max_attempt_bytes
        self._max_tenant_bytes = max_tenant_bytes

    def __call__(
        self,
        claim: ClaimedJob,
        credential: ResolvedLlmCredential,
        control: ExecutionControl,
    ) -> ExecutionOutcome:
        if self._isolate_process:
            return self._execute_subprocess(claim, credential, control)
        return self._execute_inline(claim, credential, control)

    def _execute_subprocess(
        self,
        claim: ClaimedJob,
        credential: ResolvedLlmCredential,
        control: ExecutionControl,
    ) -> ExecutionOutcome:
        control.assert_active()
        remaining = control.remaining_seconds()
        attempt_scope = f"attempt-{claim.attempts}-{claim.lease_id.hex}"
        run_root = (
            self._root / str(claim.tenant_id) / str(claim.run_id) / attempt_scope
        ).resolve()
        if self._root not in run_root.parents:
            raise GraphExecutionFailure(
                JobUsage(), "WORKER_RUNTIME_PATH_INVALID", retryable=False
            )
        run_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        evidence_path = run_root / "attempt-evidence.jsonl"
        result_path = run_root / f"attempt-result-{secrets.token_hex(16)}.json"
        payload = {
            "claim": {
                "id": str(claim.id),
                "run_id": str(claim.run_id),
                "tenant_id": str(claim.tenant_id),
                "requested_by_user_id": str(claim.requested_by_user_id),
                "lease_id": str(claim.lease_id),
                "lease_owner": claim.lease_owner,
                "lease_expires_at": claim.lease_expires_at.isoformat(),
                "command": claim.command.model_dump(mode="json"),
                "run_context": claim.run_context.model_dump(mode="json"),
                "attempts": claim.attempts,
                "prior_usage": asdict(claim.prior_usage),
                "claimed_at": claim.claimed_at.isoformat(),
            },
            "credential": {
                "connection_id": str(credential.connection_id),
                "provider": credential.provider,
                "base_url": credential.base_url,
                "version": credential.version,
                "api_key": credential.api_key,
            },
            "runtime_root": str(self._root),
            "pricing": self._pricing.export(),
            "request_timeout_seconds": self._request_timeout_seconds,
            "remaining_seconds": remaining,
            "result_path": str(result_path),
        }
        child_env = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP"}
        }
        child_env.update(
            {
                "PYTHONNOUSERSITE": "1",
                "PYTHONUTF8": "1",
                "TRADINGAGENTS_DISABLE_DOTENV": "true",
            }
        )
        creation_flags = 0
        if os.name == "nt":
            creation_flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        process = subprocess.Popen(
            [sys.executable, "-m", "tradingagents.platform.attempt_runner"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            env=child_env,
            # Keep cwd outside the writable runtime root.  The child validates
            # that root as a dedicated data directory and must never treat it
            # as its code/import working directory.
            cwd=str(Path(__file__).resolve().parents[2]),
            creationflags=creation_flags,
            start_new_session=os.name != "nt",
        )
        assert process.stdin is not None
        process.stdin.write(json.dumps(payload, separators=(",", ":"), default=str))
        process.stdin.close()
        process.stdin = None
        failure_code = _supervise_attempt_process(
            process,
            control,
            attempt_root=run_root,
            tenant_root=self._root / str(claim.tenant_id),
            max_attempt_bytes=self._max_attempt_bytes,
            max_tenant_bytes=self._max_tenant_bytes,
        )
        if failure_code is not None:
            raise _protocol_failure(claim, evidence_path, failure_code)
        if process.returncode != 0:
            raise _protocol_failure(claim, evidence_path)
        response, usage = _load_attempt_result(claim, result_path, evidence_path)
        if response.get("kind") == "failure":
            raise GraphExecutionFailure(
                usage,
                str(response.get("code") or "WORKER_EXECUTION_FAILED"),
                retryable=response.get("retryable") is True,
                partial_manifest=response.get("partial_manifest"),
            )
        if response.get("kind") != "success":
            raise _protocol_failure(claim, evidence_path)
        result = response.get("result")
        manifest = response.get("manifest")
        if not isinstance(result, dict) or not isinstance(manifest, dict):
            raise _protocol_failure(claim, evidence_path)
        return ExecutionOutcome(result=result, manifest=manifest, usage=usage)

    def cleanup_attempt(self, claim: ClaimedJob) -> None:
        attempt_scope = f"attempt-{claim.attempts}-{claim.lease_id.hex}"
        target = (
            self._root / str(claim.tenant_id) / str(claim.run_id) / attempt_scope
        ).resolve()
        if self._root not in target.parents:
            raise RuntimeError("attempt cleanup path escaped the worker root")
        if target.exists() and not target.is_symlink():
            shutil.rmtree(target)

    def _execute_inline(
        self,
        claim: ClaimedJob,
        credential: ResolvedLlmCredential,
        control: ExecutionControl,
    ) -> ExecutionOutcome:
        attempt_scope = f"attempt-{claim.attempts}-{claim.lease_id.hex}"
        run_root = (
            self._root / str(claim.tenant_id) / str(claim.run_id) / attempt_scope
        ).resolve()
        if self._root not in run_root.parents:
            raise RuntimeError("run path escaped the worker root")
        run_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        run_root.chmod(0o700)
        public_config = effective_analysis_config(claim.command)
        public_digest = hashlib.sha256(
            json.dumps(public_config, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if not secrets.compare_digest(public_digest, claim.run_context.config_digest):
            raise GraphExecutionFailure(
                JobUsage(),
                "RUN_CONTEXT_CONFIG_MISMATCH",
                retryable=False,
            )
        control.assert_active()
        remaining = control.remaining_seconds()
        if remaining <= 2.0:
            raise GraphExecutionFailure(
                JobUsage(),
                "WORKER_DEADLINE_WINDOW_INSUFFICIENT",
                retryable=False,
            )
        request_timeout = min(self._request_timeout_seconds, remaining - 2.0)
        config = deepcopy(PLATFORM_DEFAULT_CONFIG)
        config.update(public_config)
        config.update(
            {
                "llm_provider": claim.command.provider,
                "deep_think_llm": claim.command.model,
                "quick_think_llm": claim.command.model,
                "backend_url": credential.base_url,
                "llm_api_key": credential.api_key,
                "platform_strict_credentials": True,
                "llm_allowed_origins": credential.allowed_origins,
                "results_dir": str(run_root / "results"),
                "data_cache_dir": str(run_root / "cache"),
                # The legacy Markdown memory store is not concurrency-safe. Keep
                # it isolated to this immutable run until tenant memory moves to
                # a transactional database repository in a later gate.
                "memory_log_path": str(run_root / "memory" / "trading_memory.md"),
                "checkpoint_enabled": True,
                "llm_request_timeout_seconds": request_timeout,
                # SDK retries can outlive the lease and duplicate cost. Durable
                # job retries are handled by the queue with a new fencing lease.
                "llm_max_retries": 0,
            }
        )
        if claim.command.cost_budget_microusd is not None and not self._pricing.has_price(
            provider=claim.command.provider,
            model=claim.command.model,
        ):
            raise GraphExecutionFailure(
                JobUsage(),
                "LLM_COST_UNMETERED",
                retryable=False,
            )
        usage = LlmUsageCallback(
            provider=claim.command.provider,
            model=claim.command.model,
            pricing=self._pricing,
            abort_event=control.lease_lost,
            max_total_tokens=claim.command.token_budget,
            max_cost_microusd=claim.command.cost_budget_microusd,
            max_output_tokens_per_request=int(config["max_tokens"]),
            prior_usage=claim.prior_usage,
            deadline_monotonic=control.deadline_monotonic,
            minimum_request_window_seconds=request_timeout + 0.5,
        )
        evidence_records = None
        try:
            control.assert_active()
            graph = TradingAgentsGraph(
                selected_analysts=claim.command.selected_analysts,
                config=config,
                callbacks=[usage],
                isolated_config=True,
            )
            with capture_evidence(
                journal_path=run_root / "attempt-evidence.jsonl"
            ) as evidence_records:
                final_state, signal = graph.propagate(
                    claim.command.symbol,
                    claim.command.trade_date,
                    asset_type=claim.command.asset_type,
                    run_context=claim.run_context,
                )
        except GraphExecutionFailure:
            raise
        except UsagePolicyError as exc:
            raise GraphExecutionFailure(
                usage.snapshot(),
                exc.code,
                retryable=False,
                partial_manifest=_partial_attempt_manifest(
                    claim, usage.snapshot(), evidence_records
                ),
            ) from None
        except RuntimeError:
            raise GraphExecutionFailure(
                usage.snapshot(),
                partial_manifest=_partial_attempt_manifest(
                    claim, usage.snapshot(), evidence_records
                ),
            ) from None
        except Exception:
            raise GraphExecutionFailure(
                usage.snapshot(),
                partial_manifest=_partial_attempt_manifest(
                    claim, usage.snapshot(), evidence_records
                ),
            ) from None
        result = _project_result(final_state, signal)
        manifest = claim.run_context.manifest()
        usage_snapshot = usage.snapshot()
        manifest.update(
            {
                "schema": "analysis-result-manifest/1",
                "signal": str(signal),
                "provider": claim.command.provider,
                "model": claim.command.model,
                "llm_usage": usage_snapshot.as_dict(),
                "evidence_ledger": serialize_evidence(evidence_records),
            }
        )
        return ExecutionOutcome(result=result, manifest=manifest, usage=usage_snapshot)


def _partial_attempt_manifest(
    claim: ClaimedJob,
    usage: JobUsage,
    evidence_records: Any,
) -> dict[str, Any]:
    """Build a secret-free record even when an attempt exits before a result."""

    manifest = claim.run_context.manifest()
    if isinstance(evidence_records, list):
        serialized_evidence = evidence_records
    elif evidence_records is not None:
        serialized_evidence = serialize_evidence(evidence_records)
    else:
        serialized_evidence = []
    manifest.update(
        {
            "schema": "analysis-attempt-manifest/1",
            "attempt": claim.attempts,
            "llm_usage": usage.as_dict(),
            "evidence_ledger": serialized_evidence,
        }
    )
    return manifest


class WorkerRunner:
    def __init__(
        self,
        *,
        jobs: JobService,
        credentials: LlmConnectionService,
        executor: GraphExecutor,
        config: WorkerConfig,
        maintenance: Callable[[], object] | None = None,
    ) -> None:
        self._jobs = jobs
        self._credentials = credentials
        self._executor = executor
        self._config = config
        self._maintenance = maintenance

    def run_once(self) -> bool:
        claim = self._jobs.claim_next(
            worker_id=self._config.worker_id,
            lease_seconds=self._config.lease_seconds,
        )
        if claim is None:
            return False
        stop = threading.Event()
        lease_lost = threading.Event()
        deadline_remaining = max(
            0.0,
            (claim.run_context.deadline_at - claim.claimed_at).total_seconds(),
        )
        control = ExecutionControl(
            lease_lost=lease_lost,
            deadline_monotonic=time.monotonic() + deadline_remaining,
        )
        heartbeat = threading.Thread(
            target=self._heartbeat,
            args=(claim, stop, lease_lost),
            name=f"heartbeat-{claim.id}",
            daemon=True,
        )
        usage_persisted = False
        provider_execution_started = False
        disposition_persisted = False
        try:
            self._jobs.start(claim)
            heartbeat.start()
            credential = self._credentials.resolve_for_worker(
                job_id=claim.id,
                tenant_id=claim.tenant_id,
                lease_id=claim.lease_id,
                lease_owner=claim.lease_owner,
                attempt_number=claim.attempts,
                connection_id=claim.command.llm_connection_id,
                credential_version=claim.command.llm_credential_version,
                expected_provider=claim.command.provider,
                actor_user_id=claim.requested_by_user_id,
            )
            resolved_snapshot = credential.provenance(model=claim.command.model)
            if (
                resolved_snapshot.application_build_digest
                != claim.run_context.application_build_digest
                or resolved_snapshot.calendar_engine_version
                != claim.run_context.calendar_engine_version
            ):
                raise GraphExecutionFailure(
                    JobUsage(),
                    "RUN_CONTEXT_BUILD_MISMATCH",
                    retryable=False,
                )
            if not secrets.compare_digest(
                resolved_snapshot.digest,
                claim.run_context.provider_digest,
            ):
                raise GraphExecutionFailure(
                    JobUsage(),
                    "RUN_CONTEXT_PROVIDER_MISMATCH",
                    retryable=False,
                )
            control.assert_active()
            provider_execution_started = True
            outcome = self._executor(claim, credential, control)
            if lease_lost.is_set():
                raise GraphExecutionFailure(
                    outcome.usage,
                    "WORKER_LEASE_LOST",
                    retryable=False,
                )
            if (
                len(json.dumps(outcome.result, default=str).encode()) > 2_000_000
                or len(json.dumps(outcome.manifest, default=str).encode()) > 2_000_000
            ):
                raise GraphExecutionFailure(
                    outcome.usage,
                    "WORKER_RESULT_TOO_LARGE",
                    retryable=False,
                )
            # Persist the externally billed fact in its own transaction before
            # storing the potentially large result.  If result completion then
            # fails, this attempt is terminal and cannot spend again.
            self._jobs.record_usage(claim, usage=outcome.usage)
            usage_persisted = True
            self._jobs.complete(
                claim,
                result=outcome.result,
                manifest=outcome.manifest,
                usage=outcome.usage,
            )
            disposition_persisted = True
        except JobError as exc:
            disposition_persisted = self._safe_fail(
                claim,
                (
                    "WORKER_JOB_CANCELLED"
                    if str(exc) == "JOB_CANCELLED"
                    else "WORKER_JOB_STATE_FAILED"
                ),
                retryable=str(exc) != "JOB_CANCELLED" and not usage_persisted,
            )
        except LlmConnectionError:
            disposition_persisted = self._safe_fail(
                claim, "WORKER_CREDENTIAL_FAILED", retryable=False
            )
        except GraphExecutionFailure as exc:
            retryable_without_billed_work = (
                exc.retryable
                and exc.usage.total_tokens == 0
                and exc.usage.llm_requests == 0
                and exc.usage.unmetered_llm_requests == 0
            )
            disposition_persisted = self._safe_fail(
                claim,
                exc.code,
                retryable=retryable_without_billed_work,
                partial_manifest=exc.partial_manifest,
                usage=exc.usage,
            )
        except Exception:
            # Provider exceptions can echo request headers or URLs. Persist and
            # log only a fixed code; never the exception text or traceback.
            logger.error("analysis worker failed", extra={"job_id": str(claim.id)})
            conservative_usage = (
                _conservative_attempt_usage(claim)
                if provider_execution_started and not usage_persisted
                else None
            )
            disposition_persisted = self._safe_fail(
                claim,
                "WORKER_EXECUTION_FAILED",
                retryable=not provider_execution_started and not usage_persisted,
                usage=conservative_usage,
            )
        finally:
            stop.set()
            if heartbeat.is_alive():
                heartbeat.join(timeout=5)
            cleanup = getattr(self._executor, "cleanup_attempt", None)
            if disposition_persisted and callable(cleanup):
                try:
                    cleanup(claim)
                except Exception:
                    logger.error(
                        "could not remove terminal attempt workspace",
                        extra={"job_id": str(claim.id)},
                    )
        return True

    def _heartbeat(
        self,
        claim: ClaimedJob,
        stop: threading.Event,
        lease_lost: threading.Event,
    ) -> None:
        execution_deadline_monotonic = time.monotonic() + max(
            0.0,
            (claim.run_context.deadline_at - claim.claimed_at).total_seconds(),
        )
        normal_interval = max(1.0, min(10.0, self._config.lease_seconds / 3))
        deadline = min(
            time.monotonic() + self._config.lease_seconds,
            execution_deadline_monotonic,
        )
        retrying = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                lease_lost.set()
                return
            interval = min(normal_interval, max(0.1, remaining / 3))
            if retrying:
                interval = min(interval, 2.0)
            if stop.wait(interval):
                return
            try:
                self._jobs.heartbeat(
                    claim,
                    lease_seconds=self._config.lease_seconds,
                )
                deadline = min(
                    time.monotonic() + self._config.lease_seconds,
                    execution_deadline_monotonic,
                )
                retrying = False
            except JobError:
                # Cancellation and a fenced/terminal lease are authoritative,
                # unlike a transient database transport failure.
                lease_lost.set()
                return
            except Exception:
                retrying = True

    def _safe_fail(
        self,
        claim: ClaimedJob,
        code: str,
        *,
        retryable: bool = True,
        partial_manifest: dict[str, Any] | None = None,
        usage: JobUsage | None = None,
    ) -> bool:
        try:
            self._jobs.fail(
                claim,
                error_code=code,
                retryable=retryable,
                partial_manifest=partial_manifest,
                usage=usage,
            )
            return True
        except Exception:
            logger.error("could not persist worker failure", extra={"job_id": str(claim.id)})
            return False

    def run_forever(
        self,
        *,
        poll_seconds: float = 2.0,
        stop: threading.Event | None = None,
    ) -> None:
        if not 0.1 <= poll_seconds <= 30:
            raise ValueError("poll_seconds must be between 0.1 and 30")
        stop_event = stop or threading.Event()
        database_failures = 0
        next_maintenance = 0.0
        while not stop_event.is_set():
            try:
                now = time.monotonic()
                if self._maintenance is not None and now >= next_maintenance:
                    self._maintenance()
                    next_maintenance = now + self._config.maintenance_seconds
                handled = self.run_once()
                database_failures = 0
            except (OperationalError, DBAPIError):
                database_failures += 1
                delay = min(30.0, float(2 ** min(database_failures, 5)))
                delay += secrets.randbelow(1000) / 1000
                logger.error("worker database temporarily unavailable")
                stop_event.wait(delay)
                continue
            if not handled:
                stop_event.wait(poll_seconds)


def _project_result(final_state: Mapping[str, Any], signal: object) -> dict[str, Any]:
    allowed = (
        "company_of_interest",
        "asset_type",
        "trade_date",
        "market_report",
        "sentiment_report",
        "news_report",
        "fundamentals_report",
        "investment_plan",
        "trader_investment_plan",
        "final_trade_decision",
        "transport_security",
        "workflow_schema_version",
        "run_context_digest",
        "analysis_run_id",
        "tenant_id",
    )
    result = {key: final_state[key] for key in allowed if key in final_state}
    result["signal"] = str(signal)
    return result

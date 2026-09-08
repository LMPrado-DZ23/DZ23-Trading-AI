"""Lease-based analysis worker with tenant credential and run isolation."""

from __future__ import annotations

import json
import logging
import secrets
import threading
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.exc import DBAPIError, OperationalError

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.platform.jobs import ClaimedJob, JobError, JobService
from tradingagents.platform.llm_connections import (
    LlmConnectionError,
    LlmConnectionService,
    ResolvedLlmCredential,
)
from tradingagents.platform.usage import JobUsage, LlmUsageCallback, PricingCatalog

logger = logging.getLogger(__name__)


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

    def assert_active(self) -> None:
        if self.lease_lost.is_set():
            raise GraphExecutionFailure(JobUsage())


class GraphExecutionFailure(RuntimeError):
    def __init__(self, usage: JobUsage) -> None:
        super().__init__("graph execution failed")
        self.usage = usage


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    worker_id: str
    lease_seconds: int = 120

    def __post_init__(self) -> None:
        if not 3 <= len(self.worker_id) <= 120:
            raise ValueError("worker_id is invalid")
        if not 30 <= self.lease_seconds <= 900:
            raise ValueError("lease_seconds must be between 30 and 900")


class ProductionGraphExecutor:
    """Construct one isolated graph from immutable job inputs and BYOK secret."""

    def __init__(
        self,
        *,
        runtime_root: Path,
        pricing: PricingCatalog | None = None,
        request_timeout_seconds: float = 30.0,
    ) -> None:
        if runtime_root.is_symlink():
            raise RuntimeError("worker runtime root cannot be a symlink")
        self._root = runtime_root.resolve()
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._root.chmod(0o700)
        self._pricing = pricing or PricingCatalog()
        if not 1 <= request_timeout_seconds <= 300:
            raise ValueError("LLM request timeout is invalid")
        self._request_timeout_seconds = request_timeout_seconds

    def __call__(
        self,
        claim: ClaimedJob,
        credential: ResolvedLlmCredential,
        control: ExecutionControl,
    ) -> ExecutionOutcome:
        run_root = (self._root / str(claim.tenant_id) / str(claim.run_id)).resolve()
        if self._root not in run_root.parents:
            raise RuntimeError("run path escaped the worker root")
        run_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        run_root.chmod(0o700)
        config = deepcopy(DEFAULT_CONFIG)
        config.update(deepcopy(claim.command.config))
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
                "llm_request_timeout_seconds": self._request_timeout_seconds,
            }
        )
        usage = LlmUsageCallback(
            provider=claim.command.provider,
            model=claim.command.model,
            pricing=self._pricing,
            abort_event=control.lease_lost,
            max_total_tokens=claim.command.token_budget,
            max_cost_microusd=claim.command.cost_budget_microusd,
        )
        try:
            control.assert_active()
            graph = TradingAgentsGraph(
                selected_analysts=claim.command.selected_analysts,
                config=config,
                callbacks=[usage],
                isolated_config=True,
            )
            final_state, signal = graph.propagate(
                claim.command.symbol,
                claim.command.trade_date,
                asset_type=claim.command.asset_type,
                run_context=claim.run_context,
            )
        except Exception:
            raise GraphExecutionFailure(usage.snapshot()) from None
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
            }
        )
        return ExecutionOutcome(result=result, manifest=manifest, usage=usage_snapshot)


class WorkerRunner:
    def __init__(
        self,
        *,
        jobs: JobService,
        credentials: LlmConnectionService,
        executor: GraphExecutor,
        config: WorkerConfig,
    ) -> None:
        self._jobs = jobs
        self._credentials = credentials
        self._executor = executor
        self._config = config

    def run_once(self) -> bool:
        claim = self._jobs.claim_next(
            worker_id=self._config.worker_id,
            lease_seconds=self._config.lease_seconds,
        )
        if claim is None:
            return False
        stop = threading.Event()
        lease_lost = threading.Event()
        control = ExecutionControl(lease_lost=lease_lost)
        heartbeat = threading.Thread(
            target=self._heartbeat,
            args=(claim, stop, lease_lost),
            name=f"heartbeat-{claim.id}",
            daemon=True,
        )
        try:
            self._jobs.start(claim)
            heartbeat.start()
            credential = self._credentials.resolve_for_worker(
                tenant_id=claim.tenant_id,
                connection_id=claim.command.llm_connection_id,
                credential_version=claim.command.llm_credential_version,
                expected_provider=claim.command.provider,
                actor_user_id=claim.requested_by_user_id,
            )
            control.assert_active()
            outcome = self._executor(claim, credential, control)
            if lease_lost.is_set():
                raise GraphExecutionFailure(outcome.usage)
            if (
                len(json.dumps(outcome.result, default=str).encode()) > 2_000_000
                or len(json.dumps(outcome.manifest, default=str).encode()) > 2_000_000
            ):
                raise GraphExecutionFailure(outcome.usage)
            self._jobs.complete(
                claim,
                result=outcome.result,
                manifest=outcome.manifest,
                usage=outcome.usage,
            )
        except JobError as exc:
            if str(exc) != "JOB_CANCELLED":
                self._safe_fail(claim, "WORKER_JOB_STATE_FAILED")
        except LlmConnectionError:
            self._safe_fail(claim, "WORKER_CREDENTIAL_FAILED", retryable=False)
        except GraphExecutionFailure as exc:
            try:
                self._jobs.record_usage(claim, usage=exc.usage)
            except JobError:
                logger.error("could not persist worker usage", extra={"job_id": str(claim.id)})
            self._safe_fail(claim, "WORKER_EXECUTION_FAILED")
        except Exception:
            # Provider exceptions can echo request headers or URLs. Persist and
            # log only a fixed code; never the exception text or traceback.
            logger.error("analysis worker failed", extra={"job_id": str(claim.id)})
            self._safe_fail(claim, "WORKER_EXECUTION_FAILED")
        finally:
            stop.set()
            if heartbeat.is_alive():
                heartbeat.join(timeout=5)
        return True

    def _heartbeat(
        self,
        claim: ClaimedJob,
        stop: threading.Event,
        lease_lost: threading.Event,
    ) -> None:
        interval = max(10.0, self._config.lease_seconds / 3)
        consecutive_failures = 0
        while not stop.wait(interval):
            try:
                self._jobs.heartbeat(
                    claim,
                    lease_seconds=self._config.lease_seconds,
                )
                consecutive_failures = 0
            except Exception:
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    lease_lost.set()
                    return
                if stop.wait(float(consecutive_failures)):
                    return

    def _safe_fail(self, claim: ClaimedJob, code: str, *, retryable: bool = True) -> None:
        try:
            self._jobs.fail(claim, error_code=code, retryable=retryable)
        except Exception:
            logger.error("could not persist worker failure", extra={"job_id": str(claim.id)})

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
        while not stop_event.is_set():
            try:
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

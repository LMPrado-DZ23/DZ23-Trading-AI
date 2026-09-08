"""Trusted handlers that connect durable jobs to analysis and backtesting."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from tradingagents.backtest import (
    BacktestRunner,
    GraphDecisionProvider,
    load_yfinance_price_series,
)
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.runtime import build_runtime_config

from .jobs import JOB_SCHEMA_VERSION, JobRecord, JobSubmission


class TradingJobHandler:
    def __init__(self, runtime_config: dict[str, Any] | None = None) -> None:
        self.runtime_config = dict(runtime_config or {})

    def __call__(self, job: JobRecord) -> dict[str, Any]:
        submission = JobSubmission.model_validate(job.payload)
        base_config = build_runtime_config(self.runtime_config)
        job_results = (
            Path(base_config["results_dir"])
            / "jobs"
            / job.job_id
        )
        config = {
            **base_config,
            "results_dir": str(job_results),
            "memory_log_path": str(job_results / "memory" / "decisions.md"),
        }
        graph = TradingAgentsGraph(
            submission.selected_analysts,
            config=config,
        )
        if submission.kind == "analysis":
            _state, signal = graph.propagate(
                submission.ticker,
                submission.trade_date.isoformat(),
                asset_type=submission.asset_type,
                run_mode="analysis",
            )
            return {
                "schema": JOB_SCHEMA_VERSION,
                "kind": submission.kind,
                "signal": signal,
                "results_dir": str(job_results),
                "telemetry": graph.last_telemetry,
            }

        benchmark = submission.benchmark_ticker or config.get("benchmark_ticker") or "SPY"
        fetch_start = submission.start_date - timedelta(days=60)
        fetch_end = submission.end_date + timedelta(days=10)
        prices = load_yfinance_price_series(submission.ticker, fetch_start, fetch_end)
        benchmark_prices = load_yfinance_price_series(benchmark, fetch_start, fetch_end)
        result = BacktestRunner(
            GraphDecisionProvider(graph), runtime_config=config
        ).run(
            prices,
            benchmark_prices,
            start_date=submission.start_date,
            end_date=submission.end_date,
            asset_type=submission.asset_type,
        )
        json_path, markdown_path = result.write(job_results / "backtest")
        return {
            "schema": JOB_SCHEMA_VERSION,
            "kind": submission.kind,
            "metrics": result.metrics,
            "json_path": str(json_path),
            "markdown_path": str(markdown_path),
        }

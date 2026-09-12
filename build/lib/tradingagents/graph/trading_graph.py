# TradingAgents/graph/trading_graph.py

import json
import logging
import math
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yfinance as yf
from langgraph.prebuilt import ToolNode

# Import the abstract tool methods from agent_utils
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_global_news,
    get_income_statement,
    get_indicators,
    get_insider_transactions,
    get_macro_indicators,
    get_news,
    get_prediction_markets,
    get_stock_data,
    get_verified_market_snapshot,
    resolve_instrument_identity,
)
from tradingagents.agents.utils.memory import TradingMemoryLog
from tradingagents.dataflows.utils import safe_ticker_component
from tradingagents.llm_clients import create_llm_client
from tradingagents.manifest import build_run_manifest, write_run_manifest
from tradingagents.observability import TelemetryCallbackHandler
from tradingagents.portfolio import (
    MarketRiskSnapshot,
    PortfolioConstraints,
    PortfolioSnapshot,
    size_position,
    validate_sizing_inputs,
)
from tradingagents.reporting import write_report_tree
from tradingagents.runtime import (
    RunContext,
    build_runtime_config,
    freeze_config,
    thaw_config,
)

from .checkpointer import checkpoint_step, clear_checkpoint, get_checkpointer, thread_id
from .conditional_logic import ConditionalLogic
from .propagation import Propagator
from .reflection import Reflector
from .setup import GraphSetup
from .signal_processing import SignalProcessor

logger = logging.getLogger(__name__)


def _coerce_max_retries(value):
    """Validate an ``llm_max_retries`` value to a non-negative int.

    Accepts an int or a numeric string (env vars arrive as strings). Rejects
    booleans and negatives loudly so a misconfiguration fails at startup rather
    than silently disabling retries.
    """
    if isinstance(value, bool):
        raise ValueError(f"llm_max_retries must be an integer, not a boolean: {value!r}")
    try:
        n = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"llm_max_retries must be an integer, got {value!r}") from exc
    if n < 0:
        raise ValueError(f"llm_max_retries must be >= 0, got {n}")
    return n


def _coerce_max_tokens(value):
    """Validate a ``max_tokens`` value to a positive int (env vars are strings)."""
    if isinstance(value, bool):
        raise ValueError(f"max_tokens must be an integer, not a boolean: {value!r}")
    try:
        n = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"max_tokens must be an integer, got {value!r}") from exc
    if n <= 0:
        raise ValueError(f"max_tokens must be > 0, got {n}")
    return n


def _coerce_timeout_seconds(value):
    """Validate the positive finite timeout forwarded to every LLM SDK."""
    if isinstance(value, bool):
        raise ValueError(
            f"llm_timeout_seconds must be a number, not a boolean: {value!r}"
        )
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"llm_timeout_seconds must be a positive finite number, got {value!r}"
        ) from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(
            f"llm_timeout_seconds must be a positive finite number, got {value!r}"
        )
    return seconds


class TradingAgentsGraph:
    """Main class that orchestrates the trading agents framework."""

    def __init__(
        self,
        selected_analysts=("market", "social", "news", "fundamentals"),
        debug=False,
        config: dict[str, Any] = None,
        callbacks: list | None = None,
    ):
        """Initialize the trading agents graph and components.

        Args:
            selected_analysts: List of analyst types to include
            debug: Whether to run in debug mode
            config: Configuration dictionary. If None, uses default config
            callbacks: Optional list of callback handlers (e.g., for tracking LLM/tool stats)
        """
        self.debug = debug
        self.config = freeze_config(build_runtime_config(config))
        self.callbacks = list(callbacks or [])
        self.telemetry = None
        if self.config.get("telemetry_enabled", True):
            self.telemetry = TelemetryCallbackHandler(self.config.get("model_pricing", {}))
            self.callbacks.append(self.telemetry)
        self.last_telemetry = None

        # Create necessary directories
        os.makedirs(self.config["data_cache_dir"], exist_ok=True)
        os.makedirs(self.config["results_dir"], exist_ok=True)

        # Initialize LLMs with provider-specific thinking configuration
        llm_kwargs = self._get_provider_kwargs()

        # Add callbacks to kwargs if provided (passed to LLM constructor)
        if self.callbacks:
            llm_kwargs["callbacks"] = self.callbacks

        deep_client = create_llm_client(
            provider=self.config["llm_provider"],
            model=self.config["deep_think_llm"],
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )
        quick_client = create_llm_client(
            provider=self.config["llm_provider"],
            model=self.config["quick_think_llm"],
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )

        self.deep_thinking_llm = deep_client.get_llm()
        self.quick_thinking_llm = quick_client.get_llm()

        self.memory_log = TradingMemoryLog(self.config)

        # Create tool nodes
        self.tool_nodes = self._create_tool_nodes()

        # Initialize components
        self.conditional_logic = ConditionalLogic(
            max_debate_rounds=self.config["max_debate_rounds"],
            max_risk_discuss_rounds=self.config["max_risk_discuss_rounds"],
        )
        self.graph_setup = GraphSetup(
            self.quick_thinking_llm,
            self.deep_thinking_llm,
            self.tool_nodes,
            self.conditional_logic,
            runtime_config=thaw_config(self.config),
        )

        self.propagator = Propagator(
            max_recur_limit=self.config.get("max_recur_limit", 100),
        )
        self.reflector = Reflector(self.quick_thinking_llm)
        self.signal_processor = SignalProcessor(self.quick_thinking_llm)

        # State tracking
        self.curr_state = None
        self.ticker = None
        self.log_states_dict = {}  # date to full state dict

        # Graph-shape-affecting run choices, kept for the checkpoint signature.
        self.selected_analysts = tuple(selected_analysts)

        # Set up the graph: keep the workflow for recompilation with a checkpointer.
        self.workflow = self.graph_setup.setup_graph(selected_analysts)
        self.graph = self.workflow.compile()
        self._checkpointer_ctx = None
        self._resuming = False
        self._last_run_context = None

    def begin_observability(self, run_context: RunContext) -> None:
        """Start one metadata-only telemetry session for an execution path."""
        if self.telemetry is not None:
            self.telemetry.begin_run(run_context)

    def finish_observability(
        self,
        run_context: RunContext,
        status: str,
        error: BaseException | None = None,
    ) -> dict[str, Any] | None:
        """Persist telemetry without exposing prompts, tool payloads or outputs."""
        if self.telemetry is None:
            return None
        destination = (
            Path(self.config["results_dir"])
            / safe_ticker_component(run_context.ticker)
            / "telemetry"
            / run_context.trade_date.isoformat()
        )
        self.last_telemetry = self.telemetry.finish_run(status, destination, error)
        return self.last_telemetry

    def _get_provider_kwargs(self) -> dict[str, Any]:
        """Get provider-specific kwargs for LLM client creation."""
        kwargs = {}
        provider = self.config.get("llm_provider", "").lower()

        if provider == "google":
            thinking_level = self.config.get("google_thinking_level")
            if thinking_level:
                kwargs["thinking_level"] = thinking_level

        elif provider == "openai":
            reasoning_effort = self.config.get("openai_reasoning_effort")
            if reasoning_effort:
                kwargs["reasoning_effort"] = reasoning_effort

        elif provider == "anthropic":
            effort = self.config.get("anthropic_effort")
            if effort:
                kwargs["effort"] = effort

        # Sampling temperature is cross-provider: forward it whenever set.
        # float() here so a value coming from a TRADINGAGENTS_TEMPERATURE env
        # string ("0.2") works the same as a programmatic float.
        temperature = self.config.get("temperature")
        if temperature is not None and temperature != "":
            kwargs["temperature"] = float(temperature)

        # SDK retry budget is cross-provider. Forward it only when explicitly set
        # so each provider keeps its own default (usually 2) otherwise (#1091).
        max_retries = self.config.get("llm_max_retries")
        if max_retries is not None and max_retries != "":
            kwargs["max_retries"] = _coerce_max_retries(max_retries)

        timeout_seconds = self.config.get("llm_timeout_seconds")
        if timeout_seconds is not None and timeout_seconds != "":
            kwargs["timeout"] = _coerce_timeout_seconds(timeout_seconds)

        # Output-token cap is cross-provider, but Gemini names it
        # ``max_output_tokens``; forward under the right key when set (#1204).
        max_tokens = self.config.get("max_tokens")
        if max_tokens is not None and max_tokens != "":
            key = "max_output_tokens" if provider == "google" else "max_tokens"
            kwargs[key] = _coerce_max_tokens(max_tokens)

        return kwargs

    def _create_tool_nodes(self) -> dict[str, ToolNode]:
        """Create tool nodes for different data sources using abstract methods."""
        return {
            "market": ToolNode(
                [
                    # Core stock data tools
                    get_stock_data,
                    # Technical indicators
                    get_indicators,
                    # Deterministic verification snapshot (bound to the analyst
                    # LLM and required by its prompt; must be executable here or
                    # the call fails and the model reports it "unavailable").
                    get_verified_market_snapshot,
                ]
            ),
            "social": ToolNode(
                [
                    # News tools for social media analysis
                    get_news,
                ]
            ),
            "news": ToolNode(
                [
                    # News and insider information
                    get_news,
                    get_global_news,
                    get_insider_transactions,
                    get_macro_indicators,
                    get_prediction_markets,
                ]
            ),
            "fundamentals": ToolNode(
                [
                    # Fundamental analysis tools
                    get_fundamentals,
                    get_balance_sheet,
                    get_cashflow,
                    get_income_statement,
                ]
            ),
        }

    def _resolve_benchmark(self, ticker: str) -> str:
        """Pick the benchmark ticker for alpha calculation against ``ticker``.

        ``config["benchmark_ticker"]`` overrides everything when set; otherwise
        the suffix map matches the ticker's exchange suffix (e.g. ``.T`` for
        Tokyo). US-listed tickers without a dotted suffix fall through to the
        empty-suffix entry (SPY by default). Unrecognised suffixes (including
        US tickers with dots like ``BRK.B``) also fall back to the empty-suffix
        entry, which is the right default because the alpha calculation works
        in USD.
        """
        explicit = self.config.get("benchmark_ticker")
        if explicit:
            return explicit
        benchmark_map = self.config.get("benchmark_map", {})
        ticker_upper = ticker.upper()
        for suffix, benchmark in benchmark_map.items():
            if suffix and ticker_upper.endswith(suffix.upper()):
                return benchmark
        return benchmark_map.get("", "SPY")

    def _fetch_returns(
        self, ticker: str, trade_date: str, holding_days: int = 5,
        benchmark: str = "SPY",
    ) -> tuple[float | None, float | None, int | None, str | None]:
        """Fetch raw and alpha return for ticker over holding_days from trade_date.

        ``benchmark`` is the index used as the alpha baseline (resolved by the
        caller via ``_resolve_benchmark``). Returns ``(raw_return, alpha_return,
        holding_days, resolution_date)`` — where ``resolution_date`` is the date
        of the last price bar used, i.e. when the outcome became known (#1251) —
        or ``(None, None, None, None)`` when the outcome cannot be settled yet:
        the full holding window has not traded (#1169), or the symbol is delisted
        or unreachable.
        """
        from tradingagents.dataflows.symbol_utils import normalize_symbol

        try:
            start = datetime.strptime(trade_date, "%Y-%m-%d")
            end = start + timedelta(days=holding_days + 7)  # buffer for weekends/holidays
            end_str = end.strftime("%Y-%m-%d")

            # Normalize so the realized-return lookup hits the same instrument
            # the analysis priced (e.g. XAUUSD -> GC=F) (#984). The benchmark is
            # already a canonical Yahoo symbol from ``_resolve_benchmark``.
            stock = yf.Ticker(normalize_symbol(ticker)).history(start=trade_date, end=end_str)
            bench = yf.Ticker(benchmark).history(start=trade_date, end=end_str)

            # Require the full holding window in both series. A rerun before it
            # has traded leaves the entry pending to retry next run, rather than
            # settling on a premature partial return (#1169).
            if len(stock) <= holding_days or len(bench) <= holding_days:
                return None, None, None, None

            raw = float(
                (stock["Close"].iloc[holding_days] - stock["Close"].iloc[0])
                / stock["Close"].iloc[0]
            )
            bench_ret = float(
                (bench["Close"].iloc[holding_days] - bench["Close"].iloc[0])
                / bench["Close"].iloc[0]
            )
            alpha = raw - bench_ret
            # The date of the last price bar used is when this outcome became
            # known — the point-in-time cutoff for injecting the lesson (#1251).
            resolution_date = stock.index[holding_days].strftime("%Y-%m-%d")
            return raw, alpha, holding_days, resolution_date
        except Exception as e:
            logger.warning(
                "Could not resolve outcome for %s on %s vs %s (will retry next run): %s",
                ticker, trade_date, benchmark, e,
            )
            return None, None, None, None

    def _resolve_pending_entries(self, ticker: str) -> None:
        """Resolve pending log entries for ticker at the start of a new run.

        Fetches returns for each same-ticker pending entry, generates reflections,
        then writes all updates in a single atomic batch write to avoid redundant I/O.
        Skips entries whose price data is not yet available (too recent or delisted).

        Trade-off: only same-ticker entries are resolved per run.  Entries for
        other tickers accumulate until that ticker is run again.
        """
        pending = [e for e in self.memory_log.get_pending_entries() if e["ticker"] == ticker]
        if not pending:
            return

        benchmark = self._resolve_benchmark(ticker)
        updates = []
        for entry in pending:
            raw, alpha, days, resolution_date = self._fetch_returns(
                ticker, entry["date"], benchmark=benchmark,
            )
            if raw is None:
                continue  # price not available yet — try again next run
            reflection = self.reflector.reflect_on_final_decision(
                final_decision=entry.get("decision", ""),
                raw_return=raw,
                alpha_return=alpha,
                benchmark_name=benchmark,
            )
            updates.append({
                "ticker": ticker,
                "trade_date": entry["date"],
                "raw_return": raw,
                "alpha_return": alpha,
                "holding_days": days,
                "reflection": reflection,
                "resolution_date": resolution_date,
            })

        if updates:
            self.memory_log.batch_update_with_outcomes(updates)

    def resolve_instrument_context(self, ticker: str, asset_type: str = "stock") -> str:
        """Resolve ticker identity once and return the full instrument context.

        Deterministic yfinance lookup (cached, fail-open) injected into a
        context string so every agent anchors to the real company instead of
        hallucinating one from the price chart (#814). Both the propagate()
        path and the CLI call this so the resolved identity reaches the whole
        graph regardless of entry point.
        """
        identity = resolve_instrument_identity(ticker)
        return build_instrument_context(ticker, asset_type, identity)

    def _memory_as_of(self, trade_date) -> str | None:
        """Point-in-time cutoff for past-context lessons (#1251).

        A historical/backtest run (trade date before today) filters lessons to
        those already resolved by the trade date. A current-date run returns
        None, disabling the filter so live behavior and pre-migration entries
        (which have no stored resolution date) are unaffected.
        """
        td = str(trade_date)
        return td if td < datetime.now().strftime("%Y-%m-%d") else None

    def _run_signature(
        self, asset_type: str, run_context: RunContext | None = None
    ) -> str:
        """Graph-shape inputs that must invalidate a checkpoint if changed.

        Keyed into the checkpoint thread ID so a resume under a different analyst
        selection, debate/risk depth, or asset mode starts fresh instead of
        silently continuing the previous graph (#1089).
        """
        context = run_context or getattr(self, "_last_run_context", None) or RunContext.create(
            getattr(self, "ticker", None) or "UNKNOWN",
            datetime.now().strftime("%Y-%m-%d"),
            asset_type, self.config,
        )
        return "|".join([
            f"schema={context.workflow_schema_version}",
            f"config={context.config_sha256}",
            f"cutoff={context.decision_at.isoformat()}",
            f"mode={context.run_mode}",
            "analysts=" + ",".join(self.selected_analysts),
            f"debate={self.config['max_debate_rounds']}",
            f"risk={self.config['max_risk_discuss_rounds']}",
            f"asset={asset_type}",
        ])

    def propagate(
        self,
        company_name,
        trade_date,
        asset_type: str = "stock",
        *,
        decision_at: datetime | None = None,
        run_mode: str = "analysis",
        portfolio_snapshot: PortfolioSnapshot | None = None,
        market_risk: MarketRiskSnapshot | None = None,
        portfolio_constraints: PortfolioConstraints | None = None,
        statistical_context: str = "",
    ):
        """Run the trading agents graph for a company on a specific date.

        ``asset_type`` selects between the stock pipeline (default) and the
        crypto pipeline (``"crypto"``) shipped in #567 — the CLI auto-detects
        from the ticker; programmatic callers pass it explicitly. When
        ``checkpoint_enabled`` is set in config, the graph is recompiled with
        a per-ticker SqliteSaver so a crashed run can resume from the last
        successful node on a subsequent invocation with the same ticker+date.

        Returns ``(final_state, signal)`` where ``signal`` is one of the 5-tier
        ratings (Buy / Overweight / Hold / Underweight / Sell) or ``"REVIEW"``
        when the decision had no parseable rating (#1170); guard with
        ``tradingagents.agents.utils.rating.is_review`` before mapping it to the
        PortfolioRating enum.
        """
        self.ticker = company_name
        run_context = RunContext.create(
            company_name, trade_date, asset_type, self.config,
            decision_at=decision_at,
            run_mode=run_mode,
        )
        if (portfolio_snapshot is None) != (market_risk is None):
            raise ValueError("portfolio_snapshot and market_risk must be supplied together")
        sizing_policy = portfolio_constraints or PortfolioConstraints.from_mapping(
            thaw_config(self.config).get("portfolio_constraints")
        )
        if portfolio_snapshot is not None and market_risk is not None:
            validate_sizing_inputs(
                run_context, portfolio_snapshot, market_risk, sizing_policy
            )

        # Resolve any pending memory-log entries for this ticker before the pipeline runs.
        self._resolve_pending_entries(company_name)
        self.begin_observability(run_context)
        try:
            with self.checkpoint_scope(
                company_name, trade_date, asset_type, run_context=run_context
            ) as thread_id_value:
                result = self._run_graph(
                    company_name, trade_date, asset_type=asset_type,
                    checkpoint_thread_id=thread_id_value,
                    run_context=run_context,
                    portfolio_snapshot=portfolio_snapshot,
                    market_risk=market_risk,
                    portfolio_constraints=sizing_policy,
                    statistical_context=statistical_context,
                )
        except BaseException as exc:
            status = "cancelled" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "failed"
            try:
                self.finish_observability(run_context, status, exc)
            except Exception:
                logger.exception("Failed to persist telemetry for failed run")
            raise
        self.finish_observability(run_context, "succeeded")
        return result

    def begin_checkpoint(
        self,
        company_name,
        trade_date,
        asset_type: str = "stock",
        run_context: RunContext | None = None,
    ) -> str | None:
        """Recompile the graph with a per-ticker checkpointer and return the
        ``thread_id`` to inject into the stream/invoke ``config`` (or ``None``
        when checkpointing is disabled).

        Pair every call with :meth:`end_checkpoint` in a ``finally``. Both
        ``propagate`` (via :meth:`checkpoint_scope`) and the CLI stream path use
        this so ``--checkpoint`` actually resumes (#1249); previously the setup
        lived only inside ``propagate`` and the CLI streamed the checkpointer-less
        graph, making the flag a no-op.
        """
        self._resuming = False
        if not self.config.get("checkpoint_enabled"):
            return None
        context = run_context or RunContext.create(
            company_name, trade_date, asset_type, self.config
        )
        self._last_run_context = context
        signature = self._run_signature(asset_type, context)
        self._checkpointer_ctx = get_checkpointer(self.config["data_cache_dir"], company_name)
        saver = self._checkpointer_ctx.__enter__()
        self.graph = self.workflow.compile(checkpointer=saver)

        step = checkpoint_step(
            self.config["data_cache_dir"], company_name, str(trade_date), signature
        )
        self._resuming = step is not None
        if step is not None:
            logger.info("Resuming from step %d for %s on %s", step, company_name, trade_date)
        else:
            logger.info("Starting fresh for %s on %s", company_name, trade_date)
        return thread_id(company_name, str(trade_date), signature)

    def checkpoint_input(self, init_state):
        """The value to stream/invoke: ``None`` to resume an existing checkpoint,
        else the initial state for a fresh run.

        LangGraph resumes an interrupted thread when invoked with ``None``;
        re-passing the initial state instead appends it through the message
        reducer, duplicating messages in the resumed state (#1249).
        """
        return None if self._resuming else init_state

    def end_checkpoint(self):
        """Restore the plain uncheckpointed graph after a checkpointed run."""
        if self._checkpointer_ctx is not None:
            self._checkpointer_ctx.__exit__(None, None, None)
            self._checkpointer_ctx = None
            self.graph = self.workflow.compile()
        self._resuming = False

    @contextmanager
    def checkpoint_scope(
        self, company_name, trade_date, asset_type: str = "stock",
        run_context: RunContext | None = None,
    ):
        """Context-manager form of begin/end_checkpoint for the propagate path."""
        try:
            yield self.begin_checkpoint(
                company_name, trade_date, asset_type, run_context=run_context
            )
        finally:
            self.end_checkpoint()

    def clear_checkpoint_on_success(
        self,
        company_name,
        trade_date,
        asset_type: str = "stock",
        run_context: RunContext | None = None,
    ):
        """Drop a completed run's checkpoint so a later run starts fresh (#1249)."""
        if self.config.get("checkpoint_enabled"):
            clear_checkpoint(
                self.config["data_cache_dir"], company_name, str(trade_date),
                self._run_signature(asset_type, run_context),
            )

    def save_reports(self, final_state, ticker, save_path=None) -> Path:
        """Write the markdown report tree for a completed run, like the CLI does.

        Programmatic callers get the same on-disk reports the CLI produces. Pass
        an explicit ``save_path`` or let it default under ``results_dir``.
        """
        if save_path is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            save_path = (
                Path(self.config["results_dir"])
                / "reports"
                / f"{safe_ticker_component(ticker)}_{stamp}"
            )
        return write_report_tree(final_state, ticker, save_path)

    def _run_graph(self, company_name, trade_date, asset_type: str = "stock",
                   checkpoint_thread_id: str | None = None,
                   run_context: RunContext | None = None,
                   portfolio_snapshot: PortfolioSnapshot | None = None,
                   market_risk: MarketRiskSnapshot | None = None,
                   portfolio_constraints: PortfolioConstraints | None = None,
                   statistical_context: str = ""):
        """Execute the graph and write the resulting state to disk and memory log."""
        # Initialize state — inject memory log context for PM and the
        # deterministically resolved instrument identity for all agents. On a
        # historical run, gate lessons to those whose outcome was known by the
        # trade date so a backtest can't learn from the future (#1251).
        context = run_context or RunContext.create(
            company_name, trade_date, asset_type, self.config
        )
        past_context = self.memory_log.get_past_context(
            company_name, as_of=self._memory_as_of(trade_date)
        )
        if statistical_context:
            past_context = "\n\n".join(
                part for part in (past_context, statistical_context) if part
            )
        instrument_context = self.resolve_instrument_context(company_name, asset_type)
        init_agent_state = self.propagator.create_initial_state(
            company_name,
            trade_date,
            asset_type=asset_type,
            past_context=past_context,
            instrument_context=instrument_context,
            config=self.config,
            run_context=context,
            output_language=self.config.get("output_language", "English"),
        )
        args = self.propagator.get_graph_args(
            callbacks=self.callbacks,
            runtime_config=thaw_config(self.config),
            run_context=context,
        )

        # Inject the checkpoint thread_id (from checkpoint_scope) so the same
        # ticker+date+graph-shape resumes; a different one starts fresh (#1089).
        if checkpoint_thread_id is not None:
            args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = checkpoint_thread_id

        # None resumes an existing checkpoint; init_agent_state starts fresh (#1249).
        graph_input = self.checkpoint_input(init_agent_state)
        if self.debug:
            trace = []
            last_printed = None
            for chunk in self.graph.stream(graph_input, **args):
                if chunk["messages"]:
                    msg = chunk["messages"][-1]
                    # Nodes after the trader don't append to messages, so the
                    # same trailing message repeats across chunks. Print it only
                    # when it changes (#1027); the trace/state merge is unchanged.
                    signature = (type(msg).__name__, getattr(msg, "content", None))
                    if signature != last_printed:
                        msg.pretty_print()
                        last_printed = signature
                    trace.append(chunk)
            # Streamed chunks are per-node deltas. Merge them so the returned
            # state matches what graph.invoke() yields in the non-debug path.
            final_state = {}
            for chunk in trace:
                final_state.update(chunk)
        else:
            final_state = self.graph.invoke(graph_input, **args)

        signal = self.process_signal(final_state["final_trade_decision"])
        if portfolio_snapshot is not None and market_risk is not None:
            plan = size_position(
                signal,
                context,
                portfolio_snapshot,
                market_risk,
                portfolio_constraints,
            )
            final_state["portfolio_plan"] = plan.to_dict()

        # Use the concrete implementation so lightweight MagicMock-based
        # compatibility harnesses still exercise the real persistence path.
        TradingAgentsGraph.finalize_state(self, final_state, context)
        return final_state, signal

    def finalize_state(self, final_state: dict[str, Any], context: RunContext) -> None:
        """Persist the common successful-run artifacts for API and CLI paths."""
        # Transport-security mark travels in AgentState from the initial state;
        # re-stamp defensively for non-standard callers that built their own input.
        from tradingagents.gates import MARK_KEY, transport_security_mark

        final_state.setdefault(
            MARK_KEY,
            transport_security_mark(
                self.config,
                run_ticker=context.ticker,
                run_trade_date=context.trade_date.isoformat(),
            ),
        )

        self.ticker = context.ticker
        self.curr_state = final_state
        self._log_state(context.trade_date.isoformat(), final_state)
        self._write_run_manifest(context)
        self.memory_log.store_decision(
            ticker=context.ticker,
            trade_date=context.trade_date.isoformat(),
            final_trade_decision=final_state["final_trade_decision"],
        )
        self.clear_checkpoint_on_success(
            context.ticker,
            context.trade_date.isoformat(),
            context.asset_type,
            run_context=context,
        )

    def _log_state(self, trade_date, final_state):
        """Log the final state to a JSON file."""
        self.log_states_dict[str(trade_date)] = {
            "company_of_interest": final_state["company_of_interest"],
            "trade_date": final_state["trade_date"],
            "market_report": final_state["market_report"],
            "sentiment_report": final_state["sentiment_report"],
            "news_report": final_state["news_report"],
            "fundamentals_report": final_state["fundamentals_report"],
            "investment_debate_state": {
                "bull_history": final_state["investment_debate_state"]["bull_history"],
                "bear_history": final_state["investment_debate_state"]["bear_history"],
                "history": final_state["investment_debate_state"]["history"],
                "current_response": final_state["investment_debate_state"][
                    "current_response"
                ],
                "judge_decision": final_state["investment_debate_state"][
                    "judge_decision"
                ],
            },
            "trader_investment_decision": final_state["trader_investment_plan"],
            "risk_debate_state": {
                "aggressive_history": final_state["risk_debate_state"]["aggressive_history"],
                "conservative_history": final_state["risk_debate_state"]["conservative_history"],
                "neutral_history": final_state["risk_debate_state"]["neutral_history"],
                "history": final_state["risk_debate_state"]["history"],
                "judge_decision": final_state["risk_debate_state"]["judge_decision"],
            },
            "investment_plan": final_state["investment_plan"],
            "final_trade_decision": final_state["final_trade_decision"],
            "portfolio_plan": final_state.get("portfolio_plan"),
            "transport_security": final_state.get("transport_security"),
            "run_context": final_state.get("run_context"),
            "workflow_schema_version": final_state.get("workflow_schema_version"),
        }

        # Save to file. Reject ticker values that would escape the
        # results directory when joined as a path component.
        safe_ticker = safe_ticker_component(self.ticker)
        directory = Path(self.config["results_dir"]) / safe_ticker / "TradingAgentsStrategy_logs"
        directory.mkdir(parents=True, exist_ok=True)

        log_path = directory / f"full_states_log_{trade_date}.json"
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(self.log_states_dict[str(trade_date)], f, indent=4)

    def _write_run_manifest(self, run_context: RunContext) -> Path:
        """Persist the exact source/dependency/config identity for this run."""
        safe_ticker = safe_ticker_component(run_context.ticker)
        path = (
            Path(self.config["results_dir"]) / safe_ticker /
            "run_manifests" / f"{run_context.trade_date.isoformat()}.json"
        )
        manifest = build_run_manifest(
            self.config["project_dir"], run_context, self.selected_analysts
        )
        return write_run_manifest(path, manifest)

    def process_signal(self, full_signal):
        """Process a signal to extract the core decision."""
        return self.signal_processor.process_signal(full_signal)

# M5 Backtest and Evaluation Design

## Purpose and boundary

M5 must answer whether a strategy was mechanically evaluated without known
look-ahead and after declared costs. It does not prove future profitability and
does not authorize broker execution. Backtest state is isolated from the M4
capital ledger and M6 broker reservations.

The authoritative pipeline is:

`frozen PIT dataset -> derived evidence -> immutable decisions -> simulated
next-open execution -> double-entry simulation ledger -> metrics -> OOS report`.

LLMs may produce a rating and explanation. They never produce executable
quantity, fill price, fee, target weight or portfolio accounting entries.

## Non-negotiable time semantics

- `signal_session_close` is the exchange close of session D.
- `bar_available_at` is when the completed bar is usable. The current daily-bar
  policy is `signal_session_close + 15 minutes`.
- `decision_at` is not earlier than `bar_available_at`.
- An order decided from D can fill only at the raw open of the next eligible
  exchange session, strictly after `decision_at`.
- The D+1 open may price the fill. D+1 high, low, close and full-day volume may
  not influence that fill. V1 liquidity is bounded by information available at
  D; auction or intraday liquidity requires a separately timestamped dataset.
- Every observation used by a decision satisfies
  `available_at <= decision_at`. Missing availability is exclusion, not an
  invitation to substitute fetch time.

## M5.0 — frozen dataset authority

Create an isolated `tradingagents.platform.backtest` package. Immutable,
extra-forbidden contracts describe instruments, raw daily bars, corporate
actions, FX observations, schedule snapshots and dataset manifests.

A dataset is ingested once, canonicalized and sealed by SHA-256. Replay reads
only the sealed bytes and verifies the manifest before parsing. It never calls a
vendor and never consults wall-clock time. The digest binds tenant, instrument,
source vintage, schedule, security-master identity, rows, corporate actions,
FX observations and declared limitations.

Security master records listing, delisting, venue, calendar, asset type, base
and quote currency. Missing delisting/cash-consideration evidence makes an
affected run `INCOMPLETE_DATA`; it must not silently discard failed securities.

Yahoo and current Alpha Vantage adapters may seed a limited single-instrument
dataset, but they cannot support a claim of survivorship-bias-free universe or
vintage-correct corporate actions. That limitation is machine-readable.

## M5.1 — candlestick evidence

The detector is pure Python, deterministic and versioned. It may recognize:

- Doji and Marubozu;
- bullish/bearish Engulfing;
- Hammer/Hanging Man and Inverted Hammer/Shooting Star;
- Morning Star and Evening Star.

Output contains only pattern identity, constituent bars, geometric ratios,
prior trend, availability and parent digests. It contains no trade action,
rating, expected return, profit confidence, target weight or quantity.

Trend uses only bars before the first constituent candle. A split in the trend
or constituent window breaks context until sufficient post-split history
exists. Derived output is serialized canonically and enters prompts only as
wrapped `UntrustedEvidence` in a data/user message.

External open-source repositories were design references only. V1 copies no
code and adds no TA-Lib, Rust, ML or Pine dependency.

## M5.2 — accounting and execution simulation

Use `Decimal` throughout. The simulation ledger is double-entry and enforces
non-negative cash and no short selling in V1. Event order per session is:

1. apply known effective corporate actions;
2. value the pre-decision portfolio;
3. consume immutable decisions and derive target changes deterministically;
4. queue orders;
5. at the next session, fill from raw open under declared liquidity policy;
6. record adverse slippage, fees, taxes and PIT FX separately;
7. value the close and append an equity point.

Splits adjust quantity and cost basis before the effective open. Dividends
become cash only on a verified pay date. Delisting requires a versioned
consideration policy and evidence. Missing required data fails closed.

## M5.3 — graph and rating adapter

One parent backtest run creates child analysis jobs per session and symbol.
Each child keeps the existing isolated worker, budget, run context, manifest
and evidence journal. Signals can be generated concurrently, but portfolio
events are applied in a stable chronological order.

The versioned rating policy is deterministic: `REVIEW` never trades; Buy,
Overweight, Hold, Underweight and Sell map to bounded target changes under the
selected M4 profile. The map is an explicit product policy and receives an
independent review before use.

Generated LLM signals are captured immutably. A replay may be mechanically
deterministic while regeneration is not; reports expose
`signal_replayable` and `decision_regenerable` separately.

## M5.4 — metrics and walk-forward

Reports include net TWR, total return, CAGR, annualized volatility, Sharpe,
Sortino, max drawdown/duration/recovery, Calmar, hit rate, profit factor,
turnover, gross/net exposure, cost and slippage attribution, coverage,
`REVIEW`/exclusion rate, alpha, beta, tracking error and information ratio.

The risk-free assumption, annualization convention and benchmark dataset are
explicit and digested. Benchmarks use the same calendar, base currency and
declared costs.

Walk-forward folds have disjoint train/validation/test periods, purge labels
whose horizon crosses a boundary and embargo at least the maximum holding
horizon. Parameters freeze before the out-of-sample interval; aggregate claims
use OOS results only. Multiple hypotheses require a declared correction and
confidence interval.

## M5.5 — durable product surface

Persist tenant-scoped run/spec/dataset/fold/session/decision/order/fill/equity
records instead of embedding curves in `AnalysisJob.result`. A coordinator
worker owns a lease and fencing token, while child analyses remain on the
existing worker path. API operations are create, status, cancel, result and
export. Exports bind every digest needed for replay.

The web UI describes limitations in plain Portuguese and must never present a
candlestick or backtest as a promise of profit.

## Required gates

- **M5.0 PIT gate:** canonical digest, immutable replay, calendar boundaries,
  tamper/path/symlink checks, availability and survivorship limitations.
- **M5.2 accounting gate:** golden ledgers, conservation, raw next-open,
  splits/dividends/delisting, fees/slippage/FX and no D+1 leakage.
- **M5.5 final gate:** PostgreSQL RLS/IDOR, lease/retry/cancel idempotency,
  child-budget aggregation, exports and browser/API journey.

Metamorphic leakage tests append or mutate future bars and require every prior
feature, decision and fill to remain byte-identical. Reordering or duplicating
input rows is rejected. A synthetic fixture proves mechanics only; performance
claims require frozen real data and remain subject to dataset limitations.

## M5.0 quantitative-claim boundary

M5.0 deliberately emits `coverage_status="unverified"`, requires the complete
canonical limitation set, and keeps `quantitative_claims_eligible=False`.
Revision continuity is proven only inside one frozen snapshot. No Sharpe,
alpha, return, ranking, model comparison, or other performance claim may be
published until M5.5 provides an authoritative append-only lineage between
dataset manifests and independently attested coverage for availability,
survivorship, delistings, corporate actions, FX, and vendor adjustments.

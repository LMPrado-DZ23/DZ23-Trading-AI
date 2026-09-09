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

The pure detector is private to the frozen-data authority. The supported
entrypoint is `FrozenDatasetRepository.derive_candlestick_bundle`, which first
reloads and verifies dataset ID, tenant ID and the externally trusted manifest
digest. A caller-constructed `EligibleMarketView` is never an accepted public
derivation boundary.

### M5.1 detector policy v1

The policy is fixed in code and content-addressed; callers cannot override its
thresholds.  All comparisons use exact `Decimal` cross-products.  Ratios are
display-only parts-per-million values rounded half-even in a local decimal
context.  A candle is bullish when `close > open`, bearish when `close < open`,
and flat otherwise.  Body, upper wick and lower wick are measured against the
raw high-low range.

- Doji: body is at most 10% of range.
- Marubozu: directional body is at least 90% of range and each wick is at most
  5% of range.
- Hammer-family geometry: non-zero body, long wick at least twice the body and
  opposite wick at most 10% of range.  A down prior trend names Hammer or
  Inverted Hammer; an up prior trend names Hanging Man or Shooting Star.
- Engulfing: two opposite directional real bodies, with the current body
  containing the previous body and at least one boundary strictly exceeding
  it.  Bullish requires a down prior trend and bearish requires an up prior
  trend.
- Morning/Evening Star: three directional candles; the middle body is at most
  30% of the first body, and the third candle closes through the exact midpoint
  of the first body.  Morning requires down/bearish/bullish and Evening requires
  up/bullish/bearish.  V1 deliberately does not require a price gap so it is
  usable on continuous crypto markets.

Overlaps are retained rather than hidden (for example, a small-body Hammer may
also be a Doji) and are serialized in the fixed enum order above.  A flat body
can be Doji but never Marubozu or a hammer-family pattern.  The M5.0
`corporate_action_vintage_unverified` limitation remains attached to the
provenance bundle and keeps quantitative claims disabled; it does not rewrite
raw candle geometry into an unearned conclusion.

Prior trend uses exactly the three completed bars strictly before the first
constituent: strictly rising closes are `up`, strictly falling closes are
`down`, otherwise `flat`.  Full evaluation therefore requires the last six
eligible bars.  The frozen dataset ordinal of every eligible bar is bound by a
new, versioned M5.1 provenance digest together with the immutable content hash;
the already-approved M5.0 eligible-view digest is not rewritten.  A
missing/excluded ordinal in that six-bar window is `discontinuous_history`;
replay never consults the currently installed market calendar.  A split
strictly after the first bar in the six-bar window and on or before the decision
session is a `split_barrier`; evaluation resumes only after six post-split bars
exist.  A zero-range constituent window is `zero_range`. Non-evaluated statuses
emit no matches.

The deterministic feature set binds only decision-eligible content and is
therefore byte-identical when a later frozen snapshot merely appends future
bars.  A separate provenance bundle binds tenant, dataset, manifest and full
eligible-view digests.  Only the feature set enters an LLM prompt, wrapped as
derived `UntrustedEvidence`; full lineage remains in the audit record.  This
separation prevents an irrelevant future snapshot identifier from changing a
historical prompt while retaining complete custody evidence.

Visible split history is represented in output by one bounded aggregate digest
plus the latest effective date, not by an unbounded list of action digests. The
aggregate still binds every eligible split record while keeping the prompt JSON
within the evidence-size contract.

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

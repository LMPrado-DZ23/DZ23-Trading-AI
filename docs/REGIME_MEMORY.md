# P6 — Regime-aware statistical memory

`tradingagents.memory` is a second, structured memory channel for evaluated
outcomes. Unlike the legacy reflection log, it stores no model prose, prompts
or external text.

Each immutable observation binds:

- ticker, five-tier rating and deterministic market regime;
- timezone-aware decision and resolution timestamps;
- raw total return, benchmark-relative alpha and holding-session count;
- a content hash that detects modified records.

Queries require an `as_of` cutoff and exclude every outcome whose
`resolution_at` is later. Context is emitted only when a rating/regime group has
the configured minimum sample size. It reports sample count, mean alpha, alpha
hit rate, sample deviation and a 95% confidence interval; sparse anecdotes are
not promoted as learned evidence.

The classifier consumes only the supplied close prefix. High-volatility takes
precedence, followed by bull/bear trend and low-volatility/neutral regimes.
Backtests resolve observations online at the configured horizon, immediately
before a later close-time decision can query them. Total returns account for
splits and dividends in both asset and benchmark.

The JSONL store is append-only in semantics, atomically replaced on update,
idempotent by observation identity, size-bounded and strict about schema and
hash integrity. Conflicting outcomes for the same evaluation fail closed.

`DecisionRequest.memory_context` carries only the qualified statistical text.
`GraphDecisionProvider` forwards it as derived past context, so the existing
untrusted-evidence envelope protects the Portfolio Manager prompt.

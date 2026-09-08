# Gate B — Point-in-time and runtime contract

Status: implemented; promotion requires a green full suite and independent review.

## Runtime boundary

Every run creates one frozen `RunContext`. It binds ticker, trade date, asset
type, logical decision timestamp, market timezone, run mode, effective-config
digest and workflow schema. LangChain tools receive the context and an isolated
config through `RunnableConfig`; neither is exposed in the model's tool schema.

Tool calls fail when the model tries to use another ticker or a date after the
run cutoff. Checkpoint IDs include the workflow schema, config digest, cutoff and
run mode, so an incompatible run cannot resume an older state.

The default equity cutoff is 16:00 America/New_York. Other exchanges must set
`market_timezone` and `market_session_close`. Crypto defaults to 23:59:59 UTC.
Backtests set `run_mode="backtest"`; this forces historical rules even when the
requested date is in the future relative to the host clock.

## Data admissibility

- Fundamentals: a fiscal period end is never treated as publication time.
  Historical Alpha Vantage rows require a publication/filing timestamp no later
  than `decision_at`. yfinance snapshots have no verifiable publication time and
  are excluded from historical runs.
- Polymarket: Gamma is a live snapshot. Historical runs return
  `PIT_DATA_EXCLUDED` before any request is made.
- Insider transactions: current snapshots are excluded from historical runs
  until a filing-publication timestamp is carried by the source adapter.
- News/social: dated records are bounded by the exact decision timestamp, not
  merely the calendar day. Undated historical records are excluded.
- OHLCV: Yahoo and Alpha Vantage historical paths expose raw OHLCV only.
  Current adjusted-close values and corporate-action columns are removed because
  they can encode facts learned after the historical decision.
- Evidence: `available_at` must be timezone-aware and no later than
  `decision_at`; missing, malformed or future values are inadmissible.

Unavailable evidence is explicit. Agents are instructed not to estimate or
reconstruct it.

## Reproducibility

Each completed run writes a canonical manifest containing source commit,
workflow schema, full run context, selected analysts, config digest, Python and
platform identity, plus hashes of `pyproject.toml` and `requirements.txt`.

## Deliberate limitations

- A live API snapshot is not promoted to historical evidence just because it was
  fetched successfully.
- This gate does not authorize PAPER, SHADOW or real-capital execution.
- Exchange calendars and early-close schedules are not inferred. Non-default
  markets must provide the correct timezone/close configuration; the backtest
  layer will add calendar-aware session enumeration.

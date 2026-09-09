# M5.1 Candlestick Evidence — implementation evidence

Date: 2026-09-09
Parent commit: `0c7213a6f945e1f66130ae25a9aa56b113bcf135`

## Delivered scope

- Pure, deterministic, versioned candlestick detector over an
  `EligibleMarketView` reloaded by `FrozenDatasetRepository`; callers cannot
  submit a self-constructed view to the public derivation API.
- Doji, bullish/bearish Marubozu, bullish/bearish Engulfing,
  Hammer/Hanging Man, Inverted Hammer/Shooting Star and Morning/Evening Star.
- Exact `Decimal` cross-product classification and fixed-point ppm geometry.
- Frozen source ordinals carried by the eligible view so an excluded bar cannot
  be bridged into a false multi-candle pattern.
- Separate stable feature digest and complete provenance bundle. The approved
  M5.0 eligible-data/view digest preimage remains unchanged.
- Bounded split provenance (`count`, latest effective date and an incremental
  context digest) supports the full dataset row limit without materializing an
  oversized canonical sequence or prompt payload.
- Exact runtime validation of instrument, bar, corporate-action and FX identity,
  ordering, availability, duplicates and authority digests before derivation.
- Typed fail-closed states for insufficient, discontinuous, split-barrier and
  zero-range history.
- Conversion to derived `UntrustedEvidence`; no action, rating, return,
  confidence, target, quantity, broker or portfolio surface.

The open-source candlestick projects supplied by the owner were used only as
design references. No source code was copied and no TA-Lib, Rust, ML,
PineScript, pandas, stockstats or vendor dependency was added to this path.

## Deliberate boundary

M5.1 is a library milestone. It is not wired into the live graph or ToolNode.
The production prompt injection belongs to M5.3 after the analysis job and run
context carry a trusted frozen dataset reference. Wiring earlier would bypass
the M5 dataset authority and fall back to current vendor data.

Pattern evidence never authorizes or constructs an order. M5.0 limitations
still keep `quantitative_claims_eligible=False`.

## Independent-review correction round

The first independent review of commit `e296602b583836e22d3a27867def6d1676391aa6`
rejected the public caller-supplied view boundary, unbounded split-digest output,
foreign corporate actions and a bypassable structural test. This correction
removes the public naked-view function, makes the repository the public
authority, bounds the output, streams split provenance across more than 10,000
actions, validates FX and instrument cutoff invariants, and strengthens the
structural guard. The original findings and their regression tests remain in
the frozen tree.

## Verification

- Focused contract + M5.1 suite: `96 passed` (including a 30-example
  property-based OHLC run and the independent-review regressions).
- Full suite with disposable PostgreSQL 16 and Redis 7:
  `1455 passed, 2 skipped, 71 subtests passed`.
- Skips: DeepSeek live credential absent; Windows-only taskkill failure path.
- Ruff: green.
- Compileall: green.
- JUnit: `audit/m5-1-full-junit.xml`
- JUnit bytes: `202204`
- JUnit SHA-256:
  `b394fd94bb1c7e21d3a3efd4db45bd676139ca95eed637ee5a70140c93891a37`

## Independent-review gate

This implementation is not a release or capital-facing promotion. It must be
reviewed at its exact commit before M5.2 starts. Later M5.3 wiring and the final
M5.5 product journey receive separate independent gates.

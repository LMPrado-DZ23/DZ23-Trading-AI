# M5.1 Candlestick Evidence — implementation evidence

Date: 2026-09-09
Parent commit: `0c7213a6f945e1f66130ae25a9aa56b113bcf135`

## Delivered scope

- Pure, deterministic, versioned candlestick detector over
  `EligibleMarketView` only.
- Doji, bullish/bearish Marubozu, bullish/bearish Engulfing,
  Hammer/Hanging Man, Inverted Hammer/Shooting Star and Morning/Evening Star.
- Exact `Decimal` cross-product classification and fixed-point ppm geometry.
- Frozen source ordinals carried by the eligible view so an excluded bar cannot
  be bridged into a false multi-candle pattern.
- Separate stable feature digest and complete provenance bundle. The approved
  M5.0 eligible-data/view digest preimage remains unchanged.
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

## Verification

- Focused M5.0 + M5.1 suite: `139 passed` (including a 30-example
  property-based OHLC run).
- Full suite with disposable PostgreSQL 16 and Redis 7:
  `1447 passed, 2 skipped, 71 subtests passed`.
- Skips: DeepSeek live credential absent; Windows-only taskkill failure path.
- Ruff: green.
- Compileall: green.
- JUnit: `audit/m5-1-full-junit.xml`
- JUnit bytes: `201027`
- JUnit SHA-256:
  `50a6a1ec5e02499989dc878f481a2960a9cc715b3be5b43b8dc25cab1672d485`

## Independent-review gate

This implementation is not a release or capital-facing promotion. It must be
reviewed at its exact commit before M5.2 starts. Later M5.3 wiring and the final
M5.5 product journey receive separate independent gates.

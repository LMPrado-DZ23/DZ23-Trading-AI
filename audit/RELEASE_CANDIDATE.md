# TradingAgents v0.5.0 release candidate

This candidate completes the agreed roadmap over the approved Gate A r12
baseline: Gate B/PIT, P4/P7 observability, P2 deterministic sizing, P1
next-open backtesting, P3 parallel analysts, P6 causal statistical memory, P5
durable API/worker, and gated SHADOW/PAPER execution.

## Verification

- Full suite on final source: 957 passed, 2 skipped, 71 subtests.
- Full repository Ruff gate: passed.
- Python compile and dependency compatibility checks: passed.
- Wheel build: passed.
- Security scan: no critical, high or medium finding. One low PAPER retry
  identity issue was found in `1f7e853` and fixed by `6ac235e`, with a focused
  regression test.

The two skipped tests require optional Bedrock support and a live DeepSeek API
key. The 18 warnings are model-catalog warnings exercised intentionally by
compatibility tests.

## Safety boundary

Real-capital execution is absent. Only SHADOW, local simulated PAPER and the
fixed official Alpaca PAPER endpoint exist. Configuration cannot switch an
adapter to a live endpoint. External PAPER, PostgreSQL and provider validation
remain operator-environment checks because no credentials were supplied.

## Promotion

This is a candidate, not a human-promoted release. Claude should audit the
shared source and evidence directly. Promotion to `aprovados` remains Prado's
explicit decision after that independent review.

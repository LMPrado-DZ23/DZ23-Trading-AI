# M5.2b1 — Three-agent review

## Audited target

- Commit: `150c097b17379751302d983ce61115deb0513d6d`
- Scope: pure deterministic simulation ledger only
- Independent method: each auditor reconstructed an immutable `git archive`
- Executor full-suite JUnit SHA-256:
  `50c24b78ff6e85a5924405ba22238915362837a5318371eadaf4524e393c1f27`

## Reproduction

- Windows focal: `39 passed`
- Windows backtest set: `203 passed, 5 skipped`
- Linux/WSL backtest set: `208 passed`
- Windows full suite: `1494 passed, 24 skipped, 71 subtests passed`
- Ruff and compileall: PASS

## Independent verdicts

### Architect / engineering — APPROVED

Confirmed external request/state anchors, atomic rollback, strict idempotency,
maximum-size replay and removal of the importable unchecked transition.
Measured 500 sessions at 1.722 s and 2,500 at 7.595 s on WSL.

### Security / contracts — APPROVED

Reproduced all eight findings from `fb085ce` as fixed: partial-session commit,
accumulated Decimal overflow, hostile Decimal context, impossible result
contracts, late aggregate bounds, replay at the limit, private-helper bypass and
superlinear fold. Measured 500/2,500/4,000 sessions at
0.958/5.879/10.833 s.

### QA / performance — APPROVED

Reproduced malformed/extra digest maps, empty and oversized runs, 5,000+5,000
order fail-fast, reversed-order determinism, second-batch rollback, hostile
Decimal inputs, forged results and 9,999-order apply/replay. Measured
500/2,500/4,000 sessions at 1.993/5.361/8.521 s.

## Remaining boundary

`expected_last_event_hash` and `expected_request_digest(s)` are verification
inputs, not self-authority. M5.2b2 must fetch them from trusted immutable
storage and must derive every `raw_open` through the approved M5.2a execution
price authority. Until that integration passes independent review, this review
does not approve a full backtest pipeline, broker, PAPER or LIVE execution.

## Verdict

**M5.2b1 APPROVED.** No CRITICAL or HIGH finding remains within this slice.

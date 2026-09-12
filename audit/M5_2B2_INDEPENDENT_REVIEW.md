# M5.2b2 independent correction review

- **audited commit:** `99548a7`
- **baseline:** `c1e8e3b`
- **date:** 2026-09-12 America/Sao_Paulo
- **scope:** offline authenticated simulation authority only
- **verdict:** APPROVED for M5.2b2 offline

## Initial blocking findings

The first candidate was rejected because the backtest specification did not
bind the complete ledger economics, decision-session liquidity was not
enforced, caller references could cross the validation/use boundary, the
backtest window was incomplete, duplicate instruments amplified dataset reads,
and two Windows publication failures could leave permanent claims. A final
review also proved that execution evidence was not bound to the actual fill.

## Corrections verified

1. `BacktestSpec` v2 binds the complete simulation-policy digest and the
   executor explicitly checks currency, initial cash, fees and instrument rules.
2. The execution-price authority v2 exposes only authenticated decision-session
   volume for liquidity. D+1 volume cannot influence the cap.
3. One net order per instrument and one decision session are required.
4. Both decision and fill sessions must be inside the frozen backtest window.
5. Returned references are reconstructed from authenticated records.
6. Execution evidence v2 is built after the reducer and binds submitted,
   requested and executed quantity plus the canonical fill digest.
7. Windows staging-write, rename and staging-creation failures remove partial
   files and claims and allow immediate retry.

## Independent verification

- Three independent reviewers re-ran focused tests and adversarial PoCs.
- Security reviewer: APPROVED; quantity/fill-digest mutation, altered reducer
  output and staging-creation failure were rejected or recovered.
- Ledger reviewer: APPROVED; both evidence attacks were rejected even after
  recomputing the outer result digest.
- QA reviewer: APPROVED; claim cleanup and retry were reproduced independently.
- Focused backtest suite: **233 passed, 5 skipped**.
- External adversarial file: **9 passed**.
- Wide regression: **1520 passed, 24 skipped, 4 deselected, 71 subtests**.
- The four deselected tests are pre-existing Windows/Codex subprocess-host
  incompatibilities already reproduced on the unchanged baseline; they are not
  waived for release and must be rerun in the Linux verification environment.
- Ruff and compileall: passed.
- Wide JUnit SHA-256:
  `07615ee8e16f3a8c2371a3b1a0e89c75ab9d13990946e49373537dbc968d191f`.

## Residual boundary

`AuthorizedSimulationBatchResult` has an unkeyed canonical digest and is not a
standalone custody attestation. The executor derives it from authenticated
inputs, so this is not an M5.2b2 bypass. M5.5 must persist it under the expected
tenant/run with an authenticated PostgreSQL head transition and must not trust
arbitrary result JSON. Corporate actions, FX, full runner, metrics, API/UI and
all broker/PAPER/LIVE paths remain outside this approval.

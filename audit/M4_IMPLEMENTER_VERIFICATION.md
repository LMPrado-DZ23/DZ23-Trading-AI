# M4 — Portfolio, Suitability and Deterministic Risk Verification

- Base commit: `fec27f61a91f883397fcfc00289ce85e50621a6a`
- Scope: deterministic portfolio policies, versioned suitability and mandates,
  double-entry ledger, atomic cash reservations, valuation snapshots, drawdown
  circuit breakers, authenticated API and PostgreSQL RLS.
- Capital boundary: this milestone enables only `backtest`, `shadow` and
  `paper`. Both LIVE modes fail closed in the domain service and are absent
  from the public mandate schema.

## Invariants implemented

- The effective risk profile is always the more restrictive of the user's
  requested profile and the suitability ceiling.
- Five immutable built-in policies cap volatility, single-position exposure,
  crypto exposure, correlated clusters, order notional, liquidity and minimum
  cash.
- The LLM-facing signal has no quantity field. Quantity is derived solely by
  the deterministic sizing function from capital, price, volatility,
  liquidity, costs, current exposure and the bound policy.
- Mandate lifecycle changes append a new version. A paused/closed latest
  version cannot fall back to an older active row.
- Every ledger event writes two postings. PostgreSQL verifies a zero sum using
  a deferred constraint trigger at commit.
- Reservations serialize on the portfolio row in PostgreSQL and on a local
  lock in SQLite; concurrent attempts cannot exceed available cash.
- Suitability, mandates, ledger and snapshots are append-only in both ORM and
  PostgreSQL.
- Total and same-day drawdown thresholds create a persistent open circuit
  breaker; sizing and reservation then fail closed.
- Every portfolio table has forced tenant RLS for the restricted application
  role.

## Independent-ready evidence produced by implementer

- Full suite with PostgreSQL 17 and Redis 7.4:
  `1224 passed, 1 skipped, 21 warnings, 71 subtests` in 180.13 seconds.
- Only skip: optional live DeepSeek call without a process environment key.
- JUnit: `audit/m4-portfolio-junit.xml`
- JUnit SHA-256:
  `78039e5beadccbfb3615cb3a40d46c301bc16ab95f1a0bbdeb5bb4a49b52256d`
- JUnit size: `166761` bytes.
- PostgreSQL adversarial test proves cross-tenant invisibility, append-only
  history, two-process reservation serialization and database rejection of an
  unbalanced ledger transaction.
- Migration path `head -> base -> head` completed successfully. Test-only role
  passwords were restored after role recreation, then the PostgreSQL test was
  repeated successfully.
- Ruff, compileall and `git diff --check` are green (line-ending warnings only).
- Hypothesis generated 250 deterministic property examples covering sizing and
  correlated-allocation caps.

## Not claimed by M4

- No broker order is submitted.
- No fill, fee or reconciliation event is yet applied to positions; that is
  the execution/reconciliation milestone.
- No backtest result is promoted as evidence of strategy performance.
- No LIVE mode or real-capital route is approved.
- Browser onboarding and operator dashboard remain subsequent milestones.

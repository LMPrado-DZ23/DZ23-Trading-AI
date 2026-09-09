# M4 Audit Fix — Implementer Verification

- Base commit: `784d16e0605a49ad146415fc7ee726e76efa716c`
- Scope: corrections to the independently reported M4 portfolio, suitability,
  ledger, valuation, allocation and API trust-boundary findings.
- Capital boundary: `BACKTEST`, `SHADOW` and `PAPER` only. This candidate does
  not enable any LIVE mode or broker order path.

## Corrected invariants

- The effective mandate is addressed by a composite, tenant-bound
  `current_mandate_id`; historical status labels cannot select an old mandate.
- A new suitability assessment atomically pauses every effective active mandate
  for that owner. Activation and resume require the newest valid assessment and
  reject a profile above its effective ceiling.
- Suitability and portfolio locks use one ordering to avoid the reassessment vs.
  activation deadlock.
- The PostgreSQL migration rejects unsafe legacy rows and installs deferred
  guards for latest-mandate pointers and active suitability compatibility.
- The database mandate constraint permits only `backtest`, `shadow` and `paper`.
- Ledger postings are bound to the same tenant and portfolio as their immutable
  transaction; deferred guards require exactly one cash leg and one external
  leg, balanced and denominated in the transaction currency.
- Ledger provenance and verification status are coherent. Migration backfill
  temporarily removes and then restores the append-only trigger transactionally.
- User-declared PAPER funding is explicitly unverified, changes fund units rather
  than PnL, remains idempotent, and is blocked after a positive position exists.
- Drawdown is calculated on NAV per unit, including a cumulative day baseline;
  external cash flow does not manufacture performance.
- Snapshot time, price and provenance are internal-only trusted inputs. Browser
  endpoints for client-supplied snapshots and sizing inputs were removed.
- Allocation caps require an explicit asset class for every symbol and enforce
  the portfolio-wide crypto, correlation, position and cash limits.
- Privacy-request event timestamps advance monotonically under the request lock;
  same-clock-tick transitions no longer fall back to random UUID ordering.

## Reproduction

- Full local suite: `1228 passed, 12 skipped, 71 subtests passed`.
- Focused portfolio suite: `37 passed`.
- Formerly flaky LGPD event-order test: `10/10 passed` after the monotonic-time fix.
- SQLite migration compatibility test: `1 passed`.
- Ruff: green for the repository (permission warnings only for stale sandbox
  temporary directories).
- Compileall: green for `tradingagents`, `service` and `cli`.
- PostgreSQL 0019 -> 0020 offline SQL generation: green.
- JUnit: `audit/m4-fix-local-junit-2.xml`
  - SHA-256: `df0e60c7017a03c60226515f6342394db751d449b60c50aaf002117efb32294f`
- Generated PostgreSQL SQL: `audit/m4-fix-0020-postgresql.sql`
  - SHA-256: `307663a87cfa908dd2f493206a73c336c890ec460e9a0cfa4bc0aab640f54123`

## Required independent gates

- The 12 skipped tests are external integration tests: PostgreSQL, Redis and a
  live DeepSeek smoke. They are not represented as passed.
- Before promotion, run migration 0019 -> 0020, downgrade/re-upgrade and
  `tests/integration/test_portfolio_postgres_rls.py` against disposable
  PostgreSQL roles with forced RLS.
- Independent review must attempt stale suitability insertion, pointer rollback,
  live-mode persistence, incomplete/cross-portfolio ledger events, replay after
  positions, forged valuation timelines and aggregate crypto-cap bypasses.
- This implementer verification is evidence for review, never promotion authority.

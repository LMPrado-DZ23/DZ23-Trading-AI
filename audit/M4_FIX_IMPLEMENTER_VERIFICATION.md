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
- Incremental allocation now consumes the headroom already occupied by current
  positions. The allocator requires a complete correlation matrix, fresh
  proposal and baseline snapshots, matching policy version/digest and every
  current position symbol before it can reserve cash.
- Allocation quantity is derived only inside the deterministic portfolio
  service. The public API exposes no route that accepts price, target weight,
  correlation or executable quantity as trusted client input.
- Allocation replay is bound to the same idempotency key and immutable request;
  a finalized reservation cannot be reused as a new order attempt.
- Mandate pause provenance is persisted. Suitability reassessment records a
  dedicated pause reason, while an open circuit breaker takes precedence over
  suitability and exposes only the risk-review action.
- Privacy-request event timestamps advance monotonically under the request lock;
  same-clock-tick transitions no longer fall back to random UUID ordering.

## Reproduction

- Final full local suite: `1260 passed, 12 skipped, 71 subtests passed`.
- Final focused portfolio/API/audit suite: `69 passed`.
- Formerly flaky LGPD event-order test: `10/10 passed` after the monotonic-time fix.
- SQLite migration compatibility test: `1 passed`.
- Ruff: green for the repository (permission warnings only for stale sandbox
  temporary directories).
- Compileall: green for `tradingagents`, `service` and `cli`.
- PostgreSQL 0019 -> 0020 offline SQL generation: green.
- Final JUnit: `audit/m4-fix-local-junit-6.xml`
  - bytes: `177607`
  - SHA-256: `57cb44dcf311e6d6fe2766f3619d32a1637df3222fd8ffa704cdd106fca583c9`
- Final generated PostgreSQL SQL: `audit/m4-fix-postgresql-offline-6.sql`
  - bytes: `22444`
  - SHA-256: `cfcb5e344b0927ab5a6b62122fbddf0ea72a3a7f580ac63364202cd9190b9552`
- Offline-generation log: `audit/m4-fix-postgresql-offline-6.log`
  - bytes: `341`
  - SHA-256: `5b42a7b0d1c408d037a1e826e99840b6f33eaa884c444b13fa7029f311ee9d82`
- Diff secret scan for OpenAI, AWS, JWT and generic secret assignments: zero
  matches. `git diff --check`, Ruff and compileall are green.

## Independent review status

- Product/API review: **approved for the M4 slice**, with no remaining blocker.
- Deterministic allocator security review: **no BLOCKER or HIGH finding**;
  approval is technical continuation evidence, not release authority.
- PostgreSQL/RLS migration review: **approved statically with an external
  execution gate**. No static blocker remains, but disposable PostgreSQL is
  mandatory before promotion.
- The allocator is currently an internal application port. M5/M6 must call it
  from the operational proposal/execution flow; this candidate does not claim
  end-to-end broker execution.

## Required independent gates

- The 12 skipped tests are external integration tests: PostgreSQL, Redis and a
  live DeepSeek smoke. They are not represented as passed.
- Before promotion, run migration 0019 -> 0020, downgrade/re-upgrade and
  `tests/integration/test_portfolio_postgres_rls.py` against disposable
  PostgreSQL roles with forced RLS. Exercise a legacy paused mandate, active
  reservation without baseline followed by release/expire, valid and invalid
  legacy ledgers, a `NOBYPASSRLS` application role, deferred-trigger commits,
  numeric rounding and concurrent reservation/funding/snapshot/reassessment.
- Independent review must attempt stale suitability insertion, pointer rollback,
  live-mode persistence, incomplete/cross-portfolio ledger events, replay after
  positions, forged valuation timelines and aggregate crypto-cap bypasses.
- This implementer verification is evidence for review, never promotion authority.

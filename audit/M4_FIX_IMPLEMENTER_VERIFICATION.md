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
- Cash-reservation replay is accepted only while the reservation remains active,
  unexpired and bound to the currently eligible mandate. It rechecks the risk
  baseline and circuit breaker and never masks unrelated integrity violations.
- PostgreSQL requires an operational mandate and baseline for both active and
  consumed reservations; only release/expiry can unwind after authority loss.
- PostgreSQL also requires every reservation to be inserted as `active` and
  refuses to consume an expired reservation even when its stale row label has
  not yet been changed. Future-dated consumption is refused as well.
- PostgreSQL application, worker and public roles cannot mutate reservation
  rows directly. The application can reserve and release only through private,
  versioned `SECURITY DEFINER` ports owned by a dedicated non-login guard role
  with a fixed search path, forced RLS and no unsafe attributes or memberships.
- The reserve port binds the actual opaque session and derives tenant, actor,
  role, portfolio, mandate, currency, TTL and database time. It enforces cash,
  breaker, suitability, replay and balance-version invariants in one lock
  order. The release port is idempotent and increments the version once.
- Reservation consumption is unavailable in M4 by design. M6 must introduce a
  separate job/lease/allocation/order-intent-bound consume port before an order
  worker can spend reserved cash.
- Mandate pause provenance is persisted. Suitability reassessment records a
  dedicated pause reason, while an open circuit breaker takes precedence over
  suitability and exposes only the risk-review action.
- Privacy-request event timestamps advance monotonically under the request lock;
  same-clock-tick transitions no longer fall back to random UUID ordering.

## Reproduction

- Final full suite with real PostgreSQL and Redis:
  `1279 passed, 1 skipped, 71 subtests passed`.
- Final focused portfolio/API/audit suite: `75 passed`.
- Formerly flaky LGPD event-order test: `10/10 passed` after the monotonic-time fix.
- SQLite migration compatibility test: `1 passed`.
- Ruff: green for the repository (permission warnings only for stale sandbox
  temporary directories).
- Compileall: green for `tradingagents`, `service` and `cli`.
- PostgreSQL 0019 -> 0020 online migration, offline generation and execution:
  green.
- Final full JUnit: `audit/m4-final-full-junit.xml`
  - bytes: `175202`
  - SHA-256: `55581db123995028cf646bfffb0576c947532017b899b88cf81073ca19ce8d7c`
- Final PostgreSQL focal JUnit: `audit/m4-final-postgresql-junit.xml`
  - bytes: `401`
  - SHA-256: `f139c4f6a5b1781555a42e74ea519f9d6c7cc2a71d5a287c06b76d627bf296eb`
- Final integration JUnit: `audit/m4-final-integration-junit.xml`
  - bytes: `1915`
  - SHA-256: `158cddb60f86f38dfe333a542b0b5337d459eb22bd6bdda509f2143d9a1d2cb1`
- Final generated and executed PostgreSQL SQL:
  `audit/m4-final-postgresql-offline.sql`
  - bytes: `36993`
  - SHA-256: `4b734a16543ac2deb8811a0f9722d19485f7f09757d391f4cb7e0297bb33b4c1`
- Offline-generation log: `audit/m4-final-postgresql-offline.log`
  - bytes: `341`
  - SHA-256: `5b42a7b0d1c408d037a1e826e99840b6f33eaa884c444b13fa7029f311ee9d82`
- Diff secret scan for OpenAI, AWS, JWT and generic secret assignments: zero
  matches. `git diff --check`, Ruff and compileall are green.
- Dynamic PostgreSQL report: `audit/M4_POSTGRES_GATE_VERIFICATION.md`.

## Independent review status

- Independent re-review found four final defects after the first PostgreSQL
  run: expired/stale reservation replay, `active -> consumed` without current
  authority, loss of the public database-time error contract, and direct SQL
  consumption of an expired reservation. All four are corrected and covered by
  local and real PostgreSQL tests.
- Candidate `f6cd577f28604dbfe74999d35a9a1b29aba2438a` was then rejected because
  direct reservation DML remained a capital-control bypass. That bypass is
  closed by ACL revocation and the private mutation ports described above.
- The previous independent verdicts are superseded by these corrections. A
  fresh review of the final commit is still required; this document does not
  claim independent approval.
- The allocator is currently an internal application port. M6 must call it
  from the operational proposal/execution flow; this candidate does not claim
  end-to-end broker execution.

## Required independent gates

- The only remaining skipped test is the live DeepSeek smoke; it is not
  represented as passed and is unrelated to M4.
- Migration 0019 -> 0020, offline SQL execution, operational consumption gate,
  `NOBYPASSRLS` roles, deferred-trigger commits, rounding and concurrent
  reservation paths were exercised on disposable PostgreSQL 17. Earlier legacy
  preflight/backfill checks remain recorded in the dynamic gate report. The
  downgrade is intentionally forbidden and its fail-closed refusal was verified.
- Independent review must attempt stale suitability insertion, pointer rollback,
  live-mode persistence, incomplete/cross-portfolio ledger events, replay after
  positions, forged valuation timelines and aggregate crypto-cap bypasses.
- This implementer verification is evidence for review, never promotion authority.
- The real database gate modified production code after the prior static
  reviews; the final diff must be independently re-audited before promotion.

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
- Independent review of commit `6894872` proved that tenant and session GUCs
  were still caller-controlled authority. Migration 0021 replaces every
  application portfolio RLS policy with a short HMAC-SHA256 capability bound to
  the opaque session token hash, PostgreSQL backend PID and transaction id. The
  database derives tenant, user, membership and current role only after
  verifying the capability and active session.
- Reserve/release capabilities additionally bind the exact resource, canonical
  amount, purpose, idempotency digest and TTL. Their random nonce is consumed
  once in a private table, so altered parameters and replay fail closed.
- The PostgreSQL legacy preflight is emitted as SQL for online and offline plans
  before M4 DDL. Fractional TTLs are preserved exactly in microseconds on both
  SQLite and PostgreSQL.
- Mandate pause provenance is persisted. Suitability reassessment records a
  dedicated pause reason, while an open circuit breaker takes precedence over
  suitability and exposes only the risk-review action.
- Privacy-request event timestamps advance monotonically under the request lock;
  same-clock-tick transitions no longer fall back to random UUID ordering.

## Reproduction

- Final full suite with real PostgreSQL and Redis:
  `1282 passed, 1 skipped, 71 subtests passed`.
- Final focused portfolio/API/audit suite: `78 passed`.
- Formerly flaky LGPD event-order test: `10/10 passed` after the monotonic-time fix.
- SQLite migration compatibility test: `1 passed`.
- Ruff: green for the repository (permission warnings only for stale sandbox
  temporary directories).
- Compileall: green for `tradingagents`, `service` and `cli`.
- PostgreSQL 0019 -> 0021 online migration, offline generation and execution:
  green.
- Final full JUnit: `audit/m4-final-full-junit.xml`
  - bytes: `175612`
  - SHA-256: `b4812cf0132b73e5ab1bd0071de6591329ff991abe46ed64783f56c7898f1477`
- Final PostgreSQL focal JUnit: `audit/m4-final-postgresql-junit.xml`
  - bytes: `401`
  - SHA-256: `66af43b49966ed7433a121894cae0ff481760cbfc19cb5d96f20c41c844d597b`
- Final integration JUnit: `audit/m4-final-integration-junit.xml`
  - bytes: `1914`
  - SHA-256: `a13105902e6a90e0ac88236a93d161384e69fc3241d8be13600714cbca0962c2`
- Final generated and executed PostgreSQL SQL:
  `audit/m4-final-postgresql-offline.sql`
  - bytes: `60824`
  - SHA-256: `158e32cb99cd6565cdb246e0227625c81d9dd9ea1e1c900ca01020be8df138ab`
- Offline-generation log: `audit/m4-final-postgresql-offline.log`
  - bytes: `489`
  - SHA-256: `effd8a8743ecd81e67de389c3da2e143c1a8a4e578b7c17b80760a01e8e01fe7`
- The offline SQL deliberately provisions no capability key. Applying it to a
  database at `20260908_0019` reaches `20260909_0021` with zero private keys and
  `portfolio_capability_ready(...) = false`; production readiness therefore
  fails closed. Production provisioning must use the online migration with a
  valid `TRADINGAGENTS_SESSION_TOKEN_PEPPER`.
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
- Candidate `689487294e49c9090ce9eaca39f86893c0b48ba6` was rejected because the
  private ports and portfolio RLS still trusted caller-settable tenant/session
  GUCs, offline migration omitted the legacy-data preflight, and fractional TTLs
  regressed. Signed transaction/operation capabilities, the SQL preflight and
  microsecond TTL path close those findings in this candidate.
- The previous independent verdicts are superseded by these corrections. A
  fresh review of the final commit is still required; this document does not
  claim independent approval.
- The allocator is currently an internal application port. M6 must call it
  from the operational proposal/execution flow; this candidate does not claim
  end-to-end broker execution.

## Required independent gates

- The only remaining skipped test is the live DeepSeek smoke; it is not
  represented as passed and is unrelated to M4.
- Migration 0019 -> 0021, offline SQL preflight, operational consumption gate,
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

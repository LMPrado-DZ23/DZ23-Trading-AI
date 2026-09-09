# M4 PostgreSQL 17 Dynamic Gate Verification

- Date: `2026-09-09` (`America/Sao_Paulo`).
- Superseded candidates: `1cc9584d2e4be987ba11d48ce15dafdb4180b4ce`,
  `feecf5500447d45ed656f0d364e0ace3004630f0` and
  `f6cd577f28604dbfe74999d35a9a1b29aba2438a`. The last candidate was rejected
  because the application role could mutate cash reservations directly.
- Database: PostgreSQL `17.11`, official image
  `postgres@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73`.
- Cache: Redis `7.4.11`.
- Isolation: disposable containers on loopback-only test ports. No shared or
  production database was changed.

## Defects found and corrected

1. PostgreSQL rejected two PL/pgSQL functions because `CASE` expressions used
   directly as comparison operands in `IF` were not grouped. Both operands are
   now parenthesized.
2. Reservation eligibility mixed the PostgreSQL clock with the application
   process clock. Suitability, reservation, release and portfolio reads now use
   `clock_timestamp()` from their transaction on PostgreSQL. SQLite keeps the
   injectable application clock used by local tests.
3. Idempotency recovery returned expired reservations and reservations bound to
   a superseded mandate. Replay now locks the portfolio and reservation,
   rechecks the current mandate, baseline, circuit breaker, status and expiry,
   and recognizes only the named reservation idempotency constraint.
4. The database trigger allowed `active -> consumed` after operational mandate
   loss. Both `active` and `consumed` now require the current eligible mandate
   and a risk baseline. Only `released` and `expired` remain available as
   fail-safe unwind transitions.
5. The portfolio list adapter allowed a database-clock failure to escape as an
   unstructured 500. It now preserves the public `503`, `retryable=true`,
   `retry_later` contract.
6. Destructive downgrade implementation below the unconditional fail-closed
   exception was removed. Migration 0020 has no latent downgrade path.
7. An expired reservation that still had the `active` label could be changed
   directly to `consumed` in SQL. PostgreSQL now rejects consumption after
   `expires_at`, rejects future-dated consumption, and permits inserts only in
   `active` state. This prevents stale cash authority from being consumed after
   new cash has been reserved.
8. The PostgreSQL integration fixture used the Windows host clock to build a
   trusted valuation that the database compared with `clock_timestamp()`. The
   fixture now obtains the observation time from the same transaction database
   clock, eliminating cross-host clock drift from the security proof.
9. Application and worker roles could bypass deterministic cash, currency,
   TTL, circuit-breaker and version checks with direct reservation DML. Direct
   `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, `REFERENCES` and `TRIGGER`
   privileges are now revoked. Reservation and release mutations are exposed
   only through two private, versioned `SECURITY DEFINER` ports owned by a
   dedicated `NOLOGIN`/`NOBYPASSRLS` guard role. The ports derive tenant,
   actor, role, portfolio, mandate, currency and database time; bind the opaque
   authenticated session; and reject unsafe values before taking authority.
   Consuming a reservation is intentionally unavailable in M4.

All artifacts generated before these corrections, including the `1cc9584`
bundles and `m4-fix-*-6/9/10/11/12` evidence, were removed as superseded.

## PostgreSQL assertions

- Fresh online migration from an empty PostgreSQL 17 database through 0020:
  passed.
- Generated offline SQL starts with `BEGIN`, ends with `COMMIT`, contains the
  `active/consumed` operational gate and the grouped `CASE` comparison.
- Offline SQL was applied with `ON_ERROR_STOP=1` to a second disposable database
  prepared at 0019. It committed and left `alembic_version=20260908_0020`.
- The installed function body contains
  `requires_operational := NEW.status IN ('active','consumed')`.
- Direct `active -> consumed` after mandate pause is rejected by PostgreSQL.
- Direct expired `active -> consumed` is rejected by PostgreSQL, even if the
  row has not yet been relabelled `expired`.
- Direct insertion of a reservation already labelled `consumed` is rejected;
  every reservation must be born `active`.
- Direct `active -> released` and `active -> expired` after mandate pause are
  accepted as risk-reducing exits.
- A process clock forced one day backward cannot invalidate a just-created
  mandate or break the two-writer reservation serialization.
- Concurrent reservations of 60 against 100 yield exactly one success and one
  `INSUFFICIENT_AVAILABLE_CASH`.
- `tradingagents_app`, `tradingagents_worker` and `PUBLIC` have no direct
  reservation mutation privilege. A direct oversized, wrong-currency,
  long-TTL insert and direct `consumed` update are rejected.
- The private reserve port rejects non-finite/non-positive amounts, TTL outside
  30..3600 seconds, insufficient cash, stale mandate, open circuit breaker and
  a session/tenant mismatch. Currency, expiry, mandate and status are derived.
- The private release port changes `balance_version` exactly once and is
  idempotent. There is no private or public consume port in M4.
- Both ports have fixed `pg_catalog` search paths, force row security, are owned
  by `tradingagents_reservation_api`, and are not executable by `PUBLIC`.
- `tradingagents_reservation_api` is `NOLOGIN`, `NOSUPERUSER`, `NOCREATEDB`,
  `NOCREATEROLE`, `NOINHERIT`, `NOREPLICATION`, `NOBYPASSRLS` and has no role
  memberships.
- `tradingagents_app` and `tradingagents_worker` are `NOSUPERUSER`,
  `NOCREATEDB`, `NOCREATEROLE`, `NOINHERIT`, `NOREPLICATION` and `NOBYPASSRLS`.
- Downgrade 0020 -> 0019 exits non-zero with
  `M4_SECURITY_DOWNGRADE_FORBIDDEN`; schema version remains 0020.
- Earlier preflight checks for an invalid legacy ledger, paused-mandate
  backfill, coherent PAPER funding backfill and active legacy reservation
  unwind remain part of the prior dynamic run. No final claim depends only on
  an obsolete offline SQL artifact.

## Final evidence

- Focused PostgreSQL RLS/race/operational-transition test: `1 passed`.
  - `audit/m4-final-postgresql-junit.xml`
  - bytes: `401`
  - SHA-256 `f139c4f6a5b1781555a42e74ea519f9d6c7cc2a71d5a287c06b76d627bf296eb`
- PostgreSQL/Redis integration directory: `11 passed`.
  - `audit/m4-final-integration-junit.xml`
  - bytes: `1915`
  - SHA-256 `158cddb60f86f38dfe333a542b0b5337d459eb22bd6bdda509f2143d9a1d2cb1`
- Full suite with real PostgreSQL and Redis: `1279 passed, 1 skipped`,
  `71 subtests passed`, `21 warnings`.
  - Only skip: opt-in live DeepSeek request, unrelated to M4.
  - `audit/m4-final-full-junit.xml`
  - bytes: `175202`
  - SHA-256 `55581db123995028cf646bfffb0576c947532017b899b88cf81073ca19ce8d7c`
- Executed offline SQL:
  - `audit/m4-final-postgresql-offline.sql`
  - bytes: `36993`
  - SHA-256 `4b734a16543ac2deb8811a0f9722d19485f7f09757d391f4cb7e0297bb33b4c1`
- Offline generation log:
  - `audit/m4-final-postgresql-offline.log`
  - bytes: `341`
  - SHA-256 `5b42a7b0d1c408d037a1e826e99840b6f33eaa884c444b13fa7029f311ee9d82`
- Ruff, compileall and `git diff --check`: passed.

## Gate conclusion

The implementer PostgreSQL gate is green. This is evidence for independent
review, not release or capital authority. M4 remains unpromoted until a fresh
independent review approves the final commit and its regenerated bundles.

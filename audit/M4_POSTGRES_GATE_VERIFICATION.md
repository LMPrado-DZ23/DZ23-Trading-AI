# M4 PostgreSQL 17 Dynamic Gate Verification

- Date: `2026-09-09` (`America/Sao_Paulo`).
- Superseded candidates: `1cc9584d2e4be987ba11d48ce15dafdb4180b4ce`
  and `feecf5500447d45ed656f0d364e0ace3004630f0`.
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
  - SHA-256 `3022e48def714ef68524e487622656d36f28ebee2b5c91142420e32f6297c35c`
- PostgreSQL/Redis integration directory: `11 passed`.
  - `audit/m4-final-integration-junit.xml`
  - SHA-256 `800534ef45b1185fa7a6d61bf2caa6b1e635cf50643ecbe455d1a491e5991164`
- Full suite with real PostgreSQL and Redis: `1279 passed, 1 skipped`,
  `71 subtests passed`, `22 warnings`.
  - Only skip: opt-in live DeepSeek request, unrelated to M4.
  - `audit/m4-final-full-junit.xml`
  - SHA-256 `0156f75226ff9444b4217b485bf1ea5eb301ba5f8d5c0b28f1b58ac46bd289ac`
- Executed offline SQL:
  - `audit/m4-final-postgresql-offline.sql`
  - SHA-256 `952ed50f8ac60d8ca06c72e6e61e4e4276bd5421426a3f15aee8fbb5a20fcf17`
- Offline generation log:
  - `audit/m4-final-postgresql-offline.log`
  - SHA-256 `5b42a7b0d1c408d037a1e826e99840b6f33eaa884c444b13fa7029f311ee9d82`
- Ruff, compileall and `git diff --check`: passed.

## Gate conclusion

The implementer PostgreSQL gate is green. This is evidence for independent
review, not release or capital authority. M4 remains unpromoted until a fresh
independent review approves the final commit and its regenerated bundles.

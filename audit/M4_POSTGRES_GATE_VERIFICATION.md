# M4 PostgreSQL 17 Dynamic Gate Verification

- Date: `2026-09-09` (`America/Sao_Paulo`).
- Superseded candidate: `1cc9584d2e4be987ba11d48ce15dafdb4180b4ce`.
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
  - SHA-256 `0f525542e73156b36260d57583082ffc69e877e7ffc36a00e00ea8f84b8559bf`
- PostgreSQL/Redis integration directory: `11 passed`.
  - `audit/m4-final-integration-junit.xml`
  - SHA-256 `80a525ab6c11969ecf234b595f13655fba693bafdb79e2d183833e762b76d361`
- Full suite with real PostgreSQL and Redis: `1279 passed, 1 skipped`,
  `71 subtests passed`, `22 warnings`.
  - Only skip: opt-in live DeepSeek request, unrelated to M4.
  - `audit/m4-final-full-junit.xml`
  - SHA-256 `eb73cc589f97ea8c6ced8788169d386c4fca637e51c8891df376bf00344c7e8c`
- Executed offline SQL:
  - `audit/m4-final-postgresql-offline.sql`
  - SHA-256 `c8a14d591a6c5b070db15aaee72001f75a47e480112c64cbd8ff616aeab4afa6`
- Offline generation log:
  - `audit/m4-final-postgresql-offline.log`
  - SHA-256 `5b42a7b0d1c408d037a1e826e99840b6f33eaa884c444b13fa7029f311ee9d82`
- Ruff, compileall and `git diff --check`: passed.

## Gate conclusion

The implementer PostgreSQL gate is green. This is evidence for independent
review, not release or capital authority. M4 remains unpromoted until a fresh
independent review approves the final commit and its regenerated bundles.

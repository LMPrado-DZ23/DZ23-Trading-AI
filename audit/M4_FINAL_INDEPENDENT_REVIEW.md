# M4 Final Independent Review

- **Frozen code commit:** `6eeae2b84752fe23e0e6b3e0bd6335bbdc19c71e`
- **Scope:** suitability, mandate lifecycle, portfolio accounting, deterministic
  sizing, risk controls, cash reservations and their PostgreSQL authority
  boundary.
- **Capital boundary:** BACKTEST, SHADOW and PAPER only. No LIVE order route is
  enabled or approved by this milestone.
- **Verdict:** **APPROVED FOR M4 COMPLETION**. This is a milestone gate, not a
  release promotion or authorization to trade real capital.

## Independent reviewers

1. **PostgreSQL/RLS/locking review — APPROVED.** The reviewer verified that
   migration 0025 serializes replay validation as
   `verify -> portfolio lock -> verify again -> TTL precheck -> protected
   implementation`, preserves the global `portfolio -> reservation` lock order,
   and introduces no tenant, ACL, atomicity or deadlock bypass.
2. **Service/runtime review — APPROVED.** The reviewer found no blocker in the
   final service boundary. The only inherited low-risk note is that a direct
   PostgreSQL RPC call with several invalid parameters may select a different
   primary validation error than the service/SQLite path; every path still
   fails closed.
3. **Adversarial dynamic review — APPROVED.** On a disposable PostgreSQL 16
   database, the reviewer independently applied migrations 0001 through 0025,
   ran all 8 PostgreSQL integrations and 45 focused portfolio audit tests, and
   inspected grants and installed function bodies. No new blocking, high,
   medium or low finding was reported.

## Implementer reproduction

- Full suite with real PostgreSQL and Redis: **1308 passed, 2 skipped, 71
  subtests passed**.
- Fresh PostgreSQL migration through 0025: passed.
- Upgrade paths 0022 -> 0023 -> 0024 -> 0025: passed.
- PostgreSQL/Redis integration on a fresh database: **8 passed**.
- Ruff and compileall: passed.
- Full JUnit: `audit/m4-final-full-junit.xml`, 180224 bytes, SHA-256
  `1b71466374e936b2792d3141d03ec4f279100b6a3551eaa7f28788dd0a9aa178`.
- Integration JUnit: `audit/m4-final-integration-junit.xml`, 1506 bytes,
  SHA-256
  `931b8eac1399ac3846b83dd6a68c02ba7166ccf07c7be67acdb1ad69ee04c825`.

The two skipped tests are the opt-in live DeepSeek smoke and a Windows-only
`taskkill` failure-path test. Neither is represented as passed.

## Accepted residuals and follow-up

- A direct database RPC with multiple simultaneously invalid arguments may
  produce a different first error than the service adapter, while remaining
  fail closed.
- Capability-nonce pruning is implemented as maintenance but has no operational
  backlog metric yet; observability belongs to M8.
- A clean SQLite Alembic migration still fails at the pre-existing migration
  0009 because SQLite cannot apply that constraint-alteration form. Production
  uses PostgreSQL; M9 must either provide a supported local bootstrap path or
  repair the historical SQLite chain before claiming one-command local setup.
- Reservation consumption remains intentionally absent. M6 must add a separate
  job/lease/allocation/order-intent-bound consume port and receive an independent
  capital-boundary review.


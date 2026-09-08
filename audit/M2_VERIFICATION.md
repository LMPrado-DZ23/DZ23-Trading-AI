# M2 verification — encrypted tenant connections

Date: 2026-09-08  
Base: `6e191914e4f351af72454e555857bd7282e9a2eb`

## Deterministic evidence

- Full suite before the final rewrap pagination fix: `1083 passed`, `2 skipped`,
  `71 subtests passed`.
- Final focused suite: `30 passed` for connection crypto, lifecycle, diagnostics,
  idempotency and Transit rewrap.
- PostgreSQL runtime-role RLS integration: `1 passed` against local PostgreSQL
  using `tradingagents_app_test` with `FORCE ROW LEVEL SECURITY`.
- Ruff: all checks passed.
- Alembic `0005 -> 0006` downgrade/upgrade cycle: passed on PostgreSQL.

## Independent review

Result: **approved with external-validation reservations**. No blocking code
finding remains in the reviewed M2 fixes. The reviewer independently reproduced
41 focused tests and verified the request-digest, external-Transit-only
production policy and keyset-paginated rewrap workflow.

## Explicit external gates

- OpenBao Transit was proven with an HTTP protocol-compatible mock, not a live
  OpenBao instance.
- CCXT testnet credentials previously published in chat are not admissible;
  real venue validation requires new restricted credentials.
- LIVE connections remain fail-closed as `PERMISSIONS_NOT_VERIFIABLE` until M6
  supplies venue-specific permission probes and the isolated execution worker.
- Infrastructure egress policy remains part of M9; application-level exact
  origin, public DNS, proxy disabling and redirect disabling are already active.
- M3 must supply the tenant-scoped LLM credential resolver and force strict
  credential mode in the worker composition root.

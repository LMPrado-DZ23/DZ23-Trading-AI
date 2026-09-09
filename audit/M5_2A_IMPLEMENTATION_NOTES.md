# M5.2a execution-price boundary

Date: 2026-09-09
Parent commit: `c0fd3646db84955ac17a2c82233f99b2e8936a85`

## Scope

This checkpoint establishes the narrow point-in-time boundary that the M5.2
accounting reducer will consume. It does not yet implement orders, fills,
postings, corporate-action settlement or portfolio state.

- `ExecutionPriceProjection` contains only the raw next open and the minimum
  economic identity/timestamps required to price a fill.
- `ExecutionPriceProvenance` separately binds the projection to the frozen
  dataset, manifest, eligible decision view and full parent bar/source digests.
- `ExecutionPriceBundle` validates both children and their binding.
- `FrozenDatasetRepository.load_execution_price()` is the only public producer,
  revalidates tenant, manifest and eligible-view authority from frozen bytes,
  and rejects repository subclasses.
- Changing D+1 high, low, close or volume while retaining the same raw open
  leaves the economic projection byte-identical and changes only full
  provenance. Changing the raw open changes the economic projection.

The separation prevents future D+1 fields from influencing fill economics
while retaining complete chain-of-custody evidence. The existing
`ExecutionOpen` contract remains backward compatible and unchanged.

## Retained boundaries

- No broker, order, target, side, quantity, ledger, database, service, LLM,
  network or wall-clock dependency is introduced.
- A later simulation reducer must receive projections produced internally from
  trusted dataset identifiers. Client-built projections are not authority.
- Dataset limitations remain active and quantitative claims remain disabled.

## Verification

- Focused contracts + frozen repository suite: `107 passed`.
- Full suite with disposable PostgreSQL 16 and Redis 7:
  `1473 passed, 2 skipped, 71 subtests passed`.
- Skips: DeepSeek live credential absent; Windows-only taskkill failure path.
- Ruff: green.
- Compileall: green.
- JUnit: `audit/m5-2a-full-junit.xml`
- JUnit bytes: `205427`
- JUnit SHA-256:
  `39c3e555cfbf4ae162fa8a77ec61f62c44787b3d63ba54557294d5ac348b9ef7`

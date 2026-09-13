# M5.2c3 — Integrated economic-session authority review

## Decision

**APPROVED only for offline/backtest execution with the exact local custody
store.** This checkpoint is **not approved** for SaaS production, multiple
portfolios/accounts, broker dispatch or real capital.

## Implemented boundary

- One public outer authority resolves authenticated order, price, corporate
  action, FX and quantity evidence before reducing a single economic ledger.
- Economic checkpoints use domain-separated HMAC records and a dedicated CAS
  stream.
- Genesis rejects incomplete or inconsistent instrument rules/bindings before
  publishing either a checkpoint or head.
- The local CAS store is exact-type constrained; returned heads are bound to
  scope, generation, predecessor, checkpoint identity and authenticated
  reference, then read back.
- Historical idempotent retries are accepted only while the newer visible head
  remains byte-identical.
- Existing trade-v1 custody bytes and digests remain readable and appendable;
  economic-v2 bytes retain an explicit, separate stream identity.

## Independent review

Three independent review passes covered architecture, adversarial CAS/security
and test vectors. They found and caused fixes for:

1. invalid genesis heads with missing/mismatched instrument authority;
2. duck-typed CAS success without a persisted head;
3. trade-v1 canonical-byte incompatibility after adding stream identity.

Final reviewers approved the frozen offline/backtest scope. Security explicitly
rejected production use until transactional PostgreSQL custody and authenticated
server-side identity composition exist.

## Verification

- Focused outer authority: **17 passed**.
- Checkpoint compatibility + outer authority: **39 passed**.
- Related backtest regression: **139 passed** locally; independent review also
  reproduced **160** security tests and **178** related tests on frozen hashes.
- Full suite: **1636 passed, 24 skipped, 71 subtests passed**.
- Four transient Windows subprocess failures observed during overlapping test
  processes were repeated alone: **4 passed**. The authoritative final full run
  then passed without failures.
- Ruff and compileall checks: green.
- JUnit: `audit/m5-2c3-final-full-junit.xml`
- JUnit SHA-256:
  `9293a385649483192bd7443bd1d3685e2de7158db1c2c9a0d680990b2f06761f`

## Production blockers retained

1. PostgreSQL must atomically publish checkpoint bytes, lineage, receipt and
   current head in one transaction.
2. Scope v2 must bind tenant, portfolio, broker connection, external account,
   venue and credential version without accepting identity from an HTTP body.
3. A tenant risk governor must reserve capital before dispatch; one fenced
   serial dispatcher per external broker account must own submit/reconcile.
4. UNKNOWN broker outcomes must freeze retries and rerouting until venue
   reconciliation proves the terminal state.
5. Request storage must be tenant/backtest namespaced; global `batch_id`
   identity remains a multi-tenant denial-of-service risk.
6. Aggregate workload limits must be enforced before loading/projecting all
   economic datasets.

These blockers are the next implementation milestone and are not waived by
this offline checkpoint.

# M5.0 corrected candidate notes

Status: candidate awaiting independent review. This document is not an approval.

The first M5.0 candidate at `d093c285e7424e30f1965046a35cafe32f2565ca`
was rejected. The corrected candidate addresses the reproduced findings:

- context-independent canonical Decimal serialization;
- exact runtime UUID, digest, date, datetime, and contract validation;
- bounded JSONL ingestion and canonical limitation metadata;
- pinned repository identity and atomic Linux `renameat2(RENAME_NOREPLACE)`;
- separate data and provenance digests with tenant, dataset, session, and cutoff binding;
- factual, unknown, and settlement-policy bar availability;
- contiguous calendars, no skipped execution session, and minimal decision-bound raw open;
- fail-closed corporate-action revisions and delisting evidence;
- one dataset reference per distinct tradeable or benchmark instrument;
- no public full-snapshot loader for future market data.

M5.0 remains deliberately ineligible for quantitative claims. Cross-vintage
corporate-action lineage and positive coverage attestations are deferred to an
append-only M5.5 authority and block performance publication until independently
reviewed.

## Reproduction before freeze

- Focused backtest suite: `99 passed`.
- Full suite with PostgreSQL 16 and Redis 7: `1407 passed, 2 skipped, 71 subtests`.
- Ruff over `tradingagents` and `tests`: pass.
- Compileall over `tradingagents` and `service`: pass.
- JUnit: `audit/m5-0-corrected-full-junit.xml`.
- JUnit bytes: `194930`.
- JUnit SHA-256: `a8d53c14cf9710aff3de24cdfc31a21b5f991bdf540a83722f007c1c30a7a017`.

The skips are the credential-gated DeepSeek live call and the Windows-only
`taskkill` failure path. Neither is silently counted as passed.

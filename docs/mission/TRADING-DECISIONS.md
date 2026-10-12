# TRADING DECISIONS

ADR-01: preserve main and work in audit branch
- status: accepted
- rationale: keep production branch untouched and validate changes safely

ADR-02: default to single-owner, no public signup
- status: accepted
- rationale: user explicitly requested a single-owner, personal trading lab without SaaS flows

ADR-03: keep LIVE disabled until owner authorization and gate evidence
- status: accepted
- rationale: this is a direct safety requirement from the mission file and matching repository policy

ADR-04: do not broaden scope to multi-tenant or SaaS billing features during this branch
- status: accepted
- rationale: scope is personal single-owner trading; billing and signup are outside the goal

Rejected alternatives:
- full SaaS conversion to multi-tenant signup/trial billing
- enabling live execution without separate authorization
- merging PR #3 or PR #5 directly into main before independent validation

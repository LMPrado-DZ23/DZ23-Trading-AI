# M5.1 independent review

Date: 2026-09-09
Audited commit: `4f57572a65bb8158a1c83cafbe80b26012d70aab`
Verdict: **APPROVED for the M5.1 library milestone**

This is not a release, production deployment, live-trading approval or a
quantitative-performance claim.

## Independent reproduction

Three read-only reviewers independently approved the exact commit after two
rejected correction rounds. Their reproduced evidence included:

- public repository-backed derivation with 10,001 distinct split actions;
- public derivation at the exact 25,000-row ceiling (24,993 actions, six bars
  and one instrument), with integral JSON evidence and no truncation;
- byte-equivalent eligible-data digest against the prior canonical preimage for
  datasets below the generic sequence limit, including an FX-bearing case;
- rejection of wrong tenant/manifest, repository subclasses, virtual load
  overrides, foreign/future/reordered/duplicate corporate-action and FX data,
  post-cutoff instruments and forged ordinal continuity;
- exact import allowlist and private-consumer scan across `tradingagents/`,
  `service/` and `cli/`;
- no broker, portfolio, order, rating, quantity or capital-facing surface.

Independent focused runs reported 110 and 161 passing tests. The frozen full
JUnit was verified as:

- `1469 passed, 2 skipped, 71 subtests passed`;
- 1,542 XML test cases, zero failures and zero errors;
- 204,855 bytes;
- SHA-256
  `60f7a006995c1712c1587f3a547223dfd80e1babe3070a38fe7e5641221a5b0c`.

## Retained boundaries

- `CandlestickEvidenceBundle` and `UntrustedEvidence` must never be accepted
  from a client as trusted input. M5.3 must derive them internally from a
  trusted tenant, dataset identifier and manifest digest.
- Python private functions and AST guards are anti-regression controls, not a
  sandbox against arbitrary code execution in the process.
- Runtime type introspection of the repository derivation return currently
  resolves to `typing.Any` to avoid the import cycle; static type checking sees
  the concrete type. Resolve this before a framework depends on runtime return
  introspection.
- M5.0 limitations remain active and
  `quantitative_claims_eligible=False`.

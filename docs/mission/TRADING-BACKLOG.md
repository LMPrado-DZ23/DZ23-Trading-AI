# TRADING BACKLOG

P0 | CI reproduction and root cause
- owner: LMPrado-DZ23
- severity: critical
- dependencies: local toolchain; GitHub Actions logs if available
- tests: pytest, compileall, smoke scripts, dependency audit
- status: TODO

P0 | review PR #3 (audit/fixed kernel and financial blockers)
- owner: LMPrado-DZ23
- severity: critical
- dependencies: PR diff and branch review
- tests: ledger/fee/recovery/reconciliation targeted tests
- status: TODO

P0 | review PR #5 (personal trading lab architecture)
- owner: LMPrado-DZ23
- severity: high
- dependencies: repository diff and capability tests
- tests: personal exchange catalog/capability smoke tests
- status: TODO

P1 | exchange testnet validation and external credentials
- owner: LMPrado-DZ23
- severity: high
- dependencies: host secret manager / testnet keys
- tests: integration smoke tests with testnet-only accounts
- status: BLOCKED_BY_EXTERNAL_DEPENDENCY

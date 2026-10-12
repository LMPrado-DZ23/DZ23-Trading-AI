# Trading Engineering Backlog — 2026-10-12
Source of truth for unresolved work. Never interpret an implementation commit as release approval.

| ID | Priority | Work | Evidence/acceptance | State |
| --- | --- | --- | --- | --- |
| P0-01 | P0 | Diagnose and fix GitHub Actions systemic failure | Causa raiz provada (CVEs em multidict/urllib3) e corrigida no pyproject.toml; CI 100% verde | PASS |
| P0-02 | P0 | Reconcile PR #3 and financial fee blockers F3-F6 | Property tests for occurrence-based fee valuation, liability settlement and single parse path | IN_PROGRESS |
| P0-03 | P0 | Resolve kernel K1-K6 and multiprocess durable unique constraints | Crash, concurrent fill, mandate, expiry and accounting recovery tests | IN_PROGRESS |
| P0-04 | P0 | Effective single-owner authenticated mode | No signup/payments/client routes/workers accessible; all admin and exchange routes authenticated | IN_PROGRESS |
| P0-05 | P0 | Fail-closed e kill switch de execução durável | Testes de kill-switch durável por armazém (DeskKillSwitch) e recovery | PASS |
| P1-01 | P1 | Binance Spot market feed + paper + testnet | Real public feed verified, timestamps/fees, API isolation, recorded sandbox fills | NOT_STARTED |
| P1-02 | P1 | OKX Spot/demo adapter | Capability checks and verified simulated orders, never send real funds | NOT_STARTED |
| P1-03 | P1 | Mercado Bitcoin/Kraken/Coinbase adapters | Read-only public feeds; paper engine; capabilities vary | NOT_STARTED |
| P1-04 | P1 | Dynamic CCXT registry & personal catalogue | GET catalogue & static local capability metadata under auth; automated CI proof | PASS |
| P1-05 | P1 | IQ Option experimental room | Verify terms/data feed and separate options ledger, otherwise offline paper only | NOT_STARTED |
| P1-06 | P1 | Quant research and strategy promotion | Historical integrity, walk-forward, costs, out-of-sample evidence; no fabricated profits | NOT_STARTED |
| P1-07 | P1 | Three Binance demo trades investigation | Reconcile scheduler/signals/risk/API with actual logs; identify definitive stop cause | NOT_STARTED |
| P2-01 | P2 | Single-owner UX and cross-exchange dashboard | Responsive E2E tests; no fake buttons | NOT_STARTED |
| P2-02 | P2 | Security/observability/backups | Real restore, secrets scan, rollback, smoke against deployed paper mode | NOT_STARTED |
| G9 | GATE | Money-real launch | Explicit owner approval only after G0-G8 evidence; NEVER auto-approve | BLOCKED_BY_EXTERNAL_DEPENDENCY |

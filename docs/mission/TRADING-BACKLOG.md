# Trading Engineering Backlog — 2026-10-12
Source of truth for unresolved work. Never interpret an implementation commit as release approval.

| ID | Priority | Work | Evidence/acceptance | State |
| --- | --- | --- | --- | --- |
| P0-01 | P0 | Diagnose and fix GitHub Actions systemic failure | Causa raiz provada (CVEs em multidict/urllib3) e corrigida no pyproject.toml; CI 100% verde | PASS |
| P0-02 | P0 | Reconcile PR #3 and financial fee blockers F3-F6 | Property tests for occurrence-based fee valuation, liability settlement and single parse path — entregue (checkpoint C003; suíte 2131 passed no HEAD `2562bd6`) | PASS |
| P0-03 | P0 | Resolve kernel K1-K6 and multiprocess durable unique constraints | Crash, concurrent fill, mandate, expiry and accounting recovery tests — entregue (checkpoint C003; suíte 2131 passed) | PASS |
| P0-04 | P0 | Effective single-owner authenticated mode | Superfície SaaS 404 por padrão incl. prefixo `/v1/auth/oauth`; dono nunca trancado por plano vencido (`tests/test_single_owner_modo_windows.py`, 3/3; CI run `38429848704`) — checkpoint C004 | PASS |
| P0-05 | P0 | Fail-closed e kill switch de execução durável | Testes de kill-switch durável por armazém (DeskKillSwitch) e recovery | PASS |
| P1-01 | P1 | Binance Spot market feed + paper + testnet | Real public feed verified, timestamps/fees, API isolation, recorded sandbox fills | NOT_STARTED |
| P1-02 | P1 | OKX Spot/demo adapter | Capability checks and verified simulated orders, never send real funds | NOT_STARTED |
| P1-03 | P1 | Mercado Bitcoin/Kraken/Coinbase adapters | Read-only public feeds; paper engine; capabilities vary | NOT_STARTED |
| P1-04 | P1 | Dynamic CCXT registry & personal catalogue | GET catalogue & static local capability metadata under auth; automated CI proof | PASS |
| P1-05 | P1 | IQ Option experimental room | Verify terms/data feed and separate options ledger, otherwise offline paper only | NOT_STARTED |
| P1-06 | P1 | Quant research and strategy promotion | Backtest com histórico estritamente passado, walk-forward com revisão por janela, custos/slippage, teto PAPER do champion — `tests/test_research.py` + `tests/test_quant_intelligence.py` (checkpoint C004) | PASS_LOCAL |
| P1-07 | P1 | Three Binance demo trades investigation | Reconcile scheduler/signals/risk/API with actual logs; identify definitive stop cause | NOT_STARTED |
| P2-01 | P2 | Single-owner UX and cross-exchange dashboard | Painel PT-BR + camada quant/IA com testes de contrato; **E2E em navegador real executado (C006)**: login do dono pela tela "Entrar na sua mesa" e Trading Floor embutido provados; 2 defeitos corrigidos (login do dono, iframe `X-Frame-Options`); **automatizado em `tests/e2e/` (C007)** — 5 testes Playwright verdes com servidor real | PARTIAL |
| P2-02 | P2 | Security/observability/backups | Diagnóstico de subsistemas + central de incidentes + kill switch entregues; restore/rollback e smoke em modo paper implantado pendentes | PARTIAL |
| G9 | GATE | Money-real launch | Explicit owner approval only after G0-G8 evidence; NEVER auto-approve | BLOCKED_BY_POLICY |

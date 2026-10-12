# TRADING BACKLOG

## P0 — segurança e integridade financeira

| ID | Item | Owner | Severidade | Dependências | Evidência | Estado |
|---|---|---|---|---|---|---|
| P0-001 | Validar ledger durável e constraints transacionais | principal engineer | crítica | B6, K1–K6 | logs de CI, testes de reconciliation/ledger | IN_PROGRESS |
| P0-002 | Fechar F3–F6 e K1–K6 para taxas, passivos e freshness | auditor/quant | crítica | PR #3, kernel | matrizes de mutação e testes de falha | IN_PROGRESS |
| P0-003 | Provar autenticação e autorização do proprietário | security/devsecops | crítica | A1, A2 | testes de acesso anônimo e falso, logs de auth | IN_PROGRESS |
| P0-004 | Fail-closed e kill switch de execução | sre/ops | crítica | runtime, execution router | testes de kill-switch e recovery | PASS |
| P0-005 | Redigir e revisar auditoria de risco e recuperação | auditor | crítica | ledger/reconciliation | PR #3, docs/finalization | PASS |

## P1 — corretoras e catálogo pessoal

| ID | Item | Owner | Severidade | Dependências | Evidência | Estado |
|---|---|---|---|---|---|---|
| P1-001 | Catálogo dinâmico de exchanges com capacidades testadas | quant/ops | alta | CCXT, readiness checks | capability tests | IN_PROGRESS |
| P1-002 | Binance/OKX/Mercado Bitcoin/Kraken/Coinbase como salas prioritárias | architecture | alta | docs pessoais | documentação de escopo | IN_PROGRESS |
| P1-003 | IQ Option isolada e claramente experimental | architecture | média | documentação legal/termos | gate de isolamento | IN_PROGRESS |

## P2 — UX e operação do laboratório pessoal

| ID | Item | Owner | Severidade | Dependências | Evidência | Estado |
|---|---|---|---|---|---|---|
| P2-001 | UI/desk premium e menu funcional | product/design | média | front-end | revisão visual e smoke | NOT_STARTED |
| P2-002 | Paper/shadow/testnet com execução confiável | execution lead | média | ledger, router | smoke no modo simulado | NOT_STARTED |
| P2-003 | Observabilidade, backup/restore e incident recovery | sre | média | ops, backup | runbooks e smoke | NOT_STARTED |


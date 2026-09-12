# BASELINE E BACKLOG ÚNICO — DZ23 TradingAgents (retomada V2)

Branch de integração: `integracao-v2` (base `codex-v0.5.1` + camada Claude).
Data da inspeção: 12/09/2026. Suíte reproduzida nesta árvore:
**1031 passed / 2 skipped / 71 subtests**.

## 1. Estado real encontrado (não presumido)

- `main` @ 4cdd1fc = núcleo Gate A r12 + serviço com painel + broker CCXT.
  **Não continha** backtest, portfolio/sizing, memory, runtime, manifest,
  observability, execution nem worker durável.
- `codex-v0.5.1` @ 48bf8c8 = trabalho do Codex com esses módulos, Gate B/PIT
  cabeado nas fontes, `tradingagents/service/` (store/jobs/worker/api) —
  mas sem painel e com executor Alpaca (superado por decisão do dono).
- O zip enviado pelo Prado é byte-a-byte a árvore `main`; nada novo a recuperar.

**DECISÃO ASSUMIDA (integração):** base = `codex-v0.5.1` (núcleo mais
completo), sobre ela portada a camada Claude — `service/app.py`, painel,
`brokers/ccxt_bridge.py`, testes próprios e docs. Justificativa: preserva
integralmente o trabalho correto do Codex, evita motores duplicados e mantém
a decisão de corretora (CCXT) do dono. O `redact.py` ficou na versão do Codex
(import no topo, resolve o E402 anotado no Gate A) **com a correção de
folhas-string reaplicada** — vazamento provado corrigido e idempotência
verificada.

## 2. Matriz TA — verificada nesta árvore

| ID | Item | Status | Evidência |
|----|------|--------|-----------|
| TA-001 | Baseline consolidado | VERIFIED | árvores comparadas; integração em `integracao-v2`; 1031 testes |
| TA-002 | Instalação/empacotamento | IMPLEMENTED_NOT_VERIFIED | Dockerfile + pyproject presentes; falta validar wheel em ambiente vazio |
| TA-003 | Segurança web | VERIFIED | auth Bearer, trava anti-força-bruta (5/IP ⇒ 15 min), headers, config 0600 mascarada — 16 testes |
| TA-004 | RunContext imutável | IMPLEMENTED_NOT_VERIFIED | `tradingagents/runtime.py` + 16 testes Gate B; falta ligar ao caminho do serviço |
| TA-005 | PIT / available_at | IMPLEMENTED_NOT_VERIFIED | `dataflows/fundamentals_pit.py`, cutoff nas fontes; auditoria externa pendente |
| TA-006 | Banco/worker durável | **IN_PROGRESS (lacuna nº1)** | `tradingagents/service/store.py` fala Postgres (psycopg) e `worker.py` existe, mas o painel usa SQLite próprio em `service/app.py` — **dois armazenamentos de job** |
| TA-007 | Decisão/intenção | IMPLEMENTED_NOT_VERIFIED | `execution/models.py`, `engine.py` |
| TA-008 | Sizing/reservas | IMPLEMENTED_NOT_VERIFIED | `portfolio/sizing.py` — 25 testes |
| TA-009 | Ordens/reconciliação | PARTIAL | `execution/engine.py` + journal; ponte CCXT tem idempotência e gate, mas reconciliação pós-timeout não exercitada |
| TA-010 | Ledger | NOT_STARTED | sem partidas dobradas na árvore |
| TA-011 | Backtest | IMPLEMENTED_NOT_VERIFIED | `backtest/runner.py` — 18 testes; auditoria própria pendente |
| TA-012 | Avaliação temporal | PARTIAL | walk-forward/OOS não localizados |
| TA-013 | CCXT | VERIFIED (funcional) | 9 exchanges, testnet/live com frase, gate incontornável — 32 testes + prova viva |
| TA-014 | Jornada simulada | PARTIAL | análise ponta a ponta provada; PAPER/SHADOW com 13 testes, sem jornada integrada única |
| TA-015 | Observabilidade/custo IA | IMPLEMENTED_NOT_VERIFIED | `observability.py` — 9 testes |
| TA-016 | Frontend | PARTIAL | painel operável (config+operação+ordem); falta Mesa de Agentes, risco/carteiras, admin |
| TA-017 | Recuperação/release | NOT_STARTED | backup/restore e runbook não exercitados |

## 3. Matriz MA (mesa multiagente) — meta ampliada

| ID | Item | Status |
|----|------|--------|
| MA-01 | Mandatos/registry/permissões (DeskMandate, AgentSpec) | NOT_STARTED |
| MA-02 | Snapshots/propostas (MarketSnapshot, TradeProposal) | NOT_STARTED |
| MA-03 | Orquestrador persistente por eventos | NOT_STARTED (worker existe, ciclo da mesa não) |
| MA-04 | Três traders distintos e versionados | NOT_STARTED |
| MA-05 | Arbitragem + reserva transacional | NOT_STARTED (sizing existe, allocator não) |
| MA-06 | Execução + PositionGuardian | PARTIAL (execução sim, guardião não) |
| MA-07 | Reconciliação/atribuição | NOT_STARTED |
| MA-08 | Painel Mesa de Agentes | NOT_STARTED |
| MA-09 | Experimentos de equipe | NOT_STARTED |
| MA-10 | Testes de falha/aceite (18 cenários) | NOT_STARTED |

## 4. Próxima fatia (dependência real, não preferência)

**TA-006 — unificar o armazenamento de jobs**: o painel deve ler e escrever
no store durável do Codex, não num SQLite paralelo. Sem isso, a mesa (MA-03)
nasceria sobre duas filas com autoridade sobre a mesma carteira — exatamente
o que a missão proíbe. Depois: MA-01/MA-02 (contratos), MA-03 (ciclo),
MA-04/05 (traders + arbitragem), MA-06/07, MA-08 e MA-10.

## 5. Vereditos separados (nada declarado além do provado)

- IMPLEMENTAÇÃO: integração das duas árvores concluída e testada.
- VALIDAÇÃO LOCAL: 1031 testes; serviço, painel e broker provados ao vivo.
- IA/DADOS EXTERNOS: não comprovados (chaves de teste; falha de rede é o
  comportamento esperado observado).
- TESTNET: não comprovado (exige credencial real do dono).
- REVISÃO INDEPENDENTE: pendente — auditoria externa da v1.1.0 está com o
  Codex; esta integração exigirá nova rodada.
- PRODUÇÃO/LIVE: fora do escopo desta missão; capacidades preservadas sem
  acionamento.

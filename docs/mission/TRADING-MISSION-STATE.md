# TRADING MISSION STATE

- Fase atual: G0 — inventário, linha de base e CI
- Branch: `correcao-auditoria`
- Base: `main` (`527757a032d1a55287181937c7d574d74e21bb04`)
- HEAD atual (PR #3): `247038c6e076b00516954a9fc21a01c42a47c73e`
- UTC: `2026-10-12T00:00:00Z`
- Status: `IN_PROGRESS`

## Baseline verificada
- Repositório privado `LMPrado-DZ23/DZ23-Trading-AI`.
- PR #3 (`audit: DZ23 Trading release candidate — correcao-auditoria`) está aberto, draft, head `247038c6e076b00516954a9fc21a01c42a47c73e`.
- PR #5 (`feat/personal-trading-lab-20261012`) está aberto, draft, documento de plano pessoal; não é merge ou deploy.
- Arquitetura atual do repo já contém backend FastAPI, `service/app.py`, módulos `tradingagents/`, testes e workflows CI; o foco de auditoria permanece a preservação da lógica F1/F2 e dos bloqueadores P0 financeiros/segurança.

## Bloqueadores conhecidos
- F3/F4/F5/F6 ainda abertos.
- K1–K6 e A1/A2 continuam como arquitetura/qualidade de engenharia, exigindo validação e evidências antes de qualquer LIVE.
- `B6 multiprocess` permanece `BLOCKED_BY_ARCHITECTURE`: o ledger em memória ainda não garante `UNIQUE(fee_component_id)` entre processos; a expansão para multi-process exige prova de isolamento transacional ou migração para banco transacional.
- `LIVE_TRADING=false` e `REAL_MONEY_EXECUTION=false` continuam como estado de segurança.
- `SIGNUP_ENABLED` e billing/tenant continuam desabilitados ou isolados no runtime; não confundir o plano pessoal com "conversão concluída".

## Riscos operacionais
- Sem autenticação forte do proprietário em todos os endpoints privados, risco de autorização indevida.
- Sem validação de ledger e reconciliador por processo/multiprocess, risco de duplicidade de fills e reconciliation inconsistentes.
- Sem CI verde, qualquer release candidate permanece em risco de regressão de segurança e financeiras.

## Próximo comando executável
- `uv sync --locked && uv run pytest -q`
- `uv run python -m compileall -q tradingagents service scripts`

## Rollback
- Não há deploy de produção em `main`; base de integração e correções ficam em branch reversível.
- Em caso de regressão, revertem-se os commits da branch `correcao-auditoria` sem tocar `main`.

## Observação de segurança
- Não há autorização para operações LIVE, depósitos, saques, transferências ou alavancagem real.
- Qualquer credencial de teste/live deve permanecer em secret manager do host; nunca em logs, prompts, branch ou evidências públicas.


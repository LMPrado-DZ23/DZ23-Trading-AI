# TRADING CHECKPOINTS

## C000 — Inventário do repositório, baseline e preparação do CI

- Data UTC: `2026-10-12T00:00:00Z`
- Branch: `correcao-auditoria`
- Base: `main` (`527757a032d1a55287181937c7d574d74e21bb04`)
- Head: `247038c6e076b00516954a9fc21a01c42a47c73e`
- PR: #3 (`audit: DZ23 Trading release candidate — correcao-auditoria`)
- Resultado: `IN_PROGRESS`

### O que foi validado
- Repositório contém backend FastAPI em `service/` e módulos em `tradingagents/`.
- PR #3 já agrega correções em segurança, execução e auditoria; PR #5 é um plano documental pessoal, não um runtime operacional.
- `LIVE_TRADING=false` e `REAL_MONEY_EXECUTION=false` permanecem como requisito de segurança.
- `B6 multiprocess` continua bloqueado por arquitetura; qualquer operação em paralelo deve ser tratada como risco de ledger durável.

### Evidência de baseline
- CI e suíte de testes ainda precisam ser executados no ambiente oficial do projeto.
- Localmente a limitação de Python/OS requer cautela: ambiente do projeto não tem wheels para `langgraph`/`pandas`/`ccxt` em Python 3.14, portanto a validação final deve ocorrer em CI com a matriz correta.

### Próximo passo
- Executar:
  - `uv sync --locked`
  - `uv run pytest -q`
  - `uv run python -m compileall -q tradingagents service scripts`

### Resultado esperado
- a partir do conteúdo atual do repo e do PR em análise, não se deve declarar sucesso sem logs do CI e sem validação real da suíte.

### Próxima fatia
- `C001` — reproduzir a falha do CI real e corrigir a primeira regressão detectada, preservando blocos financeiros e arquitetura.


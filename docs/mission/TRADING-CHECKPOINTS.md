# TRADING-CHECKPOINTS (C002)

Data: 2026-10-12T22:12:00Z
Branch de trabalho: fix/ci-personal-catalog
Commit SHA (head nesta branch): d5c4a4da9cbb7f50c87f4000c50c518161bb0d7c
Workflow run relevante: https://github.com/LMPrado-DZ23/DZ23-Trading-AI/actions/runs/38306945719

Resumo do progresso até aqui
- Criei a branch `fix/ci-personal-catalog` e apliquei alterações no `service/app.py` para proteger as rotas do "personal trading lab" com a flag `PERSONAL_TRADING_LAB_ENABLED` (fail-closed).
- Adicionei testes de regressão em `tests/test_personal_endpoints.py` que verificam: (a) rota bloqueada por padrão; (b) rota disponível em modo apenas leitura quando a flag é habilitada.
- Workflow run 38306945719 existe e está com status `completed` (falha). A tentativa de recuperar logs via API automatizada retornou: "Job log not found" para job id interno — logs podem estar expirados, restritos por permissão, ou o job id preciso é necessário.

Checkpoint C002 — Objetivos e passos imediatos (P0-01: CI)
- Objetivo: identificar a causa-raiz das falhas de CI para SHA alvo e obter CI verde para o candidato de integração.
- Artefatos produzidos: branch `fix/ci-personal-catalog`, commit `d5c4a4da9cbb7f50c87f4000c50c518161bb0d7c` (teste + guard pessoal), teste `tests/test_personal_endpoints.py`.

Ações técnicas específicas a executar (ordem prioritária)
1. Recuperar logs completos do run 38306945719 via UI GitHub Actions (botão "Download logs") e colar o arquivo de log aqui ou permitir acesso. Alternativa: forneça o job id específico (por exemplo, 115003179054) se disponível no UI; eu posso tentar recuperar com `get-actions-job-logs`.
2. Reproduzir localmente o pipeline para o mesmo SHA:
   - python -m venv .venv && source .venv/bin/activate
   - pip install -U pip
   - uv sync --locked --extra desk --extra dev
   - uv run pytest -q
   - uv run python -m compileall -q tradingagents service scripts
   - uv run python scripts/smoke_agency_api.py  # defina ACCESS_PASSWORD=test-password
   - uv run python scripts/smoke_multitenant.py
   Capture e anexar qualquer traceback completo aqui.
3. Priorizar correções por tipo de falha (exemplos comuns):
   - ImportError / ModuleNotFound: instalar dependência faltante no pyproject/extra
   - SyntaxError / typing: ajustar compatibilidade Py310–Py313
   - Test failures: analisar asserts, fixtures e mocks (especialmente multi-tenant vs legacy)
   - Smoke failures: checar variáveis de ambiente esperadas pelo script (ACCESS_PASSWORD, TENANT_TOKEN_SIGNING_KEY, MULTI_TENANT_ENABLED)
4. Corrigir em branch reversível, rodar localmente até green, e push para disparar CI iterativo.
5. Documentar evidências: link do run com logs, artefato de testes, SHA final e resumo das correções.

Riscos conhecidos / hipóteses iniciais
- Falha frequente: import-time de módulos novos -> solução: lazy-import (ja aplicada para rotas pessoais).
- Dependências opcionais (ccxt) podem não estar instaladas no matrix do CI; testes/smoke devem mockar ou condicionar a presença do pacote.
- Multi-tenant bootstrapping exige chaves/variáveis; scripts smoke podem precisar sobrescrever variáveis para testes automatizados.

Dados de evidência necessários para fechar P0-01
- Logs completos do Actions run (zip ou colar aqui).
- Output de `pytest -q` local reproduzível para o mesmo SHA.
- Lista de jobs falhos e mensagens de erro principais.

Próximas ações que posso executar agora (escolha)
- Se você autorizar: eu posso tentar recuperar logs de job novamente se você fornecer o job id específico ou a permissão necessária. (Diga: "recuperar logs <job-id>")
- Criar/update do TRADING-BACKLOG.md com status P0/P1/P2 e tarefas bloqueadas por CI (faço isso automaticamente na mesma branch se você autorizar).
- Abrir PR draft unificando a correção de guard + testes contra `feat/personal-trading-lab-20261012` ou `main` (me diga o alvo: `feat/personal-trading-lab-20261012` ou `main`).

Checkpoint durável: este arquivo é salvo em `docs/mission/TRADING-CHECKPOINTS.md` na branch `fix/ci-personal-catalog`.

Assinatura: Copilot (execuções e commits automáticos autorizados nesta sessão)

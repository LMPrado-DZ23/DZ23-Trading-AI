# TRADING-CHECKPOINTS — DZ23-Trading-AI

Checkpoints duráveis da missão "terminar o projeto, testar e retestar" (Master V4.0 —
Auditoria Adversarial + Execução até os Gates). Um checkpoint por fatia fechada.
Valores de estado: NOT_STARTED, IN_PROGRESS, PASS, FAIL, BLOCKED_BY_EXTERNAL_DEPENDENCY,
BLOCKED_BY_ARCHITECTURE.

---

## C002 — INÍCIO OBRIGATÓRIO: auditoria de repo, PRs, CI e ambiente

- **UTC:** 2026-10-12T22:35Z–22:55Z
- **Branch:** feat/diagnostico-ci-20261012 (criada a partir de main @ 80e99fb)
- **SHAs:** b24e830 (workflow de diagnóstico) → a580f93 (bissecção + workflow_dispatch) → 2229e89 (correção de segurança)
- **Executor:** agente Qoder autenticado como LMPrado-DZ23 (ADMIN no repo; token com escopos repo+workflow)
- **PR:** https://github.com/LMPrado-DZ23/DZ23-Trading-AI/pull/6 (draft)

### O que foi auditado (com evidência)

1. **Repo e ambiente**
   - Clone local completo; `uv 0.13.0` instalado via pip (ferramenta do projeto);
     Python 3.12.10 local; `uv sync --locked --extra desk --extra dev` OK (exit 0).
   - `uv run ruff check .` → **"All checks passed!"** (exit 0) — repo limpo no lint strict.
   - Repo é **PUBLIC**; Actions habilitadas (`allowed_actions: all`); workflows ativas.

2. **CI — causa raiz da falha atual (P0-01): PROVADA com log**
   - Run 38318271663 (push @ a580f93, workflow CI): 4 de 5 jobs **verdes**
     (tests py3.12, clean-install smoke, public smoke checks, ruff). Único job que falhou:
     `dependency audit`, no step "Audit installed project dependencies", com log:
     - `multidict 6.7.1` — CVE-2026-104874 (correção: 6.9.1)
     - `urllib3 2.7.0` — PYSEC-2026-4177, PYSEC-2026-4176, PYSEC-2026-4175 (correção: 2.8.0)
   - **Runners funcionam**: workflow de diagnóstico trivial (run 38317719752, commit b24e830)
     executou `echo` com sucesso no ubuntu-latest.
   - **Correção aplicada** (commit 2229e89):
     - `urllib3>=2.8.0` via `constraint-dependencies` (ccxt 4.5.85 já pinna ==2.8.0).
     - `multidict>=6.9.1,<7` via `override-dependencies`: ccxt >=4.5.65 pinna exatamente
       `multidict==6.7.1` (vulnerável); sem override o resolver rebaixaria ccxt para 4.5.64.
       Mantido **ccxt 4.5.85** (última) — tradeoff documentado no pyproject.
     - Lock final: ccxt 4.5.85, multidict 6.9.1, urllib3 2.8.0, aiohttp 3.14.3.
   - **Validação local da correção:** `uv run pip-audit --local` → **"No known
     vulnerabilities found"** (exit 0). `uv run ruff check .` → limpo.
   - **Reteste em andamento:** suíte completa local com dependências novas (log:
     /tmp/pytest-retest.log); CI push run 38319157301 e pull_request run 38319185676
     no SHA 2229e89 — resultados a registrar no próximo checkpoint.

3. **CI — falhas históricas "zero steps" (desde 2026-09-25 em main): NÃO REPRODUZIDAS**
   - Evidência: 72 failures / 5 cancelled desde o último verde (run 34865826287,
     2026-09-14, commit 51cfc0e). Jobs falhavam em ~2s com 0 steps, sem output e com
     logs 404 (BlobNotFound). Última mudança no ci.yml antes das falhas: db5533e
     (2026-09-24, "harden multi-tenant operations and CI").
   - **Não reproduz:** o MESMO ci.yml (conteúdo de main + workflow_dispatch) executou
     steps normalmente nas runs 38318271663 (push), 38319157301 e 38319185676
     (pull_request — jobs criados e executando).
   - **Sem logs disponíveis (404), a causa histórica permanece SEM ATRIBUIÇÃO.**
     Hipóteses testadas e descartadas: infraestrutura de runner (workflow trivial
     passou), conteúdo atual do ci.yml (runs verdes/rodando), permissões de Actions
     (habilitadas), billing (repo público).
   - Nota: três workflows de bissecção adicionadas em a580f93 falharam com 0 jobs por
     YAML invocado por este agente (`name:` com dois-pontos não citado) — erro meu,
     anulado como experimento; a remover do branch.

4. **PRs abertos**
   - **PR #3** (correcao-auditoria → main): draft, CONFLICTING, 88 arquivos,
     +17079/−177. Patches de kernel/fee/segurança ainda não integrados. Não toca workflows.
   - **PR #5** (feat/personal-trading-lab-20261012 → main): draft, MERGEABLE, 16
     arquivos, +627/−0. Catálogo pessoal CCXT, discovery/capabilities, validação de
     quotes, API autenticada de leitura, testes novos, docs de missão. Não toca workflows.
   - **PR #4** e demais: bumps de Dependabot (actions checkout/setup-python/setup-uv v7).

### Estado dos gates (parcial, ver TRADING-MISSION-STATE.md na branch do PR #5)

- G0 AUDIT: IN_PROGRESS (este checkpoint).
- G7 QUALITY: IN_PROGRESS — causa raiz do audit corrigida; aguardando CI verde no SHA 2229e89.
- G1–G6, G8: NOT_STARTED neste ciclo (ver backlog).
- G9 LIVE: FORA DO ESCOPO (exige autorização explícita do proprietário).

### Riscos e rollback

- Risco: override de multidict contraria pin exato do ccxt. Mitigado: mesma major (6.x),
  piso sem salto de major, compatível com aiohttp 3.14.3, suíte completa revalidada.
- Risco: ccxt 4.5.85 (upgrade de 4.5.84): patch release de metadados de exchanges.
  Mitigado pela reexecução da suíte completa.
- Rollback: `git revert 2229e89` restaura lock anterior; nenhuma migração de dados envolvida.

### PRÓXIMA AÇÃO EXECUTÁVEL (primeira)

Aguardar conclusão das runs 38319157301 (push) e 38319185676 (pull_request) no SHA 2229e89;
se os 5/9 jobs verdes → checkpoint C003 fechando G0 (auditoria) e P0-01 (CI) como PASS,
remover as workflows de bissecção quebradas, e iniciar a integração do PR #5 (MERGEABLE)
sobre esta branch, seguida do PR #3 (CONFLICTING, resolução de conflitos preservando
correções contábeis), rodando a suíte completa após cada integração.

# DZ23 Trading AI — TRADING MISSION STATE

- **Fase atual (V7):** **G3 FECHADO (PASS)** — ciclo final de homologação executado no ARTEFATO FINAL
  (run `38450504893`, bundle do merge `0c3fc26`, estação Windows 11, item 12 de `WINDOWS-RELEASE-EVIDENCE.md`):
  instalar → abrir sem timeout falso → login do dono após 16 s no portão → **encerrar pelo botão "Fechar aplicativo"**
  (launcher exit 0 sozinho, porta livre) → **intruso real na 8000 → diálogo nativo "porta ocupada" → WM_CLOSE → exit limpo**
  → reinstalar (dados byte a byte idênticos) → desinstalar (dados preservados): **todos PASS** (ressalvas declaradas:
  sleep/wake/offline/crash não exercitados; assinatura de código BLOCKED_BY_EXTERNAL_DEPENDENCY).
  **MISSÃO QODER 01 concluída na branch de entrega `feat/qoder-maestro-traders` e mergeada por fast-forward na branch de integração (`d6491ab`):** Maestro Central + 10 traders com
  tiers aprovados×experimentais (3 homologados operam; 7 nascem SHADOW), gate de evidência `pode_promover()`
  (≥12 operações fechadas, retorno>0, acerto≥45%), rotas promover/pausar/retomar persistidas, Mesa de Agentes no
  painel e PAPER E2E com reinício — evidência em `QODER-MAESTRO-TRADERS-EVIDENCE.md` (16 testes novos; suíte completa
  `2196 passed / 5 skipped / 71 subtests`, exit 0).
- **C013 — bundle da entrega M01 homologado** (run Windows `38453302293`, artefato de `d6491ab`, item 13 de
  `WINDOWS-RELEASE-EVIDENCE.md`): `sha256sum -c` OK → instalar exit 0 → healthz 1ª tentativa → 16 s no portão → `ADMIN`
  → **Mesa de Agentes com os 10 traders por tier** (3 `aprovado (paper)` / 7 `experimental (paper)`) → encerramento pelo
  botão (launcher exit 0, porta livre) → reinstalar dados idênticos → desinstalar preservando `%APPDATA%`. CI da branch
  verde (`38453302349`/`38453307482`). Sem tag/release — publicação aguarda autorização explícita do dono.
- **C014 — operação PAPER ponta a ponta no bundle INSTALADO** (item 14 de `WINDOWS-RELEASE-EVIDENCE.md`, mesmo artefato
  do item 13): ciclo disparado pelos botões do painel (`20 propostas, 2 admitidas, 2 entradas, 0 saídas, 0 vetos` com veto
  do Supervisor visível), ledger balanceado e `pendências=[]`; **reinício do processo restaura o estado durável do disco**
  (2 ordens do OMS, posições BTC/USDT+ETH/USDT, contador do agendador com `ultimo_ciclo` preservado, disponível
  699.70000105/1000); encerramento pelo botão nas 2 sessões. **App deixado instalado na estação (estado de entrega).**
- **C015 — painel sem o SaaS morto + re-homologação (item 15)** (run Windows `38456790614`, artefato de `ba3307d`, item 15
  de `WINDOWS-RELEASE-EVIDENCE.md`): o DOM morto do SaaS (planos/pagamento, administração, cadastro, bootstrap, login
  social) foi **removido do painel** — o dono agora vê **exatamente 6 páginas**; contratos pinam a ausência; suíte completa
  `2193 passed / 4 skipped / 71 subtests` + e2e 6/6; no bundle instalado: `sha256sum -c` OK, 16 s no portão → `ADMIN`,
  6 páginas com título correto e **zero 5xx**, Mesa 3+7 por tier, PAPER ciclo e reinício durável re-provados. **App deixado
  instalado (estado de entrega).**
- **C017 — portão do dono único premium + auditoria Open Source (#28/#30 parcial)** na branch `feat/login-dono-unico-premium`
  (de `5fea049`): credencial do dono em **Argon2id** com migração silenciosa do `owner_password.txt`; assistente de primeira execução
  (código de recuperação exibido uma única vez); portão premium de 4 estados com "DZ23 Trading AI" no lugar de "DZ23 AGENCY" e badge
  MULTI-TENANT removida (`SINGLE_OWNER_ENFORCED` é trava de código); auditoria open source: Apache-2.0 preservada, `backtrader` (GPLv3+,
  sem uso) removido, CI com gitleaks + gate de licenças + SBOM, README/SECURITY/CONTRIBUTING/DEPLOY e site sem SaaS. **Prova:** suíte
  completa `2226 passed / 5 skipped / 71 subtests` (exit 0), e2e Playwright `9 passed` (primeira execução + recuperação com a senha
  antiga morrendo, mobile 390×844), ruff limpo. **Homologação no bundle concluída — item 17** (ver C018).
- **C018 — itens 16–17 homologados no bundle da integração** (run Windows `38467455263`, artefato de `d8892dc` = C016 + C017 + kernel fix PR #11; Setup `sha256:24b29dc1…` / zip `sha256:865bb4e9…`, `sha256sum -c` OK direto): **item 16 — fonte de dados (22/22 PASS, `HARNESS_EXIT=0`)** — seletor "Fonte de dados" no painel instalado com 2 opções honestas; feed real `PRONTO` (239 velas BTC/ETH, qualidade `ok`, cutoff na vela fechada); ciclo real se recusa a operar em dado velho ("snapshot vencido, não vira ordem"); **o defeito do "primeiro Salvar" morreu no kernel fix (PR #11) — salvar os padrões do painel em instalação nova não reconstrói mais a mesa (ciclos 1 → 1)**; volta a fixture com ledger balanceado (699.70) e `pendências=[]`. **item 17 — portão premium do dono único (24/24 PASS, `HARNESS_EXIT=0`)** — assistente de 1ª execução (instância limpa `setup_required=true`/`multi_tenant=false`), código de recuperação exibido uma única vez, **migração real do `owner_password.txt` → Argon2id sem perder acesso** (senha antiga continua entrando, `role=admin`; txt removido, JSON com `senha_hash`/`codigo_hash` Argon2id), marca "DZ23 Trading AI" e selos sem nenhum texto morto do SaaS, senha errada com aviso claro em PT, exatamente 6 páginas, encerramento pelo botão exit 0. App deixado instalado (estado de entrega).
- **Branch:** `feat/integracao-mestre-20261012` (PR #7; M01 mergeado por fast-forward; C016 direto; PR #10 (login C017) e PR #11 (kernel fix) **mergeados**) + `feat/qoder-maestro-traders` (entrega M01) + `feat/login-dono-unico-premium` (C017) + `fix/primeiro-salvar-efetivo` (PR #11)
- **Base:** `main` (`80e99fb`)
- **PR:** #7 (integração mestre) + branch M01 publicada + **#10 (C017 login) e #11 (primeiro Salvar) mergeados na integração**
- **HEAD:** `d8892dc` (integração: C016 + C017 + kernel fix do primeiro Salvar)
- **UTC:** `2026-10-13T21:24:00Z`
- **Status:** `IN_PROGRESS` — gates atuais em `docs/mission/TRADING-GATES.md` (G3 fechado; re-provado nos bundles M01, C015 e da integração — itens 13–17); checkpoints em `TRADING-CHECKPOINTS.md` (C000–C017 + M01 + C018)
- **LIVE:** `BLOCKED_BY_POLICY` (inalterado; nenhuma operação com dinheiro real)

## Baseline Verificada e Conquistas
- Repositório `LMPrado-DZ23/DZ23-Trading-AI`.
- **CI 100% Verde**: A causa raiz das falhas históricas do CI foi identificada como vulnerabilidades de dependências (`multidict` e `urllib3`). Após atualização no `pyproject.toml` e `uv.lock`, **todos os 8 jobs do CI** na matriz Python 3.10–3.13 passaram com sucesso (Run GitHub Actions #38321175829).
- **Integração de PRs**:
  - PR #5 (`feat/personal-trading-lab-20261012`): catálogo de corretoras pessoais CCXT e rotas autenticadas integradas.
  - PR #3 (`correcao-auditoria`): correções de kill-switch durável (`DeskKillSwitch`), reconciliação estrita do servidor e isolamento de ledger integrados sem conflitos.

## Próximos Passos (Fases P0/P1/P2)
1. **Fee Pipeline (F3-F6)** e **Kernel Durability (K1-K6)**: finalizar testes de propriedade e concorrência no livro-razão.
2. **Single-Owner Auth (A1/A2)**: desativar endpoints remanescentes de SaaS/multitenancy/billing e manter acesso estritamente autenticado ao proprietário único.
3. **LIVE Fail-Closed Protections**: validar em testes simulados a parada de emergência e reconciliação.

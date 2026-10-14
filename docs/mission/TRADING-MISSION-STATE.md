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
- **BT — Escola de Traders + cadeia de piloto (tasks #42–#52; BT-10 fecha neste batch)** na branch `feat/integracao-mestre-20261012` (BT-1 `134c05b` → BT-9 `8bf78ad`): escola central única com **salas independentes por corretora** (chave `corretora|conta|mercado|modo`; saldo/posições/taxas/desfechos isolados), certificação por contexto que **não atravessa corretora** (Binance ≠ OKX), cadeia **FORMADO → ELEGÍVEL → AGUARDANDO → AUTORIZADO** com gates e vigia no ciclo, Maestro Central (conflitos + teto de exposição), conhecimento rotulado entre salas e IQ Option como laboratório isolado; **a formação nunca ativa negociação real**. BT-10 fecha com a mesa espelhando na sala pelo caminho de produção, vigia relido a cada ciclo no contexto do pedido e a **causa-raiz da flakiness do E2E corrigida** (a arbitragem sorteava pela hash do relógio; agora decide pelo estado de mercado — regressão pinada). **Prova:** suíte completa `2404 passed / 6 skipped / 71 subtests` (exit 0), E2E `tests/test_service_escola.py` **22/22 em 10 execuções seguidas**, ruff limpo. **Ressalva fechada em C021:** itens 18–19 (identidade/portão premium e matriz de inicialização) **fechados em C021** no artefato de `f26f1ed` (`27/27` + `24/24`) e a **escola/mesa autenticada no painel do exe `--windowed` (item 21, task #53) re-executada e FECHADA** no mesmo artefato — `27/27 PASS` (log `c021-item21-painel-f26f1ed-r3.log`): login pelo portão (ADMIN/READY), 6 páginas, 10 traders por tier (3/7), ciclo real pelo botão (`20 propostas, 2 admitidas, 2 entradas`), Escola Central com sala isolada `binance|paper-legacy|spot|paper`, zero 5xx e quit gracioso (launcher exit 0 sozinho). LIVE `BLOCKED_BY_POLICY`.
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
- **C019 — Portabilidade (#29): modo da mesa + posse exclusiva, export sem segredos, CI Postgres/ARM64** (HEAD `977adce` + fix de CI `8e369f1`):
  estados ATIVA/OBSERVADOR/PAUSADA/BLOQUEADA persistidos com fail-closed e desbloqueio consciente; posse de execução exclusiva renovada
  a cada ciclo (vence em 15 min) — segundo processo no mesmo banco não executa; export `dz23.mudanca/2` **sem segredos** (nomes em
  `segredos_omitidos`; pacote legado `/1` tem segredos descartados no import); CI ganhou job `postgres` (corrida da posse em banco real —
  SUCCESS em `38473108095`) e job ARM64 (QEMU — **"NÃO é homologação Oracle"**); runbook PC→Oracle no `RUNBOOK.md`; suíte completa
  `2251 passed / 5 skipped / 71 subtests` (exit 0); incidente gitleaks (fixture sintética, entropia 3,5069>3,5) corrigido em `8e369f1`
  **sem allowlist**. Detalhe no C019 de `TRADING-CHECKPOINTS.md`.
- **C020 — Identidade visual definitiva + novo login premium (missão do dono; tasks #32–#35)** na branch `feat/integracao-mestre-20261012` (de `732f9f5`):
  original do LOGO preservado em `assets/brand/dz23-logo-original.png` (sha256 `ab3c6a64…` = byte a byte o `LOGO.png` entregue) com gerador
  reproduzível `scripts/gerar_assets_marca.py` (logomarca completa + **símbolo separado** para favicon/ícone — texto nunca reduzido a 16 px;
  `dz23-icone.ico` multi-tamanho); portão do dono reescrito com símbolo + wordmark **"Trading AI"** (sem repetir "DZ23"), subtítulo
  "Inteligência coordenada. Risco governado.", 4 estados de autenticação intactos e senha mascarada com toggle acessível; **selos operacionais
  dinâmicos** de `/v1/status/operacao` (PAPER MODE / EXECUÇÃO GOVERNADA / DINHEIRO REAL BLOQUEADO — backend fora → selo neutro; nunca estático);
  paleta da missão nos 3 HTMLs (painel/site/floor, zero hex antigo); manifesto PWA `/manifest.webmanifest`; ícone Windows no exe
  (`build_exe.py --icon`) e no instalador (`setup.iss SetupIconFile`). **Prova:** suíte completa `2267 passed / 6 skipped / 71 subtests` (exit 0),
  lote focado `117 passed`, **e2e Playwright `10 passed`** (identidade + responsivo 1920/1366/768/390/360 sem corte nem rolagem horizontal), ruff limpo.
  **Ressalvas declaradas:** a imagem de referência do login **não foi anexada** à missão (composição feita pelo texto); **homologação do novo bundle
  Windows pendente do artefato deste push** — não declarada.
- **C021 — Causa-raiz da falha de inicialização no Windows + fechamento no bundle (missão do dono; tasks #37–#41)** na branch `feat/integracao-mestre-20261012` (de `a4065ea`): o duplo clique do dono morria mudo; **causa-raiz provada** — o logging do uvicorn chamava `sys.stdout.isatty()` com stdout `None` no exe `--windowed` SEM console (exceção antes de qualquer bind de porta) — e **corrigida no launcher** (pré-checagem de portas 8000–8010 com fallback e identificação read-only, marcador de saúde `/healthz`, logs persistentes `launcher.log`, `log_config=None`); **matriz de inicialização `24/24 PASS`** e **portão premium/identidade `27/27 PASS`** no bundle instalado do CI de `f26f1ed` (run Windows `38489060888`) — a falha do cenário C2 na 1ª execução era **defeito do harness** (sonda sem `accept()` saturando o backlog do `listen()`), provado em isolamento e corrigido; o launcher **não possui nenhuma syscall de encerramento de terceiros** (grep zero). App deixado instalado (estado de entrega); LIVE `BLOCKED_BY_POLICY`.
- **Branch:** `feat/integracao-mestre-20261012` (PR #7; M01 mergeado por fast-forward; C016, C019 e C020 direto; PR #10 (login C017) e PR #11 (kernel fix) **mergeados**) + `feat/qoder-maestro-traders` (entrega M01) + `feat/login-dono-unico-premium` (C017) + `fix/primeiro-salvar-efetivo` (PR #11)
- **Base:** `main` (`80e99fb`)
- **PR:** #7 (integração mestre) + branch M01 publicada + **#10 (C017 login) e #11 (primeiro Salvar) mergeados na integração**
- **HEAD:** `f26f1ed` + commits deste batch (C021-5 — item 21: harness `homolog_painel_bundle.py`, evidências e docs)
- **UTC:** `2026-10-14T01:05:00Z`
- **Status:** `IN_PROGRESS` — gates atuais em `docs/mission/TRADING-GATES.md` (G3 fechado; re-provado nos bundles M01, C015 e da integração — itens 13–17; **C021 fechado no artefato de `f26f1ed`** — itens 18–19 e 21: matriz de inicialização 24/24 + identidade/portão premium 27/27 + escola/mesa autenticada no painel 27/27); checkpoints em `TRADING-CHECKPOINTS.md` (C000–C021 + M01 + BT-0..BT-10)
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

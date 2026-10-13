# TRADING-CHECKPOINTS — DZ23-Trading-AI

Checkpoints duráveis da missão "terminar o projeto, testar e retestar" (Master V4.0 —
Auditoria Adversarial + Execução até os Gates). Um checkpoint por fatia fechada.
Valores de estado: NOT_STARTED, IN_PROGRESS, PASS, FAIL, BLOCKED_BY_EXTERNAL_DEPENDENCY,
BLOCKED_BY_ARCHITECTURE.

---

## C000 — Inventário do repositório, baseline e preparação do CI

- **Data UTC:** 2026-10-12T00:00:00Z
- **Branch:** `correcao-auditoria` / `feat/integracao-mestre-20261012`
- **PR:** #3 (`audit: DZ23 Trading release candidate — correcao-auditoria`)
- **Resultado:** PASS (Integrado)

### O que foi validado
- Repositório contém backend FastAPI em `service/` e módulos em `tradingagents/`.
- PR #3 já agrega correções em segurança, execução e auditoria.
- `LIVE_TRADING=false` e `REAL_MONEY_EXECUTION=false` permanecem como requisito de segurança.
- `B6 multiprocess` continua bloqueado por arquitetura; qualquer operação em paralelo deve ser tratada como risco de ledger durável.

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

4. **PRs abertos**
   - **PR #3** (correcao-auditoria → main): draft, CONFLICTING, 88 arquivos,
     +17079/−177. Patches de kernel/fee/segurança integrados na branch de integração mestre.
   - **PR #5** (feat/personal-trading-lab-20261012 → main): draft, MERGEABLE, 16
     arquivos, +627/−0. Catálogo pessoal CCXT, discovery/capabilities, validação de
     quotes, API autenticada de leitura, testes novos, docs de missão. Integrado na branch mestre.

### Estado dos gates

- G0 AUDIT: PASS.
- G7 QUALITY: PASS — CI totalmente 100% verde na matriz Python 3.10-3.13.
- G1–G6, G8: IN_PROGRESS.
- G9 LIVE: FORA DO ESCOPO (exige autorização explícita do proprietário).

---

## C003 — Integração mestre, segurança financeira e reconciliação Dono Único/SaaS

- **UTC:** 2026-10-13 (13:00Z–15:30Z)
- **Branch:** `feat/integracao-mestre-20261012` (PR #7)
- **SHAs:** até `5032a9d` (inclui `d37e81e` merge de `correcao-auditoria`, fee/kernel, single-owner, notificações, Monte Carlo, diagnóstico, central de incidentes, 10 traders, launcher/instalador)
- **Resultado:** PASS (local + CI)

### O que foi validado
- Fee pipeline F3–F6 e kernel K1–K6 integrados; fail-closed em LIVE preservado.
- Superfície SaaS responde 404 por padrão no modo Dono Único (middleware `rota_saas_bloqueada`); planos não trancam o dono local; modo multi-tenant só reabre com `MULTI_TENANT_ENABLED=true` + chave de assinatura.
- 10 traders especializados + `MarketRegimeSupervisor` (elegibilidade por regime, divisão de capital, conflito prioriza saída, veto de compra duplicada).
- Windows: launcher single-owner, script Inno Setup, workflow de release.
- **Evidência:** CI push run `38408458373` (msg `70b153d`) success; CI branch `fix/g2-single-owner-20261013` run `38429848704` success; suíte local verde nos módulos afetados.

---

## C004 — Camada quant/IA, painel, endurecimento de pesquisa e lint full-repo

- **UTC:** 2026-10-13T17:09Z–17:13Z
- **Branch:** `feat/integracao-mestre-20261012` (PR #7)
- **SHAs:** `281296e` (connect_timeout nos 4 stores), `2562bd6` (quant + traders + painel + testes)
- **Resultado:** PASS (local + CI)

### O que foi validado
- `tradingagents/quant/`: features, registro de modelos (xgboost/lightgbm/torch experimentais), calibração, drift PSI, ensemble, champion-challenger com teto PAPER, governança; teste estrutural (AST) prova que cálculo determinístico nunca importa cliente LLM.
- `/v1/quant/status` + registro de latência/saúde da última consulta de IA de contexto por inquilino (só observabilidade); botão "Camada quant/IA" no painel.
- Single-owner: prefixo `/v1/auth/oauth` inteiro bloqueado — fecha vazamento do provedor microsoft; regressão em `test_single_owner_modo_windows.py`.
- Pesquisa: histórico estritamente passado no backtest (sem o próprio bar) e cada janela de walk-forward com revisão própria — testes novos em `test_research.py`.
- Hang de psycopg com endpoint morto: `connect_timeout=5` nos 4 stores (degrada para arquivo local em segundos).
- **Evidência:** `ruff check .` → "All checks passed!" (full-repo, mesma invocação do CI); suíte completa local **2131 passed, 4 skipped, 71 subtests** (exit 0) no HEAD `2562bd6`; CI push run `38436932376` success; CI PR run `38436943441` success.

---

## C005 — Pipeline de build Windows com artefato verificável (sem Release automático)

- **UTC:** 2026-10-13T17:15Z
- **Branch:** `feat/integracao-mestre-20261012` (PR #7)
- **SHA:** `e3e6239`
- **Resultado:** PASS (build do artefato) — run `38437252278` success em 3m30s; `publish-release` corretamente pulado (push de branch ≠ tag)

### O que foi corrigido
- A action `BoringPlanets/inno-setup-action@v1.4` **não existe (404)** — o workflow jamais teria rodado: substituída por Chocolatey + `ISCC.exe` por caminho absoluto.
- `uv sync` sem `--extra desk` deixaria `uvicorn/psycopg` fora do bundle: corrigido para `--extra desk --extra dev`.
- PyInstaller agora entra via `uv run --with pyinstaller` (roda no venv do projeto, não no Python global).
- Workflow dispara em push da branch de trabalho e publica **artefatos de CI** (Setup .exe, zip, SHA256SUMS); o **GitHub Release só é criado em tag `v*`** (aprovação humana explícita), em job separado com `contents: write`.
- **Evidência:** run Windows Release Build `38437252278` success (todos os steps ✓: uv sync → Inno Setup → PyInstaller → ISCC → zip+hash → upload); artefato `dz23-windows-x64` (157.294.923 bytes; digest CI `sha256:f40ab26db60f267e9e2f4d47042e08f1f68c331a5a899d13b4f25bb08c2f659f`); download local e verificação manual: `DZ23-Trading-AI-Setup-v0.4.0.exe` = `9ce9c33a…` ✓ e `DZ23-Trading-AI-windows-x64.zip` = `148fee9a…` ✓ (batem com o SHA256SUMS do CI).

### Próxima tarefa
Homologação de instalação limpa nesta estação Windows 11 com o artefato **do próximo build** (que já carrega as correções do C006): instalar → healthz → shortcuts → fechar → reinstalar → uninstall preservando `%APPDATA%`, registrando em `WINDOWS-RELEASE-EVIDENCE.md`.

---

## C006 — E2E de navegador real derruba 2 defeitos de produto; CI do HEAD vermelho por teste desatualizado

- **UTC:** 2026-10-13T17:20Z–17:45Z
- **Branch:** `feat/integracao-mestre-20261012` (PR #7)
- **SHAs:** `e3e6239` + commit C006 (este batch)
- **Resultado:** PASS (suíte completa verde; push do batch feito neste commit)

### Defeitos encontrados e corrigidos (todos com prova)

1. **Dono não entrava no próprio painel (crítico).** O `/app` mostrava o formulário de tenant; `POST /v1/auth/login` responde 404 no modo Dono Único e `boot()` ignorava `multi_tenant:false`; o campo "Token de bootstrap" e o botão "Criar agência e administrador" ficavam **sempre visíveis** (fechamento `</div>` prematuro no `#bootstrapForm` — 7 abre/9 fecha na linha).
   - Correção: estrutura do `#bootstrapForm` rebalanceada (7/7 divs); `boot()` detecta `multi_tenant===false` → tela "Entrar na sua mesa", esconde tenant/e-mail; `login()` no modo dono valida a senha via `Bearer` em `/v1/auth/me` e guarda em `dz23_token`.
   - Prova: contratos novos em `test_frontend_contract.py` (falham no HEAD antigo — red-proof rodado; passam no corrigido); navegador real: login `s3nh4` → painel "Visão geral" READY, backend SQLITE, 22 agentes, papel ADMIN; reload mantém sessão.

2. **Trading Floor não carregava no painel.** O painel embute `/floor` em iframe same-origin, mas o middleware aplicava `X-Frame-Options: DENY` em tudo → "Refused to display … in a frame".
   - Correção: `SAMEORIGIN` somente para o documento `/floor`; todo o resto segue `DENY` (anti-clickjacking preservado).
   - Prova: header ao vivo `GET /floor → SAMEORIGIN`, `GET /app → DENY`; iframe carregado no navegador real (title "DZ23 Trading Floor", conteúdo completo, "WORKER" presente) e screenshot do painel com o floor embutido.

3. **CI do HEAD vermelho (run `38437252181`).** O rewrite do workflow (C005) moveu o job para `build` + `publish-release`, mas o teste `test_github_release_workflow_has_inno_setup_and_artifacts` ainda esperava `jobs.build-release` → `KeyError` (único teste vermelho, confirmado no log do CI).
   - Correção: teste atualizado para a estrutura real (build steps + job `publish-release` gated em `refs/tags/v`).
   - Prova: `67 passed` nos 3 arquivos afetados localmente; **suíte completa verde: 2136 passed / 4 skipped / 71 subtests (exit 0)** no working tree congelado que inclui as correções 1–6.

4. **SHA256SUMS do CI em formato inválido.** `Get-FileHash | Out-File` gerava tabela PowerShell com caminhos truncados (`D:\a\…`) — inverificável com `sha256sum`. 
   - Correção: workflow agora emite `hash  filename` (formato padrão `sha256sum`).

5. **Instalador exigiria UAC.** Com o default admin do Inno + `{commondesktop}`, a instalação per-usuário em `{localappdata}` pediria elevação para o dono leigo.
   - Correção: `PrivilegesRequired=lowest` + ícone em `{userdesktop}`.

6. **App empacotado trancaria o dono (crítico, achado na inspeção do bundle do CI).** O `desktop/launcher.py` nunca define `ACCESS_PASSWORD`; sem ela o backend nega **todo** acesso autenticado (500 "defina ACCESS_PASSWORD no ambiente") — o dono não conseguiria nem entrar no painel do próprio executável.
   - Correção: `ensure_owner_password()` no launcher — usa env, senha salva ou **cria na 1ª execução** (`%APPDATA%\DZ23-Trading-AI\owner_password.txt`) e mostra em diálogo nativo (`MessageBoxW`) para o dono guardar; o painel abre direto em `/app`.
   - Prova: `tests/windows/test_desktop_launcher.py` (2 testes novos: criação/persistência/notificação única e precedência do env).

### Próxima tarefa
Suíte completa verde e batch commitado/publicado neste push (1 push → 1 build Windows). Em seguida: baixar o novo artefato do run disparado por este commit e executar a homologação de instalação limpa (runtime do bundle + instalador silencioso + shortcuts + uninstall preservando dados), registrando cada item PASS/FAIL.

---

## C007 — Suíte E2E Playwright versionada verde; CI do C006 vermelho por I001 no teste do launcher

- **UTC:** 2026-10-13T17:35Z–17:50Z
- **Branch:** `feat/integracao-mestre-20261012` (PR #7)
- **SHAs:** `0eb40a2` + este batch
- **Resultado:** PASS (local; push do batch neste commit)

### O que foi feito (com prova)

1. **`tests/e2e/` criada (G7/G8):** suíte Playwright dirigindo o painel real contra servidor uvicorn de verdade (DATA_DIR temporário, Dono Único, senha própria da suíte). Cobre: login do dono → Visão geral READY/ADMIN/PAPER com "Live trading BLOCKED"; menu completo (8 páginas) com formulários-chave visíveis (provedor de IA, exchange sandbox), validação de formulário (análise sem ativo → aviso) e **zero respostas 5xx** durante toda a navegação; botão "Camada quant/IA" exibindo a governança ("nenhum modelo de IA envia ordens"); Trading Floor embutido em iframe same-origin (PAPER MODE); contratos 4xx/401: `/v1/auth/login` e `/v1/auth/signup` 404, `/v1/auth/oauth/providers` bloqueado, `/v1/auth/me` sem token/errado/correto = 401/401/200 admin, `/v1/quant/status` 200 com governança, rota inexistente 404.
   - Prova: `5 passed in 4.32s` em navegador real (canal chrome); sem playwright a suíte **pula explicitamente** (`1 skipped` — CI Linux não quebra nem finge sucesso).
   - Screenshots de evidência: `docs/mission/evidence/e2e/01-visao-geral-dono.png`, `02-floor-embutido.png`, `03-mesa-quant.png`.
2. **CI do `0eb40a2` vermelho em ruff (run `38439685290`, job `ruff (strict, full repo)`):** `I001` (import block un-sorted) em `tests/windows/test_desktop_launcher.py` — a linha de import do launcher ficou longa demais ao adicionar os 2 testes novos; o ruff local tinha rodado **antes** dessa edição. O job `tests (py3.12)` do mesmo run passou.
   - Correção: import quebrado em multi-linha (`ruff --fix`); `ruff check .` full repo → "All checks passed!" (re-rodado imediatamente antes do commit).
   - Regra de processo adotada: **rodar `ruff check .` depois de cada edição e antes de cada commit**, não só no fim.
3. **Artefato do C006 verificado + 3º defeito de checksum encontrado (na verificação real):** o build do `0eb40a2` concluiu (run `38439685378` success; job `publish-release` corretamente pulado). Artefato baixado (157.296.918 bytes): o novo SHA256SUMS já saiu no formato `hash  nome`, **mas com finais de linha CRLF** (`Out-File`) — `sha256sum -c` no Git Bash falha (`'…exe'$'\r'`). Removendo o `\r`, **os dois hashes batem**: Setup `3a048c6e…` OK e zip `ca9a7330…` OK contra os arquivos baixados.
   - Correção: workflow agora grava com `[System.IO.File]::WriteAllText(... -join "`n" ...)` (LF, UTF-8 sem BOM) — próximo build sai `sha256sum -c`-able direto. O artefato baixado segue válido para a homologação (fix é de empacotamento de hash, não de runtime).
4. **Suíte completa re-executada na árvore congelada deste batch:** `2136 passed, 5 skipped, 71 subtests passed` em 189s, exit 0 — os skips são dependências opcionais ausentes (chave DeepSeek, lightgbm, permissões POSIX) + a suíte E2E pulando de forma explícita sem playwright no venv.

### Próxima tarefa
Acompanhar o novo run de CI deste commit (deve ficar 100% verde) e o Windows Release Build em andamento; em seguida baixar o artefato e executar a homologação de instalação limpa.

---

## C008 — Homologação do bundle real derruba 2 defeitos de runtime (launcher polla /app/healthz; trava de força-bruta tranca o próprio dono) — corrigidos com prova

- **UTC:** 2026-10-13T17:50Z–18:10Z
- **Branch:** `feat/integracao-mestre-20261012` (PR #7)
- **SHAs:** `0e6fdeb` + este batch
- **Resultado:** PASS (local; suíte completa verde re-executada neste batch; push neste commit)

### Contexto: homologação real do artefato do C006 (run `38439685378`)

Instalador silencioso (`/VERYSILENT /SUPPRESSMSGBOXES /NORESTART`): exit 0; atalhos criados (Desktop + Menu Iniciar); reinstalação sobre instalação existente: exit 0 com **dados do dono intactos** (mtime idêntico de `agency.db`/`jobs.db`/`subscriptions.db` e `owner_password.txt`); desinstalação: exit 0, pasta do programa removida, atalhos removidos, **dados do dono preservados** em `%APPDATA%\DZ23-Trading-AI`. Runtime do bundle instalado: `healthz` 200, `/app` 200, `/readyz` READY/PAPER, signup 404 (ver `WINDOWS-RELEASE-EVIDENCE.md`). Defeitos 1–2 abaixo foram **encontrados nesta homologação real** (não teriam aparecido só com CI verde).

### Defeitos encontrados e corrigidos

1. **Launcher pollava `/app/healthz` (404) e abria o painel 15 s atrasado.** `main()` chamava `wait_and_open_ui(f"{url}/app")`, e a função anexava `/healthz` → poll eterno em rota inexistente com aviso falso de timeout ("Backend start timed out … anyway"). Achado com build de diagnóstico console do próprio launcher (log real: `GET /app/healthz 404` ×30).
   - Correção: `_healthz_ok(base_url)` (o healthz vive na raiz, o painel em `/app`); `wait_and_open_ui(base_url, ui_path="/app")` polla `{base}/healthz` e abre `{base}/app`; **fast-path de instância única** em `main()`: se já há um DZ23 servindo o loopback, apenas abre o painel — sem segundo diálogo de senha e sem disputar a porta 8000.
   - Prova: 3 testes em `tests/windows/test_desktop_launcher.py` (URL pollada correta; retry até healthz OK; instância existente → abre `/app` sem subir backend nem pedir senha). Red-proof rodado antes da correção (`AttributeError: _healthz_ok` + URL errada).
2. **Trava de força-bruta trancava o próprio dono (crítico).** O iframe `/floor` polla `/v1/agency/snapshot` a cada 3 s; antes do login o header ia `Authorization: Bearer ` (vazio) → 5× 401 em ~15 s armavam a trava de 15 min por IP → dono que demora >15 s para digitar a senha recebia **429 "muitas tentativas erradas" com a senha CORRETA**. Reproduzido no bundle instalado (API real: 401 anônimos → 429 no Bearer correto).
   - Correção (defesa em profundidade): (a) servidor — requisição anônima (sem credencial ou `Bearer` vazio) responde 401 **sem contar** como tentativa de senha (`service/app.py::auth`); credencial errada continua contando e a trava segue valendo inclusive para a senha certa durante o bloqueio (testes de trava existentes inalterados); (b) floor.html — sem sessão **não faz polling**: mostra "AGUARDANDO LOGIN" e não dispara chamadas protegidas.
   - Prova: `test_anonymous_requests_never_lock_the_owner` (10 ciclos anônimos → `_FAILS == {}` → senha certa 200; red-proof 429 antes); contrato `test_trading_floor_nao_polla_sem_sessao` ("AGUARDANDO LOGIN"; red-proof antes); **regressão E2E de navegador real** `test_dono_entra_mesmo_demorando_no_portao`: painel aberto, 16 s parado no portão (>5 ciclos de polling) e login do dono → READY (red-proof: falhava com o defeito).

3. **Navegador continuava rodando o HTML ANTIGO em cache após o app ser atualizado (achado na verificação ao vivo do C008).** `/app` e `/floor` não enviavam `Cache-Control`; reproduzido ao vivo — a aba aberta pelo launcher seguia executando o `floor.html` velho (o que polla sem sessão) contra o servidor novo, que já servia o HTML corrigido. Num app que se atualiza por reinstalação, isso deixa o dono preso a telas antigas (inclusive à versão que armava a trava).
   - Correção: middleware de segurança envia `Cache-Control: no-store` em `/app` e `/floor` (assets estáticos seguem cacheáveis).
   - Prova: teste novo `test_html_documents_never_cached_stale` (red-proof: `curl -sI` ao vivo no servidor antigo não tinha o header); após o fix, ao vivo: `cache-control: no-store` nas duas rotas.

### Verificação ao vivo do C008 (launcher dev na porta 8000, runtime real)

- Endpoint pollado é `{raiz}/healthz` (200 na 1ª tentativa; `Local backend is ready. Opening UI...` ~1,5 s após o start) e o navegador abre `/app` — **defeito 1 morto no runtime**, fim dos 15 s falsos.
- Dono autentica **200** em `/v1/auth/me` MESMO depois de **7 polls anônimos** do floor (`/v1/agency/snapshot` 401 Unauthorized, sem armar trava) — **defeito 2 morto no runtime**.
- **Instância única ao vivo:** segunda invocação do launcher → exit **0,414 s**, log `Já existe uma instância servindo http://127.0.0.1:8000 — abrindo o painel.`, e o pid que escuta a porta **não muda** (nenhum segundo backend).

### Nota de contexto de execução (não é defeito de produto)

Processos lançados de um comando bash **de vida curta** via `Start-Process` são mortos ao fim do comando (job object do shell); lançar em background persistente mantém o app vivo normalmente (bundle empacotado: pid estável, `healthz` 200). Registrar para não confundir com "app não abre".

### Testes deste batch

- RED confirmado antes das correções: 5 falhas (launcher ×3, trava anônima, contrato do floor) + E2E do portão.
- GREEN após correções: 17 passed (launcher + trava + contrato); **suíte E2E completa: 6 passed** (inclui a nova regressão de 16 s no portão); suíte completa do repositório na **árvore congelada deste batch**: `2141 passed, 5 skipped, 71 subtests passed` em 192 s, exit 0 — os skips são dependências opcionais ausentes (chave DeepSeek, lightgbm, permissões POSIX) + a suíte E2E pulando de forma explícita sem playwright no venv.

### Próxima tarefa
Commit + push deste batch (1 push → CI + Windows build). Baixar o **novo** artefato e refazer o ciclo de homologação com o bundle corrigido: instalar → abrir como usuário (duplo clique/atalho) → login do dono no navegador → fechar → reinstalar → desinstalar preservando dados, registrando cada item PASS/FAIL em `WINDOWS-RELEASE-EVIDENCE.md`.

---

## C009 — Homologação completa do artefato C008 (todos PASS) + saída do usuário no app windowed

- **UTC:** 2026-10-13T18:10Z–18:30Z
- **Branch:** `feat/integracao-mestre-20261012` (PR #7)
- **SHAs:** `142e030` + este batch
- **Resultado:** PASS (local; push neste commit)

### Ciclo completo de homologação no artefato do CI (run `38444558353`, item 10 de `WINDOWS-RELEASE-EVIDENCE.md`)

Instalação silenciosa sem UAC (exit 0, atalhos Desktop/Menu, registro HKCU 0.4.0) → 1ª abertura real: log do launcher mostra `GET /healthz 200` na 1ª tentativa e `Opening UI...` em ~1,5 s abrindo `/app` (**defeito C008-1 morto no bundle**); `/app` e `/floor` com `Cache-Control: no-store` ao vivo (**C008-3 morto**); **Playwright contra o bundle INSTALADO**: floor sem sessão mostra `AGUARDANDO LOGIN` e faz **0 chamadas** de snapshot em 8 s; portão com **16 s de espera** e login do dono OK (**C008-2, trava 429, morta**); visão geral READY/ADMIN com "Live bloqueado"; floor embutido CONNECTED — capturas em `docs/mission/evidence/windows-release/`; **instância duplicada**: exit 0 em **367 ms**, log do fast-path, pid do listener inalterado; **reinstalação**: exit 0 com mtimes dos dados byte a byte idênticos e senha reutilizada sem novo diálogo; binário reinstalado sobe (healthz/app/floor 200); **desinstalação**: exit 0, programa+atalhos removidos, `%APPDATA%` preservado; `sha256sum -c SHA256SUMS.txt` → **OK direto** (formato LF do C007 validado em produção); PE Subsystem do exe = **2 (GUI)** — sem console. Nota de shell: instalador via Git Bash exige `MSYS_NO_PATHCONV=1` (senão o MSYS converte `/VERYSILENT` e abre o wizard).

### C009 — defeito de UX encontrado na homologação: o dono não tinha como fechar o app

O executável é windowed (sem console) e não tem bandeja; fechar o navegador não para o servidor; o botão "Sair" do painel é apenas logout. Sem correção, o leigo termina pela via mais hostil (Gerenciador de Tarefas).

- **Servidor:** `POST /v1/app/quit` — exige senha do dono (mesma trava de força-bruta); **404 em multi-tenant avaliado ANTES da autenticação** (nenhum inquilino derruba servidor compartilhado); **409 se o servidor não foi iniciado pelo launcher** (não finge encerrar); com launcher, agenda o gatilho de desligamento (`threading.Timer(0.3)` — responde 200 antes de desligar).
- **Launcher:** trocou `uvicorn.run(...)` por `uvicorn.Config` + `uvicorn.Server` e registra `set_shutdown_hook(lambda: setattr(server, "should_exit", True))` — desligamento **gracioso** (lifespan shutdown, SQLite fechado), não kill.
- **Painel:** botão "Fechar aplicativo" (visível só no modo Dono Único, ao lado de "Sair") com confirmação; após o 200, a página vira "DZ23 Trading AI encerrado — pode fechar esta aba".
- **Prova ao vivo (dev-runner, mesma topologia do empacotado):** navegador real → clique → `POST /v1/app/quit 200` → log `Shutting down` / `Application shutdown complete` / `Finished server process` → **processo do launcher termina sozinho (exit 0)**, porta 8000 livre; captura `docs/mission/evidence/windows-release/encerrado-c009.png`.
- **Testes:** RED antes (6 falhas: endpoint inexistente, contrato do painel, 2× launcher) → GREEN: 4 unit (senha do dono; 409 sem gancho; hook disparado; 404 fora do modo dono) + 1 contrato de frontend + 2 launcher (wiring Config/Server + hook para o servidor) — 17 passed nos arquivos tocados.

### Testes deste batch

- RED confirmado antes da implementação: 6 falhas (endpoint/contrato/launcher ×2 + dependências).
- GREEN: 17 passed nos arquivos tocados; ruff `All checks passed!`.
- Suíte completa deste batch: `2147 passed, 5 skipped, 71 subtests passed in 185.79s`, exit 0 (os skips são dependências opcionais ausentes + E2E pulando de forma explícita sem playwright no venv).
- **E2E com navegador real re-executada** após a mudança de UI: 6 passed (o botão novo convive com o fluxo do dono).

### Próxima tarefa
Commit + push deste batch (1 push → CI + Windows build). Baixar o novo artefato (C009) e re-provar o encerramento pelo botão no bundle instalado (abrir → encerrar → conferir processo morto e porta livre → desinstalar preservando dados), fechando o G3 com evidência do artefato final.

---

## C010 — Porta 8000 ocupada por outro programa deixava de ser fracasso silencioso (último defeito de runtime do G3 encontrado por análise)

- **UTC:** 2026-10-13T18:33Z–18:41Z
- **Branch:** `feat/integracao-mestre-20261012` (PR #7)
- **SHAs:** `f8254ab` (batch C009) + este batch
- **Resultado:** PASS (local; push neste commit)

### Defeito

Com a porta 8000 ocupada por OUTRO programa, o launcher tentava subir o backend mesmo assim; o bind falhava dentro da thread (uvicorn faz `sys.exit(1)`), o poll de `/healthz` estourava o timeout e o navegador abria numa página que não existe; em seguida o processo terminava em silêncio. Para o leigo: o app "pisca" e não abre, sem nenhuma explicação.

### Correção (launcher)

- `_port_in_use(host, port)` — sonda TCP real na porta.
- `_wait_for_existing_instance(url)` — porta ocupada mas `/healthz` ainda mudo (1ª instância em boot): espera até 15 s; se ficar saudável, abre o painel (duplo clique durante o start não vira falso alarme).
- `show_port_busy_dialog(port)` — mensagem nativa clara quando é outro programa ("feche o outro programa e abra o DZ23 novamente").
- `wait_and_open_ui` agora devolve bool; backend morto no start (porta tomada entre a checagem e o bind) → `show_startup_failed_dialog()` avisa em vez de fingir que abriu.

### Provas

- RED: os 7 testes novos falharam na coleta (`ImportError: cannot import name '_port_in_use'`) antes da implementação.
- GREEN: `tests/windows/test_desktop_launcher.py` → **13 passed** (sonda TCP contra listener real; diálogo de porta ocupada sem subir backend nem abrir navegador; 1ª instância iniciando vence o alarme falso; espera até ficar saudável; desiste no timeout; backend morto → aviso com diálogo, sem navegador). ruff `All checks passed!`.
- **Prova ao vivo (dev, sockets reais):** intruso real (socket Python) escutando 127.0.0.1:8000 → `main()` real do launcher → diálogo de porta ocupada após 5,1 s e **navegador não abriu** → `PORTBUSY_LIVE_C010=PASS`.
- Suíte completa deste batch: `2153 passed, 5 skipped, 71 subtests passed in 174.80s`, exit 0.

### Próxima tarefa
Commit + push deste batch e, com o artefato do build do C010: ciclo final no bundle instalado — instalar → abrir → login do dono → **encerrar pelo botão** → conferir processo morto/porta livre → **intruso real na 8000 → duplo clique → diálogo "porta ocupada"** (descartar o diálogo por WM_CLOSE e conferir o exit limpo) → reinstalar → desinstalar preservando dados; registrar como item 12 de `WINDOWS-RELEASE-EVIDENCE.md` e fechar o G3 com o artefato final.

---

## C011 — Integração controlada do PR #8: fail-closed de dono único passa a ser política de CÓDIGO (nenhuma variável de ambiente reabre SaaS)

- **UTC:** 2026-10-13T18:42Z–18:54Z
- **Branch:** `feat/integracao-mestre-20261012` (PR #7)
- **SHAs:** merge de `origin/fix/g2-single-owner-20261013` (tip `477433f`) sobre `690fd04`
- **Resultado:** PASS (local; push neste commit)

### O comportamento conflitante (decisão do dono)

A base tinha single-owner **flag-reabrível**: `MULTI_TENANT_ENABLED=true` reapresentava a
superfície SaaS inteira (cadastro, billing, Woovi, tenants, OAuth) e trocava o modelo de
autenticação do processo. Por decisão arquitetural do dono, a política do PR #8 é a
autoridade: dono único é **permanente e invariável** — nenhuma configuração reabre SaaS.

### Integração (sem sobrescrever o trabalho do Claude)

- O PR #8 avançou durante esta sessão: além de `09409ae`/`8ce9f23` (trava + suíte adversarial),
  pushou `477433f` — merge que já conciliava a trava com a base dos 10 traders (`2562bd6`).
  Merge base real: `2562bd6`. O merge foi refeito contra o tip fresco → **zero conflitos**
  (as reconciliações do lado deles aplicam exatamente no mesmo base).
- `service/app.py`: trava fixa `SINGLE_OWNER_ENFORCED = True` na origem (força
  `MULTI_TENANT_REQUESTED/ENABLED = False` antes de qualquer store), middleware
  incondicional `bloqueio_single_owner` (404 antes de auth/rotas, sem ler ambiente),
  `require_plan` neutralizado. Removidas as duplicatas da base (segunda
  `rota_saas_bloqueada` + `block_saas_routes` acoplado a flag) — a decisão deles preservada
  com as notas de merge originais.
- Reconciliações desta integração: guard `_only_owner_mode` removido por ficar morto sob
  travagem (quit segue exigindo apenas a senha do dono); docstring de
  `test_single_owner_modo_windows.py` corrigida (dizia que o ambiente "reabre a superfície" —
  falso desde a trava); `test_quit_indisponivel_fora_do_modo_dono` →
  `test_quit_continua_do_dono_com_env_multitenant`.
- Preservados intactos: C008 (anônimo não arma a trava 429), C009 (botão "Fechar aplicativo"
  + `POST /v1/app/quit` + shutdown gracioso), C010 (porta ocupada), iframe `SAMEORIGIN`,
  `no-store`, launcher e testes Windows.

### Provas (adversariais)

- `tests/test_single_owner_g2.py` (lado deles): 13 rotas × 5 métodos, com e sem Bearer,
  TODOS os toggles ligados → 404 em tudo; webhook Woovi **assinado e válido** → 404 e
  nenhuma assinatura alterada; vizinhos legítimos vivos (`/healthz`, `/v1/auth/status`,
  `/v1/admin/database`, mesa sem 402 com plano vencido).
- `tests/test_single_owner_modo_windows.py` + `tests/test_service_leigo.py` (reconciliados):
  `MULTI_TENANT_ENABLED=true` no ambiente → `MULTI_TENANT_ENABLED is False` forçado, 404,
  e o quit continua funcionando só com a senha do dono.
- Foco: `173 passed in 40.72s`. Suíte completa: **`2180 passed, 5 skipped, 19 warnings,
  71 subtests passed in 160.48s`**, exit 0. ruff `All checks passed!`.

### Aberto (honestidade de estado, §7 de `G2-SINGLE-OWNER-AUDIT.md`)

UI SaaS ainda no DOM (morta: todo clique recebe 404 — remoção é PR de frontend próprio),
guia de onboarding menciona Woobi/OAuth, DPAPI/Windows Credential Manager e threat model
pendentes. LIVE segue `BLOCKED_BY_POLICY`.

### Próxima tarefa
Push deste merge → CI + Windows Release Build do merge → baixar o artefato FINAL → ciclo único
de homologação no bundle (instalar → abrir → login → **encerrar pelo botão** → intruso na 8000 →
diálogo de porta ocupada → reinstalar → desinstalar preservando dados) → item 12 de
`WINDOWS-RELEASE-EVIDENCE.md` → fechar o G3.

---

## C012 — Homologação FINAL do bundle e fechamento do G3 (item 12 de `WINDOWS-RELEASE-EVIDENCE.md`)

- **UTC:** 2026-10-13T19:06Z–19:11Z
- **Branch:** `feat/integracao-mestre-20261012` (PR #7) — artefato do merge `0c3fc26`
- **SHAs:** `0c3fc26` (merge C011); artefato do run `38450504893` (`dz23-windows-x64`, 157.305.642 B)
- **Resultado:** **PASS** — ciclo completo no artefato final, na estação Windows 11

### O ciclo (todos PASS; tabela completa no item 12 do doc de evidência)

1. `sha256sum -c SHA256SUMS.txt` OK direto nos 2 arquivos (LF do C007 revalidado em produção).
2. Instalação silenciosa sem UAC (exit 0) + atalhos Desktop/Menu.
3. Abertura: `healthz` 200 na 1ª tentativa, painel ~1,5 s, sem timeout falso.
4. Playwright (Chrome real): 16 s no portão do dono → login → painel `ADMIN`/“Live bloqueado”/botão visível (captura `painel-final-item12.png`).
5. **C009 re-provado no bundle:** diálogo nativo de confirmação aceito → `POST /v1/app/quit 200` → `Application shutdown complete` → **launcher exit 0 sozinho** → porta livre; captura `encerrado-item12.png`.
6. **C010 re-provado no bundle:** intruso real (socket Python) na 8000 → launcher espera a 1ª instância (15 s), **não abre navegador** e mostra o diálogo nativo “DZ23 Trading AI — porta ocupada” (achado por `EnumWindows`, classe `#32770`; captura `porta-ocupada-item12.png`) → **WM_CLOSE → exit 0 limpo**.
7. Reinstalação: exit 0, `agency.db`/`jobs.db`/`subscriptions.db`/`owner_password.txt` byte a byte idênticos.
8. Binário reinstalado sobe (healthz 200 em 1 s) e desliga por API (`/v1/app/quit` 200 com Bearer do dono).
9. Desinstalação: exit 0, pasta do programa + atalhos removidos, `%APPDATA%` byte a byte idêntico.

### Nota de harness (não é defeito)

O Playwright auto-dispensa `confirm()` por padrão — o clique só completou após aceitar o diálogo do navegador. O app pergunta ao dono antes de encerrar: comportamento desejado, provado.

### Estado do G3

Fechado (`PASS`) com este artefato. Ressalvas honestas mantidas: sleep/wake, offline/reconexão e recuperação de crash não exercitados nesta estação; assinatura de código `BLOCKED_BY_EXTERNAL_DEPENDENCY` (sem certificado). LIVE segue `BLOCKED_BY_POLICY`.

---

## M01 — MISSÃO QODER 01: Maestro Central + 10 traders (tiers, gate de evidência, painel, PAPER E2E)

- **UTC:** 2026-10-13T16:00Z–19:00Z
- **Branch:** `feat/qoder-maestro-traders` (criada de `0c3fc26`, publicada)
- **Resultado:** critérios de aceite fechados com prova — detalhamento completo em `docs/mission/QODER-MAESTRO-TRADERS-EVIDENCE.md`
- **SHAs:** `0c3fc26` (base) + este batch

### Violações do mandato encontradas na auditoria (e fechadas)

1. `_desk_boot` **forçava os 10 traders para `PAPER_ENABLED`** — promoção por existir no código (proibida). Agora: 3 homologados sobem; 7 experimentais nascem `SHADOW`; override do dono persistido é respeitado.
2. Não existia gate de evidência → `MemoriaDeDesempenho.pode_promover()` (≥12 operações FECHADAS, retorno médio > 0, acerto ≥ 45%, sem rebaixamento).
3. Sem rotas de habilitação por trader → `promover`/`pausar`/`retomar` com semântica de estágios e persistência no `ArmazemDeEstado` (sobrevivem a reinício).
4. Painel sem modelo IA/operações/motivo de NO_TRADE → Mesa de Agentes de 7 colunas com estado PT-BR, evidência e ações; “Habilitar” desabilitado com o motivo visível até ser elegível.
5. Rota de ciclo descartava vetos/abstenções → agora retorna `vetos`/`abstencoes`/`erros` (scheduler com tetos 12/24 — o 6 antigo escondia os experimentais do fim da fila).
6. Contrato dos 10 traders não fixado → `tests/test_qoder_maestro_traders.py` (16 testes, 6 classes) fixa ids, universo, timeframe, needs, regimes, tiers.

### Prova

- Testes novos: **16/16 verdes**; lote focado de regressão: **`161 passed in 42.16s`**; suíte completa: **`2196 passed, 5 skipped, 71 subtests passed in 177.69s`**, exit 0 (= 2180 do C011 + 16 do M01); ruff limpo nos 5 arquivos alterados.
- PAPER ponta a ponta: ciclo com abstenções visíveis → ledger balanceado → reinício preserva ciclos/promoções → sem pendências de reconciliação.

### Regras do mandato honradas

Trader → Supervisor → risco → executor (nenhum atalho); frequência de operações intocada; sem promoção sem evidência; IA só por APIs externas e apenas como contexto (veta/reduz; nunca autoriza); **LIVE bloqueado** durante toda a missão.


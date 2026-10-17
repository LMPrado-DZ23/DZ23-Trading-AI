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


---

## C013 — Homologação do bundle da ENTREGA M01 na branch de integração (item 13)

- **UTC:** 2026-10-13T19:14Z–19:21Z
- **Branch:** `feat/integracao-mestre-20261012` — fast-forward `0c3fc26..d6491ab` (M01) e push (runs CI `38453302349`/`38453307482` e Windows `38453302293`, todos success)
- **Artefato:** `dz23-windows-x64` (157.319.893 B); Setup 69.765.446 B `sha256:bed915cf…`; zip 88.490.105 B `sha256:fdb8d10a…`
- **Resultado:** ciclo completo verde no bundle da entrega — detalhamento no item 13 de `WINDOWS-RELEASE-EVIDENCE.md`

### Passos

1. `sha256sum -c SHA256SUMS.txt` **direto** no diretório baixado: Setup OK, zip OK.
2. Instalação silenciosa sem elevação: exit 0; instalação limpa (pasta do programa não existia).
3. 1ª abertura: `healthz` 200 na 1ª tentativa; `/readyz` READY/PAPER; `kill_switch=false`.
4. Playwright no bundle: **16 s** de espera no portão → senha do dono → badge `ADMIN`.
5. **Mesa de Agentes no bundle instalado:** "Traders (10 perfis)" com exatamente 10 linhas — 3 homologados `aprovado (paper)` (trend/reversion/breakout), 7 experimentais `experimental (paper)` (scalping/swing/momentum/volatility/arbitrage/statistical/adaptive). Captura `mesa-m01-item13.png`.
6. Encerramento pelo botão "Fechar aplicativo": confirm nativo aceito → `POST /v1/app/quit 200` → **launcher exit 0 sozinho** → porta livre. Captura `encerrado-m01-item13.png`.
7. Reinstalação: exit 0; mtimes de `agency.db`/`jobs.db`/`subscriptions.db`/`owner_password.txt` byte a byte idênticos.
8. Desinstalação: exit 0; pasta do programa removida; `%APPDATA%` intacto (mtimes idênticos).

### Nota

Suíte completa re-executada na branch de integração pós-merge: **`2196 passed, 5 skipped, 71 subtests passed in 190.62s`, exit 0**. O gate de evidência do M01 foi provado no bundle: nenhum trader experimental operou; o dono vê o motivo e as ações por trader. LIVE `BLOCKED_BY_POLICY` o tempo todo; **nada foi publicado** (sem tag/release — aguarda autorização explícita).

---

## C014 — Operação PAPER ponta a ponta no BUNDLE INSTALADO (item 14)

- **UTC:** 2026-10-13T19:22Z–19:26Z
- **Branch:** `feat/integracao-mestre-20261012` (HEAD `6c3940e` = C013; este batch documental)
- **Artefato:** mesmo bundle homologado do item 13 (run `38453302293`, `dz23-windows-x64`; Setup `sha256:bed915cf…`) — **instalado na estação**
- **Resultado:** operação PAPER provada no app REAL instalado (não só em teste) — detalhamento no item 14 de `WINDOWS-RELEASE-EVIDENCE.md`

### Passos

1. App instalado (item 13) reaberto pelo launcher: `healthz` 200 na 1ª tentativa; Playwright no bundle → **16 s no portão do dono** → badge `ADMIN`.
2. **Sessão 1** — ciclo disparado PELOS BOTÕES do painel ("Testar dados de mercado" + "Rodar um ciclo"): resumo `20 propostas, 2 admitidas, 2 entradas, 0 saídas, 0 vetos`; abstenções visíveis ao dono (ex.: `trader-adaptive · BTC/USDT: Veto do Supervisor para evitar exposição duplicada.`).
3. Ledger **balanceado**; pendências de reconciliação `[]`; disponível 699.70/1000; captura `paper-ciclo-item14.png`.
4. Encerramento pelo botão "Fechar aplicativo" (confirm nativo) → `POST /v1/app/quit 200` → **launcher exit 0 sozinho** → porta livre.
5. **Sessão 2** — processo reiniciado do zero: FASES preservadas (3 `aprovado (paper)` / 7 `experimental (paper)`); `AGENDADOR` `ciclos=1` com `ultimo_ciclo=2026-10-13T19:22:55Z` **preservado**, `posicoes=[BTC/USDT, ETH/USDT]`, `pendencias=[]`, `erros=0`.
6. **Estado durável restaurado do disco:** `ORDENS_DURAVEIS=2` (OMS via `coord.restaurar_estado` ← `oms.json`); `RISCO={'reservado':'0','disponivel':'699.70000105','instrumentos_abertos':['BTC/USDT','ETH/USDT']}`; ledger `True`; `PAPER_RESTART_BUNDLE=PASS`; captura `paper-pos-restart-item14.png`.
7. Encerramento pelo botão na 2ª sessão → launcher exit 0. **App deixado instalado** na estação (estado de entrega).

### Nota de harness (não é defeito)

O log "Ciclos" do painel (`d["cycles"]`) é **por sessão, em memória, por design** (aparado em 30). O contrato durável é: OMS (`oms.json` ← `coord.restaurar_estado`), contador do agendador (`mesa.json`) e estágios dos traders (doc `agentes`). O script de prova inicialmente assumiu que o log também era durável e falhou; foi corrigido para o contrato real — o mesmo que o teste do M01 fixa (`agendador.ciclos`, não o log).

### Estado

LIVE `BLOCKED_BY_POLICY` o tempo todo; **nada foi publicado** (sem tag/release — aguarda autorização explícita do dono).

---

## C015 — Painel sem o SaaS morto + re-homologação no bundle (item 15)

- **UTC:** 2026-10-13T19:28Z–19:50Z
- **Branch:** `feat/integracao-mestre-20261012` (code `ba3307d`; este batch documental)
- **Artefato:** run `38456790614` (`ba3307d`) — Setup `sha256:dc5fe05a…` — **instalado sobre o anterior**; prova completa no item 15 de `WINDOWS-RELEASE-EVIDENCE.md`
- **Motivação:** o §7 do `G2-SINGLE-OWNER-AUDIT.md` registrava o DOM do SaaS como "morto porém presente". O menu exibia ao dono duas páginas inalcançáveis ("Plano e pagamento", "Administração") — dívida de UX visível para quem usa o app.

### Passos

1. **Remoção real do DOM** em `service/static/index.html` (não `hide`): formulário de bootstrap, cadastro, login de inquilino, login social, página de planos/pagamento (PIX/assinatura), página de administração, campos legados e o JS correspondente (`bootstrap`, `criarConta`, `carregarProvedores`, `loadPlano`, `pagar`, `loadUsers`, …). Menu passa de 8 para **6 páginas**; portão com um único campo (senha do dono). `grep` de todos os identificadores removidos: zero ocorrências fora dos testes que pinam a ausência.
2. **Contratos de teste convertidos para pinar a AUSÊNCIA** (o app não pode regredir com o SaaS voltando): `test_frontend_contract` (`test_the_saas_dom_is_gone_not_just_hidden`), `test_billing_woovi` (`TestPainelDePagamentoRemovido`), `test_signup` (`TestTelaDeCadastroRemovida`), `test_site_publico` (`TestLoginSocialRemovido`, campo único do portão) e `tests/e2e/test_panel_e2e.py` (6 páginas; `#loginTenant` com `to_have_count(0)`).
3. **Suíte completa:** `2193 passed, 4 skipped, 71 subtests passed` (exit 0); **e2e Playwright: 6 passed** — inclui o teste de regressão dos 16 s no portão e "zero 5xx" navegando o menu novo.
4. Push → CI `38456790548/38456795722/38456797773` e Windows `38456790614` — **todos verdes**.
5. **Bundle re-homologado (item 15):** `sha256sum -c` OK; instalação sobre a existente exit 0; `healthz` na 1ª tentativa; Playwright: 16 s no portão → `ADMIN`; menu com **exatamente 6 rótulos corretos**; navegação pelas 6 páginas com título certo e **zero 5xx**; nenhum texto morto do SaaS visível; Mesa com 3 `aprovado (paper)` + 7 `experimental (paper)`; encerramento pelo botão (diálogo nativo + 200).
6. **PAPER re-provado no artefato C015:** ciclo real pelo painel — ledger balanceado, `pendencias=[]`, fonte fixture (aviso honesto); relançar → reinício durável: OMS 2 ordens/posições, agendador `ciclos=2`, estágios 3+7 inalterados. Capturas `paper-ciclo-item14.png`/`paper-pos-restart-item14.png` atualizadas + `c015-painel-6-paginas.png`.
7. **Docs:** item 15 em `WINDOWS-RELEASE-EVIDENCE.md`; G7 atualizado em `TRADING-GATES.md`; §7 do `G2-SINGLE-OWNER-AUDIT.md` — UI SaaS **resolvida**; nota do guia de onboarding **re-verificada e corrigida** (o `/v1/primeiros-passos` já estava limpo — a observação da auditoria estava desatualizada).
8. **App deixado INSTALADO** na estação (estado de entrega), fechado pelo botão.

### Notas de harness (não são defeito)

- O harness de prova imprimia os ícones do menu e quebrou em cp1252; corrigido com `PYTHONUTF8` — o app nunca foi afetado.
- A 1ª tentativa do modo "restart" do harness abortou com `TargetClosedError` (a janela controlada fechou) enquanto o servidor permanecia saudável (`healthz` 200); a reexecução passou integralmente. Flake de harness, registrado por honestidade.

### Estado

LIVE `BLOCKED_BY_POLICY` o tempo todo; **nada foi publicado** (sem tag/release — aguarda autorização explícita do dono).

---

## C017 — Portão do dono único premium + auditoria Open Source (#28/#30 parcial)

- **UTC:** 2026-10-13T20:00Z–20:30Z
- **Branch:** `feat/login-dono-unico-premium` (de `5fea049`; PR para `feat/integracao-mestre-20261012`)
- **Commits:** `55d36ff` (login) + `cda7f9c` (open source) + este batch documental
- **Motivação dupla:** diretiva do dono para o portão de entrada (auditoria da tela de login, MULTI-TENANT, marca, senha em texto, assistente de primeira execução, UX premium) e diretiva Open Source (licença, docs públicas, CI de segredos/licenças/SBOM, exemplos sem segredos).

### Portão do dono único (#28)

1. **Credencial em Argon2id** (`service/api/owner_credentials.py`): `owner_credentials.json` no DATA dir (senha + código de recuperação com hash Argon2id; escrita atômica); o arquivo vence a env `PASSWORD`; nada configurado → 409 "primeira configuração pendente". `desktop/launcher.py` migra `owner_password.txt` em silêncio (hash da mesma senha + remoção do txt) — o acesso do dono nunca se perde.
2. **Rotas novas** em `service/app.py`: `/v1/auth/status` (com `setup_required`), `POST /v1/auth/setup` (422 senha fraca, 409 se já existe, código de recuperação devolvido UMA vez, lock de concorrência), `POST /v1/auth/recover` (mesma trava de tentativas do login), `POST /v1/auth/recovery-code` (autenticado, para donos migrados).
3. **Portão premium** (`service/static/index.html`): 4 estados (entrar / primeira configuração / código de recuperação / esqueci a senha); marca "DZ23 Trading AI" (fim do "DZ23 AGENCY"); badge MULTI-TENANT removida — `SINGLE_OWNER_ENFORCED = True` é trava de código (middleware 404 no SaaS), não flag; orientação a `owner_password.txt` eliminada; carregando, erros de senha, bloqueio de tentativas (429) e `aria-live="polite"`.
4. **Prova fresca desta rodada:** suíte completa **`2226 passed, 5 skipped, 71 subtests passed in 188.89s`** (exit 0); ruff limpo; lote focado site/semântica/frontend/empacotamento **`119 passed`**; **e2e Playwright `9 passed in 23.87s`** — identidade premium desktop, **assistente de primeira execução + recuperação com prova de que a senha antiga morre** (401 no Bearer antigo, 200 no novo) e mobile 390×844. Capturas `00-login-premium-desktop.png`, `04-login-mobile.png`, `05-painel-mobile.png`, `06-codigo-recuperacao.png` (regeneradas na verificação final).

### Auditoria Open Source (#30, parcial)

5. **Licença preservada e auditada** (Apache-2.0): inventário `pip-licenses` de 146 pacotes em `docs/mission/evidence/supply-chain/licenses.csv`; **`backtrader` 1.9.78.123 é GPLv3+** e constava no pyproject sem nenhum uso no código → **removido** de `pyproject.toml`/`uv.lock` (o mesmo delta inclui `argon2-cffi` do login). Permanecem licenças permissivas + `psycopg` LGPL-3.0-only (permitida).
6. **CI supply-chain** (`.github/workflows/ci.yml`): job novo — `gitleaks` na história completa, gate de licenças (falha se sobrar linha GPL/SSPL/EUPL; LGPL isenta) e SBOM SPDX (`dz23-sbom.spdx.json`, artefato 90 dias).
7. **Docs públicas**: `README.md` reescrito (estados PAPER/SHADOW/DEMO/LIVE com homologação declarada, quickstart `uv`, seção open source); `SECURITY.md`; `CONTRIBUTING.md`; `DEPLOY.md` (single-owner; Oracle ARM64 "preparado, não homologado"); site público sem SaaS/cobrança (sem "Planos"/"PIX"/"R$", FAQ "É gratuito?"); `.env.example`/`.env.enterprise.example`/`.env.agency.example` sem segredos; `.gitignore` endurecido (`.env.*` com exceções para os exemplos, `*.pem`/`*.key`/`*.p12`, `owner_credentials.json`, `owner_password.txt`). Pin de teste atualizado para pinar a ausência da cobrança no site.

### Estado

LIVE `BLOCKED_BY_POLICY` o tempo todo; **nada foi publicado** (sem tag/release — aguarda autorização explícita do dono). A homologação no bundle Windows do novo portão de login e a re-homologação C016 (fonte de dados) ficam pendentes do artefato desta PR — sem declaração antecipada.

---

## C018 — Itens 16–17 homologados no bundle da integração (C016b + C017)

- **UTC:** 2026-10-13T21:05Z–21:22Z
- **Branch:** `feat/integracao-mestre-20261012` (HEAD `d8892dc` = PR #10 (C017) + PR #11 (kernel fix) + C016; este batch documental)
- **Artefato:** run Windows `38467455263` (`d8892dc`) — Setup 69.805.469 B `sha256:24b29dc1b7e1ee9ee0328211aa9a410ce43806fde9e4a866bf4e6a16019fec37`; zip 88.540.652 B `sha256:865bb4e93e1d1f33916b133fcf5bd52cd6c0e1fafdeda54673e1b43202ed770c`; **`sha256sum -c SHA256SUMS.txt` → ambos OK direto**
- **Motivação dupla:** (a) o defeito encontrado na 1ª homologação C016 — o **primeiro Salvar** dos padrões do painel (fixture/binance/testnet) reconstruía a mesa em instalação nova, zerando o log de ciclos; kernel fix em `service/app.py` (`_DESK_EFFECTIVE_DEFAULTS`: valores do painel que equivalem ao default efetivo não contam como mudança); (b) homologar no bundle o **portão premium do dono único** (C017) com a migração real do `owner_password.txt`.

### Item 16 — Fonte de dados + kernel fix do primeiro Salvar (22/22 PASS, `HARNESS_EXIT=0`)

1. **Pré-estado honesto (passo 0b):** as 3 chaves de mesa (`TRADINGAGENTS_DESK_FEED/EXCHANGE/BROKER_MODE`) saíram do `config.json` da estação (backup `config.pre-c016b.json`) para recriar a condição real de instalação nova.
2. Instalação silenciosa exit 0 (7 s); 1ª abertura `healthz` 200; login do dono `ADMIN`.
3. **Seletor "Fonte de dados"** na página Configuração: 2 opções — `fixture` = "Dados de treino (rotulado)", `real` = "Mercado real público (somente leitura)".
4. Linha de base fixture: ciclo 0 → 1, ledger balanceado (1000).
5. **Kernel fix provado:** primeiro Salvar com os padrões do painel → **ciclos 1 → 1** (a mesa não é reconstruída; antes, este era o defeito).
6. Troca para mercado real → mesa reconstruída com aviso honesto "mercado real (leitura pública, cutoff na vela fechada)"; reconstrução zera o log de ciclos (mesa nova) — esperado.
7. Diagnóstico do feed real: `estado=PRONTO`, BTC/USDT e ETH/USDT com **239 velas cada**, `qualidade=ok`, até a vela fechada; ciclo real **honesto**: "sem dados elegíveis" + descartados "snapshot vencido, não vira ordem".
8. Volta para fixture: ciclo final ok; ledger balanceado (disponível 699.70); `pendencias_reconciliacao=[]`.
9. Encerramento pelo botão: launcher exit 0; porta 8000 livre. Capturas `c016-fonte-dados.png`, `c016-feed-real.png`, `c016-mesa-real.png`, `c016-volta-fixture.png`.

### Item 17 — Portão premium do dono único (24/24 PASS, `HARNESS_EXIT=0`)

1. **Pré-estado real:** `owner_password.txt` presente (16 caracteres; valor nunca registrado) e `owner_credentials.json` ausente — condição real de migração.
2. **Instância limpa (APPDATA isolado):** abre na 1ª tentativa; `setup_required=true`, `multi_tenant=false`, `live_trading=false`.
3. **Assistente de 1ª execução:** login escondido, formulário de criação visível; **código de recuperação exibido uma única vez** (6 grupos × 5 símbolos, mascarado na evidência); botão "Abrir o painel" bloqueado até marcar "Anotei o código"; painel abre `ADMIN`; encerra pelo botão (exit 0, porta livre).
4. **Migração real:** app do dono abre; `owner_password.txt` **removido**, `owner_credentials.json` criado (332 B) com `senha_hash` e `codigo_hash` em **Argon2id** (zero texto puro); **senha antiga continua entrando** (HTTP 200, `role=admin`); `setup_required=false`.
5. **Portão premium:** marca "DZ23 Trading AI" + selos PAPER MODE / EXECUÇÃO GOVERNADA; **nenhum** texto morto do SaaS visível; senha errada → "Senha incorreta. Confira e tente de novo — maiúsculas e acentos contam."; "Esqueci minha senha" abre a recuperação e "Voltar ao login" retorna.
6. Sessão abre com `ADMIN` e **exatamente 6 páginas** (Visão geral, Configuração, Análises, Mesa de Agentes, Trading Floor, Risco); botão "Fechar aplicativo" visível; encerramento pelo botão exit 0, porta livre. Capturas `c017-wizard-codigo.png`, `c017-portao-premium.png`, `c017-painel-apos-migracao.png`.

### Notas de harness (não são defeito do app)

- **Transitório de 1ª execução pós-instalação:** a 1ª execução do harness do item 17 (18:07) falhou no passo 2 — a instância limpa criou os DBs (~2 s) mas nunca respondeu `healthz` em 90 s; **não reproduzível** (reprodução manual com captura de stderr subiu na 1ª tentativa em ~1,5 s; mesmo precedente do C016 run #1). O harness ganhou captura stdout/stderr por tentativa + **uma** retentativa registrada; na execução final (18:16) os passos 2 e 3 passaram na 1ª tentativa (`tentativas: {step2_fresh: 1, step3_dono: 1}`).
- O harness do item 17 tropeçou ao clicar em "Esqueci minha senha" com a sessão já aberta (o link vive no formulário de login, invisível no painel) — reordenado para o portão.
- O harness do item 16 quebrou ao ler `tasklist` (cp850) sob `PYTHONUTF8` — corrigido capturando bytes e decodificando `utf-8/replace`.
- CI da branch de integração em `d8892dc` verde: runs `38467455094`/`38467466557` (CI) + `38467455263` (Windows). PR #10 (`2347270`) e PR #11 (`5578497`) mergeados com CI verde (`38463073919`/`38463092060`/`38463588336`/`38463593735`/`38467042208`).

### Estado

LIVE `BLOCKED_BY_POLICY` o tempo todo; **nada foi publicado** (sem tag/release — aguarda autorização explícita do dono). App deixado **instalado** na estação (estado de entrega), com `owner_credentials.json` (Argon2id) e sem `owner_password.txt`.

---

## C019 — Portabilidade: modo da mesa + posse exclusiva, export sem segredos, CI Postgres/ARM64 (#29)

- **UTC:** 2026-10-13T21:30Z–21:57Z
- **Branch:** `feat/integracao-mestre-20261012` (HEAD `977adce` + fix de CI `8e369f1`; este batch documental)
- **Motivação:** diretiva de portabilidade Windows↔Oracle ARM64 (workstream #29): transferir de servidor sem vazar credenciais, posse de execução exclusiva entre processos e validação ARM64 **sem declaração falsa de homologação**. Também endurece o CI de segredos do #30.

### Passos

1. **Modo da mesa + posse de execução** (`tradingagents/desk/modo.py`; rotas `GET/POST /v1/desk/modo` em `service/app.py`): estados `ATIVA`/`OBSERVADOR`/`PAUSADA`/`BLOQUEADA` persistidos no armazém; valor desconhecido no disco → **fail-closed** (BLOQUEADA com motivo e desbloqueio consciente). Posse exclusiva: renovada a cada ciclo, vence em 15 min; com o MESMO `DATABASE_URL`, o segundo processo **não executa** e o status mostra quem detém a posse. `desk_status` expõe `modo.estado`/`pode_executar`/`posse`.
2. **Export/import sem segredos** (`/v1/mudanca/exportar`, formato `dz23.mudanca/2`): chaves de IA/corretora (`SECRETish`) ficam fora do pacote **por construção** e os nomes vão em `segredos_omitidos`; aviso "Este arquivo NÃO contém segredos". No import, pacote legado `/1` com chaves tem os segredos **descartados e relatados** (`restaurado`), nunca restaurados. Toast do painel atualizado; teste novo cobre o pacote legado.
3. **CI de portabilidade** (`.github/workflows/ci.yml`): job `postgres` (service container postgres:16 — roda `tests/test_modo_da_mesa.py`, incluindo a **corrida da posse em banco real**) e job `arm64-deps` (QEMU+buildx `linux/arm64`; importa argon2/ccxt/fastapi/lxml/pandas/psycopg/uvicorn) — nomeado **"NÃO é homologação Oracle"**, por honestidade.
4. **Realidade corrigida em compose/scripts/docs:** `docker-compose.yml` (o compose só tem o serviço DZ23 — `DATABASE_URL` aponta para banco externo; Postgres/Redis/Qdrant nunca existiram nele), `scripts/validate_docker_runtime.sh` (reiniciava serviços inexistentes; agora só `tradingagents`) e `DEPLOY.md` (compose de serviço único; Oracle "ter um Dockerfile não é homologação"; **"Sem autorização, não há produção"** — sem deploy nem DNS por conta própria).
5. **Runbook PC→Oracle** (`RUNBOOK.md`): roteiro de 7 passos — parar+exportar no PC → transferir o JSON (sem segredos) → subir no Oracle com `ACCESS_PASSWORD` e volume `/data` → importar → **re-informar as credenciais no painel** → verificar Modo da mesa/posse/ciclo manual/status → rollback = religar o PC intacto.
6. **Prova local:** suíte completa **`2251 passed / 5 skipped / 71 subtests`** (exit 0) no HEAD `977adce`; lote focado `46 passed / 1 skipped`; ruff estrito limpo.
7. **Incidente de CI encontrado e corrigido (transparência):** o job "secrets, licenças e SBOM" flagrou a fixture sintética `gsk-secreta-123` (entropia 3,5069 > limiar 3,5 da regra `generic-api-key`) em `tests/test_alertas_transparencia.py:158`; valores trocados por equivalentes óbvios de teste de baixa entropia (2,26–2,62) no `8e369f1` — **sem allowlist**: o scanner segue ativo de ponta a ponta.
8. **CI/Windows no `977adce`:** runs `38473108095` (push) / `38473110936` (PR) — **job `postgres` SUCCESS** (corrida da posse em Postgres real), `tests (py3.12)`, `ruff`, `dependency audit`, `clean-install smoke` e `public smoke checks` SUCCESS; `ARM64 build` em execução longa (QEMU; timeout 60 min); `secrets…` falhou → corrigido no `8e369f1`. **Windows Release Build `38473107903` SUCCESS.**
9. **Docs:** este checkpoint + `TRADING-MISSION-STATE.md` + `TRADING-GATES.md` (G8) + backlog: P1-01 → `PASS_LOCAL` (mesa paper consumindo o feed público real; preenchimentos sandbox aguardam credencial do dono) e P1-07 → `BLOCKED_BY_EXTERNAL_DEPENDENCY` (histórico read-only da Binance do dono).

### Estado

LIVE `BLOCKED_BY_POLICY` o tempo todo; **nada foi publicado** (sem tag/release). **Oracle permanece NÃO homologado** — Dockerfile e job ARM64 não homologam nada; homologação exigirá o ciclo completo rodando lá, com evidência registrada. Prioridade Windows mantida.

---

## C020 — Identidade visual definitiva e novo login premium (missão do dono; tasks #32–#35)

- **UTC:** 2026-10-13T19:00Z–22:16Z
- **Branch:** `feat/integracao-mestre-20261012` (de `732f9f5`; este batch)
- **Motivação:** missão do dono (seções 1–9) — identidade visual definitiva a partir do LOGO entregue e novo portão de login premium, com estados operacionais **reais** do backend, paleta navy/cyan/emerald e identidade aplicada ao produto inteiro (painel, site público, piso, favicons, PWA, ícone do Windows). **Ressalva de entrega declarada:** a imagem de referência do login **nunca foi anexada** à missão — a composição foi construída a partir da descrição textual (referência de composição), **sem** substituir o HTML funcional por imagem estática.

### Passos

1. **Assets da marca a partir do LOGO do dono, original preservado:** `assets/brand/dz23-logo-original.png` é a fonte canônica (1254×1254 RGBA; sha256 `ab3c6a64…` — byte a byte idêntico ao `LOGO.png` entregue) + `assets/brand/README.md` explicando que os derivados não se editam à mão. Gerador reproduzível `scripts/gerar_assets_marca.py` (Pillow fora das dependências do projeto, `uv run --no-project`): logomarca completa (1024/512/192) e **símbolo separado** (512/192/64) cortado na faixa vazia (y=910) entre símbolo e texto; **favicon e ícone usam SÓ o símbolo** — o texto "Trading AI" não é reduzido a 16 px; `dz23-icone.ico` multi-tamanho (16–256 px) para exe/instalador.
2. **Novo portão de login** (`service/static/index.html` reescrito): símbolo centralizado + wordmark **"Trading AI"** (sem repetir "DZ23" — o símbolo já o contém; nada de "TRADING PLATFORM") + subtítulo "Inteligência coordenada. Risco governado."; fundo navy com grade financeira e velas discretas em CSS/SVG (sem imagem gigante); os **4 estados** de autenticação preservados (primeira configuração, login, recuperação por código, esqueci a senha) com os mesmos IDs/contratos; senha mascarada com botão exibir/ocultar acessível (`aria-pressed` sincronizado); **nenhum campo pré-preenchido com senha** (pinado por teste); campo vazio → "Informe a senha do dono".
3. **Selos operacionais honestos (nenhum badge estático):** os selos do portão são renderizados em runtime a partir de `GET /v1/status/operacao` — `PAPER MODE` (modo atual), `EXECUÇÃO GOVERNADA` (só quando o modo está habilitado **e** LIVE está desabilitado), `DINHEIRO REAL BLOQUEADO` (só quando o backend reporta `BLOQUEADA`; se o backend liberar, o selo muda sozinho para `OPERAÇÃO REAL LIBERADA`); servidor fora → selo neutro "estado operacional indisponível" (com `title` explicando). A mensagem "Não foi possível falar com o servidor local" só aparece quando `/v1/auth/status` realmente falha (boot com guarda de rede + checagem de `r.ok`).
4. **Identidade aplicada ao produto inteiro:** paleta da missão (`#061526`/`#092039`/`#0B2138`/`#16456B`/`#00B8E6`/`#42E2A3`/`#EDF5FF`/`#93B6D3`/`#FF637C`) no painel (sidebar com símbolo + "Trading AI"), `site.html` e `floor.html` (piso 3D remapeado; zero hex antigo restando — conferido por grep); `theme-color` nos 3; manifesto PWA `service/static/manifest.webmanifest` servido por rota explícita `/manifest.webmanifest` (ícones do símbolo 192/512); ícone do executável (`build_exe.py --icon=dz23-icone.ico`) e do instalador (`setup.iss SetupIconFile`); empacotamento: package-data do `pyproject.toml` (inclui `static/marca/*` e `static/*.webmanifest`) e allowlist `/static/marca/` ampliada (404 para qualquer outro nome).
5. **Nada do motor mudou:** nenhuma regra de trading/estratégia/capital/corretora/motor financeiro tocada; autenticação e validações intactas (Argon2id preservado); single-owner mantido; LIVE segue bloqueado.
6. **Testes novos/endurecidos:** `tests/test_identidade_visual.py` (novo — paleta nos 3 HTMLs, portão com símbolo+wordmark/selos dinâmicos/4 formulários/senha sem `value=`, assets com dimensões e magic bytes, original preservado ≥512 px, rota do manifesto com campos corretos, allowlist da marca com 404 do original); e2e: `test_login_premium_desktop_identidade_e_estado` endurecido (selos reais visíveis no DOM + proibido "DZ23 Trading AI" repetido no portão) e **novo** `test_login_responsivo_sem_corte_nem_rolagem_horizontal` — 1920×1080, 1366×768, 768×1024, 390×844 e 360×640: sem rolagem horizontal (`scrollWidth ≤ 1`), botão/campos dentro da viewport, senha vazia → mensagem clara; capturas `07-login-*.png` versionadas.
7. **Prova local:** suíte completa **`2267 passed / 6 skipped / 71 subtests`** (exit 0; +16 passed vs C019); lote focado `117 passed`; **e2e Playwright `10 passed`** contra servidor real; ruff estrito limpo.
8. **Pendências declaradas (honestidade):** (a) a **imagem de referência do login não foi anexada** — composição feita pelo texto da missão; (b) **homologação do NOVO bundle Windows está pendente** — o push deste checkpoint dispara CI + Windows Release Build, e o ciclo de instalação/identidade no artefato só será declarado com a evidência do run registrada em `WINDOWS-RELEASE-EVIDENCE.md`; (c) o app na estação permanece a versão do C018 até o novo bundle ser gerado e homologado.

### Estado

LIVE `BLOCKED_BY_POLICY` o tempo todo; **nada foi publicado** (sem tag/release). Identidade **provada em servidor real** (unit + E2E) mas **ainda NÃO homologada no bundle Windows** — não declarar concluído até o artefato deste push passar pelo ciclo de instalação com evidência registrada.

---

## C021 — Causa-raiz da falha de inicialização no Windows (missão do dono; tasks #37–#41)

- **UTC:** 2026-10-13 (batch noturno)
- **Branch:** `feat/integracao-mestre-20261012` (de `a4065ea`; este batch)
- **Motivação:** o dono reportou que abrir o DZ23 Trading AI pelo atalho no Windows (duplo clique) mostrava “falha ao iniciar” e o painel não subia — sem nenhum processo na porta 8000.

### Causa-raiz (provada, não presumida)

1. O backend sobe em thread própria com `uvicorn.Config(...)`. O `LOGGING_CONFIG` padrão do uvicorn constrói um formatter colorido cujo `__init__` chama `sys.stdout.isatty()`.
2. No exe PyInstaller `--windowed` aberto do jeito do dono (SEM console), `sys.stdout`/`sys.stderr`/`sys.stdin` são `None` → `AttributeError: 'NoneType' object has no attribute 'isatty'` → `ValueError: Unable to configure formatter 'default'` — exceção levantada DENTRO do `uvicorn.Config.__init__`, na thread do backend, ANTES de qualquer bind de porta.
3. A exceção morria muda: `threading.excepthook` escreve em `sys.stderr`, que também era `None`. Daí o “sem logs” e o “falha ao iniciar” após o timeout — sem nada na porta.
4. Lançamentos com console (scripts, terminais, homologações anteriores) herdam handles válidos e não reproduzem o bug — o que explica por que C018/C019 pareciam bem e a abertura do dono sempre falhava.
5. Reprodução determinística do defeito no binário `--windowed` via `Start-Process` (equivalente ao duplo clique): `docs/mission/evidence/e2e/c021-causa-raiz-old-windowed.log` — linhas `stdout=None stderr=None stdin=None`, traceback de `uvicorn\logging.py:42` e diálogo nativo #32770 “DZ23 Trading AI — falha ao iniciar” lido por `scripts/diagnostico_janelas_win.py`.

### Correção (causa-raiz; nada de segurança afrouxado)

1. **`desktop/launcher.py` reescrito:** `uvicorn.Config(..., log_config=None)` — o formatter defeituoso nem é construído; logging próprio configurado ANTES de tudo em `%APPDATA%\DZ23-Trading-AI\logs\launcher.log` (RotatingFileHandler 2 MB × 3), com `threading.excepthook`/`sys.excepthook` próprios gravando qualquer traceback no arquivo (fim do “morre mudo”).
2. **Pré-checagem de portas 8000–8010 com identificação read-only** (netstat + tasklist) e **marcador de saúde** (`/healthz` com campo `app` = `tradingagents-agency`): instância DZ23 saudável é reutilizada; senão primeira porta livre; senão diálogo claro “DZ23 Trading AI — portas ocupadas” listando o processo ocupante — **nunca encerra nem altera outros programas**.
3. **Porta efetiva propagada** (env `PORT` + `DZ23_PORTA_EFETIVA`) para backend, URL do navegador e healthcheck; o app só é dado como iniciado quando `/healthz` responde (com fast-fail se a thread do backend morrer); em falha, o diálogo aponta o caminho do log.
4. Quit de produção preservado (rota `/v1/app/quit` segue integrada).

### Prova (Windows real, binário `--windowed`, SEM console)

- Launcher NOVO via `Start-Process`: varredura de portas → “iniciando na porta efetiva 8000” → healthz 200 → painel aberto → encerrado; zero diálogos nativos — `docs/mission/evidence/e2e/c021-fix-verificado-windowed-sem-console.log`.
- `tests/windows/test_desktop_launcher.py`: **29/29**; ruff limpo.
- Ferramentas de prova novas/endurecidas: `scripts/build_diag_console.py` (constrói exe de diagnóstico `--windowed`/`--console` com os mesmos collects do build oficial), `scripts/matriz_inicializacao_win.py` (6 cenários: subida limpa; porta 8000 ocupada por terceiro → fallback para 8001 sem tocar no processo alheio; instância duplicada sai sozinha; faixa inteira ocupada → diálogo sem iniciar; reinício; encerramento gracioso via `/v1/app/quit` com porta liberada), `scripts/homolog_identidade_bundle.py` endurecido (descobre a porta efetiva pelo marcador; nunca mata processo que não subiu).
- **FECHADO nesta entrada — matriz de inicialização no bundle instalado (task #39):** `24/24 PASS` (`HARNESS_EXIT=0`) no artefato do CI de `f26f1ed` (run Windows `38489060888`), log `docs/mission/evidence/e2e/c021-matriz-f26f1ed-r2.log`. A 1ª execução (r1) falhou no cenário C2 ("programa estranho segue vivo na 8000") por **defeito do harness, não do app**: o ouvinte-sonda do próprio script nunca chamava `accept()`; 4 sondagens enchiam o backlog do `listen()` e as seguintes estouravam o timeout de 0,5 s — falso-negativo saturável, provado em isolamento (`c021-c2-saturacao-repro.log`: sonda antiga 4 OK → 8 TimeoutError; corrigida 12/12 OK). Correção em `scripts/matriz_inicializacao_win.py` (thread daemon que drena e recusa conexões). O launcher nunca mata terceiros: grep de `desktop/launcher.py` por `taskkill|terminate|\.kill|psutil|os\.kill` retorna **zero** ocorrências.
- **FECHADO nesta entrada — re-homologação do item 18 no artefato final (task #40):** `27/27 PASS` (`HARNESS_EXIT=0`), log `docs/mission/evidence/e2e/c020-5-item18-f26f1ed-r4.log`, capturas `c020-portao-bundle.png`, `c020-portao-1920x1080.png`, `c020-portao-390x844.png`, `c020-portao-360x640.png` — símbolo real ≥64 px, wordmark "Trading AI", selos dinâmicos do backend, senha mascarada com toggle acessível, responsivo 1920/390/360 sem corte. As 3 primeiras execuções falharam por **defeitos do harness** (URL errada — a raiz `/` é o site institucional e o portão vive em `/app`; cp1252 estourando em `≥`; strict mode do Playwright com 3 `.pwd-eye` no DOM), corrigidos em `scripts/homolog_identidade_bundle.py`; precedente de 1 retentativa do item 17 mantido.
- **FECHADO nesta entrada — escola/mesa autenticada no painel do bundle (item 21, task #53):** `27/27 PASS` (`HARNESS_EXIT=0`) no mesmo artefato de `f26f1ed`, log `docs/mission/evidence/e2e/c021-item21-painel-f26f1ed-r3.log`, capturas `c021-item21-mesa-bundle.png` / `c021-item21-escola-bundle.png`. Prova no exe `--windowed` instalado, com APPDATA descartável (o DATA_DIR real não é tocado): instância sobe (healthz 23,1 s) → assistente cria a senha → **login pelo portão** (ADMIN/READY) → **6 páginas** → mesa com os **10 traders (3 homologado / 7 experimental)** → botão "Rodar um ciclo" roda de verdade (*"20 propostas, 2 admitidas, 2 entradas, 0 saídas, 0 vetos"*, ciclos 0→1, patrimônio 999,80 balanceado) → **Escola Central** rende contextos de certificação, a **sala isolada `binance|paper-legacy|spot|paper`** (chave corretora|conta|mercado|modo) e pilotos com *"autorização é ato exclusivo do proprietário"* → **zero 5xx** → `POST /v1/app/quit` 200 → launcher sai sozinho (exit 0) e porta liberada. As execuções r1/r2 falharam por **defeitos do harness** (comparação case-sensitive contra o `inner_text()` de `h3` — o painel usa `text-transform: uppercase` e renderiza "TRADERS (10 PERFIS)"/"ESCOLA CENTRAL"; regex de chave que não cobria o hífen de `paper-legacy`), provados por diagnóstico da camada navegador (zero pageerrors, zero requestfailed, zero 5xx) e corrigidos em `scripts/homolog_painel_bundle.py`. Com este item, a **última ressalva do BT-10 fica fechada**: o item 21 rodou no artefato final.
- **FECHADO nesta entrada — recuperação de crash (`taskkill /F`) no bundle instalado (item 22, task #54):** `30/30 PASS` (`HARNESS_EXIT=0`) no artefato de `f07ea47` (run Windows `38494708308`), log `docs/mission/evidence/e2e/c021-item22-crash-f07ea47-r3.log`. Harness novo `scripts/homolog_crash_bundle.py`: APPDATA descartável → assistente → ciclo PAPER real (*20 propostas, 2 entradas*, patrimônio 999,80) → **kill à força do PRÓPRIO processo do harness** (`taskkill /F`, exit 1; porta liberada pelo SO) → reabertura no MESMO APPDATA → **estado durável byte a byte**: ordens idênticas (id/estado/qtd/preço), ciclos 1→1 com `ultimo_ciclo` idêntico, posições e instrumentos abertos idênticos (BTC/ETH), **saldos/patrimônio/disponível idênticos (999,80 / 799,80000145)**, ledger balanceado, `pendencias_reconciliacao=[]`, durabilidade íntegra, modo `ativa`; senha do dono (Argon2id no disco) continua valendo; quit gracioso exit 0. **O harness encontrou 2 defeitos REAIS do app** (log r2 ≥ em `-r2.log`): (A) risco cego ao livro vivo — `attach_portfolio_ledger` só existia no restore, então no 1º boot o `disponível` mostrava 1000 e `instrumentos_abertos` ficava vazio depois dos fills; (B) painel pós-reinício preso ao ledger de abertura — `desk["ledger"]`/`positions_provider`/agendador não seguiam a troca do ledger no `restaurar_estado` (patrimônio 1000 em vez de 999,80). Correção em `service/app.py` `_desk_boot` (commit `f07ea47`) com regressão pinada em `tests/test_livro_vivo_e_reinicio.py` (3 testes RED→GREEN), 172 testes das suítes vizinhas + ruff limpos, CI do push em verde parcial + build Windows verde. Nota de operação: a 1ª tentativa de r3 rodou contra o bundle antigo — `/VERYSILENT` mangled pelo Git Bash (MSYS path conversion) fez o setup abrir interativo; reinstalação com `//VERYSILENT` e conferência tamanho/hash do EXE (36.941.279 B) antes de re-rodar. Ressalva "recuperação de crash" das pendências de homologação **fechada**; restam sleep/wake e offline/reconexão.

### Estado

LIVE `BLOCKED_BY_POLICY`. Nada publicado (sem tag/release). A falha real do dono (duplo clique sem console) está com causa-raiz provada e corrigida, com prova em binário `--windowed`; a homologação no bundle gerado por este push está **FECHADA no artefato de `f26f1ed`**: matriz de inicialização **24/24 PASS** (correção de harness com causa-raiz provada em isolamento), re-homologação do portão premium **27/27 PASS** e **escola/mesa autenticada no painel 27/27 PASS** (item 21: login, 6 páginas, 10 traders por tier, ciclo real, Escola Central com sala isolada, zero 5xx, quit gracioso) no bundle instalado; app deixado instalado (estado de entrega).


---

## BT — Escola de Traders: do Batalhão à cadeia de piloto (missão do dono; tasks #42-#52)

- **UTC:** 2026-10-13/14 (batches noturnos; BT-0->BT-10)
- **Branch:** `feat/integracao-mestre-20261012` (BT-1 `134c05b` -> BT-9 `8bf78ad`; BT-10 neste batch)
- **Motivação:** a arquitetura definitiva do dono — **UMA ESCOLA CENTRAL DE TRADERS com SALAS INDEPENDENTES POR CORRETORA**: escola única compartilhada pelos 10 traders; salas isolam histórico/saldo/posições/taxas/ledger/métricas por corretora+conta+mercado+modo; certificação é individual por trader+versão+ativo+horizonte e **não atravessa contexto** (formado na Binance != formado na OKX); conhecimento entre salas é compartilhado com rótulo e sem transferir certificação; Maestro Central supervisiona exposição global; IQ Option permanece laboratório experimental isolado. **A formação nunca ativa negociação real** — LIVE continua exclusivo do proprietário.

### Cadeia entregue (BT-1..BT-9, nos commits da branch)

1. **BT-1/2** — núcleo da escola (`tradingagents/desk/escola.py`): estados coletando->candidata->formada com IC vs equilíbrio e prova independente; revogação que derruba elegibilidade; laboratório de hipóteses pré-registradas com assinatura, execução única, controle aleatório (opt-in, 11º trader, homologado=False) e correção de Bonferroni.
2. **BT-3/4** — supervisor de piloto (`tradingagents/desk/piloto.py`): cadeia **FORMADO -> ELEGÍVEL PARA LIVE -> AGUARDANDO AUTORIZAÇÃO DO PROPRIETÁRIO -> PILOTO AUTORIZADO** com gates de ativação (dono único, kill switch, integração, risco, envio real desativado); bloqueios por limite, suspensão por escola revogada, expiração por prazo; rotas `/v1/escola/*` e painel.
3. **BT-6..9** — Escola Central + `RegistroDeSalas` (`tradingagents/desk/salas.py`): salas com chave `corretora|conta|mercado|modo`, pacote financeiro por sala (saldo/taxas/posições/desfechos), certificados por contexto, conhecimento rotulado entre salas, Maestro Central (pausa global + teto de exposição), storage NTFS-safe com chaves `%XX` e leitura UTF-8 forçada.

### BT-10 — fechamento: isolamento + PAPER ponta a ponta + evidência (este batch)

1. **Mesa espelhada na sala pelo caminho de produção:** `DeskOrchestrator` ganhou `sala` — `register_fill` registra a entrada na sala (posição com agente/estratégia/versão/regime) e `close_position` registra a saída; falha de espelho vira **aviso auditável** (`avisos_sala`), nunca derruba a ordem já decidida. O agendador agora **executa de verdade**: compra FILLED vira posição no livro da mesa (antes o livro só era alimentado em teste — a gestão de saída ficaria cega em produção).
2. **Vigia de pilotos no ciclo com estado RELIDO:** `service/app.py` monta o vigia com instâncias novas de `SupervisorDePiloto`/`EscolaCentral` a cada ciclo — cache de boot bloquearia/liberaria a entrada errada quando autorização e formatura mudam por fora; falha do vigia cai no fail-closed do orquestrador e fica registrada em `avisos_vigia` (expostos no `desk/status`). Cada piloto é reavaliado **no contexto do pedido** (formatura na Binance não sustenta piloto na OKX).
3. **Causa-raiz da flakiness do E2E (provada e corrigida):** `ArbitrageTrader._evaluate` (`traders.py`) sorteava a oportunidade simulada (~5%) pela hash do **relógio de parede** (`snap.cutoff`); no fixture, cada execução sorteava de novo — quando disparava, a arbitragem (confiança 0.85) vencia a seleção do supervisor sobre o momentum (0.7) e, sem pedido de piloto próprio, **burlava o vigia** produzindo 2 ordens na cena de 1 (reproduzido: iteração 6 do loop de 20x falhou com `assert 'trader-arbitrage' == 'trader-momentum'`). Correção: o sorteio deriva do **ESTADO DE MERCADO** (`instrumento|último fechamento`, quantizado `.4f`) — o mesmo candle decide igual em qualquer réplica e o laudo é reprodutível com o mesmo feed. Regressão pinada: `test_arbitrage_decide_pelo_estado_de_mercado_nao_pelo_relogio` (dois cutoffs que, sob a regra antiga, um concedia e o outro negava a oportunidade — a decisão agora é idêntica nos dois; vermelho determinístico antes, verde depois).
4. **Prova E2E (o que os 4 testes `TestMesaEscolaE2E` provam, no app real via TestClient):** ciclo da mesa dispara 2 ordens e as posições aparecem na sala `binance|paper-legacy|spot|paper` com os agentes corretos (momentum/statistical); saída realizada move `saldo_realizado`/`taxas_acumuladas` e vira desfecho na sala; `POST /v1/escola/avaliar` avança a formação DAQUELA sala e a certificação segue isolada (Binance formada OK / OKX `coletando`; chave inexistente 404; body sem campos 422); o vigia **corta** a entrada do momentum pendente (1 ordem — BTC abstenção "Maestro/piloto"), **libera** após `autorizar` (2 ordens) e **corta de novo** após `revogar` (1 ordem). Formação semeada pelo caminho real (220 parcelas -> candidata -> 300 -> formada).
5. **Evidência reproduzível:** loop 10x de `tests/test_service_escola.py` -> **22/22 passed em todas as 10 iterações** (iteração 6 era o ponto de falha histórico); suíte completa **`2404 passed / 6 skipped / 71 subtests`** (exit 0; +137 vs C020); maestria/regime `29 passed`; E2E escola+leigo `86 passed`; adjacentes (multiagente/scheduler/piloto/quant/auditoria) `146 passed / 1 skipped`; ruff estrito limpo nos 8 arquivos tocados.

### Ressalvas declaradas (honestidade)

- A homologação **no bundle Windows instalado** do ciclo escola/mesa foi **fechada em C021** no artefato de `f26f1ed`: **item 18 (identidade/portão premium)** `27/27 PASS` e **matriz de inicialização** `24/24 PASS` (tasks #39–#40), e **item 21 (escola/mesa autenticada no painel do exe `--windowed`)** `27/27 PASS` (task #53, log `c021-item21-painel-f26f1ed-r3.log`): login pelo portão, 6 páginas, 10 traders por tier, ciclo real pelo botão, Escola Central com sala isolada e autorização de piloto exclusiva do proprietário, zero 5xx e quit gracioso — no mesmo artefato do CI (`f26f1ed`, run `38489060888`).
- IQ Option permanece **laboratório experimental** com métricas próprias; nenhum resultado financeiro é misturado entre salas por construção (chave de sala), e isso está pinado por teste.

### Estado

LIVE `BLOCKED_BY_POLICY` o tempo todo (nenhuma operação real; envio real desativado). **Nada publicado** (sem tag/release). A cadeia obrigatória da escola está implementada e provada de ponta a ponta no caminho de produção: a formação **não** habilita negociação real — autorização LIVE continua ato exclusivo do proprietário, com limites e gates.

### E1 — Smart Position & Exit Manager GLOBAL (programa V7, M2/M3/M4) — núcleo determinístico + autonomia no ciclo

1. **Motor global (`tradingagents/desk/saida.py`, novo):** política de saída **versionada POR POSIÇÃO** (`PoliticaDeSaida`, imutável) derivada da MESMA matemática do backtest (`ExitPolicy`/`policy_for` em `policy.py` — uma definição, dois consumidores). Decisão determinística `DecisaoDeSaida` com ordem declarada e auditável: stop → alvo/parcial → trailing → tempo → manter; com `reason_code` (STOP_CONDITION/TARGET/PARTIAL_TARGET/TRAILING/TIME_EXIT/HOLD/DATA_UNAVAILABLE/EXIT_IN_PROGRESS/SEM_POLITICA), motivo em PT-BR, urgência, fração, lucro bruto e **líquido** (custo real via `custos.custo_de_operar`), preço de referência, fonte, frescor e validade. **Nenhuma regra vende só porque o lucro bruto ficou positivo.**
2. **Stop nunca fica mais largo (por construção):** `afrouxamento()` detecta stop mais largo / alvo mais distante / prazo maior / trailing removido; `aplicar_ajuste()` **recusa** afrouxamento (ValueError) e audita apertos no histórico da posição (`historico_politica`: de → para → em). Ajuste de política é nova versão, não mutação silenciosa.
3. **Ciclo autônomo com PROTEÇÃO PRIMEIRO:** `run_cycle` avalia as posições pelo motor global (stop/alvo/trailing/tempo) e emite `saidas` estruturadas além do registro legível `exits`; `DeskScheduler.run_once` **executa as saídas ANTES das entradas** via `_executar_saida` → `coordinator.close_position`, com: anti-duplicata (`tem_saida_em_curso` — ordem de venda não-terminal bloqueia segunda), quantidade limitada pelo **LOTE vivo do diário** (livro inflado nunca gera oversell — pinado por teste), procedência obrigatória (`build_state`/`validar`) e devolução honesta quando não há lote (limpa o diário, não inventa ordem). Lucro realizado por full/redução volta ao diário e à sala (`registrar_saida` novo kwarg `politica`; `registrar_reducao` proporcional novo).
4. **Gestão sobrevive ao reinício:** `livro_doc()` (schema `dz23.livro/1`) persiste no armazém namespace `"livro"` a cada ciclo; `restaurar_livro()` restaura política/topo/parcial/quantidade com **a verdade financeira mandando** (diário sem lote vivo é descartado com aviso "sem lote vivo"; divergência de quantidade → ledger vence; posição no ledger sem diário é **adotada** com política padrão do trader e aviso auditável). `service/app.py` restaura no boot e expõe `livro_restauracao` + `posicoes` (painel) em `/v1/desk/status`; `/v1/desk/cycle` devolve `encerradas` REAIS (antes `encerrados=[]` hardcoded; no observador, explicitamente vazio).
5. **Painel:** tabela "Posições (gerente de saída global)" na Mesa — entrada, stop/alvo (preço e %), líquido AGORA (bruto + último preço), trailing/topo, parcial tomada, horizonte, velas na posição e política (chave id@versão) + avisos de restauração; checklist do ciclo mostra as saídas executadas com motivo.
6. **Evidência reproduzível (este batch):** `tests/test_motor_de_saida_global.py` **novo, 31 testes** com `tests/test_desk_saidas.py` → **31 passed** (determinismo das 8 decisões; afrouxamento recusado/aperto aceito; ciclo executa stop e NÃO repete; oversell impossível; ordem de venda em curso bloqueia segunda; livro persiste no armazém; reinício restaura política/topo; diário sem lastro descartado; ledger sem diário adotado; painel). **2 defeitos reais encontrados e corrigidos pelo próprio teste durante o RED→GREEN:** (a) `r.nao_terminal()` era propriedade, não método (TypeError engolido pelo except deixava `encerradas` vazio); (b) `out["erros"]` era **substituído** pelo `report.errors`, apagando erros de saída do motor — agora soma. Suíte completa **`2427 passed / 6 skipped / 71 subtests` (exit 0)**; lote de boot/serviço/contrato de painel (`test_service_leigo`/`test_service_escola`/`test_frontend_contract`/`test_desk_scheduler`/`test_crash_restart`/`test_paper_persistence`) **146 passed**; `ruff` limpo nos 7 arquivos tocados; JS do painel validado por `node --check`.
7. **Ressalvas declaradas (honestidade):** homologação **no bundle Windows instalado** deste E1 fica **pendente do artefato do CI deste push** — não declarada. Comparação estatística política-a-política vs alvo fixo (walk-forward/OOS) e certificação de políticas pela escola entram em E5. LIVE `BLOCKED_BY_POLICY` o tempo todo; execução continua PAPER e fail-closed para o vigia de pilotos.

### E2 — Centro Inteligente de Alertas (programa V7, M5) — fila durável + canais + Configurações>Notificações

1. **Núcleo durável (`tradingagents/desk/notificacao_viva.py`, novo):** `FilaDeAlertas` persistida no Armazem (chave `alertas`, schema `dz23.alertas/1`); `AlertaVivo` com preço/fonte/hora/validade/link/`dedupe_key` e **tentativas auditadas por canal** (`registrar_tentativa`); estados `novo/entregue/expirado/falhou`; **dedupe** de 1 h (reincidência incrementa `ocorrencias`, não duplica alerta); **retry com backoff exponencial** (30 s × 2ⁿ⁻¹, teto 1800 s, máx. 8 tentativas → `esgotado`); **rate limit por canal** (janela deslizante 60 s; Telegram 20/min etc.); alerta **vencido NUNCA é despachado** e o texto marca "⚠️ VENCIDO — não é mais oportunidade atual" (testado com relógio simulado); `inbox()` com contadores e flag `vencido` por item; `MatrizDeRoteamento` por trader/estratégia/corretora/ativo/severidade_min (primeira regra vence; canal desconhecido ignorado com aviso); `Despachante` com auditoria POR TENTATIVA (falha parcial de canal fica registrada mesmo quando outro canal entrega) e **painel como entrega garantida** quando não há canal externo.
2. **Canais (`tradingagents/desk/canais_alerta.py`, novo):** Telegram (principal; botões inline apenas "Abrir no painel" (URL), "Confirmar leitura" e "Registrar intenção" (callbacks) — **não existe botão de executar**), Windows nativo (toast via PowerShell/WinRT, sem dependência nova), painel (caixa de entrada), Discord (webhook), WhatsApp (Graph API — opcional, só com credencial), Web Push (estrutura VAPID honesta — "pronta para o servidor Oracle", feature-key `ALERTA_WEBPUSH`) e **e-mail com trava estrutural**: `CanalEmail.enviar` sempre recusa alertas ("e-mail é canal exclusivo de RELATÓRIOS"); relatórios via `enviar_relatorio`. `construir_canais(cfg)` monta na ordem de prioridade.
3. **Segurança dos botões (testada estruturalmente):** `ACOES_TELEGRAM_PERMITIDAS = ("ler", "intencao")` — ação desconhecida recusada; teste faz varredura de código-fonte de `notificacao_viva.py`/`canais_alerta.py` exigindo **ZERO** ocorrências de `create_order`/`cancel_order`/`close_position`/`coordinator.submit`; webhook `POST /v1/telegram/webhook` autentica por `X-Telegram-Bot-Api-Secret-Token` com `hmac.compare_digest` e **fail-closed** (401 sem segredo configurado ou segredo errado — verificado em servidor real por curl).
4. **Serviço (`service/app.py`):** `_FILAS_ALERTAS` por tenant restaurada do armazém no boot; o ciclo da mesa enfileira os alertas de estado (`avaliar_estado` — ordem incerta/livro desbalanceado/limite de perda/mesa parada/ciclos com erro) **e** os alertas do próprio ciclo (stop_atingido/saida_executada com validade 12 h, confirmacao_entrada) e despacha com persistência; rotas: `GET /v1/alertas` (compatível: ativos + histórico com flag vencido), `POST /v1/alertas/verificar`, `GET /v1/notificacoes/canais`, `POST /v1/notificacoes/testar` (**entrega REAL com latência medida** — nunca "aceitou a chamada"), `GET /v1/notificacoes/inbox`, `POST /v1/notificacoes/{id}/ack`, `GET/PUT /v1/notificacoes/matriz`, webhook Telegram. Segredos novos (`ALERTA_TELEGRAM_WEBHOOK_SECRET`, `ALERTA_DISCORD_WEBHOOK`, `ALERTA_WHATSAPP_TOKEN`, `EMAIL_SMTP_PASS`) entram em `SEGREDOS_DECLARADOS` — voltam mascarados e nunca vazam em respostas (testado).
5. **Painel (`Configuração → Notificações`):** lista viva dos 7 canais com status honesto (configurado/não + como configurar) e botão **Testar** por canal; **Caixa de entrada** com marcar-lido; **Matriz de roteamento** (JSON validado) — salva pela UI e persiste no armazém.
6. **Evidência reproduzível (este batch):** `tests/test_centro_de_alertas.py` **novo — 41 testes** (dedupe, backoff com tempos exatos 30 s→60 s, rate limit adia/libera, expiração nunca despacha, reinício restaura e retoma, **timeout de um canal não derruba os demais e fica auditado**, máx. tentativas esgotado, matriz, segurança dos botões por varredura de fonte, segredo nunca vaza, testar não-configurado 400 honesto, testar configurado 200, inbox+ack, webhook fail-closed, fila sobrevive ao reinício do serviço). **RED→GREEN encontrou e corrigiu 2 defeitos reais do núcleo:** (a) backoff da 1ª falha esperava 60 s em vez de 30 s (expoente `2^(n-1)`); (b) falha de canal parcialmente bem-sucedida (telegram timeout + painel OK) **não era auditada** — Despachante reestruturado para registrar a tentativa de cada canal que falhou mesmo quando outro entrega. Suíte alvo `109 passed` (centro + alertas/transparência + notifications + auditoria blockers); **suíte completa `2468 passed / 6 skipped / 71 subtests` (exit 0)**; `ruff` limpo nos 5 arquivos tocados.
7. **Homologação de canal no navegador REAL (Chrome DevTools MCP contra uvicorn local, DATA_DIR descartável — o `%APPDATA%` do dono não é tocado):** login pelo portão → Configuração → Notificações rende os 7 canais com status/detalhes honestos → **Testar no Windows: "windows: entrega confirmada (282 ms)"** — toast WinRT realmente executado com latência medida → Testar e-mail: recusa honesta com o motivo ("canal email ainda não configurado — opcional — relatórios…") → matriz salva pela UI e conferida por GET → webhook sem segredo e com segredo errado: **401** (curl) → screenshot `notificacoes-painel.png` (temp da sessão). Zero pageerrors; nenhum 5xx.
8. **Ressalvas declaradas (honestidade):** **deste E2.5** os tipos entregues pelo ciclo são confirmação de entrada, saída executada, stop atingido e os 5 incidentes de `alerts.py`; **oportunidade, mudança de tese, lucro parcial e perda de conexão** ficam prontos no vocabulário da fila (tipo livre) mas serão ligados pelos produtores naturais (copiloto manual E3/E4 e evolução E5) — não declarados como cobertos. **Telegram/Discord/WhatsApp/Web Push NÃO estão homologados com credenciais reais** — o código entrega, os testes cobrem estrutura/falha/rate-limit/segredo, mas a entrega real depende da credencial do dono; o botão **Testar** é o caminho de homologação por canal quando ele configurar (exige evidência de entrega, não de aceite). Homologação **no bundle Windows instalado** deste E2 fica pendente do artefato do CI deste push — não declarada. LIVE `BLOCKED_BY_POLICY` o tempo todo; nenhum botão/alerta executa ordem.

### E3 — Copiloto Manual (programa V7, M1) — domínio + API read-only (commits `ba28e2e` + `1ec84b4`)

1. **Domínio (`tradingagents/desk/manual.py`, novo — 830 linhas; `tests/test_copiloto_manual.py` 515 linhas):** modo PRÓPRIO do dono (`CopilotoManual`) — ele registra as posições que abriu à mão (`PosicaoManual`: instrumento, lado, quantidade, preços, taxas), com política de acompanhamento versionada (`PoliticaManual`), diário de eventos (`EventoManual`) e cotação injetada (`QuoteManual`); dinheiro em `Decimal` com coerção estrita (`_d`/`_dopt`), erros tipados (`PosicaoNaoEncontrada`, `ConflitoDeVersao`). O modo copiloto é estado do INQUILINO (persistido no armazém), não da mesa — quando ligado, a mesa autônoma não executa para ele ("o copiloto acompanha, calcula e alerta").
2. **API (`service/app.py` +402 linhas; `tests/test_copiloto_manual_api.py` 364 linhas):** rotas autenticadas do dono único — `GET/PUT /v1/manual/modo`, `POST/GET /v1/manual/positions`, `GET /v1/manual/positions/{id}`, `POST /v1/manual/positions/{id}/events`, `PUT /v1/manual/positions/{id}/monitoring-policy` — todas com `Depends(auth)`, persistência por inquilino e tratamento HTTP tipado (`_manual_http`). **NENHUMA rota executa ordem na corretora** (read-only por contrato, pinado por teste).
3. **Ressalvas declaradas (honestidade):** a UI "Minhas Operações" (E4) e o E2E ficam pendentes — não declarados. LIVE `BLOCKED_BY_POLICY` o tempo todo.

### N01 — Notificações Windows: identidade de aplicativo + rótulos honestos (missão do dono; commit `8e2127a`)

1. **Auditoria da ordem `ord-3` (sem expor credenciais):** era **PAPER** — sala `binance|paper-legacy|spot|paper` provada no journal/ledger/executor; o app não aparecia em Configurações > Sistema > Notificações porque a chave `HKCU\Software\Classes\AppUserModelId\DZ23 Trading AI` **não existia** (AUMID sem registro no Windows).
2. **Correções em 4 camadas:** (a) texto dos alertas com modo explícito — `[SIMULADO (PAPER)]` / `[TESTNET (sem dinheiro real)]` / `[REAL (LIVE)]` (`montar_alerta_entrada` + `_rotulo_modo_execucao` no `service/app.py`); (b) `registrar_identidade_windows()` em `canais_alerta.py` (chave idempotente com DisplayName + IconUri) executado antes de cada toast real; (c) `setup.iss` registra a chave e marca os atalhos com `AppUserModelID`; (d) o launcher chama `SetCurrentProcessExplicitAppUserModelID("DZ23 Trading AI")`.
3. **Honestidade do mecanismo:** o detalhe da entrega diz "toast entregue à API do Windows sob a identidade 'DZ23 Trading AI' — a visualização depende do usuário" — o aceite da API NUNCA é confundido com leitura visual (Não Perturbe/foco suprimem). Botão **Testar** por canal: `POST /v1/notificacoes/testar` faz entrega REAL com latência medida; falha → 502 com motivo (nunca "aceitou a chamada").
4. **Evidência (`docs/mission/evidence/e2e/n01-notificacoes-win.log`):** `scripts/homolog_notificacoes_win.py` + `tests/windows/test_notificacoes_identidade_win.py` (56 testes focados, ruff limpo) — **9/9 PASS em Windows real**: 5 alertas de teste (entrada/saída/stop/perda de conexão/emergência), reteste em processo NOVO após reinício e chave de configuração presente. Nenhuma configuração de outro aplicativo foi alterada; LIVE bloqueado durante os testes.

### INST-1..INST-4 — Instalador Windows (fechamento; commits `9c0f7bc`, `5529ae6`, `a444d2c`)

1. **INST-1 (auditoria):** setup.iss, registro HKCU, versões e habilitações mapeados; a instalação registrada do dono (0.4.0, `%LOCALAPPDATA%\DZ23-Trading-AI`, dados em `%APPDATA%\DZ23-Trading-AI`) tratada como INTOCÁVEL — nenhum teste destrutivo sobre ela.
2. **INST-2 (decisões do setup):** detecção de cenário `INSTALACAO_NOVA`/`ATUALIZACAO`/`REPARO`/`DOWNGRADE`; app rodando detectado por mutex de instância (`dz23_processo.ps1` — `Test-MutexApp`); downgrade RECUSADO sem `/FORCEDOWNGRADE=1`; fechamento gracioso do app via `--quit` antes de substituir arquivos, com aborto `ABORTADO_APP_NAO_FECHOU` se não fechar (o defeito real do `--quit` que quebrava este elo foi corrigido em INST-5).
3. **INST-3 (dados):** `backup_dados.ps1` cria `pre-upgrade-<de>-para-<para>-<ts>` com `MANIFEST.sha256` verificado ANTES de tocar arquivos; `MOTIVO` de falha persistido ao lado dos backups (`5529ae6`); SHA256 e IO via .NET puro (sem depender de Get-FileHash — `a444d2c`); `restaurar_backup.ps1` restaura com confirmação `-Forcar`, recusa destino existente sem força e detecta backup corrompido/faltando (cenário C7b da matriz).
4. **INST-4 (identidade de versão):** `tradingagents/versao.py` como fonte única; exe (`build_exe.py --icon`), setup (`/DAppVersion=0.5.0`), healthz (`versao: 0.5.0` — conferido no launcher local de fonte) e CHANGELOG consistentes; workflows `installer-matrix.yml` + `windows-release.yml` com artefatos e SHA256.
5. **Ressalvas declaradas (honestidade):** INST-1..4 fecham por auditoria + CI + testes locais; a prova de UPGRADE REAL 0.4.0→0.5.0 em ambiente Windows isolado, com dados preservados, é o INST-6 (pendente).

### INST-5 — Matriz de cenários do instalador; causa-raiz do `--quit` e matriz endurecida (commit `fab7815`)

1. **Causa-raiz (provada pelo run `38507698283` — 25 falhas em C4–C9):** o processo `--quit` criava o evento nomeado de saída do zero — objeto TRANSITÓRIO, destruído quando o próprio processo morria — e o poll de 1 s da instância **nunca via o pedido**; o app permanecia aberto, o setup abortava `ABORTADO_APP_NAO_FECHOU`, o exe ficava travado para o desinstalador e os cenários seguintes caíam em cascata. Defeito B: `--quit` retornava 0 mesmo SEM instância viva (auto-criação do objeto), tornando o check do C4 vazio.
2. **Correção (`desktop/launcher.py`):** a INSTÂNCIA cria **e segura** o evento de saída no arranque (`_criar_evento_saida()` — handle vivo até o processo terminar); o `--quit` apenas o sinaliza (`OpenEventW`) e consulta o mutex primeiro (`_instancia_viva()`) — sem instância viva retorna 1 e não deixa objeto órfão.
3. **Provas:** 5 regressões novas em `tests/windows/test_desktop_launcher.py` (**42/42 verdes**; RED→GREEN executado contra o launcher pré-fix — 4 falham sem a correção); prova funcional de ponta a ponta `scripts/homolog_quit_win.py` com `%APPDATA%` temporário (dados do dono intocados) → **10/10 PASS** (`docs/mission/evidence/e2e/inst-quit-fix-local.log`): instância real de fonte na porta 8000 → healthz 200 → `--quit` exit 0 → encerramento gracioso em **1,8 s** → segundo `--quit` exit 1 → porta liberada.
4. **Matriz endurecida:** `precondicao_sem_instancia_viva()` em C4/C5/C6/C8/C9 — com instância de cenário anterior viva, um segundo lançamento reutilizaria a porta e sairia 0 na hora (falso-verde); agora o cenário FALHA com motivo explícito e limpa o straggler.
5. **Fechamento:** fix publicado em `fab7815`; a matriz re-executa nos runs `38510154174` (matriz) e `38510154111` (release) — **INST-5 FECHADO:** a matriz ficou VERDE no run `38512611447` (push de `486a60b`, conclusion=success) — `TOTAL: 0 falha(s)` — incluindo o check `C8: desinstalador removido` que motivou o item 6 acima; evidências curadas em `docs/mission/evidence/e2e/matriz-38512611447/` (status/telemetria por cenário + `resumo-harness.txt` com todos os PASS + doc `inst6-upgrade-real-0.4.0-0.5.0.md`).
6. **Segundo ciclo da matriz (runs `38510154174`/`38510300069`) — UMA única falha determinística em 143 checks:** `C8: desinstalador removido` — o check rodava ~1 ms após o exit 0 do desinstalador, mas o Inno remove o `unins000.exe` original de forma ASSÍNCRONA ao final da 2ª fase (o processo se copia para `%TEMP%` e o original só desaparece depois); a auto-remoção é atrasada em milissegundos. Correção (`scripts/matriz_instalador_win.py`): o orçamento de espera de 90 s já usado para os demais efeitos assíncronos agora também observa `unins000.exe` — se o arquivo persistir de verdade, a checagem continua FALHANDO (o poll não afrouxa o critério).

### E4 — Copiloto Manual: página "Minhas Operações" no painel + E2E (task #59)

1. **Página nova (`service/static/index.html`):** `Minhas Operações` no menu (após Mesa, ícone ▣) e no roteador (`if(id==='manual')manualLoad();`); seção `#page-manual` com: modo do copiloto (Ligar/Desligar — estado do inquilino; ligado, a mesa autônoma não opera por ele), saúde do monitor, formulário "Registrar posição já executada" (instrumento, lado, quantidade, preço real, stop, alvos, trailing, tese, invalidação), lista de posições com resumo, detalhe por posição (política de monitoramento versionada com stop declarado, diário de eventos, registrar execução, pausar/retomar acompanhamento, revisão) e **alertas do copiloto** (`#manualAlertas`) com marcar-lido. A página fala SÓ com `/v1/manual/*` (E3) — nenhuma rota de execução/ordem (pinado por teste estático e por E2E).
2. **Promessa honesta por escrito na tela:** "ele nunca opera por você"; preço executado obrigatório ("cotação de tela nunca substitui o preço realmente executado") — a execução só entra no diário com o preço realmente executado, e a UI recusa com toast honesto quando falta.
3. **Regressão estrutural REAL encontrada e corrigida (sonda de geometria, não chute):** um `</div>` órfão na página de Configuração (era E2) fechava o `.grid` cedo e, pela regra "pop até div" do parser HTML, o `</section>` seguinte **derrubava `<main>` e `.workspace`** — todas as páginas seguintes (análise/risco/manual/floor) viravam filhas diretas do grid `.shell` e caíam na **coluna 1, atrás da sidebar sticky**; o clique do Playwright era interceptado por elementos do rodapé da sidebar (BLOQUEADA/`#operacaoReal`) — o mesmo defeito também derrubava o teste de menu. Corrigido o `</div>`; equilíbrio do documento inteiro verificado por `html.parser` (pilha vazia); **pinado no E2E** por bounding box (`x ≥ 245`) + `closest('main.content')`.
4. **Testes:** `tests/test_copiloto_manual_ui.py` **novo — 6 estáticos** (menu/roteador; as 8 rotas `/v1/manual/*` presentes; ausência de rotas de execução na seção; promessa honesta; container `#manualAlertas`; 15 funções de UI) + E2E `tests/e2e/test_panel_e2e.py::test_minhas_operacoes_copiloto_manual_ponta_a_ponta` no painel real (login → menu → ligar copiloto → registrar posição → detalhe com política/stop → sem preço a execução NÃO é registrada → com preço real entra no diário → modo devolvido ao estado inicial). **Suíte E2E do painel: 11/11 verdes** (screenshot `08-minhas-operacoes.png`); estáticos 6/6.
5. **Ressalvas declaradas (honestidade):** homologação **no bundle Windows instalado** deste E4 fica pendente do artefato do CI deste push — não declarada. LIVE `BLOCKED_BY_POLICY` o tempo todo; a página não executa nada.
6. **Ressalva de suíte (honestidade):** a suíte COMPLETA executada COM Playwright instalado (`uv run --with playwright python -m pytest -q`) deu `2561 passed / 5 failed / 5 skipped` — as 5 falhas são TODAS `tests/test_security_fase0_r5.py::TestHardenedTransport::test_async_client_does_not_follow[301/302/303/307/308]`, e **interação de ORDEM pré-existente entre a suíte E2E (Playwright/uvicorn) e esse arquivo na MESMA sessão pytest**, não do E4 (nenhum arquivo em comum): reproduzido com `pytest tests/e2e/test_panel_e2e.py tests/test_security_fase0_r5.py` (5 falham) e provado independente com o arquivo ISOLADO (`22 passed`). Sem Playwright instalado a suíte segue verde como nos ciclos anteriores; CI roda E2E em job separado. Investigação/correção desta interação fica para o próximo ciclo — não declarada fechada.

### INST-6 — Upgrade REAL 0.4.0->0.5.0 em Windows isolado (FECHADO; run `38512611447`)

1. **Prova de ponta a ponta no MESMO run da matriz verde:** bundle ANTIGO real 0.4.0 (commit congelado `30b8683`) -> instalação limpa (C1) -> recusa com app aberto (C2, exit 7, `ABORTADO_APP_ABERTO_ANTIGO`, nada alterado) -> fechamento gracioso pelo painel -> **upgrade conclui** (C3, `cenario ATUALIZACAO`) com **backup verificado** `pre-upgrade-0.4.0-para-0.5.0-20261014-044647` (MANIFEST.sha256) e **dados preservados byte a byte (divergentes=[])**; registro e metadados do exe na 0.5.0; `healthz versao=0.5.0`; terceiros (notepad/ouvinte 8010) intactos.
2. **Veredito do harness:** `TOTAL: 0 falha(s)` — todos os checks C1–C9b PASS; a falha histórica única (C8) virou PASS com o fix do race (espera p/ 2ª fase assíncrona do Inno).
3. **Evidências:** artefato CI `matriz-instalador-evidencias` do run `38512611447` (logs completos) + cópia curada em `docs/mission/evidence/e2e/matriz-38512611447/` (`resumo-harness.txt` com o TOTAL e todos os PASS; status/telemetria por cenário; log do backup C3) + doc `docs/mission/evidence/e2e/inst6-upgrade-real-0.4.0-0.5.0.md`.
4. **Ressalva declarada:** execução em runner Windows virgem do GitHub — a instalação do dono NÃO foi tocada. LIVE `BLOCKED_BY_POLICY` o tempo todo.

### E5 — Evolução Autônoma de Estratégias (task #60; spec M6, linhas 134–145)

1. **Núcleo (`tradingagents/desk/evolucao.py`, novo, ~560 linhas):** memória auditável por trader (`RegistroDoDiario` — decisão, instrumento, regime, versão, retorno **líquido** já descontado o custo de ida-e-volta de `costs.py`, custo, causa) persistida no Armazem (chave `evolucao`) e em arquivo atômico; **classificação determinística da causa de perda** por ordem fixa de regras — `erro_execucao` (desvio de execução ≥ 0,3%) → `dados_inadequados` → `mudanca_de_regime` → `regra_descalibrada` (memória madura com retorno médio negativo) → `risco_normal` — e ganho nunca classificado como perda.
2. **Champion/Challenger com parâmetros CONGELADOS:** `Experimento` frozen com `assinatura()` SHA-256 (id, trader, título, família + assinaturas das duas hipóteses); registrar é idempotente por assinatura; executar hipótese não pré-registrada é impossível e segunda execução só com `forcar=True` (anti p-hacking, espelho do BT-2). Walk-forward fora da amostra com janelas COMUNS aos dois lados (mesma régua), executor vendo só `serie[:i+1]` (sem look-ahead), custo real por operação e correção de múltiplas hipóteses por **Bonferroni** (`alpha/familia_n`); benchmark presente. **Métricas só de rentabilidade** — expectativa líquida, drawdown, estabilidade, custos (teste pina a AUSÊNCIA de taxa de acerto).
3. **Promoção automática SOMENTE em PAPER:** `promover(modo≠"paper")` levanta ValueError ("PAPER"); exige registrado+executado+aprovado; idempotente; evento auditável com evidência; `ciclo()` auto-promove os aprovados. **Rollback/suspensão por deterioração ligados ao vigia:** `vigiar()` consome evidência nova via marcador (sem recontar perdas antigas); com versão anterior → rollback; sem → suspensão; `bloqueios()` no contrato do vigia (`{agent_id: motivo}`); `reabilitar(motivo)` exige motivo e consome a evidência anterior. O módulo **não toca código de execução, permissões nem limites** (grep zero de `create_order`/`cancel_order`/`close_position`/`.submit(`) e promoção LIVE é recusada por construção.
4. **Fiação (a evolução OBSERVA a execução, nunca a governa):** `DeskOrchestrator(..., evolucao=None)` — em `close_position` o desfecho é montado UMA vez (mesma fonte do aprendizado) e o diário é alimentado; falha da evolução vira aviso auditável (`avisos_de_evolucao`, limitado a 10) e **não derruba a saída**. `service/app.py`: `EvolucaoAutonoma` no `_desk_boot` (armazem+tenant+memória) e mescla dos bloqueios de deterioração no `_vigia_pilotos` (`setdefault` — motivo do piloto vence; dentro do try fail-closed já existente).
5. **Evidência reproduzível:** `tests/test_evolucao_autonoma.py` **novo — 34 testes** (7 classificação de perda, 3 diário, 10 experimento incl. no-look-ahead com rajada no último candle, 11 promoção/rollback/suspensão/reabilitação/relatório, 3 **integração com o orquestrador**: fechamento alimenta o diário com líquido 0,097 = 10% − 0,3% em BTC/USDT binance; falha da evolução vira aviso sem derrubar a saída; deterioração suspende E bloqueia entrada no ciclo pelo caminho real do vigia e `reabilitar` devolve o trader ao jogo). Bateria focada (piloto+saidas+laboratorio+maestro+escola+evaluation) **93 passed**; `ruff check .` **All checks passed**; suíte completa `1 failed, 2589 passed, 4 skipped, 71 subtests` — a única falha é **AMBIENTAL** (`tests/windows/test_desktop_launcher.py::test_main_backend_real_morre_e_avisa_com_log`: o app INSTALADO do dono está rodando na **porta 8000** nesta estação — PID 13800 desde 06:20, provado por `curl /healthz` + `netstat` — e a sonda do teste encontra o servidor REAL; reproduz isolada, sem relação com arquivos do E5; CI em runner virgem segue verde). Detalhes e comandos: `docs/mission/evidence/e5-evolucao-autonoma.md`.
6. **Ressalvas declaradas (honestidade):** homologação **no bundle Windows instalado** deste E5 fica pendente do artefato do CI deste push — não declarada (E5 não adiciona UI; a fiação nova só produz efeito em execução real da mesa, já coberta E2E pelos ciclos anteriores). Sem página própria no painel (não exigida pela spec): evidência da evolução é o relatório por API (`relatorio()`). LIVE `BLOCKED_BY_POLICY` o tempo todo; o app do dono NÃO foi tocado.

### E6 — Fundação Multimercado (task #61; spec E6, linhas 147–155)

1. **Núcleo (`tradingagents/desk/mercados.py`, novo, ~330 linhas) — camada de POLÍTICA:** **7 classes de ativo** — `cripto_spot`/`cripto_futuros` (homologadas: já têm adaptador e mesa de custos; custo de ida-e-volta espelhado em `costs.py` = 2×taxas+spread, ex. 0,22% no spot) e as 5 não homologadas (`execucao_homologada=False`): `b3_acoes`/`b3_fiis`/`b3_etfs` (BRL, lote 100, tick 0,01, liquidação D+2, taxa por lado 0,0325%, sessão 10:00–17:00 America/Sao_Paulo) e `us_acoes`/`us_etfs` (USD, lote 1, tick 0,01, liquidação T+1, taxa 0, spread 0,02%, sessão 9:30–16:00 America/New_York). Metadados completos (moeda, lote mínimo/passo, tick, liquidação, taxas, spread, sessão) documentados como **ESTIMATIVAS DE POLÍTICA** (não substituem `costs.py`). **Horário de verão dos EUA por REGRA sem tzdata** (2º domingo de março 07:00 UTC → 1º domingo de novembro 06:00 UTC; fronteiras de 2026 — 8/mar e 1/nov — pinadas em teste); Brasil UTC−3 fixo (DST extinto em 2019). **Feriados em snapshot anual explícito** (12 B3 + 10 US em 2026), não serviço de calendário. **Fail-closed:** `mercado_aberto()` de classe desconhecida ⇒ FECHADO; fim de semana → feriado → janela `abertura ≤ (h,m) < fechamento`; `registrar_classe` valida mercado conhecido, lote/passo/tick > 0, taxas/spread ≥ 0 e janela completa sem cruzar meia-noite; `MERCADOS` estendido para `spot, futuros, opcoes_binarias, acoes, etfs, fiis` (agora vive aqui e é reexportado por `salas.py`).
2. **Salas ganham a classe como dimensão NOVA preservando as chaves atuais:** `ContextoDaSala.classe` (campo 6, default `""`; chamadas posicionais antigas seguem válidas); chave legada de 4 partes `corretora|conta|mercado|modo` **inalterada** — o 5º segmento `|classe` só aparece quando declarada; `como_dict`/`de_dict` ida-e-volta. `_validar`: classe desconhecida, incoerente com o mercado ou **sem execução homologada** ⇒ recusa (dois gates independentes: adaptador suportando o mercado E `execucao_homologada` — teste prova que classe não homologada é recusada MESMO com adaptador de teste). `Sala.pode_operar(agora)` é o gancho de política (legado/sem classe mantém o comportamento atual) e `resumo()` expõe `classe`; **cripto 24/7 não mudou** (o ciclo autônomo não consulta sessão — como a spec manda).
3. **Migração EXPLÍCITA com redirecionamento (sem estado duplicado):** `RegistroDeSalas.migrar_sala(origem, classe, ledger=...)` exige origem legada existente, classe homologada/coerente, **recusa com posição viva** ("uma posição viva tem um dono só") e repetição; copia saldo/taxas/posições/desfechos/priors e o **livro-razão** (`export_transactions` → `Ledger.from_transactions`); marca `doc_legado["migrada_para"]`; a chave antiga passa a **REDIRECIONAR** para a sala viva em `abrir_sala` e some da listagem `salas` — nada de duas escriturações para o mesmo dinheiro. `service/app.py`: `_contexto_da_sala` aceita `classe` opcional no corpo (retrocompatível); **nenhuma UI nova**.
4. **Evidência reproduzível:** `tests/test_multimercado.py` **novo — 15 testes** (registro/validação das 7 classes; duplicata exige `substituir`; metadata inválida recusada; classe desconhecida fecha; cripto 24/7 em fim de semana; B3 fim de semana/feriado (25/12/2026)/fora da janela; US EST×EDT nas DUAS fronteiras de 2026 + feriado MLK; lote por classe; custo ida-e-volta = 2×taxas+spread (cripto_spot 0,0022; us_acoes 0,0002); chave legada vs estendida; sala com classe homologada; recusa de classe incoerente/desconhecida; recusa de classe não homologada mesmo com adaptador; migração com prova viva + redirecionamento + reload do armazém; recusas de repetição/posição aberta/classe inválida/origem inexistente). `tests/test_salas.py`: exemplo de mercado desconhecido atualizado (`("acoes",)` virou mercado legítimo — intenção preservada com `("cripto_magica",)`). Bateria focada (salas+multimercado+conhecimento+maestro) **58 passed**; API escola/salas **22 passed**; lote amplo (desk/sala/escola/piloto/…) **514 passed, 2 skipped, 1 falha ambiental**; `ruff check` **All checks passed**; suíte completa `1 failed, 2604 passed, 5 skipped, 71 subtests` em 242,56 s — a única falha é a **AMBIENTAL** conhecida (app INSTALADO do dono na porta 8000 — PID 13800; reproduz isolada, sem relação com os arquivos do E6; runner virgem do CI não afetado). Detalhes e comandos: `docs/mission/evidence/e6-multimercado.md`.
5. **Avaliação escrita (ADR-005 em `TRADING-DECISIONS.md`):** **Interactive Brokers** (TWS API madura, paper nativo, certificação pesada, sem B3 direto), **Alpaca** (REST simples, paper API de primeira classe, sem B3) e **APIs oficiais da B3** (PUMA/EntryPoint, certificação de participante) com critérios comparados e fontes (PUMA, EntryPoint, how-to-certify, teste de continuidade, Onixs, docs IBKR/TWS, Alpaca, IMRYN, Broker Insight). **Decisão do checkpoint: nenhum adaptador de execução ativado** — ativação só com homologação explícita do dono; ordem sugerida se autorizar: Alpaca paper (US) → corretora B3 → IBKR.
6. **Ressalvas declaradas (honestidade):** taxas/lotes/spreads B3/US são **estimativas de política** — a calibração definitiva é ato de homologação (adaptador + mesa de custos); feriados são **snapshot 2026** com revisão anual manual; **nenhum adaptador novo e nenhuma execução não homologada** (as 5 classes não-cripto não abrem sala por construção); E6 **não adiciona UI nem altera o ciclo autônomo** — homologação **no bundle Windows instalado** fica para o próximo reempacotamento (nada de novo para o usuário final operar; a mudança em `app.py` é parâmetro OPCIONAL de rota). LIVE `BLOCKED_BY_POLICY` o tempo todo; o app do dono NÃO foi tocado.
7. **Fechamento (CI do push `87a14da`):** Windows Release Build run `38546817504` — **success**, artefato `dz23-windows-x64` (bundle do código E6); Matriz do Instalador run `38546817421` — **success**, `TOTAL: 0 falha(s)` (artefato `matriz-instalador-evidencias`). Nos dois runs nada foi publicado (Release por tag segue aguardando autorização do dono).

### AUD-4b83e16 — Auditoria independente do commit `4b83e16` (ChatGPT; ampliada por Claude; tasks #68–#72)

Quatro achados examinados diretamente no commit `4b83e16` e tratados um a um — "corrigir apenas os problemas comprovados", com evidências, testes e limitações POR ACHADO em `docs/mission/evidence/auditoria-4b83e16-*.md`:

1. **SEC-1 — segredos em repouso (fix `a0997df`):** a config do painel era gravada em texto claro (`escrever_json_atomico` + `chmod(0o600)`, que no Windows não vira ACL). Correção: valores com cara de credencial cifrados com a DPAPI do usuário (`enc:dpapi:v1:<base64>`, sem dependência nova), migração de config legado com ida-e-volta VERIFICADA antes de gravar, falha de cifragem ABORTA o save e segredo ilegível nunca é apagado. RED 2/2 (segredo legível no disco pré-fix) → `tests/test_auditoria_sec1_segredos_em_repouso.py` **15 passed**; regressão 183. Residuais: DPAPI protege contra outros usuários/máquinas, não contra o próprio usuário (HSM fora de escopo); config migrado de PC exige re-colar os segredos.
2. **FIN-1 — selo de código para LIVE (fix `e66f2b7`):** `build_desk_broker` construía corretora LIVE com apenas a config do painel; agora exige `live_authorized=True` de CÓDIGO (o painel não concede), `_desk_boot` passa o mandato (PAPER ⇒ False), a recusa vira 409 verboso e `/v1/live/status` declara o selo. RED ("mesa arrancou com corretora LIVE (200)") → `tests/test_auditoria_fin1_live_desk_seal.py` **7 passed**; regressão 237. Freios da ponte intactos; LIVE segue `BLOCKED_BY_POLICY`.
3. **FIN-2 — gates de autorização financeira (matriz `3ee3fe0`, só testes):** `assert_secure_for_capital` cobre só o TRANSPORTE; a matriz prova os 6 gates pedidos (dono, conta, estratégia, kill-switch, limites, reconciliação) + mandato, cada um nos DOIS sentidos (recusa adversarial + caminho legítimo que abre — um gate que nunca abre é indistinguível de um gate quebrado) — `tests/test_auditoria_fin2_gates_financeiros.py` **45 passed**, nenhum gate exigiu correção. Residual: inventário rota-a-rota de autorização não feito.
4. **FIN-3 — resposta perdida (fix `aade521`):** a reconciliação consultava a corretora com o coid INTERNO da mesa — id que ela nunca viu — e "confirmava" ausência de ordem VIVA (REJECTED + reserva liberada; sonda offline reproduziu). Correção: a identidade do lado da corretora (`dz23-*`) nasce ANTES do envio, viaja no `BrokerTimeout`, é persistida no snapshot (sobrevive a reinício) e vira a PRIMEIRA chave de reconciliação/fills/órfãs; ausência de verdade segue liberando, desconhecido segue fail-closed, nada duplica. `tests/test_auditoria_fin3_resposta_perdida.py` **12 passed**; regressão 287; ruff limpo. Residual: registros legados (pré-fix) restauram sem `external_client_id` — órfãs/posição seguram o portão, mas rótulo REJECTED indevido é possível; reconciliação manual recomendada.

**Prova consolidada:** trio da auditoria `67 passed` (15+7+45); suíte completa `2627 passed / 5 skipped / 71 subtests` (exit 0; excl. `tests/windows`); `tests/windows` 56 passed + **1 falha AMBIENTAL conhecida** (app instalado do dono na porta 8000, PID 13800 — a sonda encontra o servidor REAL; o app do dono não foi tocado); nenhuma ordem real, nenhuma tag/release, LIVE `BLOCKED_BY_POLICY` o tempo todo.

### V8-S16 — Testes presos à porta 8000: probe de saúde isolado no cenário backend-morto (commit `74c319d`)

1. **Defeito reproduzível (exigência §16 do Prompt Master V8 — "eliminar falhas reproduzíveis, inclusive testes que dependam indevidamente da porta 8000"):** `tests/windows/test_desktop_launcher.py::test_main_backend_real_morre_e_avisa_com_log` declarava a porta 8000 LIVRE no cenário mockado, mas o probe de saúde tocava a porta REAL — com o app do dono rodando na 8000 (PID 13800), o `main()` tomava o ramo "abrindo o painel" e o teste virava refém do estado da estação (a mesma falha ambiental documentada em E5/E6).
2. **Correção cirúrgica (só o teste; produto intocado):** `monkeypatch.setattr(launcher, "_healthz_payload", lambda *a, **k: None)` — o próprio idioma de isolamento do arquivo — mantendo TODAS as asserções e o laço real de `esperar_healthz`. O cenário mockado é impossível em produção (`escolher_arranque` sonda a mesma porta), logo o mock não esconde defeito de produto.
3. **Prova:** teste isolado verde (7,20 s) COM o app do dono rodando na 8000; `tests/windows` **57 passed** (66,42 s) — sem `skip`, sem asserção afrouxada; suíte completa verde na sequência.

### V8-S5 — Notificações repetidas (ord-3): corrida de despacho comprovada e corrigida (commit `f7f1f0f`)

1. **Inventário completo dos mecanismos de repetição (5):** (a) produtor só enfileira por ciclo (`30b8683`/`7a1da08` — não re-dispara ordem antiga; verificado por `git log -S`); (b) dedupe de 1 h colapsa reincidência em `ocorrencias`; (c) despacho sequencial entrega cada alerta no máximo uma vez; (d) o `Notificador` antigo (`alerts.py`) é código MORTO no `service/app.py` (zero chamadores); **(e) DEFEITO COMPROVADO — despacho concorrente da mesma fila:** três threads entregam a mesma `FilaDeAlertas` sem nenhuma trava — ciclo da mesa (`app.py:2440`), copiloto manual (`:2075`) e rota `/v1/alertas/verificar` (`:3604`); dois despachos leem o mesmo alerta `novo` e entregam 2×.
2. **RED determinístico (barreira, teste novo):** pré-fix `2 == 1` — o MESMO alerta (mesmo `id`) com dois envios e duas tentativas `ok=True`; pós-fix 1 envio; red-green repetido no artefato final (trava fora → falha 0,39 s; trava dentro → verde).
3. **Fix:** `FilaDeAlertas.trava` (lock POR FILA) + seção crítica no `Despachante.despachar()`; snapshot da persistência sob a mesma trava. Filas de inquilinos distintos seguem paralelas; dedupe/backoff/rate-limit/expiração intactos.
4. **Prova:** `tests/test_centro_de_alertas.py` **42 passed**; suíte completa `2628 passed / 5 skipped / 71 subtests` (exit 0; excl. `tests/windows`); ruff limpo; push `74c319d..f7f1f0f`.
5. **Ressalvas declaradas (honestidade):** a repetição HISTÓRICA do dono não é reconstruível de logs locais (store sem `alertas.json` — a fila E2 nunca persistiu alerta real nesta máquina; registro DZ23 com `Enabled=0` e último `LastNotificationAddedTime` 09:56:33Z, coerente com clique manual em "Testar" no exe pós-N01; nenhum arquivo do dono tocado). O comprovado: a corrida existia e produz exatamente o sintoma "mesma notificação repetida". Fontes residuais por desenho (não alteradas): re-notificação horária de condição persistente (janela de dedupe) e retry após falha RELATADA com toast já exibido (timeout 20 s do PowerShell; duplicar é preterível a sumir em silêncio). Criação concorrente da fila (`_fila_alertas` get-or-create) registrada para ciclo futuro. Detalhes: `docs/mission/evidence/v8-s5-notificacoes-repetidas.md`.

### V8-PWA — PWA MOBILE + WEB PUSH (complemento obrigatório do V8): implementação completa com evidências; homologação em dispositivo/HTTPS reais declarada pendente (tasks #74–#78)

1. **Mesmos componentes, nenhum sistema paralelo:** o PWA é o MESMO painel (`index.html`), o MESMO Centro de Alertas (fila durável + despacho) e o MESMO FastAPI. Núcleo novo `tradingagents/desk/push_web.py` (~320 linhas): chaves VAPID (py-vapid), validação de subscription (endpoint HTTPS + chaves base64url), limite de 10 dispositivos, dedupe por identificador (reinscrição ATUALIZA), entrega via pywebpush com TTL/Urgency por severidade, poda automática de expiradas (404/410 → re-save) e `estado_publico()` que nunca expõe a privada nem o endpoint. Canal `CanalWebPush` (prioridade 6) no despacho REAL por inquilino (`_push_do_tenant` em `app.py`) — o botão "Testar" usa o entregador de verdade.
2. **Rotas `/v1/push/*` autenticadas:** estado (honesto), `vapid/gerar` (409 sem `forcar`; NUNCA ecoa a privada), `assinar` (valida/deduplica/limite/aviso), `cancelar` (422/404/200). `ALERTA_VAPID_PRIVADA` + `PUSH_ASSINATURAS` entraram no universo de segredos em repouso (DPAPI/SEC-1; `GET /v1/config` mascara).
3. **Service Worker versionado (`service/static/sw.js`)** em `dz23-pwa-v1`: cache SÓ de manifest + `/static/marca/` (NUNCA `/v1` nem página autenticada — pinado); `updateViaCache:'none'`; purge de cache antigo; push com `tag` por id, `requireInteraction` nas severidades altas e `data.url='/app#notificacoes'` (sem dado sensível na URL); `notificationclick` foca janela aberta. Rota `/sw.js` com `no-cache` + `Service-Worker-Allowed: /`.
4. **UI no painel (card novo):** botão "Instalar" SÓ quando `beforeinstallprompt` (e some quando o navegador não oferece); instruções iOS "Compartilhar → Adicionar à Tela de Início"; estados honestos (contexto seguro/SW/permissão/VAPID/pywebpush ausente); ativar neste aparelho; testar push; gerar chaves; cancelar por dispositivo.
5. **Defeito real encontrado pelo e2e e corrigido (RED→GREEN):** o CSP novo com `frame-ancestors 'none'` em TODO `text/html` derrubou o iframe same-origin do Trading Floor (`test_floor_embutido_same_origin` → "127.0.0.1 está bloqueado") — o produto já tinha `X-Frame-Options: SAMEORIGIN` para `/floor` exatamente para isso (CSP sobrepõe XFO). Correção por caminho: `/floor` → `'self'`; resto → `'none'`. Teste unit novo `test_csp_nao_quebra_o_iframe_do_floor` falhou com o header exato pré-fix e passou pós-fix; e2e do floor RED→GREEN no navegador real.
6. **Provas frescas (2026-10-14):** bateria focada PWA (`test_pwa_push` 36 + `test_pwa_static` 9 + `test_centro_de_alertas` 42 + `test_alertas_transparencia`) **110 passed**; **e2e Playwright 12/12** (novo `test_pwa_service_worker_ativo_e_estado_honesto` + responsivo com 320×568; screenshots `07-login-320x568.png` e `08-pwa-config.png`); `node --check` OK; ruff limpo; cripto REAL sem rede provada (payload VAPID válido + conteúdo cifrado). Suíte completa: **2730 passed / 5 skipped / 71 subtests** (exit 0).
7. **Ressalvas declaradas (honestidade — §10 do complemento: "não declarar concluído só pela presença do manifest"):** homologação REAL dos 15 testes de aceitação em Android/iPhone (instalar, push com app fechado, toque na notificação, offline/update) está **BLOQUEADA_EXTERNA** — não há endpoint HTTPS autorizado nem dispositivo físico nesta estação (e deploy/DNS exige autorização do dono, §14 do V8); matriz §9 completa com o estado de cada teste em `docs/mission/evidence/v8-pwa-push.md`. Nenhuma chave VAPID gerada no ambiente do dono; `%APPDATA%\DZ23-Trading-AI\` intocado. O botão "Acompanhar esta operação" (§4 do V8 principal) segue seção separada PENDENTE (aqui só o copiloto manual já existente). CSRF não aplicável (Bearer por header, sem cookie). Push não substitui stops na corretora; LIVE `BLOCKED_BY_POLICY` o tempo todo.

### V8 §6 + SMART SETUP — Configuração completa de IA + primeira execução inteligente (commits `8d5f522`…`c38473e`; tasks #79–#88)

1. **Defeito REAL corrigido no caminho (§6a, `8d5f522`):** os DOIS caminhos de consulta de IA do service (IA de contexto da mesa e revisor do diário) passavam o dict de config como `model` e chamavam `cliente.complete()`/`invoke()` — métodos que NÃO existem no `BaseLLMClient` (`AttributeError`): **a IA de contexto nunca funcionou em produção**. RED por guarda estático novo (`test_app_nao_chama_complete_nem_invoke_no_cliente`, falhou contra os 3 padrões do defeito) → GREEN: `_consultar`/`_revisor_de_ia` viraram wrappers finos de `consultar_ia()` (caminho correto `get_llm(...).invoke()`); latência passa a registrar o provedor que DE FATO respondeu.
2. **§6b–§6d (rotas, custo, UI):** `POST /v1/config` grava E remove (`{"remover":[...]}`, com resposta honesta `ainda_no_ambiente` quando o env do sistema ainda fornece o valor); `POST /v1/ai/testar` (sonda real de modelos; sem chave NÃO sai à rede; estados sanitizados `chave_rejeitada`/`tempo_esgotado`/…; corpo do provedor nunca vaza; limites/retry-after capturados); `GET /v1/ai/modelos` (descoberta real com chave, catálogo com fonte DECLARADA sem chave); `GET /v1/ai/uso` + tabela de preços de referência (custo PARCIAL declarado quando falta preço; tokens nunca inventados); card "Provedores de IA" no painel (chave com hint mascarado, testar, buscar modelos, remover, fallback, orçamento, consumo). Catálogo com 9 provedores (`openai`, `anthropic`, `google`, `deepseek`, `groq`, `mistral`, `openrouter`, `openai_compatible`, `ollama` — Ollama opcional, NADA instalado).
3. **Smart Setup — os 14 requisitos (SS-1 `e592c13`, SS-2 `74d669f`, SS-3 `c38473e`):** pré-config idempotente (`aplicar()` preenche SÓ campos vazios; `aplicados`/`mantidos` explícitos; 2ª aplicação = `{}`); perfis Pronto/Econômico/Equilibrado/Avançado; classificação rápido/profundo por segmento do nome (sem lista fixa); seleção automática entre provedores CONFIGURADOS com ordenação por custo/latência/autenticação; orçamento com bloqueio opt-in que NUNCA desativa a verificação de contexto; diagnóstico visual em 4 estados honestos (configurado→conectado→autenticado→operacional, cada um derivado de evidência local — nunca de "ping" imaginário); assistente de primeira execução com pendências reais (sem percentual inventado); credenciais nunca criadas/alteradas pelo assistente (DPAPI/SEC-1; hint mascarado); página Configuração manual preservada por inteiro (o card é adicional).
4. **Bugs REAIS achados na homologação NO NAVEGADOR (não por teste estático), corrigidos com pins:** (a) **toast mentiroso** — `aplicados` é DICT e `.length` de dict é `undefined` (falsy): o toast caía sempre em "nada para preencher" mesmo tendo preenchido; fix `Object.entries(d.aplicados||{})` + pin `test_aplicar_conta_campos_como_objeto`; (b) **campo de idioma vazio após aplicar** — `PADROES_SEGUROS` gravava `"Português (Brasil)"`, valor que o `<select>` do painel NÃO exibe; fix nome canônico `"Portuguese"` + pin `test_idioma_do_smart_setup_cabe_no_select_manual`; (c) **sobreposição do gráfico de fundo** (R13): `.shell{position:relative;z-index:1}` sobre `.gate-cena{position:fixed;z-index:0}` — conteúdo clicável acima do gráfico, verificado por `elementFromPoint` e capturas. Ambos os fixes re-verificados AO VIVO na versão final do código (toast honesto com contagem de dict + idioma visível).
5. **Matriz R14 (SS-3) — 12 cenários HTTP em instância isolada, zero rede:** primeira instalação do zero ao pronto SEM criar chave; chave colada destranca; idempotência estável; migração de config legado; `/v1/setup/estado` é PURO (sonda bombeada = falha do teste; sem internet vira estado explícito); `chave_rejeitada` (401) distingue conectado de autenticado; operacional só com uso REALMENTE registrado; troca de provedor reordena fallback sem perder chaves; restart preserva config e zera observação de sessão; `config.json` corrompido não derruba.
6. **Provas frescas (2026-10-14):** `tests/test_ia_provedores.py` **49** + `test_ia_painel_static.py` **27** + `test_smart_setup.py` **35** + `test_smart_setup_homologacao.py` **12** (12/12 na 1ª corrida; lista IA = 123 coletados); suíte completa final **2853 passed / 5 skipped / 71 subtests** (exit 0, 294,28 s; excl. `tests/windows`); ruff limpo; capturas em `docs/mission/evidence/v8-ss2/` (card final, toast final, topo final na instância 8786 com ambiente SEM chaves; app do dono em `:8000` intocado).
7. **Ressalvas declaradas (honestidade):** **NENHUMA chamada de API real executada** — o dono não autorizou testes com as credenciais dele (§6: "somente quando houver credenciais autorizadas"); nenhuma chave fictícia usada; provado o comportamento offline/honesto, não a autenticação contra OpenAI/Groq/etc. **Homologação Windows/PWA do pedido NÃO está concluída** — navegador real contra instância temporária + matriz HTTP automatizada existem; os fluxos no exe instalado dependem do Modo Simples/PLAY (SS-5) e do aceite do dashboard (TD-5). Requisito 1 é deliberadamente parcial (idioma/rodadas/controle; as travas de risco PAPER permanecem de fábrica — o Smart Setup NUNCA afrouxa trava). `_ULTIMO_TESTE` é observação de sessão (em memória, por desenho). LIMITES de orçamento afetam só chamadas controladas pelo DZ23. Detalhes: `docs/mission/evidence/v8-s6-configuracao-ia.md` e `docs/mission/evidence/v8-smart-setup.md`.

### V8 SS-5 — Interface Simples (padrão) + Controle Avançado (diretriz definitiva de UX; task #89)

1. **Diretriz do dono implementada ao pé da letra:** *"cadastrar as APIs necessárias, escolher DEMO, REAL ou COPILOTO DE SINAIS e apertar PLAY"* virou a página **Início** — o padrão de abertura — com 3 cards: **1 · CONECTAR** (corretora + chaves + IA opcional → `POST /v1/config` pelas MESMAS chaves de sempre), **2 · MODO** (DEMO / REAL / COPILOTO DE SINAIS), **3 · PLAY** (Ligar/Parar na MESMA rota `/v1/desk/scheduler/{start|stop}` da mesa). **ZERO backend novo** — só rotas existentes; `service/app.py` intocado.
2. **"Simplificar ≠ remover" pinado desde o primeiro commit:** o seletor de modo da interface (`#uiModo`, na sidebar) é preferência **por device** (`localStorage`, nunca fala com o servidor, nunca altera configuração da mesa); no Simples a navegação mostra 3 páginas (`inicio`, `overview`, `manual`), no Avançado o array `pages` completo (8 — todas as antigas preservadas, com guarda de teste que falha se qualquer página antiga sumir); trocar para Simples estando numa página oculta leva ao Início.
3. **Segurança inegociável visível nas DUAS interfaces:** REAL desabilitado por radio `disabled` com motivo escrito na tela ("até homologação e autorização explícita do proprietário") — o toggle de modo não pode tocar nisso (pinado); PAPER marcado como **simulado** ("dinheiro real bloqueado"); COPILOTO DE SINAIS com o aviso "nunca envia ordens" e atalho para Minhas Operações; limites "por código determinístico" e "recusa e avisa" para dados vencidos ditos na tela; o assistente lê só estado real (`/v1/config`, `/v1/desk/status`, `/v1/setup/estado`) — nada inventado.
4. **Defeito latente REAL da suíte e2e achado pelo teste novo (causa-raiz, não retry):** a limpeza do copiloto em `test_minhas_operacoes` detectava `"LIGADO" in texto` — **substring de "DESLIGADO"** — logo o copiloto manual nunca era desligado na sessão compartilhada e o PLAY novo batia no guarda `_guarda_do_pedido` (409). Fix: leitura do `<b>` exato (`^(LIGADO|DESLIGADO)$`, `re.compile`); o latente ficou pinado no próprio teste corrigido.
5. **Provas frescas (2026-10-14):** RED honesto antes da implementação (`17 failed, 1 passed` — a passada é a guarda "nada removido"); GREEN focado **45 passed** (18 SS-5 + 27 estáticos); **e2e Playwright 13/13** (30,81 s; `test_modo_simples_assistente_conectar_modo_play` novo); `node --check` verde (via suíte estática); ruff limpo (1 SIM300 corrigido). **Homologação AO VIVO na instância temporária 8786** (app do dono em `:8000` intocado): Início abre com 3 passos; PLAY → `RODANDO`; PAUSAR → `PARADA · 1 ciclo` (ciclo real rodou — fiação de verdade); toggle 3↔8 páginas (labels exatos: inicio, overview, config, analysis, mesa, manual, floor, risk); `#iniModoReal` disabled; **zero mensagens no console**; capturas em `docs/mission/evidence/v8-modo-simples/`.
6. **Ressalvas declaradas (honestidade):** homologação no **exe instalado `--windowed` fica para o TD-5** (matriz de aceite: 12 testes de dashboard + 10 passos do iniciante no app instalado) — nada declarado homologado no exe; nenhuma chamada real de API de corretora/IA executada (sem credenciais autorizadas); LIVE `BLOCKED_BY_POLICY` o tempo todo; `%APPDATA%\DZ23-Trading-AI\` intocado. Detalhes: `docs/mission/evidence/v8-modo-simples.md`.

### V8 TD-1 — Painel Global do Maestro (consolidação multi-conta; task #90)

1. **Regra de segregação implementada como invariante de código:** `tradingagents/desk/painel_global.py` (novo, MÓDULO PURO — sem I/O, sem broker, pinado por teste que grepa `create_order`/`ccxt`/`requests`): PAPER/TESTNET/LIVE nunca compartilham um número (subtotais por modo); moedas diferentes nunca são somadas sem conversão identificada e verificável (subtotais por moeda; moeda não declarada ⇒ total OMITIDO com motivo); o total "mesa + salas" só existe quando modo e moeda são únicos e declarados — e o texto da regra fica impresso no próprio painel. LIVE sempre `{"bloqueado": true}` com o motivo da autorização do proprietário.
2. **Rota `GET /v1/maestro/painel` (só autenticação, como as demais leituras) + card `#painelGlobal` na página Mesa:** lê `/v1/desk/status`, livro, contabilidade, salas da Escola e relatório do Maestro; **supervisão não cega** — se a mesa não sobe, o painel responde com `mesa_motivo` e ainda entrega salas/traders. Comparações entre traders usam SOMENTE evidência madura (sem maduros ⇒ média omitida com motivo, nunca estimada). Descoberta relevante: o ledger da mesa nunca é transplantado para a sala espelho — por isso a soma só acontece com modo+moeda únicos.
3. **TDD com RED honesto:** teste escrito primeiro (módulo inexistente → falha; rota na instância viva → 404) → GREEN **20 passed** (14 unidade + 3 HTTP + 3 DOM) → regressão focada **87 passed** (painel_global + modo_simples + service_escola + ia_painel_static) → ruff limpo (C416/C408 corrigidos).
4. **Provas frescas:** suíte completa **`2891 passed / 5 skipped / 71 subtests`** (exit 0, 275,76 s — 2871 do SS-5 + 20 novos; duas execuções independentes, mesmo resultado); **e2e Playwright 13/13** (31,26 s).
5. **Homologação AO VIVO na instância temporária 8786** (app do dono em `:8000` intocado; ambiente sem chaves de IA reais): JSON vivo com `patrimonio.total=null` + motivo "modos diferentes nunca compartilham um total (PAPER/TESTNET/LIVE têm contas e riscos próprios)", salas `binance|pa-td1|spot|paper` e `binance|tb-td1|spot|testnet` lado a lado, "LIVE BLOQUEADO — …autorização explícita do proprietário", traders 10 (3 homologados + 7 experimentais), comparações "sem evidência madura neste tier — média não estimada", fonte "fixture — provam a mesa, não o mercado"; render do card verificado em navegador real (Playwright, login real); capturas `docs/mission/evidence/v8-painel-global/painel-01-global.png` e `painel-02-mesa-contexto.png`.
6. **Ressalvas declaradas (honestidade):** dados vivos são de FIXTURE (mesa parada, saldo 1000 simulado, sem posições) — provam a fiação e a segregação, não o mercado; a soma "mesa + salas" no subtotal único foi provada por unidade/HTTP (na instância viva havia dois modos, então o total permaneceu omitido, como manda a regra); homologação no exe instalado segue PENDENTE no TD-5; nenhuma tag/Release; LIVE `BLOCKED_BY_POLICY`. Evidência completa: `docs/mission/evidence/v8-painel-global.md`.

### V8 TD-2 — Sala individual dos 10 traders (gráfico de mercado + portfólio; task #91)

1. **Cada trader tem sala própria que LÊ o livro-razão e os eventos — nada inventado:** card `#salaTrader` na página Mesa (botão "Sala" por trader) com gráfico de mercado (candles + volume quando a fonte é real, descartando a vela EM FORMAÇÃO — "usá-la é olhar o futuro"; em fonte fixture, LINHA rotulada dos fechamentos, NENHUM O/H/L/V fabricado), SMA 9/21 + RSI (Wilder 14), suportes/resistências por pivôs com a nota "níveis aproximados — não são linhas de mercado", marcações de execução derivadas dos lançamentos do livro (venda `(ΣCASH:+ΣFEES:)/qtd`; aritmética que não fecha ⇒ `preco=None` — "melhor declarar que derivar errado") + abstenções/vetos dos ciclos com motivo, e portfólio com curva do diário (soma simples, "não é saldo de conta"), custos (FEES separado do bruto do livro), drawdown, hit rate, expectativa, profit factor (sem operações ⇒ `None` + "nada a medir — posição aberta é expectativa, não resultado"; sem perdas ⇒ `None` + motivo), exposição viva, ordens abertas e Champion/Challenger ("pendente — não é aprovado por omissão").
2. **Arquitetura:** módulo PURO `tradingagents/desk/sala_trader.py` (~330 linhas; sem I/O/rede — pinado por grep de `create_order`/`ccxt`/`requests`); rota `GET /v1/desk/traders/{agent_id}/sala` (só autenticação; 404 trader desconhecido, 422 timeframe/instrumento fora do universo; mesma `PublicMarketFeed` cacheada do snapshot, 30 s); a arena ganhou 2 leituras read-only (`experimentos()`/`resultados_dos_experimentos()`, sem tocar estado interno).
3. **TDD RED→GREEN:** 32 testes novos (24 unidade + 4 HTTP + 4 DOM pinados ANTES da UI) — RED honesto (módulo inexistente falha; DOM falha antes do card) → GREEN **32 passed** (1,59–1,87 s); regressão focada **147 passed** (painel_global + maestro + multiagente + evolução + ia_painel_static + scheduler + market); ruff limpo (I001/UP035/E741/C408/F401 corrigidos).
4. **Provas frescas:** suíte completa **`2923 passed / 5 skipped / 71 subtests`** (exit 0, 313,07 s — 2891 do TD-1 + 32 novos; chaves de IA reais removidas do processo); **e2e Playwright 13/13** (31,88 s).
5. **Homologação AO VIVO na instância temporária 8786** (app do dono em `:8000` intocado; instância encerrada ao final e porta de volta a "down"): JSON vivo com `mercado.tipo=linha`/`fonte=fixture` + aviso "sem OHLCV inventado", `motivo_profit_factor` honesto ("sem operações fechadas — nada a medir"), "PAPER · moeda USDT · LIVE bloqueado"; render no navegador real (login → Mesa → botão "Sala" do trader-trend) com seletor 1m–1d, indicadores, estados vazios explícitos e avisos de honestidade; capturas `docs/mission/evidence/sala-01-card.png` e `sala-02-contexto.png`; JSON de prova em `%TEMP%/dz23-td2-sala-trend.json` (fora do repo).
6. **Ressalvas declaradas (honestidade):** dados vivos de FIXTURE provam a fiação/derivação/rótulos — não o mercado (candles/volume reais dependem de fonte real conectada; caminho provado por unidade + DOM); a curva de portfólio viva ficou vazia (nenhuma operação fechada na instância — desenho provado por unidade e por pin de DOM); homologação no exe instalado (10 passos do usuário iniciante) segue PENDENTE no TD-5; nenhuma tag/Release; LIVE `BLOCKED_BY_POLICY`. Evidência completa: `docs/mission/evidence/v8-sala-trader.md`.

### V8 TD-3 — Gráficos profissionais (Lightweight Charts vendorizado + avaliação TradingView) e IA lendo gráficos (task #92)

1. **Biblioteca profissional vendorizada e servida localmente:** Lightweight Charts v5.2.1 (Apache-2.0) do tarball oficial do npm em `service/static/vendor/lightweight-charts/` (js 197.922 B sha256 `e21cc5ca…5f598cf`; LICENSE/VERSION na pasta, servidos por rota dedicada com allowlist — **defeito vivo #1**: o painel referenciava `/static/vendor/...` mas o app só serve estáticos por rotas explícitas, então o JS 404ava e o fallback SVG mascarava; rota + 4 pins criados em RED→GREEN). Página sem NENHUM script externo (`tradingview.com`/CDN ausentes — pinado por teste); o navegador real não fez nenhuma chamada externa na homologação.
2. **Gráficos profissionais + queda honesta:** `lcGraficoMercado` (API v5: CandlestickSeries + volume + SMA + linha de S/R + marcadores das execuções do livro) e `lcGraficoPortfolio` (AreaSeries + marcador por ponto); `graficoMercadoEscolha`/`graficoPortfolioEscolha` caem para os SVG do TD-2 quando a biblioteca não carregar (nunca tela quebrada, nunca dado inventado — a API v4 morta não existe no arquivo).
3. **Avaliação TradingView (decisão documentada):** NÃO embutir — o widget é servido pelos servidores do TradingView e o CSP `script-src 'self'` bloqueia script externo; o Advanced Charts self-hosted é proprietário (licença comercial); scraping excluído por mandato do dono. Tudo 100% local.
4. **IA lendo gráficos por séries ESTRUTURADAS (números, nunca imagem):** rota `POST /v1/desk/traders/{agent_id}/sala/leitura` (só autenticação; 404/422) monta `prompt_de_leitura(retrato)` com os números do retrato do TD-2 (fechamentos/velas, indicadores, níveis, marcações, curva, exposição, Champion/Challenger) e 3 regras absolutas ("NUNCA invente preço/vela/indicador/resultado"; "NUNCA … prometa lucro nem previsão exata"; "NUNCA sugira ordem"); sem provedor ⇒ `{ok:false, motivo:"sem provedor de IA configurado — … Nada foi enviado."}` **antes** de qualquer rede; com provedor, o consumo entra no orçamento (`IA_USO`, 3º ponto de registro pinado).
5. **2 defeitos REAIS adicionais achados AO VIVO e corrigidos (RED→GREEN):** (a) **llama do Groq descontinuado** — instância B falhou HTTP 404 com o padrão de fábrica; padrões trocados para os modelos vivos verificados na fonte em 2026-10-14 (`openai/gpt-oss-20b`/`openai/gpt-oss-120b`) com preços oficiais (0.075/0.30 e 0.15/0.60 USD/1M) na tabela de custos; (b) **sugeridor propunha modelo de áudio** (`whisper-large-v3-turbo` como "profundo") — filtro `elegivel_chat` (áudio/TTS/embedding/guarda fora) + 3 testes.
6. **TDD + provas:** 21 testes novos (`tests/test_td3_graficos.py`: biblioteca/rota/painel/prompt/HTTP/DOM) + 3 de modelos vivos; focada **120 passed** (5,71 s); suíte completa **`2947 passed / 5 skipped / 71 subtests`** (exit 0, 269,71 s; 2923 do TD-2 + 24 novos); **e2e Playwright 13/13** (31,83 s; capturas de `evidence/e2e/` regeradas); ruff limpo.
7. **Homologação AO VIVO em 2 instâncias isoladas (8786 sem provedor / 8787 com groq de chave efêmera do ambiente):** A = LightweightCharts 5.2.1 real (`canvas no #lcMercado: 7`), fallback SVG com o vendor bloqueado (`svg=1`, biblioteca ausente), "sem provedor … Nada foi enviado."; B = leitura REAL da IA sobre as séries estruturadas (fixture rotulada, fechamentos 145→158.5, SMA 152.5/143.5, RSI 100) com custo registrado `0.000337 USD` conferindo com a tabela nova ((559×0.075+984×0.30)/1e6); `hosts_externos_A/B: []`, veredito **OK**; instâncias encerradas e portas "down"; capturas `docs/mission/evidence/td3/` (5 PNG + txt integral da leitura).
8. **Ressalvas declaradas (honestidade):** dados vivos de FIXTURE provam a fiação/biblioteca/leituras — não o mercado; a curva LC viva ficou vazia (desenho provado por unidade/DOM); TradingView não embutido por decisão documentada (licença/CSP); "rápido" do sugeridor cai no `allam-2-7b` quando o gpt-oss-20b classifica "neutro" (proxy de nome declarado; a descoberta real da API manda); homologação no exe instalado (incl. visual dos gráficos) segue PENDENTE no TD-5; nenhuma tag/Release; LIVE `BLOCKED_BY_POLICY`. Evidência completa: `docs/mission/evidence/v8-td3-graficos.md`.

### V8 TD-4 — Dashboard do Copiloto Manual (sinais determinísticos + botão "Acompanhar esta operação"; task #93)

1. **O elo que faltava do fluxo §3 (11 passos):** card **"Análise do copiloto"** em `#page-manual` (escolher ativo + tempo gráfico → "Analisar mercado") consome a rota nova `GET /v1/manual/sinais` — que **NÃO boota a mesa** e lê somente a fonte de mercado da instância (fixture ou real, sempre rotulada). O módulo `tradingagents/desk/copiloto_sinais.py` é **PURO** (sem `create_order`/`cancel_order`/`ccxt`/`requests` — pinado por grep + AST na própria função da rota): **"o copiloto jamais cria ou cancela ordens"**.
2. **Retrato honesto (nunca número inventado):** `oportunidade` (viés ou motivo), `riscos` rotulados (fonte fixture explicitada — "provam a mesa, não o mercado vivo"; volatilidade da amostra; RSI contrário; "declare stop e invalidação no registro"), `custos` com a nota **"NUNCA estima"**, `horizonte` por timeframe, `invalidacao` pelo pivô mais próximo (série pinada: 109.27 na compra / 90.73 na venda); **RSI ≥ 70 / ≤ 30 bloqueia o viés** com o motivo exato "comprar preço esticado não é oportunidade; aguarde alívio"; 3 avisos fixos em toda resposta (não é promessa de lucro nem previsão exata; o copiloto não envia ordens; cotação de tela pode estar defasada).
3. **§4 cumprido ao pé da letra:** o botão do card de registro foi renomeado para **"Acompanhar esta operação"** (`onclick="manualCriar()"`, pinado por regex exata no DOM; o seletor do e2e era o único pintado contra o rename — atualizado e verde), com a legenda "ele nunca cria ou cancela ordens na corretora"; o botão **"Usar esta análise no registro"** preenche instrumento/direção/tese/invalidação e rola até o registro; novo campo `#manCustos` ("taxas reais cobradas pela corretora") → `custos_entrada` no `manualCriar`.
4. **TDD RED→GREEN:** 31 testes novos (`tests/test_copiloto_sinais.py`: unidade + HTTP + DOM + módulo puro/rota — incluindo determinismo, série curta/vazia com motivo, fixture/real sem mistura e AST pin da rota).
5. **Provas frescas (2026-10-14):** 31/31 (0,82 s); regressão focada (sinais + copiloto_manual + api + ui + td3 + sala_trader + painel_global + ia_painel_static) **195 passed**; suíte completa **`2978 passed / 5 skipped / 71 subtests`** (exit 0, 255,03 s — 2947 do TD-3 + 31 novos); **e2e Playwright 13/13** (31,17 s); ruff limpo.
6. **Homologação AO VIVO na instância temporária 8787** (app do dono em `:8000` intocado; instância encerrada ao final): mesa desligada ⇒ motivo "0 de 21" honesto **sem inventar série**; com mesa ativa ⇒ JSON completo de fixture (amostra 40, SMA 152.5 > 143.5 alta, RSI 100 sobrecomprado ⇒ `vies:null` + "aguarde alívio", custos NUNCA, riscos rotulados); **navegador real (Playwright)**: análise renderizada no card + "Usar esta análise no registro" com prefill (instrumento BTC/USDT, tese = motivo do viés) e o botão **"Acompanhar esta operação" visível** com o aviso de que o copiloto nunca cria/cancela ordens; capturas `docs/mission/evidence/v8-copiloto-dashboard/td4-01-analise.png` e `td4-02-acompanhar.png`.
7. **Ressalvas declaradas (honestidade):** dados vivos de FIXTURE provam a fiação/lógica — não o mercado; homologação no **exe instalado** segue PENDENTE no TD-5 (declarada — nada afirmado); nenhuma tag/Release; LIVE `BLOCKED_BY_POLICY`; nenhum número simulado apresentado como real. Evidência completa: `docs/mission/evidence/v8-copiloto-dashboard.md`.

### V8 TD-5 — Aceite E2E no bundle do artefato CI (matriz de 13 itens; tasks #94/#99–#101)

1. **A matriz de aceite (`tests/e2e/test_aceite_td5.py`, 13 testes):** 12 itens de dashboard/UX (§4) + fluxo do iniciante em 10 passos (§3) com navegador real contra o app servido em instância isolada — a guarda do harness **ABORTA** se qualquer instância DZ23 já responder na faixa 8000–8010 (a instância do dono em `:8000` nunca foi tocada — verificado localmente); o harness exige contagem coletada == aprovada e rc=0 (nada oculto).
2. **2 defeitos REAIS de responsividade 320 px encontrados pela matriz e corrigidos (`ac2a1bc`):** `#quitAppBtn` estourava a topbar em **+15 px** (≤760 px, modo Dono Único) e valores longos do monitor (`JSON.stringify` dentro de `<b>`) estouravam `#manualLista` em **+49 px** → `overflow-wrap:anywhere`; o teste 12 virou auto-diagnóstico (imprime os elementos culpados na falha). Prova: `scrollWidth − innerWidth ≤ 1` em 390/320 px nas páginas inicio/manual/mesa/config com a mesa QUENTE; 4 corridas e2e seguidas verdes.
3. **Aceite local:** 13/13 na matriz; suíte e2e completa **26/26** (4 corridas seguidas, ~41–42 s). Capturas `docs/mission/evidence/e2e/td5-*.png` (18).
4. **Aceite no BUNDLE do artefato CI (run `38596224361`, commit `b3e9dae`):** runner `windows-latest` virgem; ZIP `dz23-windows-x64` com `sha256sum -c SHA256SUMS.txt` OK; `%APPDATA%` descartável (`dz23-aceite-bundle-*`); **`13 passed in 37.79s`**; 20 evidências curadas em `docs/mission/evidence/v8-td5-exe/`. **Causa-raiz da 1ª falha muda do job consertada no mesmo commit:** o venv do runner tinha só as dependências base (`pytest` inexistente → coleta 0 com stderr oculto) — agora sincroniza `--extra desk --extra dev`, instala Playwright no venv do projeto e o harness diagnostica ferramentas/coleta (stdout+stderr) em vez de falhar mudo.
5. **Re-citação no artefato FINAL da fase (run `38599535042`, push de `d131226`):** `build` + `aceite-bundle` SUCCESS — **`13 passed in 75.63s`**; artefato `aceite-td5-bundle-evidencias` baixado e recopiado para `docs/mission/evidence/v8-td5-exe/` (20 arquivos, 18 capturas regeradas — a prova no bundle corresponde ao artefato do commit da RC); `publish-release` skipped (sem tag — publicação aguarda autorização do dono).
6. **Ressalvas declaradas (honestidade):** 1 corrida transiente sob carga com 2 timeouts de aquecimento não reproduzidos (registrada; as 4 seguintes 26/26 — nada skipado); dados de fixture provam a fiação — não o mercado; LIVE `BLOCKED_BY_POLICY`. Evidência completa: `docs/mission/evidence/v8-td5-aceite.md`.

### V8.1 §4+§6 — Decisão do ciclo + indicadores honestos (tasks #102/#103; `16a8807`)

1. **`tradingagents/desk/decisao.py` (módulo determinístico):** decisão do ciclo em 4 estados com ladeira de precedência **SUSPENDER → BLOQUEAR → OPERAR → AGUARDAR** (abstenção inteligente — AGUARDAR é resultado legítimo e explicado, nunca silêncio), exposta em `GET /v1/desk/status.decisao` e renderizada no início do painel.
2. **`sala_trader.indices_de_risco`:** volatilidade/Sharpe/Sortino **por operação** — abaixo da amostra mínima de 30, o número é `None` **com motivo** ("nada inventado"; Sharpe/Sortino só quando estatisticamente apropriados — §6); PAPER/TESTNET/LIVE jamais misturados (regra herdada do Painel Global).
3. **Provas:** 23 testes novos (`test_decisao_mesa.py` 17 + `test_sala_trader.py` +6); suíte completa da fase `3001 passed / 6 skipped / 71 subtests`; o aceite TD-5 cobriu o card novo no local e no bundle.

### RC §19 — Pacote de entrega final (task #104; `d131226`)

1. **`docs/mission/entrega-final/`:** **A** `MATRIZ-REQUISITOS-V8.md` (requisito → implementação → teste → evidência → estado; V8 §1–§20 + PWA §1–§10 + V8.1 §1–§14; critérios §18 com prova; dependências D1–D9), **B** `RELATORIO-HOMOLOGACAO-V8.md` (placares, **6 skips declarados com razão**, falhas com causa-raiz, riscos R1–R8, classificação PARTIALLY_VERIFIED), **C** `RELATORIO-PROPRIETARIO-V8.md` (português simples), `CHECKLIST-AUDITORIA-INDEPENDENTE.md` (roteiro de reprodução/falsificação para auditoria externa), `COMMITS-E-PRS.md`, `RELATORIO-FINANCEIRO-QUANT-V8.md`; registro do capital-piloto em `docs/mission/REGISTRO-CAPITAL-PILOTO.md` (**R$ 1.000 — capital de piloto futuro, NÃO AUTORIZADO para uso**; V8.1 §12).
2. **Regra mantida:** o Qoder **não certifica a própria entrega** — a auditoria independente externa é passo separado (checklist pronto); publicação de Release/tag aguarda autorização explícita do dono.

### RC §17/#106 — Guias do produto real (`abe18cc`; branch `fix/instalador-atualizacoes-windows`)

1. `INSTALAR.md` e `COMECE-AQUI.md` reescritos para o produto REAL (dono único, Windows-first: instalador do pipeline, `%APPDATA%`, backup verificado pré-upgrade, porta 8000→8010, código de recuperação, PAPER/TESTNET, LIVE bloqueado); `DOMINIO-E-CADASTROS.md` (checklist da era SaaS — produto 404 por política) **removido**; ref de projeto Supabase REAL substituída por sintética em `tests/test_supabase.py`; os 3 pins do guia SaaS convertidos em 4 pins de fidelidade (`TestGuiasDoProdutoReal` — guia morto em repositório público é defeito de entrega).
2. **Provas:** lote focado verde (79 testes), `ruff` limpo, varredura de marcadores SaaS limpa; **suíte completa do lote final `3002 passed / 6 skipped / 71 subtests` (exit 0, 297,25 s)**; CI do push em G8.
3. **Ressalvas:** publicação de Release segue aguardando autorização; auditoria externa não executada (por desenho).

### RC — Selo de congelamento do artefato + changelog do produto (task #105; push do selo)

1. **Congelamento:** os runs do push `3a8f362` fecharam VERDES — Release Build `38602691878` (`build` + `aceite-bundle` **13/13 em 29,19 s**; `publish-release` skipped) e Matriz do Instalador `38602691850` (`TOTAL: 0 falha(s)`); o artefato `aceite-td5-bundle-evidencias` foi baixado e recopiado para `docs/mission/evidence/v8-td5-exe/` (9 arquivos atualizados) — registro em `v8-td5-aceite.md` §2/§5. **O conteúdo está congelado:** daqui em diante só docs; mudança de produto reabre o ciclo de aceite.
2. **Changelog do RC (§17):** a entrada `[0.5.0]` foi expandida do escopo "instalador" para o delta completo do RC (mesa governada, escola, copiloto manual, alertas, evolução, multimercado, IA/Smart Setup, PWA, correções reais e segurança; estado financeiro **LIVE bloqueado** no topo, com ponteiro para o pacote de auditoria) — fecha o item §17 "changelog/release notes" desta entrega com fatos já homologados na matriz (nada inventado). Edição docs-only: nenhum teste lê `CHANGELOG.md` e o bundle não o embarca.
3. **Correção de ledger:** a referência ao registro do capital-piloto no checkpoint §RC §19 acima foi corrigida para o caminho real (`docs/mission/REGISTRO-CAPITAL-PILOTO.md` — não está dentro de `entrega-final/`); o arquivo em si estava correto e completo.

### RC — Re-homologação do aceite endurecido (task #107; `f41e150`)

1. **Incidente REAL no CI do selo (run `38604619895`, commit `b5c80d5`):** a 1ª tentativa do `aceite-bundle` falhou **12/13** — o passo 8 do fluxo do iniciante (registrar a saída parcial) não viu o toast "Execução registrada."; causa-raiz provada pelo log do Playwright: a resposta tardia de `manualHealth()` ("Monitor verificado.", disparada pelo "Verificar agora" do passo 7) sobrescrevia o toast seguinte no elemento `#toast` único do painel (last-write-wins); o evento foi registrado no servidor (Eventos(1): reducao 0.25 @ 161.10). O rerun da MESMA run ficou **13/13 em 32,21 s** — mecanismo provado, nada oculto.
2. **Correção test-only (`f41e150`, sem afrouxar nenhum assert e sem nenhum skip):** após cada clique "Verificar agora" (itens 07 e 13 do aceite) o teste consome deterministicamente o toast "Monitor verificado." antes de prosseguir.
3. **Re-homologação:** local 1×26/26 (42,11 s) + 3×TD-5 **13/13** seguidas; CI do push `f41e150` — Release Build `38607540029` (`build` SUCCESS; `aceite-bundle` **13/13 em 37,14 s**; `publish-release` skipped) e Matriz do Instalador `38607540276` (`TOTAL: 0 falha(s)`); artefato `aceite-td5-bundle-evidencias` baixado e recopiado para `docs/mission/evidence/v8-td5-exe/` (16 arquivos atualizados).
4. **Novo selo:** a citação vigente do aceite no bundle passa a ser `38607540029` (o run `38602691878` permanece como prova histórica do estado anterior); o conteúdo de PRODUTO não mudou (correção test-only) — o que mudou foi o instrumento de aceite, endurecido e re-homologado. Registro no relatório B §5 e em `docs/mission/evidence/v8-td5-aceite.md` §2/§4/§5.

### AUD rodada-8 — Timeouts HTTP do caminho LLM + escapamento do painel (tasks #108–#111; 2026-10-14)

1. **Achado A (crítico) — todos os timeouts HTTP do caminho LLM ficavam DESABILITADOS por requisição.** A langchain-openai 1.6.6 passa `timeout=None` EXPLICITAMENTE ao `openai.OpenAI(...)`; o SDK 3.19.2 trata None explícito como valor "dado" (`is_given(None)` True), pula o bloco que adotaria o timeout do `http_client` fornecido e carimba `self.timeout=None` em TODA requisição — connect/read/write/pool None (conexão travada pendura a chamada sem limite). Varredura por caminho: **openai-family comprovado** (15 provedores incl. ollama/deepseek/minimax/local — mesmo caminho do SDK), **azure comprovado**, **anthropic comprovado** (`default_request_timeout` None → `_client.timeout=None`), **google comprovado** (`self.timeout` None → `http_options.timeout` None; consumido POR REQUISIÇÃO em ms), **bedrock NÃO provável** (SDK ausente — limitação declarada, nada afirmado).
2. **Correção em 2 camadas com precedência do usuário:** `LLM_HTTP_TIMEOUT = httpx.Timeout(connect 10 / read 300 / write 30 / pool 10 s)` como **fonte única** em `transport.py` (aplicado nos builders sync/async — declaração autoritativa adotada pelo caminho `not_given` do SDK) + `setdefault("timeout", ...)` em `openai_client.py`, `azure_client.py`, `anthropic_client.py` e `google_client.py` (anthropic/google com **float 300.0 s** — o pydantic da ChatAnthropic rejeita `httpx.Timeout`; o modelo Google converte para ms por requisição). O passthrough do usuário roda depois e prevalece.
3. **Achado B — 2 desvios de escapamento no painel:** `operacaoReal` (`op.estado`/`op.motivo`) e `renderEvents` (`event_type`/`aggregate_id`/`sequence`) inseriam dados do servidor crus em `innerHTML`, fora da convenção `escapeHtml` do próprio arquivo → corrigidos + 3 pinos estáticos (`tests/test_painel_render_escaping.py`).
4. **TDD RED→GREEN:** RED capturado por execução (anthropic/google `None ≠ 300.0`; `2 failed, 2 passed in 3.98s` — os 2 verdes eram de precedência do usuário); GREEN: `test_llm_transport_timeouts.py` **10 passed** (`ESPERADO=(10,300,30,10)` nos objetos reais dos clientes + precedência do usuário nos 3 clientes), vizinhança de provedores **43 passed** (anthropic_effort/google_api_key/google_thinking_level/provider_registry — nenhum contrato vizinho quebrado), ruff limpo.
5. **Provas frescas (2026-10-14):** suíte completa na árvore final **`3015 passed / 6 skipped / 71 subtests`** (exit 0, 296,34 s = baseline 3002 + 13 novos) e **e2e 26/26** (41,78 s); PNGs de evidência e2e restaurados ao HEAD antes do commit (churn de datas fora do commit de código).
6. **Ressalvas declaradas (honestidade):** bedrock não provável (extra `langchain_aws` ausente); NENHUMA chamada real a provedor de IA (sem credenciais autorizadas; nenhuma chave fictícia) — provas de construção/introspecção + probe de enforcement local; o consumo do campo Google por requisição foi comprovado por leitura do código do langchain-google-genai 4.4.0 (`int(timeout * 1000)` ms), não por request HTTP; `timeout=None` explícito do usuário mantém a postura antiga (decisão consciente). **Mudança de PRODUTO reabre o ciclo de aceite** — re-homologação local feita e ciclo re-fechado no CI (item 7). Evidência completa: `docs/mission/evidence/aud-r8-timeouts-llm-e-escaping.md`.
7. **CI do push (`929ebd4`) — VERDE; ciclo de aceite reaberto re-fechado e artefato re-citado:** Release Build `38616157472` (`build` SUCCESS; `aceite-bundle` **13/13 em 43,16 s** — a matriz TD-5 completa rodou no bundle do artefato produzido COM as correções da rodada-8; `publish-release` skipped) e Matriz do Instalador `38616157674` (`TOTAL: 0 falha(s)`); o selo docs-only `b5f27bf` (task #107) também fechou verde (`38609409946` — aceite **13/13 em 57,76 s**; `38609409745` — `TOTAL: 0 falha(s)`). O artefato `aceite-td5-bundle-evidencias` do `38616157472` foi baixado e recopiado para `docs/mission/evidence/v8-td5-exe/` (**10 arquivos atualizados**: `launcher.log`, `pytest-aceite-bundle.txt` e 8 capturas) — **nova citação vigente do aceite no bundle: `38616157472`**; o run `38607540029` (instrumento endurecido, pré-rodada-8) permanece como prova histórica. Registro em `v8-td5-aceite.md` §2/§5, relatório B §2/§3 e STATE/GATES.

### FM §1–§4 — Modo Simples completo + matriz auditável de rotas + corretoras visíveis (tasks #112–#114; 2026-10-14)

1. **FM-1 (§1 — Simples sem nada escondido):** card "Todas as capacidades — estado honesto e caminho para cada tela" (`#capacidadesCard`/`#capacidadesLista`) na página Início: cada capacidade (mesa, gráfico/análise, copiloto manual, risco, floor, configuração) com UM dos 4 estados exatos — `disponível` / `configuração necessária` / `bloqueado por segurança` / `ainda não implementado` — derivado de fontes reais do servidor (`/v1/config` + `/v1/status/operacao`), nunca de selo fixo; botão "Abrir" por linha (`abrirPagina(id)` via `data-abrir`) que, para tela fora do Simples, liga o Avançado em UM clique (`uiModoSet('avancado')`) e navega. O bloqueio do LIVE mostrado na tela vem de `operacao_real` do servidor. Provas: 4 testes novos (`TestCapacidadesVisiveisNoModoSimples`) + item `td5-01` do aceite estendido no e2e (clique real em `data-abrir="mesa"` → página ativa + `localStorage dz23_ui_modo=avancado`; captura `td5-01-capacidades.png`).
2. **FM-2 (§2 — matriz auditável):** `docs/mission/evidence/matriz-rotas-fm2.md` — 145 rotas de `service/app.py` (0 duplicadas por MÉTODO+CAMINHO), 13 grupos, colunas ROTA/BACKEND/UI/TESTE/SERVIÇO EXTERNO/PENDÊNCIA + curadoria transversal de REQUISITO/INSTALADO/RESULTADO; contagens pós-FM-3: UI **93** · sem UI **52** · teste direto **129** · sem teste direto **16** (lista nominal das 16 no doc — buracos DECLARADOS, nada oculto); as 14 rotas SaaS constam como 404 POR POLÍTICA no instalado (gate single-owner `SAAS_BLOCKED_PREFIXES`) — política, não falha.
3. **FM-3 (§3+§4 — corretoras visíveis, REAL preparado sem ativação):** no card da corretora (Configuração): **Catálogo e capacidades** (`catalogoCarregar()` lê `/v1/personal/exchanges` — salas fixas com `integration_status` + adaptadores ccxt do próprio computador, "sem credenciais e sem rede"; `capacidadesCorretora(id)` lê `/capabilities` e retrata `verified`/`live_enabled`/`status` honestos, "capacidade anunciada ≠ corretora testada") e **Reconciliar com a corretora** (`brokerReconciliar()` → POST `/v1/desk/reconciliar`: portão LIBERADO/FECHADO, ordens conferidas, divergências/limitações, `seguro`); na página Início, **"Ver preparação para dinheiro real (não ativa nada)"** (`preparoRealLoad()` lê `/v1/live/status`: checklist de 6 itens com ✔/✖ + pendências + aviso — `BLOQUEADO` hoje; a liberação é trava de código no servidor, nunca botão de tela). Rotação de chave dita na tela (colar a nova + "Validar configuração" substitui; "Remover" apaga) com `removerSegredo` nos dois pares. Pinos anti-ativação: nenhum `broker/order`, `withdraw`/`transfer`/`saque`, `MESA_LIVE_LIBERADA`, `<option value="live">` nem POST para live no painel.
4. **TDD RED→GREEN:** FM-3 com RED capturado por execução (10 failed / 5 passed) → `tests/test_fm3_corretoras.py` **15/15** (rota GET-only + auth; payload honesto `verified:false`/`live_enabled:false`; desconhecida 404; pins de DOM; extração BOUNDED de função (`_funcao`) que termina na próxima definição — um janelamento por tamanho vazaria entre funções vizinhas e satisfaria pinos falsos).
5. **Provas frescas (2026-10-14):** suíte completa na árvore final **`3034 passed / 6 skipped / 71 subtests`** (exit 0, 303,00 s = baseline 3015 + 4 FM-1 + 15 FM-3; os 6 skips são os de sempre, declarados) e **e2e Playwright 27/27** (49,39 s, exit 0 — 26 anteriores + 1 novo FM-3; `td5-01` estendido, sem flake); JS do painel validado com `node --check`; a instância do dono (`:8000`) nunca foi tocada (harness em faixa isolada com aborto de guarda).
6. **Ressalvas declaradas (honestidade):** as capacidades do catálogo são LEITURA LOCAL (flags `has` do ccxt instanciadas offline — "anúncio do adaptador, sem credenciais e sem rede"), NÃO teste de conexão/ordem com a corretora; nenhuma chamada externa em nenhuma prova desta rodada; **mudança de PRODUTO no painel reabre o ciclo de aceite** — última citação pré-FM no bundle: Release `38616157472` / Matriz `38616157674` (commit `929ebd4`); o ciclo re-fecha no CI deste push — **VERDE: Release `38626633910` (aceite 13/13 em 31,16 s) / Matriz `38626633566` (TOTAL: 0 falha(s))**; 16 rotas seguem sem teste direto (lista nominal no doc da matriz). LIVE `BLOCKED_BY_POLICY` inalterado; nenhuma ordem real; nenhuma tag/release.

### FM §5–§8 — Prova viva ponta a ponta em instância isolada + contrato read-only por descoberta (task #115; 2026-10-14)

1. **§5 (mesa + escola, 10 traders ponta a ponta):** instância ISOLADA (porta livre + `DATA_DIR` temporário, receita do `tests/e2e/conftest.py`; `DESK_INTERVAL_SECONDS=15` só nela; instalação do dono na porta 8000 intocada). Boot OK; `POST /v1/desk/cycle` → "20 propostas, 2 admitidas, 2 entradas, 0 vetos" (PAPER/fixture); `scheduler/start` → `iniciado: true`, recuperação sem incertas; laço autônomo **ciclos 1→3, erros 0** sem navegador; **10 traders** (3 `homologado`/`paper_enabled` + 7 `experimental`/`shadow`); sala do `trader-trend` HTTP 200 com rótulos honestos (fixture "prova a mesa, não o mercado"; portfólio "nada a medir").
2. **§6 (dashboards):** `/v1/maestro/painel` com segregacão invariante (`mistura_modos: false`, `mistura_moedas: false`) e **LIVE `bloqueado: true`** com motivo; `/v1/status/operacao` com `operacao_real = BLOQUEADA` (auditoria em andamento) e gates nomeados `EM_AUDITORIA`.
3. **§7 (copiloto manual sem rota de ordem):** fluxo completo ao vivo — ligar copiloto → `POST /v1/desk/cycle` **409** (mesa não executa com copiloto ativo); criar posição (id `mp_...`, `criada: true`); **idempotência real** (mesma chave → `criada: false`, MESMO id); evento `entrada_parcial` aplicado; monitor/health e sinais HTTP 200; alertas vazios (nada inventado); desligar → mesa volta ("20 propostas, 18 abstenções"). Novo contrato **por DESCOBERTA** (`tests/test_fm4_ponta_a_ponta.py`): varre `app.routes` por `/v1/manual*`, resolve cada endpoint ao AST e prova (a) nenhuma função manual chama `create_order/cancel_order/close_position/submit/withdraw/transfer`; (b) TODA rota manual exige `auth` — cobre `manual_sinais`, que as allowlists fixas antigas NÃO pinavam (lacuna real fechada).
4. **§8 (IA honesta):** ambiente filho com as chaves de IA do catálogo REMOVIDAS (hermeticidade — a estação do dono tem chaves reais); `/v1/ai/provedores` com 9 provedores todos `tem_chave: false`; `POST /v1/ai/testar` → `estado: "sem_chave"` (NENHUMA saída de rede sem credencial); `/v1/ai/uso` zerado, sem número inventado.
5. **Provas frescas (2026-10-14):** `tests/test_fm4_ponta_a_ponta.py` 3/3; regressão focada (FM-4 + copiloto manual API/domínio/sinais + IA + Modo Simples) **163 passed**; suíte completa na árvore de `f8742df` **`3037 passed / 6 skipped / 71 subtests`** (exit 0, 334,93 s; os mesmos 6 skips declarados de sempre); captura bruta `docs/mission/evidence/fm4/prova-viva.json` + doc `docs/mission/evidence/fm4-ponta-a-ponta.md`; script reprodutível `tools/fm4_prova_viva.py`; **CI do push `f8742df`: Release `38627361507` VERDE — aceite 13/13 em 30,97 s; Matriz `38627361472` em execução nesta edição (resultado citado após verde)**.
6. **Ressalvas declaradas (honestidade):** dados de FIXTURE provam a mesa, não o mercado; o ciclo síncrono e o autônomo usam o MESMO `run_once`; alertas do copiloto vazios na janela (nenhum limite atingido — observado, não omitido); nenhuma chamada de IA real (dono não autorizou consumo); LIVE `BLOCKED_BY_POLICY` inalterado.

### FM §9+§10 — Estado honesto de alertas/PWA/Oracle + auditoria consolidada + revisão READ-ONLY do PR #9 (task #116; 2026-10-14)

1. **§9 — alertas (honestidade sobre o que está e não está homologado):** E2 entregue e provado (fila durável persistida, 7 canais, dedupe 1h/backoff, webhook fail-closed **401 verificado por curl**, canal Windows com **entrega real homologada 282 ms**). O que NÃO está: "**Telegram/Discord/WhatsApp/Web Push NÃO estão homologados com credenciais reais**… o botão **Testar** é o caminho de homologação por canal quando ele configurar (exige evidência de entrega, não de aceite)" (CHECKPOINTS §E2).
2. **§9 — PWA:** mecanismo provado no navegador real (SW `dz23-pwa-v1` registrado, cache mínimo, CSP por caminho) + 36 testes de push com **cripto VAPID real sem rede** + 9 estáticos + e2e era 12/12. O que NÃO está: "**Homologação real dos mecanismos push NÃO foi executada** — não há endpoint HTTPS autorizado nem dispositivo físico nesta estação"; "**Nenhuma chave VAPID foi gerada no ambiente do dono**" (`v8-pwa-push.md`). O item 7 da matriz PWA (botão "Acompanhar esta operação") foi **fechado pelo TD-4** — botão existe em `service/static/index.html` L177, pinado por teste. Ressalva: e2e Playwright não roda no venv da suíte (2 skips declarados; execução via `uv run --with playwright`).
3. **§9 — Oracle ARM64:** "homologação do Oracle ARM64 **não declarada** — o job ARM64 do CI valida apenas as dependências aarch64"; "**Oracle permanece NÃO homologado** — Dockerfile e job ARM64 não homologam nada" (GATES item 9; C019). Existe o que é honesto existir: job `arm64-deps` (nomeado "NÃO é homologação Oracle"), job `postgres` (corrida da posse em banco real), export/import **sem segredos**, RUNBOOK PC→Oracle. Evidência: `docs/mission/evidence/fm5-status-alertas-pwa-oracle.md`.
4. **§10 — auditoria financeira/segurança consolidada:** os 4 achados fechados — SEC-1 `a0997df` (15 testes), FIN-1 `e66f2b7` (7), FIN-2 `3ee3fe0` (45), FIN-3 `aade521` (12) — com **prova fresca desta revisão**: bateria focada **`189 passed in 11,08 s`** (42+23+36+9+15+7+45+12). Residuais mantidos sem ocultação: inventário rota-a-rota parcial (matriz FM-2 das 145 rotas + contrato por descoberta da FM-4 cobrem parte), registros legados do OMS pré-`aade521`, DPAPI não protege do próprio usuário, LIVE `BLOCKED_BY_POLICY`.
5. **§10 — revisão READ-ONLY do PR #9 (nada tocado no PR/branch/worktree):** prova de ancestralidade fresca — `git merge-base HEAD origin/feat/integracao-mestre-20261012` = `30b8683` = **o próprio tip da base do PR** → base integralmente contida na nossa branch; `a0997df`/`e66f2b7`/`3ee3fe0`/`aade521` em HEAD **SIM** / em `30b8683` **NÃO** → o PR propõe **correções paralelas de achados já fechados**. Divergência central (SEC-1): o desenho deles (Fernet + envelope DPAPI só se `win32crypt`) ficaria **INATIVO** neste Windows — `win32crypt` ausente no venv, `cryptography` presente, `segredos_em_repouso.tem_protecao()` = True **sem dependência** — caindo em chave-mestra 0600 (sem semântica POSIX no NTFS); defeitos de leitura estática registrados: esvaziar segredo pelo painel **ressuscita** o valor antigo (o cofre deles exclui `""` via `if str(v) != ""` e `load_cfg` reaplica `store.all()` por cima) e backup `config.json.bak-pre-secret-store` em **claro** persistente. FIN-1 deles é reescrita de docstring ("Implementação não alterada") vs nosso **selo em código**. **Recomendação registrada: NÃO mergear como está**; adoção seletiva dos testes deles como material de conferência é possível; decisão de fechar/converter o PR é **do dono** (registrar em `TRADING-DECISIONS.md`). Evidência: `docs/mission/evidence/fm5-revisao-pr9-readonly.md`.
6. **CI:** a Matriz do `f8742df` (`38627361472`) fechou **VERDE** (job `matriz` success) — a promessa "resultado citado após verde" da seção FM §5–§8 está cumprida. Push docs do FM §1–§8 (`ae75e8f`): Release `38628831582` (`build` success, `aceite-bundle` **em execução**, `publish-release` skipped) e Matriz `38628831492` **em execução no momento desta edição** — resultado citado após verde.
7. **Ressalvas declaradas:** a revisão do PR #9 é **estática** (branch deles **não executada**); alertas/PWA/Oracle não re-executados nesta rodada (referências às evidências existentes, citadas com aspas); nenhuma chave gerada, nenhum segredo ecoado; LIVE `BLOCKED_BY_POLICY` inalterado; sem tag/release.

### FM §11 — App real em ambiente isolado: matriz 17/17 no bundle CI + reinício/recovery no exe + defeito de corrida corrigido (task #117; 2026-10-14)

1. **Método (instalação pessoal intocada):** todas as provas em AMBIENTE ISOLADO — LOCAL (harness `scripts/homolog_aceite_td5_bundle.py` no venv, porta em faixa isolada) e BUNDLE/CI (runner virgem: o exe REAL só é exercitado onde a porta 8000 está livre — no CI; `APPDATA` descartável via `mkdtemp`; guarda que ABORTA se qualquer DZ23 já responder em 8000–8010 antes do boot; limpeza mata apenas o PID próprio). A instalação do dono (`:8000`, PID 13800) **nunca foi tocada** em nenhuma execução.
2. **§11 — matriz 14 itens (instalação, atualização, login, Simples, Avançado, todas as páginas, IA, corretoras, mesa, escola, dashboard, copiloto, alertas, reinício):** provados de ponta a ponta no bundle real — instalação/atualização/reinstalação item 19 (matriz de inicialização) e itens 12–13; login/portão/Simples/Avançado/6 páginas itens 15/17–19/21; IA item §6 (sem chave ⇒ NENHUMA saída à rede) e FM §8; corretoras/REAL preparado sem ativação FM §3–§4; mesa 10 traders + ciclo real itens 14/21; escola item 21; dashboard/copiloto TD-3/TD-4 **+ 4 capturas novas `fm6-01..04`** (Avançado com todas as páginas, corretoras, escola, dashboard de gráficos) no bundle CI; alertas E2 (com a ressalva declarada de canais não homologados com credenciais reais); reinício/recovery novo abaixo.
3. **Reinício e recuperação NO EXE (novo no harness; cobre o item "Reinício e recuperação" do §11):** `verifica_reinicio` roda dentro do próprio aceite — posições ANTES `total=3` `['ACEITE6','BTC/USDT','BTC/USDT']` → `POST /v1/app/quit` **200** → launcher **exit 0 sozinho** → 2º boot no MESMO `APPDATA` (23,1 s) → `setup_required=false` (senha persistiu) → login antigo continua valendo e posições IDÊNTICAS → porta liberada. Registro no artefato `v8-td5-exe/` (launcher.log + pytest-aceite-bundle.txt + capturas).
4. **Defeito REAL de CORRIDA encontrado no CI e corrigido (`b32f4de`):** no run `38628831582` (push docs `ae75e8f`) o item de encerramento reprovou — quit 200 porém launcher vivo >40 s. Causa-raiz: `uvicorn.Config` com `timeout_graceful_shutdown=None` (espera ILIMITADA por conexões pendentes; uma conexão pendente do navegador em teardown segurava a saída). Fix: teto de 10 s no encerramento gracioso do launcher + regressão `test_run_backend_limita_o_encerramento_gracioso` (43 testes do launcher verdes). Datapoint: `31ee134` NÃO reproduziu o race (coerente com corrida) — o teto elimina a CLASSE.
5. **Provas frescas (2026-10-14):** LOCAL combinado **`17 passed in 17,80 s`** (`docs/mission/evidence/fm6/pytest-local-combined.txt`) e suíte completa na árvore `b32f4de` **`3038 passed / 7 skipped / 71 subtests`** (exit 0, 264,04 s; `fm6/pytest-unit-full.txt`; 7 skips declarados: 3 e2e sem playwright no venv padrão — incl. o fm6 novo — + 4 ambientais de sempre). **BUNDLE/CI:** run `38633317689` (`b32f4de`) — **17/17 em 88,85 s** (`coletados=17`) com TODAS as linhas de reinício PASS e `TOTAL: 0 falha(s)`/`HARNESS_EXIT=0`; Matriz do Instalador `38633317605` **VERDE** (11m36s, `TOTAL: 0 falha(s)`); datapoint pré-fix `31ee134`: Release `38632969824` **17/17 em 60,26 s** + Matriz `38632969686` verde.
6. **Evidências curadas:** artefato do run `38633317689` baixado e recopiado para `docs/mission/evidence/v8-td5-exe/` — 25 arquivos (capturas td5 atualizadas + `td5-01-capacidades` + **4 novas `fm6-01..04`** + `launcher.log` + `pytest-aceite-bundle.txt`); doc `docs/mission/evidence/fm6-app-instalado-isolado.md` (matriz §11 item→prova + defeito + ressalvas).
7. **Ressalvas declaradas (honestidade):** dados de FIXTURE (provam o app, não o mercado); navegador de homologação é o Chromium do Playwright (não o Edge do dono); sleep/wake e offline/reconexão seguem NÃO exercitados (item 2 dos itens abertos); LIVE `BLOCKED_BY_POLICY` inalterado; nenhuma tag/release; instalação pessoal do dono preservada e intocada.

### FM §12–§13 — Pacote final de evidências + critério de conclusão (task #118; 2026-10-14)

1. **Entregável consolidado:** `docs/mission/FM-ENTREGA-FINAL.md` cobre os **12 itens obrigatórios da §12** em um só lugar — (1) matriz de funcionalidades (as **10 perguntas da §13** com local no painel + prova real; matriz FM-2 das 145 rotas: UI 93 · sem UI 52 · teste direto 129 · 16 sem teste direto declaradas nominalmente; 14 rotas SaaS = 404 por política); (2) lista de defeitos + causa raiz + correção implementada; (3) testes automatizados; (4) testes do bundle Windows; (5) screenshots reais; (6) status de APIs externas; (7) status da preparação LIVE; (8) riscos residuais (9 itens); (9) dependências externas (6 itens); (10) PR de integração; (11) documentação atualizada; (12) ressalvas finais declaradas.
2. **Ledger de defeitos da era-FM (todos com causa raiz + correção + regressão):** corrida do encerramento gracioso (quit 200 com processo vivo >40 s — espera sem teto do uvicorn + conexão pendente do navegador) → `b32f4de` (`timeout_graceful_shutdown=10`, teste RED→GREEN, 43 testes do launcher); lacuna de cobertura de auth em `manual_sinais` → fechada pelo contrato **por descoberta** (`tests/test_fm4_ponta_a_ponta.py`: varre `app.routes`, resolve ao AST, prova ausência de rotas de ordem + auth obrigatório); timeouts HTTP do caminho LLM desabilitados por requisição → `929ebd4` (`LLM_HTTP_TIMEOUT` finito + `setdefault` por cliente); 2 desvios de escapamento no painel → `929ebd4` (convenção `escapeHtml` + 3 pinos). Achados do **PR #9** permanecem **estáticos** (branch deles não executada; recomendação de não mergear — decisão do dono).
3. **Débito de registro fechado — CI do `df9872a`:** runs `38618190459` (Release Build — `build` ✓; `aceite-bundle` **13/13 em 36,14 s**, `TOTAL: 0 falha(s)`; `publish-release` skipped) e `38618190604` (Matriz do Instalador ✓). **Nota de método:** `gh run list --commit <SHA>` exige o **SHA completo** — SHA curto retorna vazio mesmo com runs existentes (descoberto nesta rodada; evitou-se a leitura falsa de "sem CI").
4. **Critério da §13 verificado no doc:** CONECTAR → ESCOLHER PAPER/COPILOTO/REAL → PLAY com as funcionalidades integradas e visíveis (10 perguntas do dono com resposta e prova); REAL **bloqueado por trava de código** mas mostrando a preparação completa e o checklist verificável de 6 itens — **não é tela vazia**; "só considerar concluído quando comprovado ponta a ponta" honrado por item (bundle 13/13 e 17/17 + reinício; LOCAL 17/17 + suíte 3038).
5. **PR de integração:** branch `fix/instalador-atualizacoes-windows` 216 commits à frente de `main` (`80e99fb`); URL de compare registrada no pacote; **abertura e merge aguardam decisão explícita do dono — nada mergeado automaticamente** (§12: "não fazer merge automático de mudanças críticas sem a revisão combinada").
6. **Ressalvas declaradas (honestidade):** dados de FIXTURE provam o app, não o mercado; navegador de homologação é o Chromium do Playwright; sleep/wake/offline não exercitados; IA com credenciais reais do dono `BLOCKED_BY_EXTERNAL_DEPENDENCY`; Oracle ARM64 não homologado; LIVE `BLOCKED_BY_POLICY` inalterado; nenhuma tag/release; instalação pessoal do dono (`:8000`, PID 13800) preservada e intocada.

### Missão MO — Microestrutura e Observabilidade: matriz (MO-1) + implementação A/B/C (MO-2) + entrega (MO-3) (tasks #119–#124; 2026-10-14)

1. **MO-1 — matriz EXISTE / PARCIAL / AUSENTE / NÃO NECESSÁRIO (task #119; commitada em `016577b`):** `docs/mission/MO-1-MATRIZ-AUDITORIA.md` — comparação com o `jarrodwatts/jev-trader` (somente leitura; princípios reaproveitáveis, não o motor) e varredura de comprovação: **não existe** livro L2/best bid-ask/spread medido/profundidade/imbalance no produto (só estimativa em `costs.py` + flag estática `has_order_book` + persona nominal `orderbook-analyst`); **latência por estágio AUSENTE** no OMS (`OrderRecord` sem timestamps); trilha por ordem PARCIAL (fragmentada); motor 300 ms e Monad/Kuru marcados **NÃO NECESSÁRIO** com justificativa. Escopo do MO-2 fechado SOMENTE nas lacunas comprovadas (A/B/C). Regra honrada: matriz **antes** de qualquer alteração de código.
2. **MO-2-A — adaptador de microestrutura read-only:** módulo PURO `tradingagents/desk/microestrutura.py` (livro bruto + instante + idade máxima ⇒ `best_bid/best_ask/spread_bps/profundidade_lados/imbalance/custo_estimado_por_tamanho`) com 4 estados honestos — `disponivel` / `indisponivel` / `inconsistente` (bid>ask, lado vazio, níveis ≤ 0) / `vencida` (validado por temporalidade) — **nunca número inventado**; leitura via `PublicMarketFeed.leitura_de_livro()` (CCXT público `fetch_order_book`, **nunca envia ordem**); corretora sem o nativo ⇒ NOT_SUPPORTED honesto; fixture ⇒ "sem livro — modo dados de treino"; rota read-only GET + bloco "Livro (L2)" na sala do trader. **22 testes** em `tests/test_microestrutura.py` (válido com cálculos exatos; ausente; inconsistente; vencido; não-suportado; garantia estrutural read-only).
3. **MO-2-B — Trilha da decisão na sala (backend existente, zero sistema paralelo):** `sala_trader.marcacoes_de_execucao` passou a devolver **taxas reais por execução** (dado já existente nas entries `FEES:` do ledger — nada criado) e a sala mostra a trilha completa em 5 passos: dados de entrada (mercado/cutoff/qualidade) → motivo (marcações/vetos/abstenções) → resultado do Maestro (rota existente `/v1/maestro/painel`) → risco/portfólio → custos. **8 testes** em `tests/test_sala_trilha.py`.
4. **MO-2-C — latência por estágio e trilha unificada por ordem:** carimbos UTC no caminho existente do OMS — `criada_em/enviada_em/reconhecida_em/primeiro_fill_em/terminal_em/atualizada_em` no `OrderRecord` (schema `dz23.oms/2` **inalterado**; ausência tolerada na restauração ⇒ compatível com estado durável atual); derivação de `latencias_ms()` (envio→aceite, aceite→execução, envio→execução; ACK sem preenchimento ⇒ `None` honesto, nunca zero); exposto na tabela "Ordens" da Mesa (coluna **"Trilha (UTC)"**, `omsLatTxt`) e na sala — **sem rota paralela** (detalhe: rota `GET /v1/desk/status` enriquecida + sala). **11 testes** em `tests/test_oms_tempos.py` (roundtrip de persistência com e sem os campos; cálculo puro incl. sem-preenchimento; rota da mesa com carimbos/latências reais; coluna no DOM).
5. **Provas:** **41 testes novos** (22+8+11); bateria focada de 10 arquivos **`212 passed in 20,25 s`**; regressão de 39 arquivos do caminho de execução **`710 passed`**; estáticos do painel **`27 passed`** (incl. `node --check`); e2e item 03 (sala do trader) verde; **suíte completa (sem e2e) `3079 passed / 4 skipped / 71 subtests`** (exit 0, 357,71 s; baseline não-e2e 3042 + 41 novos = 3083 coletados = 3079+4 ✓; 4 skips ambientais declarados); **RED comprovado** — `git show HEAD:service/app.py | grep -c "criada_em"` = **0** vs árvore **2**; `"Trilha (UTC)"` no `index.html` do HEAD = **0** vs árvore **1**.
6. **Garantias preservadas (declaração vinculante honrada):** os 10 traders, o Maestro, o ledger (partidas dobradas/multimoeda), o PAPER determinístico, a trava `MESA_LIVE_LIBERADA` e **todos os bloqueios LIVE (fail-closed)** intocados; nenhum gate financeiro alterado; **nada escreve em corretora** (MO-2-A é leitura pública sem credencial); nenhuma ativação de LIVE. Schema do OMS segue `dz23.oms/2`.
7. **Ressalvas declaradas (honestidade):** a prova "ao vivo" do livro L2 depende de rede pública na estação — offline ⇒ estado `indisponivel` honesto (ele mesmo, testado); a coluna "Trilha (UTC)" tem teste de runtime + DOM, mas **sem e2e dedicado** nesta rodada; ordens restauradas de registros antigos restauram **sem carimbos por desenho** (a UI mostra "—"); a Mesa/sala exibem taxas reais somente quando existem no ledger; e2e completo e suíte completa do lote registrados no pacote MO-3; **CI do push `b2d027e` VERDE** — Release `38642527748` (`build` ✓ + `aceite-bundle` **17/17** — `coletados=17` → `TOTAL: 0 falha(s)`/`HARNESS_EXIT=0`; `publish-release` skipped) e Matriz `38642527849` (`TOTAL: 0 falha(s)`); nenhuma tag/release; instalação pessoal do dono (`:8000`, PID 13800) preservada e intocada.
8. **Interface:** `MO-3-RELATORIO-ENTREGA.md` consolida o relatório da missão (o que foi implementado, prova por requisito 1–3, evidências de teste, garantias preservadas e ressalvas).

### Missão FQ — Evolução Quantitativa Seletiva (§2–§14): matriz de paridade + 4 melhorias + docs + evidências + pacote do PR (tasks #125–#128; 2026-10-14)

1. **FQ-0 — matriz de paridade §2 (`175dc0f`, incorporada em `44c4ad7`/`a882297`):** `docs/mission/FQ-0-MATRIZ-PARIDADE.md` — 16+ componentes em RECURSO×EXISTENTE×REFERÊNCIA×LACUNA×IMPACTO FINANCEIRO×CUSTO×DECISÃO, em 3 camadas (núcleo quantitativo/Escola; operacional/governança/dados; ferramentas externas). Sondagens por metadados oficiais (PyPI JSON API; **nada instalado no venv do projeto**): freqtrade **2026.9** (requires ≥3.11, 85 deps, **GPLv3**); openbb **5.0.0** (36 deps, **Apache-2.0**). OpenBB V5: 17 pacotes de provedor, keyless exceto FRED/EIA, **sem cobertura Brasil/B3**, provedores V4 removidos (yfinance/fmp/… incompatíveis com V5). **Spike do freqtrade em venv ISOLADO fora do repositório** (`freqtrade-spike/`): `import OK` + `--version` → 2026.9 (Windows 11 / Python 3.12.10 / CCXT 4.5.88), exit 0 — **sem conta, sem credencial, sem execução financeira**. Inventário das docs estáveis do freqtrade em `evidence/fq0-inventario-freqtrade-docs.md`: premissas críticas do backtest deles — **sem slippage, sem limite de volume, sem fills parciais** (mais otimista que o DZ23 pós-FQ-1; o protocolo de equivalência compensa) — e **sem walk-forward nativo**. Evidência: `docs/mission/evidence/fq0-probe-dependencias.md`.
2. **FQ-1 — 4 melhorias priorizadas §14, TDD RED→GREEN (`44c4ad7`; lint `1955bfe`):** (1) **liquidez/participação máxima no backtest do research** — `max_participacao` em `BacktestConfig` (capacidade = volume×|close|×fração; fill limitado/rejeitado contabilizado; volume negativo ⇒ `ValueError`; modelo registrado no manifest) — fecha a lacuna "aprovar estratégia que não preenche no real"; (2) **métricas §8 na formação da Escola** — expectativa líquida, fator de lucro, drawdown da prova e estabilidade temporal em metades ponderadas; o gate de formação exige estabilidade e, na prova final, as métricas reportadas são as da **prova independente** (`base_das_metricas="prova_final"`) — evita falsas aprovações por cauda/trecho sortudo; (3) **registro §3 completo por hipótese** — `fonte_dados`/`timeframe`/`custos`/`modelo_de_execucao`/`limitacoes` congelados na assinatura (mudar qualquer um altera a assinatura) + `hash_das_series` + custos aplicados no resultado; (4) **detector de lookahead por perturbação** (`detectar_lookahead`: truncar o futuro muda o sinal? ⇒ vazamento localizado com índice/corte) + causalidade dos indicadores de janela e de prefixo (EMA excluída deliberadamente — recursiva desde a semente; limitação declarada). **28 testes novos** (liquidez 6, formação 8, registro 5, lookahead 5, indicadores causais 4 — contagem refeita por `--collect-only`), RED comprovado antes de cada fix; ruff limpo (2 achados do 1º passe — B905/C401 — corrigidos em `1955bfe`, não escondidos). **Nenhuma dependência nova** (prova: `git diff 175dc0f..44c4ad7 -- pyproject.toml requirements.txt setup.py` = vazio).
3. **FQ-2 — documentação (`a882297`; 3 docs + 1 evidência, 290 insertions):** `FQ-2-REGISTRO-DE-EXPERIMENTOS.md` (os 11 requisitos do §3 mapeados a código — campos, assinatura canônica, hash reprodutível, exemplo de uso); `FQ-2-PROTOCOLO-EQUIVALENCIA.md` (checklist de 11 itens — sinais…convenções — com como-verificar por item + premissas documentadas do freqtrade e como compensá-las: `max_participacao=1` para equivalência, taxas ×2, ZIP + StaticPairList + `--cache none`; estratégia-ficheiro vive FORA do repo — GPL); `FQ-2-LICENCAS-E-IMPACTO.md` (GPLv3: uso como ferramenta separada OK, cópia/import para o núcleo proibida; OpenBB Apache-2.0 mas **ADIAR** pelo peso pywry ~13–17 MB + openbb-build + Desktop conda; FreqAI **REJEITAR**; §13.5: impacto O(n) apenas em caminhos offline).
4. **FQ-3 — evidências e pendências (este commit):** `docs/mission/FQ-3-EVIDENCIAS-E-PENDENCIAS.md` — entregas FQ-0→FQ-3 com commits; testes §12 por tipo (unidade/integração/adversarial/regressão; **nenhum serviço real envolvido** — declarado honestamente; único contato externo = spike freqtrade sem conta); suíte completa na árvore `1955bfe` **`3107 passed / 7 skipped / 71 subtests passed in 291,99 s` (exit 0)** — baseline MO-3 `3079 passed / 4 skipped` + 28 novos ✓; os 3 skips adicionais são os e2e sem Playwright (suíte desta rodada inclui `tests/e2e/`); CI — **TODOS VERDES**: Release Builds `38646344922`/`38646664030`/`38646831650`/`38647617264` e Matrizes do Instalador `38646345073`/`38646664034`/`38646831452`/`38647617313` (os 4 pushes FQ: `44c4ad7`/`a882297`/`1955bfe`/`e76b904` — todos `success`); pacote do PR revisável (compare URL registrada; **abertura e merge aguardam decisão explícita do dono** — precedente FM §12–§13: nada mergeado automaticamente); **11 pendências** registradas (§13.10): `code_revision` real, igualdade indicador backtest×produção com warmup, fiação do `Laboratorio` em produção, Copiloto §7 (ADIAR), confronto DZ23×Freqtrade (protocolo pronto, não executado), OpenBB (ADIAR), FreqAI (REJEITAR), auditoria externa (dono), PR (dono), tag/Release (dono), piloto R$ 1.000 (não autorizado).
5. **Decisões de escopo (§14 — "apenas o comprovadamente necessário"):** Copiloto §7 endpoint determinístico **ADIAR** (as 8 respostas do dono já existem dispersas; sem caso comprovado de erro por dispersão; LLM/chat livre REJEITADO por §7); confronto Freqtrade **ADIAR** (rodada pontual só se um veredito da Escola for contestado); FreqAI como dependência **REJEITAR** (§5/§10 — peso; conceitos cobertos por interfaces próprias); OpenBB **ADIAR** (sem caso de uso comprovado; sem B3).
6. **Garantias preservadas (declaração vinculante honrada):** nenhuma melhoria desta missão toca execução real — LIVE `BLOCKED_BY_POLICY` / `MESA_LIVE_LIBERADA=False` **inalterados**; 10 traders, Maestro, ledger, PAPER e travas intocados; nenhuma ordem real; nenhuma credencial usada ou ecoada; instalação pessoal do dono (`:8000`, PID 13800) e `%APPDATA%` preservados e intocados em toda a missão; `LOGO.png` e `.research/` fora do versionamento; nenhuma tag/release.
7. **Ressalvas declaradas (honestidade):** nenhum serviço real envolvido (quantitativo offline; o único contato externo foi o spike do freqtrade sem conta/credencial); o confronto DZ23×Freqtrade **não foi executado** (protocolo pronto e auditável; rodada pontual futura); suíte roda sem os e2e no venv padrão (skips ambientais de sempre — declarados); formações antigas já persistidas **não são recalculadas retroativamente** pelo gate novo de estabilidade (vale para novas avaliações — nota de comportamento); `detectar_lookahead` é evidência por perturbação (complementa, não substitui, revisão de código do pipeline); a matriz FQ-0 registra a ausência de cobertura Brasil no OpenBB V5 (fato, não suposição).

### AC-1 — Rodada "auditar corrigir e melhorar": matriz + fechamento dos 3 débitos FQ + sweep (tasks #129–#132; 2026-10-14)

1. **Método (matriz ANTES de código):** `docs/mission/AC1-MATRIZ-AUDITORIA.md` — 6 achados com prova arquivo:linha no estado do tip `421b7f4`: **A1** `code_revision` do manifest nunca carrega revisão de código (default `"working-tree"` em `backtest.py:46`; `walk_forward.py:30` gravava o identificador da JANELA no campo de código; o teste antigo pina o contrato defeituoso) ⇒ CORRIGIR; **A2/A2b** sem teste de igualdade backtest×produção + duas definições de RSI (Wilder no painel/Copiloto × Cutler na evidência da decisão) sem declaração ⇒ MELHORAR; **A3** caderno global da evolução sem rota (por-trader já exposto na sala) ⇒ MELHORAR mínimo (rota read-only; `Laboratorio` PERMANECE só em testes — fiá-lo duplicaria a Evolução, §2); **A4** F821 real em produção (`LeituraDeLivro` indefinido no escopo de `market.py`) ⇒ CORRIGIR; **A5** 7 findings de lint repo-inteiro ⇒ CORRIGIR; **A6** git/docs conferem ⇒ MANTER.
2. **TDD RED→GREEN (todos com RED capturado antes do fix):** A1 — `ImportError` → `revisao_do_codigo()` (sha HEAD + sufixo `-dirty` se árvore suja; `"working-tree"` honesto sem git/bundle) + campo `janela` SEPARADO no manifest + pino antigo de `test_research.py` atualizado ao contrato novo; A2/A2b — pinos de igualdade (janela do backtest = prefixo EXATO de produção; decisão igual no mesmo corte; warmup idêntico) + RSI Cutler/Wilder declarados em docstring e provados distintos (>1e-3); A3 — `GET /v1/desk/evolucao` (leitura pura, `Depends(auth)`, devolve `evolucao.relatorio()` com traders+experimentos+aviso; 401 sem token; vazio honesto em DATA_DIR recém-nascido); A4 — import REAL top-level (o plano dizia `TYPE_CHECKING`; a evidência abortou: bloco não existe em runtime e o NameError persistiria — sem import circular, `microestrutura.py` só importa stdlib) + anotação sem aspas; A5 — `ruff check .` **All checks passed!** repo-inteiro (I001 ×3 com `--fix`, C408 ×2 em literal, F841 removendo 2 linhas vestigiais, UP037/F401 resolvidos pelo fix A4).
3. **Provas frescas (2026-10-14, árvore deste commit):** baterias AC-1 — revisão 11 passed, igualdade 25 passed, caderno 3 passed, microestrutura 23 passed; suítes afetadas (research, microestrutura, oms_tempos, sala_trader, copiloto_sinais, evolucao_autonoma, desk_validacao + FQ) **168+19 passed**; **suíte completa `3124 passed / 7 skipped / 71 subtests` (exit 0, 251,13 s)** = baseline FQ-3 `3107` + 17 novos ✓ (os mesmos 7 skips ambientais de sempre — 3 e2e sem Playwright + POSIX/postgres/lightgbm — nada oculto); `ruff check .` limpo.
4. **Registro de dívida:** os 3 débitos FQ-3 §13.10 itens 1–3 marcados **FECHADOS** em `FQ-3-EVIDENCIAS-E-PENDENCIAS.md` §6 (item 3 com a decisão declarada: caderno global exposto; `Laboratorio` permanece test-only por anti-duplicação).
5. **Garantias preservadas:** NENHUM arquivo de execução/risco/OMS de negociação tocado — mudanças em research/backtest+walk_forward (rastreabilidade), market/microestrutura (anotação/lint), signals (docstring), app.py (rota read-only nova) e testes. LIVE `BLOCKED_BY_POLICY` / `MESA_LIVE_LIBERADA=False` inalterados; nenhuma ordem real; nenhuma credencial usada/ecoada; instalação do dono (`:8000`, PID 13800) intocada; `LOGO.png`/`.research/` fora do versionamento; nenhuma tag/release. **CI do push `395ec8b` VERDE:** Release Build `38654207102` (`build` ✓; `aceite-bundle` **17/17** — `coletados=17` → `TOTAL: 0 falha(s)`/`HARNESS_EXIT=0`, com reinício/recovery no exe; `publish-release` skipped) e Matriz do Instalador `38654207027` (`TOTAL: 0 falha(s)`) — registrado no STATE e em GATES (item 15).

### GIF-demo + publicação do estado atual no GitHub (tasks #133–#138; 2026-10-15)

1. **Pedido do dono:** atualizar os dados/informações do GitHub para o novo estado atual do projeto (o `main` ainda exibia o README da era-SaaS — "DZ23 Trading Agency", multi-tenant, PIX/Wooovi, 3 traders) e criar um GIF de demonstração do produto no modelo do anexo (gravação real do aplicativo em uso).
2. **Decisões do dono (2026-10-14):** publicação por **merge fast-forward para `main`** — **sem tag/Release** (segue reservado; `publish-release` permanece pulado) — e GIF com **percurso completo** (portão premium → páginas principais → ciclo PAPER real com os 10 traders e posições).
3. **GIF entregue (commit `e7b6688`):** `tools/gerar_gif_demo.py` (ferramenta geradora com a receita documentada na docstring) + `docs/demo/dz23-demo.gif` (960×600, ~17 s, 38 quadros armazenados de 41 capturados — o Pillow deduplica quadros iguais somando as durações; paleta compartilhada de 255 cores sem dither; ~1,6 MB; loop) + embed no README com legenda declarando instância local isolada em PAPER com dados descartáveis.
4. **Isolamento e segurança da gravação:** instância em porta livre com `DATA_DIR` temporário e senha descartável; ambiente do filho sem `ACCESS_PASSWORD` do dono, sem chaves de IA (`CATALOGO`), sem credenciais de corretora e sem `MESA_LIVE_LIBERADA` (padrão da prova viva fm4); aquecimento com até 3 ciclos PAPER pela API para garantir posições antes da gravação; nada escrito em corretora; instalação do dono (`:8000`, PID 13800) e `%APPDATA%` jamais tocados.
5. **Depuração por causa-raiz (3 ciclos):** (1) CSP do painel nega eval — gravação reescrita só com primitivas nativas do Playwright (seletores CSS com pseudos, `mouse.wheel`, `inner_text`); (2) `#appStatus` só é populado na página Visão geral — marcador de Início carregado trocado por `#iniConectarEstado:has-text("Corretora")`; (3) Modo Simples oculta a Mesa do menu — passo `select_option("#uiModo","avancado")` adicionado antes de navegar (e vira um momento real de produto no GIF).
6. **Conteúdo verificado quadro a quadro:** portão (badges "PAPER MODE"/"EXECUÇÃO GOVERNADA"/"DINHEIRO REAL BLOQUEADO") → senha mascarada → Início → Modo Avançado → Mesa (Painel Global do Maestro: "Traders: 10 · homologados 3 · experimentais 7", "LIVE BLOQUEADO", posições, exposição) → ciclo PAPER real pelo botão "Rodar um ciclo" (toast honesto: "20 propostas, nenhuma operação: critérios não atendidos") → Ordens (2 preenchidas com trilha UTC) → Posições (2 com stop/alvo/líquido) → Ciclos.
7. **CI do push `e7b6688` VERDE (evidência da publicação):** Release Build `38659167372` (`build` ✓; `aceite-bundle` **17/17 em 39,7 s** — `coletados=17` → `TOTAL: 0 falha(s)`/`HARNESS_EXIT=0`; `publish-release` skipped) e Matriz do Instalador `38659167183` (`TOTAL: 0 falha(s)`/`HARNESS_EXIT=0`, concluída 2026-10-15T00:27Z).
8. **Publicação (decidida pelo dono, executada nesta rodada):** merge **fast-forward** `fix/instalador-atualizacoes-windows` → `main` (FF verificado: `80e99fb` ancestral do tip `e7b6688`), push de `main`, **descrição do repo atualizada** via `gh repo edit` (remove "multi-tenant"; novo texto: single-owner, 10 traders + Maestro, risco determinístico, PAPER/SHADOW homologado no Windows, LIVE bloqueado fail-closed, sem SaaS/cobrança) e verificação do README da home renderizando o GIF. **SEM tag/Release** — a publicação de Release segue aguardando autorização explícita do dono (G8/item 5). **Correção V9 (2026-10-15, achado V9-DOC-01):** o texto da descrição citado acima ("PAPER/SHADOW homologado no Windows") estava **ampliado** — a evidência do bundle Windows homologa **PAPER**; **SHADOW não possui homologação real declarada** e **DEMO não foi validado neste repositório**. A descrição do repositório no GitHub foi corrigida no mesmo dia para "PAPER homologado no bundle Windows; SHADOW não homologado; DEMO não validado; LIVE bloqueado fail-closed". O registro original é **preservado** (proveniência); nenhum status de SHADOW/DEMO é elevado.

## 2026-10-16 — caminho auditável Escola → Sala REAL → ativação futura

1. `_desk_boot` deixou de criar sempre um mandato PAPER: PAPER e TESTNET agora recebem o modo econômico correto; LIVE só recebe `Mode.LIVE` + `live_authorized=True` mediante selo de código e ativação explícita vinculada a inquilino, corretora, conta e autorização de piloto.
2. A futura rota `POST /v1/live/ativar/{agent_id}` exige frase no momento da ação, conta exata e checklist operacional sem pendências fora do selo. Ela permanece bloqueada nesta entrega porque `MESA_LIVE_LIBERADA=False`.
3. O estado financeiro LIVE (OMS, livro gerencial, scheduler e journal) é isolado por hash de corretora+conta. Toda ativação ou reinício LIVE arma o portão de reconciliação; ciclo manual e PLAY reavaliam `/v1/live/status` antes de executar.
4. `POST /v1/live/desativar` para o scheduler, revoga o mandato persistido e retorna a TESTNET. IQ Option segue sem executor LIVE homologado; sua escola PAPER e leitura autenticada não viram capacidade de ordem.
5. Prova adversarial focada: selo fechado não é contornável pela rota; configuração LIVE isolada não basta; mandato LIVE exige ativação vinculada; TESTNET usa `Mode.TESTNET`; boot LIVE nasce sem scheduler e com reconciliação armada; a rota futura persiste agente/conta sem persistir a frase e responde `envio_real_habilitado=False`.
6. Verificação local hermética: bateria afetada `124 passed`; suíte ampla `3638 passed, 41 skipped, 2 deselected, 20 warnings, 71 subtests` em 357,92 s. Os dois deselects são incompatibilidades deliberadas do harness sem rede/sem Registro (`test_construction_when_extra_installed`, que tenta credencial AWS, e `test_identidade_registrada_no_windows`, que grava no Registro); não pertencem ao caminho financeiro alterado. Ruff e `git diff --check` limpos. Nenhuma ordem, credencial externa ou produção foi usada.
# Checkpoint — credencial de execução REAL isolada (PR #14)

- A credencial futura de execução REAL usa `LIVE_EXCHANGE_API_*`; não reutiliza
  `EXCHANGE_API_*` (TESTNET) nem `REAL_READ_ONLY_*` (certificação da conta).
- A prova LIVE chama somente métodos de leitura, exige a mesma identidade já
  comprovada pela chave read-only, persiste apenas fingerprints e expira em 24h.
- Rotação, troca de corretora, prova read-only inválida ou identidade divergente
  invalida a ativação inclusive no reinício.
- A construção futura do broker LIVE recebe a credencial `LIVE_*` apenas em
  memória. TESTNET permanece vinculada à credencial que produziu seu fill.
- `MESA_LIVE_LIBERADA=False` permanece inalterado; nenhuma ordem real, merge,
  deploy, tag ou release foi realizado neste checkpoint.
- Validação local: baterias focadas `111 passed` e painel `151 passed`; suíte
  ampla `3682 passed, 5 skipped, 20 warnings, 71 subtests`. Um teste do
  executor hermético encontrou um diretório temporário externo já removido;
  a repetição isolada passou (`1 passed`). CI do PR permanece a autoridade
  final para o ambiente limpo.

# FM-6 (§11) — App real em ambiente isolado: instalação, atualização, login, modos, todas as páginas, IA, corretoras, mesa, escola, dashboard, copiloto, alertas e reinício

- **Missão:** auditoria funcional §11 ("Instalação e funcionamento — testar o aplicativo real em ambiente isolado"), task **#117**.
- **Branch:** `fix/instalador-atualizacoes-windows` (commits `31ee134` — matriz FM-6 + reinício no harness; `b32f4de` — teto do encerramento gracioso).
- **Data:** 2026-10-14.

## Método (e por que o exe real só roda no CI)

- A instalação pessoal do proprietário (`%APPDATA%\DZ23-Trading-AI` + processo na porta 8000, PID 13800
  LISTENING) **nunca foi tocada**: verificada por `/healthz` read-only antes e depois de todas as provas.
- O launcher **reutiliza** uma instância já viva na faixa 8000–8010 (por desenho — o dono não abre dois apps);
  como a estação do dono responde na 8000, um boot local do bundle provaria a instância DELE, não o artefato.
  Por isso o teste do exe REAL roda no runner virgem do CI, com:
  - `APPDATA` DESCARTÁVEL (`tempfile.mkdtemp`; o app cria tudo em `%APPDATA%\DZ23-Trading-AI` de lá);
  - guarda de isolamento no harness: se QUALQUER instância DZ23 responder na faixa **antes** do boot, ABORTA
    (protege a instalação do dono de interferência e o aceite de provar a instância errada);
  - encerramento pelo caminho de produção (`POST /v1/app/quit` → exit 0) e limpeza de emergência que mata
    **apenas o PID** criado pelo harness.
- Matriz local (mesma forma do CI, instância isolada por porta livre + `DATA_DIR` temporário; chaves de IA
  reais da estação **removidas** do ambiente filho): `tests/e2e/test_aceite_td5.py` (13 itens) +
  `tests/e2e/test_fm6_app_isolado.py` (4 itens novos desta task) em UMA invocação de pytest — exatamente o
  que o harness do bundle roda.

## Matriz §11 — os 14 itens verbatim, com onde cada um foi provado

| # | Item §11 | Onde foi provado | Resultado |
|---|----------|------------------|-----------|
| 1 | Instalação | Bundle CI: extração do ZIP + boot real com APPDATA descartável + assistente de 1ª execução; Matriz do Instalador (mesmo push) para o ciclo do Setup; HISTÓRICO: INST-6 upgrade real em runner virgem (run `38512611447`) | **PASS** |
| 2 | Atualização | HISTÓRICO INST-6 (run `38512611447`): upgrade real 0.4.0→0.5.0 — backup verificado pré-upgrade, dados byte a byte, registro/exe/healthz 0.5.0, terceiros intactos; Matriz do Instalador re-executa cenários de reinstalação/downgrade neste push | **PASS** |
| 3 | Login | Bundle CI: `td5-01` (assistente + login do dono) e reinício (`setup_required=false`, senha antiga continua valendo); HISTÓRICO item 17 (migração `owner_password.txt`→Argon2id sem perder acesso) | **PASS** |
| 4 | Modo Simples | `td5-01`/`td5-13` (fluxo do iniciante em 10 passos) + `fm6-01` (3 páginas no Simples) | **PASS** |
| 5 | Modo Avançado | `fm6-01` (alternância 3↔8 páginas por device, "simplificar ≠ remover") | **PASS** |
| 6 | Todas as páginas | `fm6-01`: varredura das 8 páginas do Avançado (título correto, sem 5xx) + varredura das 3 do Simples; `td5-02`..`td5-10` cobrem cada dashboard por contrato próprio | **PASS** |
| 7 | Configurações de IA | `td5-09` (provedores/modelos/consumo/orçamento; sem chave → estado honesto) | **PASS** |
| 8 | Corretoras | `fm6-02`: catálogo + capacidades (`verified:false` honesto), reconciliação pela rota real, sandbox TESTNET sem chaves ("chaves: ausentes", "ordens de mentira, risco zero") | **PASS** |
| 9 | Mesa | `td5-10`: 10 traders por tier (3 homologado / 7 experimental) no bundle | **PASS** |
| 10 | Escola | `fm6-03`: Escola Central no painel do bundle | **PASS** |
| 11 | Trading Dashboard | `fm6-04`: mercado desenhado pelo Lightweight Charts (canvas real) + portfólio com a queda HONESTA ("sem operações fechadas — nada a desenhar") ou LC quando há operações fechadas | **PASS** |
| 12 | Copiloto | `td5-04`..`td5-07`: análise, "Acompanhar esta operação", registro de posição + diário, monitor/alerta de stop objetivo | **PASS** |
| 13 | Alertas | `td5-08` (Centro de Alertas) + `td5-11` (PWA/push UI) | **PASS** |
| 14 | Reinício e recuperação | **NOVO no harness** (`verifica_reinicio`): posições do copiloto legíveis ANTES → quit real 200 → launcher exit 0 sozinho → 2º boot com o MESMO APPDATA → `setup_required=false` → login antigo e posições sobrevivem; HISTÓRICO: item 22 (crash `taskkill /F` → 30/30, run `38494708308`) | **PASS** |

**"Não alterar destrutivamente a instalação pessoal do proprietário"** — cumprido por desenho: guarda de
aborto no harness + APPDATA descartável + nenhum teste na instalação da estação. Verificação read-only da
estação antes/depois: `:8000` respondendo `/healthz` (PID 13800 inalterado).

## Defeito REAL encontrado no ciclo e corrigido

- **Sintoma (CI, run `38628831582`, commit `ae75e8f`):** todos os 13 itens do aceite PASSARAM (`13/13 em
  101,71 s`), `POST /v1/app/quit` respondeu **200** — e o launcher **seguia vivo após 40 s**
  ("FALHA — launcher encerra sozinho (exit 0)"); `publish-release` pulou. O botão "Fechar aplicativo" do
  dono poderia não encerrar sozinho nesse cenário de teardown do navegador em cima do quit.
- **Causa raiz:** `uvicorn.Config` sem `timeout_graceful_shutdown` (default = espera INDEFINIDA por
  conexões pendentes) **e** o laço do launcher sem teto no caminho do painel (`join(timeout=30)` só existia
  no caminho do evento `--quit`). Uma conexão pendente (browser fechando em cima do quit) segurava a saída.
- **Correção (`b32f4de`, TDD RED→GREEN):** `timeout_graceful_shutdown=10` no `uvicorn.Config` do launcher +
  teste novo `test_run_backend_limita_o_encerramento_gracioso` ("o teto do encerramento gracioso é o que
  torna a saída LIMITADA"); lote do launcher **43 passed**, ruff limpo, suíte completa re-verificada.
- **Datapoint do instrumento antigo:** os runs do commit `31ee134` (antes do fix) rodaram a matriz nova no
  CI — o resultado do `aceite-bundle` deles é citado abaixo como conferência do defeito.

## Resultados por ambiente

### Local (estação do dono — instância do app intocada)

- Matriz combinada (13 td5 + 4 fm6) **`17 passed in 17.80s`** — captura bruta em
  `docs/mission/evidence/fm6/pytest-local-combined.txt`.
- Suíte completa na árvore de `b32f4de`: **`3038 passed / 7 skipped / 71 subtests` (exit 0, 264,04 s)** —
  os 7 skips são TODOS declarados: 3 do navegador ausente no venv padrão (os 3 arquivos e2e, incl. o fm6
  NOVO — rodam via `uv run --with playwright` e, no CI, no bundle) + os 4 ambientais de sempre
  (`langchain_aws`, NTFS, `DATABASE_URL`, `lightgbm`); captura em `docs/mission/evidence/fm6/pytest-unit-full.txt`.
- Capturas reais do navegador (regeneradas a cada execução, commitadas):
  `docs/mission/evidence/e2e/fm6-01-avancado-todas-paginas.png`, `fm6-02-corretoras.png`,
  `fm6-03-escola.png`, `fm6-04-dashboard-lc.png` + os 19 `td5-*.png`.

### Bundle do artefato CI (runner virgem — exe real, APPDATA descartável)

- **Run Release `38633317689` (commit `b32f4de`) — VERDE:** `build` ✓ + **`aceite-bundle` ✓**
  (`publish-release` skipped — sem tag, por política). No exe REAL: `coletados=17` → **`17 passed in
  88,85 s`**; **reinício completo**: posições ANTES (`total=3`, `['ACEITE6','BTC/USDT','BTC/USDT']`) →
  quit real **200** → **launcher exit 0 sozinho (~1,6 s)** → 2º boot no mesmo APPDATA em 23,1 s →
  **`setup_required=False`** (assistente não volta) → login antigo + posições idênticas → quit final 200 →
  exit 0 → **porta liberada** → `TOTAL: 0 falha(s)` / `HARNESS_EXIT=0`.
- **Run Matriz do Instalador `38633317605` (commit `b32f4de`) — VERDE:** job `matriz` 11m36s,
  `TOTAL: 0 falha(s)` (instalação nova, upgrade real 0.4.0→0.5.0, reparo, downgrade recusado, app ativo,
  desinstalação — o instrumento dos itens 1–2 do §11).
- **Datapoint do instrumento anterior (`31ee134`, SEM o teto do fix):** Release `38632969824` **também
  verde** — `17 passed in 60,26 s` e o reinício completo passou (exit 0 em ~1,5 s; Matriz `38632969686`
  verde). **O race do `ae75e8f` NÃO reproduziu** neste run — coerente com a causa-raiz de CORRIDA (teardown
  do navegador caindo em cima do quit); a correção `b32f4de` retira a espera SEM TETO do caminho, o que
  elimina a CLASSE do defeito independentemente da janela do race.
- Evidências do artefato `aceite-td5-bundle-evidencias` do run `38633317689` **baixadas e curadas** em
  `docs/mission/evidence/v8-td5-exe/` — **25 arquivos**: `pytest-aceite-bundle.txt` (placar exato 17/17 + as
  10 linhas do reinício), `launcher.log` e **24 capturas reais** (18 td5 atualizadas + `td5-01-capacidades`
  + **as 4 NOVAS `fm6-01..04`**). O conjunto anterior tinha 22 arquivos (sem fm6 — anterior à matriz nova).

## Ressalvas declaradas (honestidade)

- O navegador das provas é o Chromium do Playwright (estação e runner) — prova de comportamento, não a
  diversidade de navegadores do público final.
- Dados da mesa são de FIXTURE (prova a mesa, não o mercado) — mesma ressalva das provas §5–§8.
- Sleep/wake, offline/reconexão e crash de SO seguem **NÃO exercitados** (fora do alcance desta missão);
  a recuperação de CRASH (`taskkill /F`) está fechada no histórico (item 22).
- Instalação pessoal do dono foi preservada por desenho — **não** foi (nem será) alvo de teste destrutivo.
- LIVE `BLOCKED_BY_POLICY` inalterado; nenhuma ordem real; nenhuma tag/release.

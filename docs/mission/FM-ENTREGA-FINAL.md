# DZ23 Trading AI — FM: ENTREGA FINAL DA AUDITORIA FUNCIONAL (missão §12–§13)

Missão: "DZ23 TRADING AI — MISSÃO DE AUDITORIA FUNCIONAL E INTEGRAÇÃO TOTAL" — §12 (evidências obrigatórias) e §13 (critério de conclusão).
Task #118; branch `fix/instalador-atualizacoes-windows`; 2026-10-14.
Regra de casa: **nenhum item abaixo sem prova citável** (teste executado, run de CI, arquivo de evidência). Onde algo NÃO está comprovado, está declarado como tal.

---

## 1. Matriz completa de funcionalidades (§12 "matriz completa de funcionalidades")

### 1.1 §13 — as 10 perguntas do dono: onde a resposta aparece no app + prova

| # | O que o dono precisa entender | Onde aparece no painel | Prova |
|---|---|---|---|
| 1 | **O que está funcionando** | Card "Todas as capacidades — estado honesto e caminho para cada tela" (FM-1, página Início): cada capacidade com UM dos 4 estados exatos (`disponível` / `configuração necessária` / `bloqueado por segurança` / `ainda não implementado`), derivado de `/v1/config` + `/v1/status/operacao` — nunca selo fixo | `tests/test_painel_capacidades.py` (4 testes) + item `td5-01` do e2e (clique real em `data-abrir` → página ativa) + captura `td5-01-capacidades.png` |
| 2 | **O que a IA está fazendo** | Mesa de Agentes (10 traders, estado em PT-BR, modelo de IA por trader, último motivo de não operar, ações por trader) + Painel Global do Maestro (TD-1) + leitura de gráfico por séries estruturadas com 3 proibições absolutas (TD-3) | `tests/test_qoder_maestro_traders.py` (16), `tests/test_desk_multiagente.py`, `tests/test_quant_intelligence.py`, e2e item 21 (`c021-item21-*.log`), TD-1/TD-3 nas evidências `v8-painel-global/` e `td3/` |
| 3 | **Quais traders estão ativos** | Mesa de Agentes: 3 `homologado`/`paper_enabled` (sobem) + 7 `experimental`/`shadow` (nascem SHADOW); rotas promover/pausar/retomar com gate de evidência `pode_promover()` (≥12 fechadas, retorno>0, acerto≥45%) | `tests/test_qoder_maestro_traders.py`; bundle item 13 (10 traders por tier) e item 21 |
| 4 | **Em quais mercados estão trabalhando** | Escola Central — salas independentes com chave `corretora\|conta\|mercado\|modo` (ex.: `binance\|paper-legacy\|spot\|paper`); fundação multimercado (E6) com classes de ativo e salas por mercado | item 21 do bundle (sala isolada no painel autenticado); `tests/` da Escola (`BT-6`/`BT-10`); FM-2 na matriz de rotas |
| 5 | **Quanto a simulação ganhou ou perdeu** | Ciclo PAPER pelo painel + ledger balanceado (`patrimônio 999,80` num capital de 1000 — diferença = custos reais de fee, nunca inventados); Painel Global OMITE total quando há mistura de modo/moeda (invariante de código) | itens 14/21 do bundle (`20 propostas, 2 admitidas, 2 entradas`; ledger balanceado; `pendencias_reconciliacao=[]`); `tests/test_painel_global_modo.py` (TD-1, 20 testes) |
| 6 | **Quais corretoras estão conectadas** | Configuração → card da corretora: **Catálogo e capacidades** (`/v1/personal/exchanges` — salas fixas + adaptadores ccxt locais, "sem credenciais e sem rede") e **Reconciliar com a corretora** (`/v1/desk/reconciliar`) com portão LIBERADO/FECHADO | `tests/test_fm3_corretoras.py` (15/15); honestidade declarada: nenhuma conexão com credencial real nesta missão (ver §5) |
| 7 | **Quais contas estão cadastradas** | Dono único (G2 — superfície SaaS morta removida no C015; menu com exatamente 6 páginas); Escola com contas/salas e pilotos cuja autorização é "ato exclusivo do proprietário" | `tests/test_single_owner_g2.py` (13 rotas × 5 métodos → 404), `tests/test_single_owner_modo_windows.py`; item 21 do bundle |
| 8 | **Quais operações estão sendo acompanhadas** | Minhas Operações — copiloto manual: posições registradas (`mp_...`) com monitor/health, alertas e "Análise do copiloto" (TD-4, RSI bloqueando viés, custos NUNCA estimados) | FM-4 §7 prova viva (`prova-viva.json`); `tests/test_fm4_ponta_a_ponta.py` (3/3, contrato por descoberta); `tests/test_copiloto_sinais*` |
| 9 | **Por que a IA decidiu operar ou esperar** | Decisão do ciclo determinística OPERAR/AGUARDAR/BLOQUEAR/SUSPENDER (`decisao.py`, `GET /v1/desk/status.decisao`) + abstenções e vetos do Supervisor visíveis na mesa (ex.: "20 propostas, 18 abstenções" com copiloto ligado; veto visível no item 14) | V8.1 §4 (`16a8807`); itens 14/21 do bundle; `tests/test_decisao_ciclo*` |
| 10 | **O que falta para uma eventual conta REAL** | Início → "Ver preparação para dinheiro real (não ativa nada)" (`/v1/live/status`): checklist de 6 itens com ✔/✖ + pendências + aviso, hoje `BLOQUEADO`; a liberação é trava de código no servidor, nunca botão de tela | `tests/test_fm3_corretoras.py` (payload honesto `verified:false`/`live_enabled:false`); §6 abaixo |

### 1.2 Matrix técnica consolidada (145 rotas)

A matriz auditável completa está em `docs/mission/evidence/matriz-rotas-fm2.md` (FM-2, task #113): **145 rotas** de `service/app.py` (0 duplicadas por MÉTODO+CAMINHO), 13 grupos, colunas ROTA/BACKEND/UI/TESTE/SERVIÇO EXTERNO/PENDÊNCIA — contagens pós-FM: UI **93** · sem UI **52** · teste direto **129** · sem teste direto **16** (lista nominal das 16 no doc — buracos declarados). As 14 rotas SaaS constam como 404 POR POLÍTICA no instalado (gate single-owner) — política, não falha. Complemento: `entrega-final/MATRIZ-REQUISITOS-V8.md` (RC) e o contrato por descoberta da FM-4 (toda rota `/v1/manual*` sem ordem + com auth).

---

## 2. Lista dos defeitos encontrados + causa raiz + correções (§12 itens 2–4)

Defeitos da era FM/post-RC (a era RC/TD anterior está consolidada em `entrega-final/RELATORIO-HOMOLOGACAO-V8.md` §B e em `TRADING-CHECKPOINTS.md`):

| # | Defeito (ambiente) | Causa raiz (provada) | Correção (commit) | Regressão |
|---|---|---|---|---|
| 1 | **Encerramento gracioso sem limite** — CI `38628831582` (`ae75e8f`): `POST /v1/app/quit` respondia 200 mas o launcher seguia vivo >40 s | `uvicorn.Config` com `timeout_graceful_shutdown=None` = espera ILIMITADA por conexões pendentes; uma conexão pendente do navegador em teardown segurava a saída | teto de 10 s no encerramento gracioso (`b32f4de`) | `test_run_backend_limita_o_encerramento_gracioso` (43 testes do launcher verdes); datapoint `31ee134` NÃO reproduziu o race (corrida) — o teto elimina a classe; re-prova no CI 17/17 (`38633317689`) |
| 2 | **Cobertura: rota `manual_sinais` fora dos pinos de auth** (lacuna encontrada pela própria auditoria FM-4, não pelo uso) | allowlists fixas antigas enumeravam rotas manual e não cobriam a rota nova (TD-4) | contrato por DESCOBERTA (`tests/test_fm4_ponta_a_ponta.py`, task #115): varre `app.routes` por `/v1/manual*`, resolve ao AST e prova (a) nenhuma função manual chama `create_order/cancel_order/close_position/submit/withdraw/transfer`; (b) TODA rota manual exige `auth` | 3/3 verdes; passou a cobrir qualquer rota manual futura |
| 3 | **Timeouts HTTP do caminho LLM DESABILITADOS por requisição** (auditoria rodada-8, tasks #108–#111) | langchain-openai passa `timeout=None` EXPLÍCITO ao SDK; o SDK trata None como valor dado e carimba `self.timeout=None` em toda requisição (connect/read/write/pool sem limite) — comprovado também em azure/anthropic/google; bedrock não provável (SDK ausente — declarado) | `LLM_HTTP_TIMEOUT` (connect 10/read 300/write 30/pool 10 s) como fonte única em `transport.py` + `setdefault` por cliente (`openai_client.py`, `azure_client.py`, `anthropic_client.py`, `google_client.py`); precedência do usuário preservada (`929ebd4`) | 10 testes de timeout com objetos reais dos clientes + precedência; 43 vizinhos verdes; evidência `aud-r8-timeouts-llm-e-escaping.md` |
| 4 | **2 desvios de escapamento no painel (XSS-interno)** — `operacaoReal` (`op.estado`/`op.motivo`) e `renderEvents` (`event_type`/`aggregate_id`/`sequence`) inseriam dados do servidor crus em `innerHTML`, fora da convenção `escapeHtml` do próprio arquivo | escrita direta sem o helper, encontrada por varredura de auditoria | correção + 3 pinos estáticos (`tests/test_painel_render_escaping.py`) (`929ebd4`) | pinos estáticos verdes; nenhum falso positivo |
| 5 | **Achados estáticos do PR #9 (branch de terceiro, revisão READ-ONLY — task #116)** | desenho deles: envelope DPAPI **inativo** neste Windows (sem `pywin32`; cai em chave-mestra 0600 sem semântica POSIX no NTFS); esvaziar segredo pelo painel **ressuscita** o valor (cofre exclui `""` e `load_cfg` reaplica); backup `config.json.bak-pre-secret-store` em **claro** persistente | **Nenhum merge** — recomendação registrada: NÃO mergear como está (decisão do dono); nossos achados SEC-1/FIN-1/2/3 já fechados nos commits `a0997df`/`e66f2b7`/`3ee3fe0`/`aade521` | revisão estática documentada em `fm5-revisao-pr9-readonly.md`; branch deles não executada (declarado) |

Defeitos REAIS de era anterior já fechados com regressão pinada (contexto para o pacote, detalhe em CHECKPOINTS): 2 defeitos de 320 px (aceite TD-5, `ac2a1bc`); a IA de contexto **nunca funcionou em produção** (método inexistente no `BaseLLMClient` — fix `consultar_ia()`, V8 §6a); sobreposição do gráfico de fundo R13 (SS-2); **kernel fix do 1º Salvar** (C016b — salvar padrões reconstruía a mesa); 2 defeitos pegos pelo harness de crash (`f07ea47`: risco cego ao livro vivo no 1º boot; painel pós-reinício preso ao ledger de abertura — regressão `tests/test_livro_vivo_e_reinicio.py`); flake REAL do instrumento de aceite (toast sobrescrito — correção test-only `f41e150`, nenhum assert afrouxado); corrida de despacho de alertas (V8-S5 `f7f1f0f`); 3 defeitos ao vivo do TD-3 (vendor 404 mascarado; modelo llama do Groq **descontinuado — 404 real**; áudio sugerido como chat).

---

## 3. Testes automatizados (§12 item 5)

- **Suíte completa (árvore `b32f4de`):** `3038 passed / 7 skipped / 71 subtests` (exit 0, 264,04 s) — `evidence/fm6/pytest-unit-full.txt`. Os 7 skips são todos declarados com razão: 3 e2e sem playwright no venv padrão (incl. o fm6 novo; execução via `uv run --with playwright`) + 4 ambientais (langchain_aws, permissões NTFS, DATABASE_URL, lightgbm). **Nenhum skip oculta falha.**
- **e2e Playwright (painel real + uvicorn real):** 27/27 local (FM-3 adicionou 1; identidade/premium e mobile 320–390 px inclusos); FM-6 acrescentou a matriz no bundle.
- **Contratos por descoberta:** `tests/test_fm4_ponta_a_ponta.py` 3/3 (rotas manuais: zero ordem + auth obrigatória).
- **Baterias focadas recentes:** FM-5 auditoria consolidada `189 passed in 11,08 s`; vizinhança FM-4 + copiloto + IA + Modo Simples `163 passed`; FM-3 `15/15`; FM-1 `4`; TESTES de timeout AUD-8 `10` (+3 estáticos de escaping).
- **Testes do bundle Windows (§12 item 6):** matriz de aceite estendida (aceite TD-5 13 itens + 4 capturas fm6) rodando NO EXE REAL dentro do artefato de CI — **17/17 em 88,85 s** (run `38633317689`, `b32f4de`) + **reinício/recovery no exe** (quit → exit 0 → 2º boot mesmo APPDATA → `setup_required=false` → posições idênticas). Matriz do Instalador (cenários de instalação/upgrade/multi-instância) `TOTAL: 0 falha(s)` (`38633317605` e anteriores). Débito de registro fechado: **CI do `df9872a` — Release `38618190459` (aceite 13/13 em 36,14 s) + Matriz `38618190604` — VERDE.**
- Harness reprodutível versionado: `scripts/homolog_aceite_td5_bundle.py` (ambiente isolado, guarda anti-colisão, APPDATA descartável, limpeza do próprio PID).

## 4. Screenshots reais (§12 item 7)

- **`docs/mission/evidence/v8-td5-exe/` (canônico do bundle, 25 arquivos):** `fm6-01-avancado-todas-paginas`, `fm6-02-corretoras`, `fm6-03-escola`, `fm6-04-dashboard-lc`, `td5-01-capacidades`, `td5-01-inicio`…`td5-13-*`, `launcher.log`, `pytest-aceite-bundle.txt` — capturadas pelo aceite rodando no EXE do artefato (run `38633317689`).
- **`docs/mission/evidence/e2e/`:** suíte versionada, incl. capturas mobile 320/390 px e `fm6-01..04`.
- **Evidências por era (navegador real):** `v8-modo-simples/`, `v8-painel-global/`, `td3/` (Lightweight Charts real com 7 canvas), `v8-copiloto-dashboard/`, `v8-ss2/`, `fm4/` (prova viva JSON + capturas), `fm6/` (local).
- Ressalva: navegador de homologação é o Chromium do Playwright (não o Edge do dono) — declarado.

## 5. Status de APIs externas (§12 item 8)

| API/serviço | Status real (com prova) |
|---|---|
| **IA (9 provedores: OpenAI, Anthropic, Google, Groq, DeepSeek, MiniMax, Ollama, OpenAI-compatible, local)** | Código completo e honesto: sem chave ⇒ **NENHUMA saída à rede** (`/v1/ai/testar` → `estado:"sem_chave"`, FM §8 provado em ambiente hermético); timeouts finitos corrigidos (AUD-8); descoberta de modelos com fonte; custos parciais declarados, tokens nunca inventados. **Prova com credencial real do dono: `BLOCKED_BY_EXTERNAL_DEPENDENCY`** (nenhuma credencial autorizada nesta sessão; nenhuma chave fictícia usada) |
| **Corretoras (catálogo ccxt)** | Capacidades lidas LOCALMENTE (flags `has` dos adaptadores instanciados offline — "anúncio do adaptador, sem credenciais e sem rede"); estados `NOT_SUPPORTED/BLOCKED/PAPER_SIMULATED`, nunca PASS falso. **Conexões testnet/demo reais: `BLOCKED_BY_EXTERNAL_DEPENDENCY`** (sem credenciais autorizadas) |
| **IQ Option** | Laboratório read-only (BT-7); nunca execução |
| **Alertas — Telegram/Discord/WhatsApp/Web Push** | **NÃO homologados com credenciais reais** — o botão "Testar" é o caminho de homologação por canal (exige evidência de entrega). O que ESTÁ provado: fila durável persistida, 7 canais, dedupe 1 h/backoff, webhook fail-closed 401 (curl real), **canal Windows com entrega real homologada (282 ms)**, VAPID com cripto real sem rede (36 testes), SW `dz23-pwa-v1` registrado em navegador real, CSP por caminho |
| **Push em dispositivo físico / HTTPS** | **Declarado BLOQUEADO_EXTERNA** — sem endpoint HTTPS autorizado nem dispositivo nesta estação; nenhuma chave VAPID gerada no ambiente do dono |
| **Oracle ARM64** | **NÃO homologado** — o job ARM64 do CI valida apenas dependências aarch64 (nomeado "NÃO é homologação Oracle"); runbook PC→Oracle pronto, migração não exercitada ponta a ponta |
| **GitHub Actions (CI)** | Operacional e verde — Windows Release Build + Matriz do Instalador em toda a fase; `publish-release` CORRETAMENTE pulado (sem tag) |

## 6. Status da preparação LIVE (§12 item 9)

- **Estado atual: `BLOQUEADO`** — trava de CÓDIGO no servidor (`LIVE_TRADING=false`/`REAL_MONEY_EXECUTION=false` fail-closed; `MESA_LIVE_LIBERADA` permanece False), nunca botão de tela. `/v1/status/operacao` → `operacao_real = BLOQUEADA` com gates nomeados `EM_AUDITORIA`; `/v1/live/status` → checklist de 6 itens com ✔/✖.
- **No painel:** "Ver preparação para dinheiro real (não ativa nada)" mostra o checklist completo — REAL **não é tela vazia** (§13 cumprido).
- **Capital-piloto R$1.000: NÃO AUTORIZADO** (registro em `entrega-final/`, V8.1 §12) — qualquer ativação exigiria decisão humana nova e explícita.
- **O que falta para uma conta REAL:** (1) assinatura de código do instalador (sem certificado); (2) credenciais de corretora do dono + homologação de conexão/ordem em testnet/demo; (3) liberação dos gates hoje em `EM_AUDITORIA` (revisão combinada); (4) auditoria independente externa (o Qoder não certifica a própria entrega); (5) decisão explícita do dono.

## 7. Relatório de riscos residuais (§12 item 10)

1. **Cobertura:** 16 rotas sem teste direto (lista nominal na matriz FM-2 — buracos declarados, não ocultos); inventário de autorização 401-integral ainda parcial (contrato por descoberta cobre `/v1/manual*`).
2. **Dados legados:** registros do OMS pré-`aade521` (antes da reconciliação por identidade da corretora) — reconciliação manual recomendada se houver dado antigo.
3. **Proteção local:** DPAPI não protege do próprio usuário da máquina (limite do modelo de ameaça, declarado no SEC-1).
4. **Bootstrap/ambiente:** sleep/wake e offline/reconexão NÃO exercitados nesta estação (crash à força e reinício FECHADOS — item 22).
5. **Distribuição:** sem assinatura de código (`BLOCKED_BY_EXTERNAL_DEPENDENCY`); Release/tag não publicados (exige autorização).
6. **Certificação:** auditoria independente externa NÃO executada (por desenho — o próprio Qoder não certifica a própria entrega).
7. **Mercado:** provas de mesa usam dados de FIXTURE (provam a mesa e os fluxos, não previsões de mercado); feed real lido apenas como leitura pública.
8. **Terceiros:** PR #9 aberto com desenho divergente — decisão do dono pendente; branch de terceiro não executada (revisão estática).
9. **Instrumento de CI:** aceite roda com Chromium do Playwright (não o Edge do dono) — coberto por e2e local + funcional, declarado.

## 8. Lista das dependências externas (§12 item 11)

- Certificado de assinatura de código Windows (para fechar o único item `BLOCKED_BY_EXTERNAL_DEPENDENCY` da distribuição).
- Credenciais de IA do dono (autorização para a prova real de provedor).
- Credenciais de corretora (testnet/demo) para homologar conexão/ordem reais.
- Endpoint HTTPS + dispositivo físico (homologação de Web Push em Android/iPhone).
- Instância Oracle ARM64 real (homologação de portabilidade — Dockerfile/job ARM64 NÃO homologam).
- GitHub (branch remota/CI) — operacional; Playwright/Chromium (e2e) — instalado via `uv run`; Inno Setup (build Windows no CI) — embarcado no runner.

## 9. PR de integração e documentação atualizada (§12 item 12)

- **Branch de integração:** `fix/instalador-atualizacoes-windows` — **216 commits à frente de `main`**; push final desta fase: `365f2d7` (FM-6) + este batch (FM-7). Nada foi sobrescrito em outras branches; `audit/claude-financial-security` (PR #9) permanece intocada (revisão READ-ONLY).
- **PR de integração PRONTO para abertura** — comparação direta: `https://github.com/LMPrado-DZ23/DZ23-Trading-AI/compare/main...fix/instalador-atualizacoes-windows`. Resumo para o corpo do PR: RC 0.5.0 sobre `main` 0.4.0 + programa V7 (E1–E6) + V8 (PWA/Smart Setup/Modo Simples/TD-1..5) + auditoria de segurança/finanças (SEC-1/FIN-1/2/3) + auditoria funcional FM §1–§13 (esta entrega). **Abertura do PR e merge aguardam decisão explícita do dono — exatamente como manda o §12: "não fazer merge automático de mudanças críticas sem a revisão combinada".**
- **Documentação atualizada nesta entrega:** `TRADING-MISSION-STATE.md`, `TRADING-GATES.md`, `TRADING-CHECKPOINTS.md`, `WINDOWS-RELEASE-EVIDENCE.md`, `v8-td5-aceite.md`, este pacote + evidências (`fm6-app-instalado-isolado.md`, `fm6/`, `v8-td5-exe/` 25 arquivos, `fm5-*`, `fm4-*`, `matriz-rotas-fm2.md`, `aud-r8-*`); guias do produto real (`INSTALAR`/`COMECE-AQUI`); pacote RC (`entrega-final/` A/B/C + checklist de auditoria independente); CHANGELOG com o delta completo do RC (as mudanças de painel da era FM entram no changelog quando da abertura da entrega — decisão do dono).

## 10. Critério de conclusão (§13) — verificação final

- **Fluxo principal cumprido:** página **Início** é o padrão de abertura com 3 passos **CONECTAR → MODO → PLAY** (SS-5; zero backend novo — mesmas rotas); PLAY provado ao vivo (PLAY → RODANDO → PAUSAR → PARADA com 1 ciclo real; e2e 13/13+). **ESCOLHER PAPER / COPILOTO / REAL**: PAPER dito simulado; copiloto "nunca envia ordens" (e o servidor recusa o ciclo da mesa com copiloto ativo — 409 provado); **REAL `disabled` com motivo nos DOIS modos** e, no lugar de tela vazia, a preparação completa com checklist verificável (§6).
- **"Só considerar concluída quando comprovada de ponta a ponta":** cada funcionalidade listada acima cita sua prova — unidades, e2e e/ou bundle real. A matriz §11 (FM-6) foi executada NO EXE INSTALÁVEL em ambiente isolado (17/17 + reinício/recovery), com a instalação pessoal do dono intocada.
- **Débito de registro fechado:** CI do `df9872a` registrado (Release `38618190459` aceite 13/13 em 36,14 s; Matriz `38618190604` — verde).
- **Nada publicado:** sem tag, sem Release, sem merge; LIVE `BLOCKED_BY_POLICY` inalterado; nenhuma ordem real; nenhuma credencial ecoada.

## 11. Ressalvas finais (honestidade — §12)

1. Itens externos permanecem `BLOCKED_BY_EXTERNAL_DEPENDENCY` (credenciais reais de IA/corretora, push em dispositivo, Oracle, certificado) — declarados, não simulados.
2. Sleep/wake e offline/reconexão não exercitados (fora do alcance desta estação).
3. A revisão do PR #9 é estática (branch de terceiro não executada).
4. Auditoria independente externa segue NÃO executada — este pacote é a entrega PARA essa revisão (o checklist está em `entrega-final/CHECKLIST-AUDITORIA-INDEPENDENTE.md`).
5. Este documento não substitui os citados: quando houver divergência, vale o artefato/teste/run citado.

*Última atualização: 2026-10-14 — pacote FM §12–§13 (task #118) publicado junto do push de fechamento da auditoria funcional na branch `fix/instalador-atualizacoes-windows`.*

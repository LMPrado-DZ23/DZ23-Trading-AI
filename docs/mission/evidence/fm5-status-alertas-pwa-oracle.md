# FM §9 — Status honesto: Centro de Alertas, PWA e Oracle ARM64 (task #116; 2026-10-14)

- **Branch:** `fix/instalador-atualizacoes-windows`
- **Regra desta seção:** cada item é marcado como **provado** (há teste/execução
  reproduzível), **parcial** ou **não homologado / BLOQUEADO_EXTERNO** — nada é
  declarado pronto por existir código.
- **Método:** revisão das evidências já registradas na linha + **bateria focada
  reexecutada agora (2026-10-14)**: `tests/test_centro_de_alertas.py` +
  `tests/test_alertas_transparencia.py` + `tests/test_pwa_push.py` +
  `tests/test_pwa_static.py` + as 4 suítes da auditoria §10
  (`test_auditoria_sec1_segredos_em_repouso.py`, `test_auditoria_fin1_live_desk_seal.py`,
  `test_auditoria_fin2_gates_financeiros.py`, `test_auditoria_fin3_resposta_perdida.py`)
  → **189 passed em 11,08 s**.

## 1. Centro Inteligente de Alertas (E2)

**Provado (código + testes + homologação de canal com execução real):**

- Núcleo durável `tradingagents/desk/notificacao_viva.py` (`FilaDeAlertas`,
  `Despachante`): fila persistida no Armazem, dedupe de 1 h, backoff exponencial
  com tempos exatos, rate limit por canal, alerta vencido NUNCA despachado,
  auditoria por tentativa de canal (CHECKPOINTS §E2, itens 1–6).
- 7 canais em `tradingagents/desk/canais_alerta.py`; e-mail com **trava
  estrutural** (canal exclusivo de relatórios, nunca alertas).
- Webhook `POST /v1/telegram/webhook` fail-closed por segredo (401 verificado
  em servidor real por curl — CHECKPOINTS §E2, item 3).
- **Entrega real executada e medida** no navegador real: toast Windows nativo
  `"windows: entrega confirmada (282 ms)"` (CHECKPOINTS §E2, item 7).
- Na prova viva FM-4 (instância isolada), `GET /v1/alertas` respondeu 200 com
  lista vazia — **observado e registrado**, coerente com "nenhum canal externo
  configurado e nenhum incidente de ciclo na janela"
  (`docs/mission/evidence/fm4/prova-viva.json`).

**NÃO homologado (declarado, verbatim de `TRADING-CHECKPOINTS.md` §E2 item 8):**

> "**Telegram/Discord/WhatsApp/Web Push NÃO estão homologados com credenciais
> reais** — o código entrega, os testes cobrem estrutura/falha/rate-limit/segredo,
> mas a entrega real depende da credencial do dono; o botão **Testar** é o
> caminho de homologação por canal quando ele configurar (exige evidência de
> entrega, não de aceite)."

**Parcial declarado:** os tipos de alerta "oportunidade, mudança de tese, lucro
parcial e perda de conexão" estão prontos no vocabulário da fila mas aguardam os
produtores naturais (copiloto manual/evolução) — **não declarados como
cobertos** (CHECKPOINTS §E2, item 8).

## 2. PWA (instalável + Web Push VAPID)

**Provado (mecanismo):**

- Service Worker versionado `dz23-pwa-v1` (`service/static/sw.js`), manifest
  completo, rotas `/v1/push/*` autenticadas (estado, vapid/gerar, assinar,
  cancelar), card de instalação/push no painel
  (`docs/mission/evidence/v8-pwa-push.md`, itens 2–6).
- Testes: `test_pwa_push.py` (36) + `test_pwa_static.py` (9) + `test_centro_de_alertas.py`
  (42) + `test_alertas_transparencia.py` (23) = **110 passed** na era PWA; a
  bateria fresca de hoje (189 agregada) os inclui.
- **Cripto real sem rede**: payload VAPID assinado e conteúdo cifrado de verdade
  (sessão HTTP falsa) — TTL/Urgency por severidade (`TestCriptoRealSemRede`).
- e2e no navegador REAL registrou o SW ativo e o estado honesto do card
  (`test_pwa_service_worker_ativo_e_estado_honesto`, e2e 12/12 na era PWA).
- Segredos VAPID (`ALERTA_VAPID_PRIVADA`) e assinaturas vivem no universo
  cifrado em repouso (SEC-1: DPAPI no Windows) — pinado por teste.

**BLOQUEADO_EXTERNO (declarado, verbatim de `v8-pwa-push.md` §Limitações):**

> "**Homologação real dos mecanismos push NÃO foi executada** — não há endpoint
> HTTPS autorizado nem dispositivo físico nesta estação; […] Nada aqui foi
> declarado homologado em Android/iPhone."
>
> "**Nenhuma chave VAPID foi gerada no ambiente do dono** e nenhuma chave entra
> no repositório; a geração fica a um clique no painel (fica cifrada em repouso
> pelo SEC-1). A config do dono (`%APPDATA%\DZ23-Trading-AI\`) NÃO foi tocada."

Matriz §9 do doc PWA: itens 1, 2, 3, 9, 11 e parte do 15 = `BLOQUEADO_EXTERNO`;
4, 5, 6, 10, 13, 14 = `PROVADO`; 8 e 12 = `PROVADO no mecanismo`.

**Item 7 da matriz PWA — atualização de estado:** o doc PWA registrava "Ativar
'Acompanhar esta operação'" como PARCIAL (o botão de adesão seria seção
separada do V8 principal). Isso foi **fechado depois pelo TD-4 (task #93)**:
o botão existe no painel — `service/static/index.html` L177
(`<button … onclick="manualCriar()">Acompanhar esta operação</button>`) — com
fluxo e2e próprio; a matriz do PWA não foi reescrita (é documento histórico da
era) e este doc registra o estado atual.

**Ressalva de execução desta task:** o e2e Playwright **não foi reexecutado
agora** — o venv do projeto não traz Playwright instalado (e2e roda via
`uv run --with playwright`, como nas eras anteriores; por isso a suíte completa
marca 2 skips de e2e). As citações 12/12 (PWA) e 27/27 (FM §1–§4) são das
execuções registradas no ledger.

## 3. Oracle ARM64

**NÃO HOMOLOGADO — por honestidade (declarado em 3 docs).** O que EXISTE pronto:

- Job de CI `arm64-deps` (QEMU/buildx `linux/arm64`, import smoke de deps
  aarch64) — **nomeado explicitamente "NÃO é homologação Oracle"** (`ci.yml`).
- Job `postgres` (service container real) rodando `tests/test_modo_da_mesa.py`
  (24 funções — inclui a corrida da posse em banco real).
- `docker-compose.yml` de serviço único; export/import `dz23.mudanca/2`
  **sem segredos por construção** (C019); runbook PC→Oracle de 7 passos
  (`RUNBOOK.md`).

**Citações do ledger (verbatim):**

> `TRADING-GATES.md` item 9: "homologação do Oracle ARM64 **não declarada** — o
> job ARM64 do CI valida apenas as dependências aarch64 (nomeado como tal); a
> homologação exigirá o ciclo completo rodando no Oracle, com evidência
> registrada (runbook em `RUNBOOK.md`); a migração PC→Oracle ainda não foi
> exercitada ponta a ponta."

> `TRADING-CHECKPOINTS.md` C019, Estado: "**Oracle permanece NÃO homologado** —
> Dockerfile e job ARM64 não homologam nada; homologação exigirá o ciclo
> completo rodando lá, com evidência registrada. Prioridade Windows mantida."

## 4. Consolidado

| Subsystem | Implementação | Provas reproduzíveis | Homologação real | Status honesto |
| :-- | :-- | :-- | :-- | :-- |
| Centro de Alertas | fila durável + 7 canais + inbox + matriz + webhook fail-closed | 42 + 23 testes (bateria 110 da era; 189 agregada hoje); toast Windows real 282 ms; webhook 401 em servidor real | entrega nos canais externos exige credencial do dono (botão Testar = caminho) | **código pronto / homologação POR CANAL pendente da credencial do dono** |
| PWA | SW versionado + manifest + rotas push + UI instalar/ativar/testar | 36 + 9 unit/estáticos + cripto VAPID real sem rede + e2e 12/12 e 27/27 (eras registradas) | dispositivo físico + endpoint HTTPS autorizado ausentes; nenhuma chave VAPID gerada no ambiente do dono | **mecanismo provado / BLOQUEADO_EXTERNO** |
| Oracle ARM64 | job CI deps aarch64; compose serviço-único; export/import sem segredos; runbook 7 passos | `test_modo_da_mesa.py` 24 (job postgres) | migração PC→Oracle não exercitada; ciclo completo no Oracle nunca rodou | **NÃO HOMOLOGADO (por honestidade)** |

## 5. Ressalvas declaradas (honestidade)

1. Nenhuma credencial real foi usada em nenhuma prova desta seção; nenhuma
   chave VAPID gerada; `%APPDATA%` do dono intocado; instalação do dono na
   `:8000` intocada.
2. e2e Playwright não reexecutado nesta task (ausente no venv persistente —
   roda por `uv run --with playwright`); citações são das execuções registradas.
3. O item "bundle Windows instalado" das eras E2/PWA **foi fechado depois**
   pelos aceites TD-5/RC (13/13 no artefato CI — ver `v8-td5-aceite.md`); este
   doc não reabre aquelas declarações, apenas as referencia.
4. LIVE `BLOCKED_BY_POLICY` inalterado; nenhuma ordem real; nenhuma tag/release.

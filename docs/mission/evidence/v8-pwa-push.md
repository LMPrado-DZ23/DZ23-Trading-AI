# V8 (complemento PWA MOBILE + WEB PUSH) — implementação completa no app real; homologação em dispositivo físico explicitamente pendente

**Escopo:** transformar o painel existente em PWA instalável (Android/iOS) com
Web Push real (VAPID) integrado ao Centro de Alertas — **sem** criar outro
sistema, outro banco ou outro motor de execução (complemento PWA, §§1–10).
A Oracle ainda não está disponível e deploy/DNS exige aprovação do dono
(§14 do Prompt Master) → cumprida a parte que o próprio complemento permite:
implementação completa + evidências + "implantação real explicitamente
pendente" (§10).

## O que foi entregue (mesmos componentes, nenhum sistema paralelo)

| # | Requisito (complemento) | Implementação | Evidência |
|---|---|---|---|
| 1 | Preservar arquitetura | PWA é o MESMO painel `service/static/index.html` + o MESMO Centro de Alertas (`FilaDeAlertas`/`Despachante`) + o MESMO FastAPI; nenhuma tabela/banco/executor novo | suíte encosta apenas nos arquivos existentes; `push_web.py` é um canal do despacho, não um segundo sistema |
| 2 | Instalação como aplicativo | Manifest completo (standalone, start `/app`, ícones 192/512, theme/background = splash Android); SW versionado; registro `updateViaCache:'none'`; botão "Instalar" SÓ aparece quando o navegador oferece (`beforeinstallprompt`); instruções "Adicionar à Tela de Início" para iPhone; atualização controlada (cache versionado + purge + skipWaiting/claim) | testes estáticos `tests/test_pwa_static.py` (9): cache `dz23-pwa-v1`; purga de caches antigos; `register('/sw.js',{updateViaCache:'none'})`; pino do `beforeinstallprompt`; e2e `test_pwa_service_worker_ativo_e_estado_honesto` (SW registrado no navegador real; card com estados honestos; screenshot `08-pwa-config.png`) |
| 3 | Responsividade real ≥320px | e2e já cobria 1920/1366/768/390/360; **320×568 adicionado** à matriz do portão de login (sem corte, sem rolagem horizontal) | e2e `test_login_responsivo_sem_corte_nem_rolagem_horizontal` (6 larguras) + `test_login_mobile_390x844` + screenshot `07-login-320x568.png` |
| 4 | Web Push real (VAPID) | `tradingagents/desk/push_web.py` (novo, ~320 linhas): chaves VAPID (py-vapid), validação de subscription (endpoint HTTPS + chaves base64url), limite de 10 dispositivos, dedupe por identificador (reinscrever ATUALIZA, não duplica), entrega via pywebpush com **TTL e Urgency por severidade** (emergência/urgente → `requireInteraction` no SW), poda automática de inscrição expirada (404/410) com re-save, e `estado_publico()` que NUNCA expõe a chave privada nem o endpoint; canal `CanalWebPush` no Centro de Alertas (prioridade 6) com detalhe honesto quando falta config; despacho e botão "Testar" usam o entregador REAL do inquilino (`_push_do_tenant`) | `tests/test_pwa_push.py` (36): incl. **cripto real sem rede** (payload cifrado de verdade com sessão HTTP falsa — o VAPID assina, o conteúdo cifra) e rotas; `tests/test_centro_de_alertas.py` (42) re-prova fila/dedupe/backoff intactos |
| 4a | Consentimento explícito + per-dispositivo | Push só após clique em "Ativar neste aparelho" + `Notification.requestPermission()` + `pushManager.subscribe`; subscription enviada com rótulo do aparelho | estáticos pinam o fluxo; `POST /v1/push/assinar` deduplica por identificador |
| 4b | Endpoint para assinar/cancelar + armazenamento protegido | `POST /v1/push/assinar` / `POST /v1/push/cancelar` (autenticadas); `ALERTA_VAPID_PRIVADA` e `PUSH_ASSINATURAS` entraram no universo de segredos em repouso (DPAPI no Windows — SEC-1; 0600 POSIX); `GET /v1/config` mascara | testes de rota (401 sem auth; 409 limite; 422/404 cancelar; privada nunca ecoada — pinado em resposta e em armazenamento) |
| 4c | Fila durável, retry/backoff, dedupe, TTL, priorização, diagnóstico, categorias | Tudo herdado do Centro de Alertas E2 (fila persistida, dedupe 1h, backoff exponencial, validade do alerta vencido é marcada no inbox) + TTL/Urgency por severidade no push; diagnóstico = estado honesto no painel + tentativas auditadas por alerta + rota de teste com latência real | `test_pwa_push.py` + suíte E2; o teste de cripto prova TTL e payload reais; o botão "Testar push" despacha pelo canal real |
| 4d | Toque abre a área autenticada sem dado sensível na URL | SW `notificationclick` → `data.url || '/app#notificacoes'` — **nunca** id/valor na URL; foca janela já aberta em vez de abrir 2ª aba | pinos estáticos no `sw.js` (`tag: dados.id`, `/app#notificacoes`, focus/openWindow) |
| 5 | Particularidades do iPhone | UI explica "Compartilhar → Adicionar à Tela de Início" e que push só funciona no app instalado; quando o dispositivo não suporta, botão "Ativar" desabilita com o motivo | estáticos + e2e (estado honesto renderizado) |
| 6 | Segurança | Bearer single-owner nas rotas de push (mesma auth do painel); CSRF não aplicável (sessão por header, sem cookie — declarado); segredos VAPID protegidos em repouso; **CSP** no painel + **no-store** em `/v1`, `/app` e `/floor`; `nosniff`; **SW nunca cacheia `/v1` nem página autenticada** (só manifest + `/static/marca/`) — pinado por teste que remove comentários antes de procurar | `test_pwa_static.py` (cache mínimo), `test_pwa_push.py::TestRotasPush::test_cabecalhos_de_seguranca` e `test_csp_nao_quebra_o_iframe_do_floor` |
| 7 | Backend 24/7 | O monitoramento continua 100% no backend (a fila é do servidor; o celular é só cliente); offline/expirada/não-permitida/servidor reiniciado/falha/vencida têm estado honesto e trilha (poda, backoff, persistência, marca de vencido); histórico preservado no Centro de Alertas mesmo sem push; push NÃO substitui stops na corretora (desenho inalterado) | suíte E2 + rotas de estado; nenhum arquivo de execução tocado |
| 8 | Windows × Oracle sem split-brain | Nenhum executor financeiro no celular; nenhuma rota de ordem nova; posse exclusiva e travas intactas; LIVE `BLOCKED_BY_POLICY` | grep da suíte: rotas de push não chamam nada de execução; mesma API do dono único |
| 9 | 15 testes de aceitação | Matriz abaixo — separando o que é reproduzível aqui do que exige dispositivo/HTTPS reais | — |
| 10 | Critério de conclusão | **NÃO declarado concluído**: sem manifest-como-prova; homologação real pendente está registrada abaixo | — |

## Defeito real encontrado na verificação e2e (corrigido)

O e2e em navegador real pegou uma **regressão introduzida pelo próprio CSP
novo**: `frame-ancestors 'none'` aplicado a todo `text/html` derrubou o iframe
same-origin do Trading Floor (`test_floor_embutido_same_origin` → "127.0.0.1
está bloqueado"), apesar de o produto já ter `X-Frame-Options: SAMEORIGIN`
para `/floor` exatamente para permitir esse embed (CSP sobrepõe XFO).

- **RED:** `test_csp_nao_quebra_o_iframe_do_floor` (teste unit novo) falha
  mostrando o header exato com `frame-ancestors 'none'` no `/floor`; e2e do
  floor falha no browser real.
- **GREEN:** CSP passou a ser por caminho — `/floor` → `frame-ancestors 'self'`
  (mesma decisão do XFO), todo o resto → `'none'`. Teste unit verde; e2e do
  floor verde no navegador real; os demais e2e (menu/floor/iframe) sem
  regressão.

## Provas frescas (2026-10-14, branch `fix/instalador-atualizacoes-windows`)

| Evidência | Resultado |
|---|---|
| Bateria focada PWA (`test_pwa_push.py` + `test_pwa_static.py` + `test_centro_de_alertas.py` + `test_alertas_transparencia.py`) | **110 passed** |
| e2e Playwright (12 testes — inclui o novo PWA e a matriz 320×568) | **12 passed** (screenshots `00-…` a `08-pwa-config.png` em `docs/mission/evidence/e2e/`) |
| Suíte completa (`pytest tests`) | **2730 passed / 5 skipped / 71 subtests** (exit 0; skips esperados: playwright no venv, langchain_aws, POSIX 600 no NTFS, postgres via CI, lightgbm) |
| Ruff (arquivos alterados) | All checks passed |
| `node --check` (`sw.js` + script inline do painel) | OK |
| Cripto real (pywebpush/py-vapid, sem rede) | payload VAPID válido + conteúdo cifrado + TTL/Urgency por severidade |
| Commit do código (branch `fix/instalador-atualizacoes-windows`) | `bc0ba68` |

## Testes de aceitação (§9) — o que está provado e o que exige dispositivo real

| # | Teste de aceitação | Estado aqui | Base |
|---|---|---|---|
| 1 | Abrir domínio HTTPS no Android | **BLOQUEADO_EXTERNO** (sem endpoint HTTPS autorizado/dispositivo) | app local serve 127.0.0.1; contexto seguro existe só em localhost/HTTPS |
| 2 | Instalar na tela inicial | **BLOQUEADO_EXTERNO** (dispositivo) — mecanismo pronto: botão só com `beforeinstallprompt`, manifest+ícones completos | estáticos |
| 3 | Abrir PWA sem barra do navegador | **BLOQUEADO_EXTERNO** (dispositivo) — `display: standalone` no manifest | manifest + e2e |
| 4 | Fazer login | **PROVADO** (navegador real; mobile 390×844 e 320×568) | e2e |
| 5 | Navegar por todas as telas | **PROVADO** (menu completo, 7 páginas, zero 5xx) | e2e |
| 6 | Registrar operação manual | **PROVADO** (copiloto manual ponta a ponta) | e2e |
| 7 | Ativar "Acompanhar esta operação" | **PARCIAL** — o copiloto já acompanha posições registradas (política/stop/pausar/retomar provados); o botão de adesão "Acompanhar esta operação" (§4 do V8 principal) é seção separada e segue pendente — NÃO declarado | e2e copiloto |
| 8 | Autorizar notificações | **PROVADO no mecanismo** (clique → permissão → subscribe real); fluxo de permissão do SO não é exercitável em headless | estáticos + rotas |
| 9 | Push de teste real ao dispositivo | **BLOQUEADO_EXTERNO** (sem dispositivo/HTTPS) — caminho real do envio provado por cripto real + rota de teste pelo canal real | `TestCriptoRealSemRede`, rota `notificacoes/testar` |
| 10 | Alerta pelo feed de teste | **PROVADO** (fila durável → canal webpush com entregador real; TTL/Urgency por severidade) | unit (36) |
| 11 | Receber com a interface fechada | **BLOQUEADO_EXTERNO** (dispositivo) — handler do SW pronto e pinado (`tag` por id, `requireInteraction` nas severidades altas) | estáticos |
| 12 | Abrir a operação certa ao tocar | **PROVADO no mecanismo** (SW → `/app#notificacoes`, sem dado sensível na URL); toque físico: BLOQUEADO_EXTERNO | estáticos |
| 13 | Desativar notificações | **PROVADO** (rota cancelar + botão por dispositivo; 422/404/200 pinados) | unit + estáticos |
| 14 | Reiniciar backend sem perder histórico | **PROVADO** (fila persistida desde E2; assinaturas persistem cifradas; nada em memória volátil) | suítes E2 + PWA |
| 15 | Atualizações do PWA e recuperação offline | **PROVADO no mecanismo** (cache versionado, purge, `updateViaCache:'none'`, fallback de rede); update/offline reais em dispositivo: BLOQUEADO_EXTERNO | estáticos |
| — | Repetir homologação no iPhone | **BLOQUEADO_EXTERNO** (dispositivo iOS) | — |

## Limitações declaradas (honestidade)

1. **Homologação real dos mecanismos push NÃO foi executada** — não há endpoint
   HTTPS autorizado nem dispositivo físico nesta estação; o próprio complemento
   (§9) diz que "testes de navegador emulado não substituem testes reais dos
   mecanismos push". Nada aqui foi declarado homologado em Android/iPhone.
2. **Nenhuma chave VAPID foi gerada no ambiente do dono** e nenhuma chave
   entra no repositório; a geração fica a um clique no painel (fica cifrada
   em repouso pelo SEC-1). A config do dono (`%APPDATA%\DZ23-Trading-AI\`)
   NÃO foi tocada.
3. **CSRF:** as rotas usam Bearer por header (sem cookie de sessão) → CSRF não
   se aplica; validação de origem explícita não foi adicionada nesta rodada
   (a superfície é a API autenticada do dono único, como o resto do painel).
4. **Item 7 (botão "Acompanhar esta operação") é do V8 principal (§4)** —
   segue pendente como seção própria; aqui só o que o PWA precisava.
5. O push **não substitui stops protetivos na corretora** — regra inalterada;
   LIVE continua `BLOCKED_BY_POLICY`; nenhuma ordem real em nenhum teste.

## Arquivos

- `tradingagents/desk/push_web.py` (novo — núcleo VAPID/assinaturas/entrega)
- `tradingagents/desk/canais_alerta.py` (`CanalWebPush` honesto)
- `service/app.py` (rotas `/v1/push/*`, `_push_do_tenant`, `/sw.js`,
  cabeçalhos CSP/no-store, segredos novos no universo cifrado)
- `service/static/sw.js` (novo — Service Worker versionado)
- `service/static/index.html` (card PWA + fluxos instalar/ativar/testar/cancelar)
- `tests/test_pwa_push.py` (novo — 36), `tests/test_pwa_static.py` (novo — 9),
  `tests/test_centro_de_alertas.py` (ajustes de honestidade),
  `tests/e2e/test_panel_e2e.py` (novo teste PWA + matriz 320×568)
- `pyproject.toml` / `uv.lock` (`pywebpush` — extra `push`; licença MPL-2.0
  passa o portão de licenças do CI)

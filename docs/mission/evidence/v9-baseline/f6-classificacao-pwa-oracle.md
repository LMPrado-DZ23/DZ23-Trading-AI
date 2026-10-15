# F6 — Classificação PWA e Oracle ARM64 (MISSÃO V9)

Estado: **EXECUTADO em 2026-10-15** sobre o baseline `2b7754a94cc1e4233ad95ac6fa377be4078e5055` (branch `audit/v9-rc-20261015`). Nenhuma linha de produto foi alterada para produzir esta evidência; apenas execuções de teste no executor hermético, leitura de código e uma construção ARM64 local (réplica do job de CI).

## Níveis (definição da missão)

| Nível | Significado |
| --- | --- |
| IMPLEMENTADO | Código existe; nenhum serviço real exercitado nesta missão. |
| TESTADO COM MOCK | Suíte automatizada exercita o caminho contra fakes/transporte injetado/monkeypatch. |
| TESTADO (host real) | Exercitado de ponta a ponta no ambiente real disponível (processos, HTTP, navegador, build). |
| HOMOLOGADO EM APARELHO/INFRA REAL | Exercitado em aparelho físico ou na infraestrutura de destino real. |

Regra da missão aplicada: **testes em emuladores não substituem homologação em aparelhos reais.** Tudo que exige aparelho físico (Android/iPhone), serviço de push real ou a VPS Oracle real fica classificado como **DEPENDÊNCIA EXTERNA**, com o bloqueio registrado — conforme a regra "se faltar credencial, infraestrutura ou permissão, registrar precisamente o bloqueio e executar os demais testes".

## Artefatos de evidência (sha256)

| Artefato | sha256 | Conteúdo |
| --- | --- | --- |
| [f6-pwa.log](f6-pwa.log) | `feabc72a263f75d47320f062d74ef5506e97eeac0986f8b7e13d08cc5873e0ce` | `test_pwa_push.py` + `test_pwa_static.py` no executor hermético — **45 passed** |
| [f6-sessao.log](f6-sessao.log) | `e28e99bc6f278263c5afce4036cb8a74b87c52519d28b5bafc11387634aeee03` | `test_login_dono_unico.py` + `test_oauth_social.py` + `test_desk_backup.py` — **33 passed, 1 skipped** (POSIX 600 em NTFS, pulo ambiental conhecido) |
| [f6-reconciliacao.log](f6-reconciliacao.log) | `8d425ecaa34514e1fdfd6a2145b84557433f52d42323adbcb9fdd1adc1aa86b5` | `test_reconciliacao_adversarial.py` + `test_reconciliation_state.py` + `test_reconciliation_worker.py` + `test_relatorio_reconciliacao.py` — **62 passed** |
| [f6-health-recovery.log](f6-health-recovery.log) | `61224b15dd58e2b8147d693044b4f4f6cf4806d317c55791455853321fa0abf9` | `test_service_leigo.py` + `test_single_owner_g2.py` + `test_crash_restart.py` + `test_recovery_settlement.py` + `test_v9_posse_dois_processos.py` — **159 passed** em 57,2s |
| [f6-arm64-build.log](f6-arm64-build.log) | `bd3bfed34b015f665b52d54c4fc6725597e582fe307634613130c932212de069` | Réplica local do job `arm64-deps` do CI: `docker buildx build --platform linux/arm64` exit 0 + inspect (manifest list `5e39e896…`, arch=arm64, os=linux) + smoke de imports em aarch64 exit 0 |
| [f6-arm64-build-invocation.json](f6-arm64-build-invocation.json) | `65cd3a962aecfc404430581660c41d62e9a76d7112089add46272689649d17ac` | Registro dos 4 comandos (build/buildx/inspect/smoke) com saídas e exit codes; execução deliberada fora do executor hermético (Docker não é Python) |

Todos os logs de teste foram produzidos pelo executor hermético [tools/v9_pytest.py](../../../tools/v9_pytest.py) (rede só loopback, chaves de IA removidas, diretórios temporários). A construção ARM64 roda fora dele, deliberadamente (Docker não é Python).

## Resultado por item (checklist da missão F6)

| # | Item | Classificação | Evidência-chave | Bloqueio remanescente |
| --- | --- | --- | --- | --- |
| 1 | Manifest e Service Worker | **TESTADO (host real)** | `test_pwa_static.py`: SW servido com `service-worker-allowed: /` e `cache-control: no-cache`; manifesto 200 com `start_url=/app`, `display=standalone`; registro com `updateViaCache:'none'` — `feabc72a…` | — |
| 2 | Cache seguro | **TESTADO (host real)** | Pinos estáticos no `sw.js`: fetch handler intercepta **somente** `/manifest.webmanifest` e `/static/marca/`; após remover comentários, zero ocorrências de `/v1` no código; `caches.delete` no activate purga versões antigas; `/v1/push/estado` responde 401 sem sessão — `feabc72a…` | — |
| 3 | Instalação Android | **DEPENDÊNCIA EXTERNA** | UI degrada honestamente: botão Instalar inicia escondido até `beforeinstallprompt`; `isSecureContext` com aviso "exigem HTTPS ou localhost" — `feabc72a…` | Sem aparelho Android físico no ambiente; emulador não substitui homologação (regra da missão) |
| 4 | Instalação iPhone | **DEPENDÊNCIA EXTERNA** | Card "Aplicativo e Notificações Push" com dica "Adicionar à Tela de Início" (Safari não dispara `beforeinstallprompt` — coberto por texto de ajuda) — `feabc72a…` | Sem aparelho iPhone/Safari real no ambiente |
| 5 | Inscrição Web Push | **IMPLEMENTADO + TESTADO COM MOCK** | `test_pwa_push.py`: pywebpush REAL com sessão injetada — JWT vAPID ES256, `aes128gcm`, TTL asseverados; fluxo de inscrição pinado (`pushManager.subscribe`, `applicationServerKey`, `/v1/push/assinar`/`cancelar`) — `feabc72a…` | Sem serviço de push externo real e sem aparelho; entrega real não executada |
| 6 | Push com app em segundo plano | **DEPENDÊNCIA EXTERNA** | SW com `showNotification` (tag = id do alerta, dedup; urgente → `requireInteraction`; clique abre área autenticada) — `feabc72a…` | Exige serviço de push real + aparelho físico |
| 7 | HTTPS | **IMPLEMENTADO + TESTADO (degradação)** | Painel detecta contexto inseguro (`isSecureContext`) e explica o requisito; deploy TLS é do operador: [DEPLOY.md](../../../DEPLOY.md) §59-61 + [ops/Caddyfile.example](../../../ops/Caddyfile.example) (proxy reverso de referência) | Domínio/DNS/certificado reais exigem o ambiente Oracle autorizado |
| 8 | Sessão autenticada | **TESTADO (host real)** | `test_login_dono_unico.py` (dono único, fail-closed) + `test_oauth_social.py` + rotas exigindo 401 sem sessão — `e28e99bc…`, `feabc72a…` | — |
| 9 | Reconexão | **TESTADO (host real)** | `test_copiloto_manual.py::test_reconexao_rapida_volta_a_monitoring` (monitor volta a MONITORING após interrupção); painel é HTTP stateless (sem WebSocket a cair — grep: 0); retomada do servidor coberta por crash-restart — `61224b15…` | Interrupção de rede real de longo curso não simulada |
| 10 | Dados sensíveis | **TESTADO (host real)** | SW nunca cacheia `/v1`/páginas autenticadas nem credenciais (pino estático); backup cifrado por senha; SEC-1 (segredos em repouso) re-verificado nesta janela — `feabc72a…` | — |
| 11 | Backups | **TESTADO (host real)** | Lote sessão/backup (`test_login_dono_unico.py` + `test_oauth_social.py` + `test_desk_backup.py`): **33 passed, 1 skip** — o único skip é `test_desk_backup.py:58` (POSIX 600 em NTFS, ambiental; roda no Linux/CI); volume `/data` + backup do operador documentados em DEPLOY.md — `e28e99bc…` | Backup no Oracle real exige a VPS (não autorizada nesta missão) |
| 12 | Docker ARM64 | **TESTADO (build real neste host)** | Réplica do job `arm64-deps` do CI: buildx `linux/arm64` + import smoke de `argon2, ccxt, fastapi, lxml, pandas, psycopg, uvicorn` — imagem `5e39e896…` (arch=arm64, os=linux), smoke OK em aarch64 (pandas 3.0.6), ambos exit 0 — [f6-arm64-build.log](f6-arm64-build.log) `bd3bfed3…` + [invocação](f6-arm64-build-invocation.json) `65cd3a96…`; job CI: [ci.yml](../../../.github/workflows/ci.yml) §191-221 (rotulado "NÃO é homologação Oracle") | Homologação na VPS Oracle real (Ampere aarch64) exige infra do dono — não executada |
| 13 | Health checks | **TESTADO (host real)** | `/healthz` e `/readyz` existem no produto (`service/app.py` §964/§976) e são exercitados pelos testes leigos/single-owner; HEALTHCHECK do Dockerfile (`urllib` em 127.0.0.1:8080/healthz) + launcher Windows com healthz real — `61224b15…` | — |
| 14 | Recuperação do servidor | **TESTADO (host real)** | `test_crash_restart.py` + `test_recovery_settlement.py` (159 passed em lote) | — |
| 15 | Posse exclusiva da execução | **TESTADO (host real)** | `test_v9_posse_dois_processos.py` (3 testes entre processos; V9-POSSE-01) + caminho Postgres real no CI (`test_modo_da_mesa.py`) — `61224b15…` | Posse na VPS Oracle real depende da infra |

## Detalhe das evidências

### Service Worker e cache — onde a segurança mora

`service/static/sw.js` (versionado, cache `dz23-pwa-v1`): o handler de `fetch` retorna cedo para tudo que não seja GET same-origin **e** para tudo que não seja `/manifest.webmanifest` ou `/static/marca/*`. Não existe caminho de código que cacheie `/v1`, páginas autenticadas ou respostas com credencial — verificado por pino estático que remove comentários antes de procurar `/v1` (impede "prova por comentário"). O `activate` purga caches de versões anteriores; `skipWaiting`/`claim` evitam versão presa. O push usa `tag` = id do alerta (reenvio substitui, não duplica) e o clique abre `/app#notificacoes` (fragmento não viaja ao servidor).

### Sessão, backups e reconexão

- Sessão: dono único fail-closed (`test_login_dono_unico.py`), OAuth social, e as rotas sensíveis respondem 401 sem sessão (asseverado no próprio `test_pwa_static`).
- Backups: `test_desk_backup.py` verifica criação de backup cifrado; o único pulo (POSIX 600 em NTFS) é ambiental e conhecido — no Linux/CI ele roda (suíte Linux: 3221 passed, 12 skipped).
- Reconexão: não há WebSocket no painel (grep = 0 ocorrências); o monitor do copiloto tem teste explícito de reconexão rápida; a retomada do servidor após crash tem suíte dedicada (159 passed no lote F6).

### Docker ARM64 — o que foi executado aqui

Réplica fiel do job de CI `arm64-deps` (ci.yml §191-221): `docker buildx build --platform linux/arm64 --load` seguido do import smoke dentro da imagem emulada (QEMU do Docker Desktop). Resultado registrado em `f6-arm64-build.log`. Isso **valida que a árvore de dependências instala e importa em aarch64** — evidência de compatibilidade, não de homologação: a homologação exige execução na VPS Oracle real, que não foi autorizada.

### Bloqueios externos precisos (o que falta e por quê)

| Bloqueio | O que exigiria | Regra da missão |
| --- | --- | --- |
| Aparelho Android físico | Instalar PWA no Chrome Android real, inscrever push, verificar push em segundo plano | "Testes emuladores não substituem homologação em aparelhos reais" |
| Aparelho iPhone físico | Instalar via Safari (Adicionar à Tela de Início), verificar web push no iOS | idem |
| Serviço de push real (FCM/APNs) | Receber notificação de verdade com app fechado | Exige aparelho + inscrição real; sem infraestrutura no ambiente |
| VPS Oracle ARM64 real | Deploy real (docker compose) + TLS + healthcheck na Ampere | "Não alterar DNS ou implantar em produção sem autorização"; não autorizado nesta missão |

Nenhum desses bloqueios impede o veredito técnico dos itens executáveis: o produto está pronto para receber esses testes quando o dono disponibilizar aparelho/infra, e a UI degrada com mensagem honesta quando o contexto não suporta (sem prometer o que não entrega).

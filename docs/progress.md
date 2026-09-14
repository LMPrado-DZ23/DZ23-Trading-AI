# Progresso de evolução para usuário final

## Ciclo 1 — ExecutionRouter, Paper Ledger persistente e KillSwitch

### Implementado

Foi criado o `ExecutionRouter` como porta central de execução. Ele executa o `RiskEngine` antes do adapter, publica `risk.decision.created`, `order.submitted`, `order.accepted` e `order.rejected`, registra no `AuditLog` e mantém idempotência por `client_order_id`.

O `PaperAdapter` agora pode persistir estado em JSON com escrita atômica e recuperar ordens, posições, equity e parâmetros após reinício. Foi criado o `KillSwitch`, independente do frontend e dos agentes, com desbloqueio condicionado a reconciliação consistente.

### Testes executados

```text
ruff check nos módulos alterados: passou
python3 -m compileall -q tradingagents service: passou
12 testes focados: passaram
```

### Riscos restantes

O endpoint legado `/v1/broker/order` ainda precisa ser migrado formalmente para o `ExecutionRouter` com uma `RiskDecision` persistida, em vez de depender apenas do gate de capital existente. O Event Bus ainda é local em memória; o Audit Log persiste eventos, mas falta um read model/event store operacional para múltiplos processos. O Paper Ledger ainda precisa de PnL, fees, margem, slippage avançado, fills parciais e snapshots completos.

## Próximo ciclo

Integrar o router ao serviço/API, persistir decisões de risco e ordens, completar reconciliação por ordens/fills/posição/saldo, adicionar Shadow Trading e reforçar workers/observabilidade.

## Ciclo 2 — API Paper governada

### Implementado

O serviço agora cria um `ExecutionRouter` com `RiskEngine`, `AuditLog`, `EventBus` e `PaperAdapter` persistente. Foi adicionado `POST /v1/execution/paper`, que recebe decisão tipada, snapshot de mercado e evidências, executa o Risk Engine e somente então envia ao Paper Adapter.

A API retorna a decisão de risco e o ack da ordem. O smoke test agora cobre snapshot, reuniões, votos, estados de agentes e uma execução Paper completa. O endpoint não permite Live e não utiliza credenciais reais.

### Testes executados

```text
ruff E9/F63 no serviço: passou
compileall: passou
smoke_agency_api.py: passou
12 testes focados: passaram
suíte baseline anterior: 911 passed, 2 skipped
```

### Riscos restantes

A integração CCXT do endpoint legado ainda precisa migrar para uma decisão de risco persistida e o Event Bus de execução ainda precisa de read model compartilhado. O Paper Ledger não calcula ainda PnL detalhado, fees, margem, funding ou fills parciais. O deployment 24/7, alertas, backup/restore e observabilidade de produção ainda não estão concluídos.

## Próximo ciclo

Adicionar Shadow Trading, snapshots/PNL do Paper Ledger e reconciliação persistente; depois reforçar health/readiness, métricas e deployment controlado.

## Ciclo 3 — Shadow Trading e métricas Paper

### Implementado

Foi criado `ShadowAdapter`, que registra intenções e calcula PnL hipotético sem enviar ordens. O Paper Adapter agora possui marcação a mercado com equity, PnL não realizado, exposição bruta e contagem de posições.

### Testes executados

```text
ruff nos módulos alterados: passou
compileall: passou
14 testes focados: passaram
smoke_agency_api.py: passou
```

### Riscos restantes

O Paper Ledger ainda não modela todas as fees, funding, margem, fills parciais e PnL realizado. O Shadow Adapter ainda usa memória de processo e precisa de persistência/replay. A reconciliação externa, kill switch exposto via operação e observabilidade ainda precisam ser integrados ao serviço.

## Próximo ciclo

Adicionar endpoint/relatório Shadow, health/readiness, métricas de execução e persistência/replay dos eventos para suportar reinício e múltiplos workers.

## Ciclo 4 — validação integral do lote

### Resultado

A suíte completa foi executada depois das mudanças:

```text
918 passed
2 skipped
71 subtests passed
18 warnings
```

Os skips são um teste Bedrock sem `langchain_aws` instalado e um teste DeepSeek que exige credencial live. O smoke test da API, compilação e testes focados também passaram.

O lote está pronto para commit/publicação, mas o produto ainda não deve ser chamado de pronto para dinheiro real. Permanecem pendentes persistência distribuída do Event Bus, reconciliação externa completa, fees/funding/margem/fills parciais do Paper, observabilidade de produção, deployment 24/7 e validação Demo com terminal MT5 real isolado.

## Ciclo 5 — persistência de eventos e Shadow Trading

### Implementado

O Event Bus agora aceita um `AuditLog` persistente, recupera eventos no boot, preserva sequência de replay e evita duplicação após reinício. O serviço passou a usar esse barramento persistente para os eventos da agência.

O ShadowAdapter agora possui persistência atômica, recuperação após reinício, endpoint protegido para registrar intenções e endpoint de resumo. O smoke test verifica explicitamente `orders_sent = 0`.

### Testes executados

```text
ruff seletivo: passou
compileall: passou
16 testes focados: passaram
smoke_agency_api.py: passou
```

### Riscos restantes

O Event Bus persistente atual é baseado em arquivo local e não oferece coordenação distribuída para múltiplos processos. A reconciliação externa completa e os read models de produção ainda faltam. O serviço ainda precisa de métricas, alertas, backup/restore, deployment persistente e endurecimento do endpoint legado CCXT.

## Ciclo 6 — reconciliação estruturada

### Implementado

Foi adicionada `reconcile_state`, que compara snapshots locais e remotos de ordens, fills, posições, saldo, equity e margem. Diferenças numéricas respeitam tolerância explícita; divergências retornam relatório inseguro e identificam o campo conflitante.

### Testes executados

```text
ruff: passou
compileall: passou
16 testes focados: passaram
```

### Riscos restantes

A reconciliação precisa ser chamada periodicamente pelos workers e conectada aos snapshots reais de CCXT/MT5. O endpoint legado de broker ainda não usa o router tipado. Persistência distribuída, observabilidade, backup e deployment 24/7 continuam pendentes.

## Ciclo 7 — monitor de reconciliação e Event Bus de execução

### Implementado

Foi criado `ReconciliationMonitor`, que executa snapshots local/remoto, produz relatório `MATCHED`, `DIVERGED` ou `UNKNOWN`, chama callback operacional e falha fechado quando um provider está indisponível. O Event Bus de execução passou a usar `execution-events.jsonl` persistente.

### Testes executados

```text
ruff seletivo: passou
compileall: passou
18 testes focados: passaram
smoke_agency_api.py: passou
```

### Riscos restantes

O monitor ainda precisa ser registrado no supervisor de workers com providers reais CCXT/MT5 e acionar o KillSwitch automaticamente em divergência. Métricas, alertas e deployment persistente ainda não estão concluídos.

## Ciclo 8 — métricas e readiness

### Implementado

Foi criado `MetricsRegistry` thread-safe, integrado ao ExecutionRouter com contadores de tentativas, rejeições de risco, replay idempotente e submissões. Foram adicionados `/readyz` e `/metrics`; o readiness informa bloqueio do KillSwitch, estado Paper e sequência do Event Bus.

### Testes executados

```text
ruff seletivo: passou
compileall: passou
19 testes focados: passaram
smoke_agency_api.py: passou
```

### Riscos restantes

Ainda faltam métricas de workers, alertas externos, armazenamento de métricas de longa duração, backup/restore, integração do monitor com providers reais, endurecimento do endpoint CCXT legado, onboarding e deployment 24/7.

## Ciclo 9 — segurança de release e operação

### Implementado

O endpoint legado `/v1/broker/order` passou a responder `410` por padrão, impedindo bypass do ExecutionRouter. O comportamento só pode ser alterado explicitamente por `LEGACY_BROKER_ORDER_ENABLED`, que permanece false no ambiente de exemplo.

Foi criado `scripts/backup_data.py` para backup e restore atômicos dos dados operacionais permitidos, com rejeição de caminhos inseguros. O smoke test valida o bloqueio do endpoint legado.

### Testes executados

```text
ruff seletivo: passou
compileall: passou
21 testes focados: passaram
smoke_agency_api.py: passou
```

### Riscos restantes

Ainda faltam integração do monitor com adapters reais, worker 24/7 registrado no process manager, alertas externos, onboarding final, deployment documentado e teste MT5 Demo real isolado. O endpoint legado está bloqueado até a migração completa.

## Ciclo 10 — snapshots MT5/Paper e reconciliação pela API

### Implementado

O Paper Adapter agora produz snapshot normalizado de saldo, equity, ordens, fills e posições. O MT5 Adapter Demo produz snapshot equivalente usando account info, orders, deals e positions da biblioteca oficial. O serviço expõe `POST /v1/execution/reconciliation` e inclui o estado do monitor no health da execução.

O deployment foi atualizado para refletir os defaults seguros atuais, sem afirmar que Live está disponível.

### Testes executados

```text
ruff seletivo: passou
compileall: passou
23 testes focados: passaram
smoke_agency_api.py: passou
```

### Limitações

O snapshot MT5 foi validado com mock; a validação com terminal e conta Demo reais ainda depende do ambiente do usuário. O monitor Paper compara o ledger local e deve ser conectado a um provider remoto real para detectar divergência externa.

## Ciclo 11 — deployment resiliente

### Implementado

O Docker Compose agora usa `/data` de forma consistente, publica a porta 8080, configura defaults seguros, `restart: unless-stopped` e healthcheck `/healthz`. O perfil Ollama também usa o volume persistente correto e mantém Live desativado.

### Validação

```text
compileall: passou
23 testes focados: passaram
smoke_agency_api.py: passou
git diff --check: passou
```

O comando `docker compose config` não pôde ser executado neste sandbox porque o binário Docker não está instalado. A configuração precisa ser validada no host de deployment antes de publicar o serviço.

## Ciclo 12 — lifecycle do worker de reconciliação

### Implementado

O serviço registra `paper-reconciliation` no `WorkerSupervisor`, inicia no startup, encerra no shutdown, executa a cada 60 segundos e expõe heartbeat/falhas no health de execução. O monitor continua fail-closed.

### Testes executados

```text
ruff seletivo: passou
compileall: passou
25 testes focados: passaram
smoke_agency_api.py: passou
```

## Ciclo 13 — checklist de release

### Resultado verificado

```text
release_check.py: passou
compileall: passou
25 testes focados: passaram
smoke_agency_api.py: passou
git diff --check: passou
929 testes completos passaram
2 testes foram ignorados por dependências/credenciais externas
71 subtestes passaram
```

### Limitações externas honestas

O sandbox não possui Docker, portanto `docker compose config` precisa ser executado no host de deployment. O MT5 Demo real exige terminal MetaTrader 5 e conta Demo do usuário; os caminhos foram validados com mocks, não com uma conta externa. Alertas externos e coleta de métricas de longa duração dependem da infraestrutura escolhida pelo usuário.

A aplicação está pronta para onboarding em Paper/Shadow e preparação Demo, mas não deve ser apresentada como garantia de lucro nem como autorização para dinheiro real.

## Ciclo 14 — CI contínuo preparado

### Implementado

Foi preparada uma atualização do workflow existente do GitHub Actions para executar também em branches `feat/**`, além de `main`, e validar a suíte, o checklist de release e o smoke test da API com defaults sem dinheiro real. A publicação dessa alteração foi rejeitada pelo GitHub porque a credencial da sessão não possui permissão `workflows`; o workflow remoto permanece inalterado.

### Validação local

```text
workflow checks: passou
release_check.py: passou
smoke_agency_api.py: passou
git diff --check: passou
```

### Bloqueio externo

Para publicar a mudança do workflow, o usuário precisa usar uma credencial/token com permissão de workflows no repositório ou aplicar manualmente o diff preparado.

## Ciclo 15 — correção dos achados críticos da auditoria

### Objetivo

Corrigir os achados CRITICAL/HIGH de segurança e aceitação identificados pelas auditorias independentes.

### Implementado

O modo CCXT Live foi removido estruturalmente: `CCXTBridge` aceita somente testnet e o endpoint legado retorna 410 sem flag de reativação. A frase Live foi removida da configuração editável e deixa de ser retornada pela API. O KillSwitch agora persiste estado com escrita atômica, sobrevive a reinício e o reset executa reconciliação server-side; sem provider remoto, o resultado é `UNKNOWN` e o reset permanece bloqueado. O monitor não compara mais o ledger Paper consigo mesmo: a ausência de provider remoto é explicitamente `UNKNOWN`.

O ExecutionRouter rejeita request incompatível com a decisão aprovada. A API valida datas de calendário, o onboarding declara FastAPI/Uvicorn/CCXT em `pyproject.toml`, e a UI remove controles Live e botões de ordem legados. O backup operacional deixou de incluir `config.json` e chaves.

### Arquivos alterados

`tradingagents/brokers/ccxt_bridge.py`, `tradingagents/execution/kill_switch.py`, `tradingagents/execution/router.py`, `service/app.py`, `service/static/index.html`, `scripts/backup_data.py`, `scripts/smoke_agency_api.py`, `pyproject.toml`, `docs/OPERATIONS-USER-READY.md` e testes de segurança.

### Testes executados

```text
ruff seletivo: passou
compileall: passou
35 testes focados: passaram
smoke_agency_api.py: passou
922 testes completos passaram
2 testes ignorados
71 subtestes passaram
```

### Resultado

Os dois achados CRITICAL de Live/bypass foram corrigidos e cobertos por regressão. Achados HIGH remanescentes incluem ledger financeiro incompleto, persistência distribuída, RBAC, TLS do deployment, alertas duráveis, hardening de restore/container e validação MT5 Demo real.

### Riscos restantes

A auditoria independente precisa ser repetida. MT5 Demo real e Docker permanecem dependências externas. O lint estrito histórico ainda precisa de triagem, e a UI ainda requer uma auditoria completa de acessibilidade/escape/erro.

### Próximo ciclo

Executar nova suíte, consolidar auditoria de verificação e corrigir achados HIGH internos restantes antes de candidate completion.

## Ciclo 16 — regressão de segurança e estado financeiro Paper

### Objetivo

Retestar as correções críticas e fortalecer o estado financeiro mínimo usado pelo Risk Engine.

### Implementado

O Paper Ledger agora persiste equity inicial e PnL realizado, deriva margem disponível, margem usada, exposição bruta/líquida e drawdown do conjunto de posições, e calcula PnL ao fechar posição. Foi adicionado teste de compra, fechamento e PnL realizado.

### Testes executados

```text
ruff seletivo: passou
compileall: passou
35 testes críticos passaram
9 testes do ledger/segurança passaram
smoke_agency_api.py: passou
```

### Riscos restantes

Ainda falta integrar um provider remoto real ao worker e acionar bloqueio/alerta operacional de forma configurável, completar persistência distribuída, RBAC/TLS/container hardening, acessibilidade/escape do Floor e repetir a auditoria independente.

## Ciclo 17 — fechamento dos quatro achados HIGH da reauditoria

### Implementado

O ExecutionRouter agora possui guard obrigatório de reconciliação quando configurado; o serviço bloqueia Paper se o relatório não estiver `MATCHED` e seguro. O Paper Ledger agora atualiza cash, balance, equity derivada, margem, exposição e PnL realizado, persiste esses campos e rejeita over-close quando short não está habilitado. O Router compara identidade, venue, tipo, preço, níveis, quantidade e demais campos vinculantes entre OrderRequest e TradeDecision. Código morto de ordem legado foi removido da UI e do servidor, e referências Live obsoletas foram removidas dos módulos CCXT/serviço.

### Evidências

```text
28 testes focados passaram
smoke_agency_api.py passou
compileall passou
Ruff seletivo passou
scanner de referências Live em brokers/service não encontrou resultados
```

### Pendências reais

A reauditoria final ainda precisa confirmar o lote. Permanecem limitações externas: provider remoto/MT5 Demo, Docker/host de deployment e permissão de workflow no GitHub. UX de erro/loading, acessibilidade, RBAC e lint histórico continuam melhorias antes de um release público amplo.

## Ciclo 18 — hardening final de integridade e fail-closed

### Implementado

O `ExecutionRouter` agora é fail-closed mesmo quando nenhum guard é injetado; apenas uma reconciliação segura explícita permite a submissão. O PaperAdapter valida over-close antes de criar ACK ou alterar o ledger, evita registros FILLED para operações rejeitadas e unifica o mark-to-market com cash e posições marcadas. `OrderRequest` passou a transportar `time_in_force`, que é comparado ao campo correspondente da decisão.

### Evidências finais locais

```text
15 testes focados do hardening: passaram
smoke_agency_api.py: passou
compileall: passou
925 testes completos em ambiente sanitizado: passaram
2 testes ignorados por dependências/credenciais externas
71 subtestes passaram
```

### Status de release

O código está seguro por padrão para operação local governada, sem Live CCXT e sem endpoint legado de ordens. A execução Paper do serviço permanece bloqueada enquanto o provider remoto de reconciliação não produzir `MATCHED`; isso é intencional e fail-closed, não uma integração Demo concluída.

Não foram declarados como concluídos: validação de terminal/conta MT5 Demo real, Docker/Compose no host de deployment, RBAC multiusuário, acessibilidade/UX completa, lint histórico integral e CI remoto com permissão de workflows.

## Ciclo 19 — fundação multi-tenant Enterprise

### Implementado

Foi adicionado um núcleo de identidade com tenants, usuários, memberships, papéis, permissões, sessões assinadas por HMAC, expiração, revogação e hashing scrypt. A API agora suporta bootstrap one-shot do primeiro administrador, login multi-tenant, criação administrativa de tenants/usuários e autorização centralizada por rota.

Jobs e configurações são escopados por `tenant_id`. Runtime de agência, Paper, Shadow, auditoria e KillSwitch são criados por tenant, e os endpoints de Trading Floor/execução usam o runtime correspondente. O Compose foi ampliado com PostgreSQL, Redis, Qdrant e Ollama opcional; Live e dinheiro real permanecem bloqueados.

### Evidências

```text
25 testes focados passaram
smoke_multitenant.py passou com dois tenants
smoke_agency_api.py passou
compileall passou
compose-yaml-valid passou
```

### Limitações honestas

O sandbox desta sessão não possui o binário Docker, portanto `docker compose config/build/up` não foi executado. O serviço ainda mantém SQLite como fallback local para identidade/jobs; PostgreSQL/Redis/Qdrant estão declarados no Compose, mas a migração operacional para PostgreSQL e os testes de persistência no host real ainda são gates externos. MT5 Demo real, provider de reconciliação real, TLS, RBAC de deployment e CI remoto continuam dependentes do ambiente do usuário.

## Ciclo 20 — regressão da fundação multi-tenant

A suíte completa em ambiente sanitizado passou com `928 passed, 2 skipped, 71 subtests passed, 18 warnings`. Os testes focados, `smoke_multitenant.py`, `smoke_agency_api.py`, compileall e validação YAML do Compose também passaram. Docker runtime não foi executado porque o binário não está disponível neste sandbox; o Compose Enterprise deve ser validado no PC/host do usuário.

## Ciclo 21 — backend PostgreSQL de identidade

Foi adicionado `PostgresTenantStore` usando `psycopg[binary]`. Com `DATABASE_URL`, tenants, usuários, memberships e sessões são persistidos em PostgreSQL; sem a variável, o backend SQLite local continua disponível. A API exige chave de assinatura forte no modo multi-tenant. O armazenamento de jobs permanece SQLite por enquanto, explicitamente documentado como gate para escala horizontal.

Validação: `compileall` passou; `ruff` focalizado passou; 19 testes focados passaram. Não foi possível conectar a um servidor PostgreSQL neste sandbox, portanto o teste de integração real permanece pendente no Docker do usuário.

## Ciclo 22 — regressão pós-PostgreSQL

A suíte completa em ambiente sem `DATABASE_URL` passou novamente com `928 passed, 2 skipped, 71 subtests passed, 18 warnings` em 3m25s. Isso comprova que o fallback local não regrediu. A conexão real PostgreSQL depende do serviço Docker do host autorizado e permanece não declarada como validada.

## Ciclo 23 — preparação open source

O repositório foi preparado para colaboração pública com README atualizado, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, SUPPORT, NOTICE, templates de issues e pull requests, Dependabot e CI ampliado para branches de feature, compileall, lint, smoke multi-tenant e validação do Compose. A auditoria local não encontrou chaves privadas, tokens reais ou arquivos `.env` de produção rastreados. A suíte completa passou com `928 passed, 2 skipped, 71 subtests passed, 18 warnings`.

A visibilidade do repositório permanece privada até confirmação explícita do proprietário; descrição e tópicos públicos já foram configurados.

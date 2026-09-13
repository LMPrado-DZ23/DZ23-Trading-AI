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

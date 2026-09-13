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

# DZ23 AI Agency — Architecture

## Princípio central

Agentes podem **propor e executar tarefas autorizadas**, mas não podem ampliar permissões, limites financeiros ou escopo de uma missão. Um supervisor determinístico controla ciclo de vida, dependências, orçamento, concorrência, retries, cancelamento e aprovação humana.

## Componentes

```text
                    ┌──────────────────────────┐
                    │        Painel/API         │
                    │ objetivos, tarefas,      │
                    │ aprovações, eventos       │
                    └─────────────┬────────────┘
                                  │
                    ┌─────────────▼────────────┐
                    │   Agency Control Plane    │
                    │ supervisor + policy +     │
                    │ approvals + audit trail   │
                    └───────┬─────────┬────────┘
                            │         │
             ┌──────────────▼─┐   ┌──▼────────────────┐
             │ Durable Job     │   │ Event Bus         │
             │ Store            │   │ replay + SSE      │
             └──────────────┬──┘   └──┬────────────────┘
                            │         │
                   ┌────────▼─────────▼────────┐
                   │ Bounded Agent Worker Pool  │
                   │ research, ops, content,   │
                   │ finance, CRM, trading     │
                   └──────────────┬─────────────┘
                                  │
                    ┌─────────────▼────────────┐
                    │ Evidence / Memory Layer   │
                    │ sources, artifacts,       │
                    │ tenant-scoped state       │
                    └─────────────┬────────────┘
                                  │
                    ┌─────────────▼────────────┐
                    │ Trading Guarded Module     │
                    │ signal → risk → paper/    │
                    │ testnet → reconciliation  │
                    └──────────────────────────┘
```

## Estados de tarefa

`QUEUED → RUNNING → WAITING_DEPENDENCY → REVIEW_REQUIRED → SUCCEEDED`.

Estados de falha são `RETRYING`, `FAILED`, `CANCELLED` e `BLOCKED`. Uma tarefa nunca retorna sucesso apenas porque o modelo respondeu: ela precisa entregar um resultado validado, evidências e metadados de execução.

## Modelo de agente

Cada agente possui identidade, tenant, módulo, versão, permissões, ferramentas permitidas, orçamento de tokens/tempo, política de dados, fila e supervisor. O agente recebe um envelope de tarefa imutável e devolve um envelope de resultado com status, artefatos, evidências, eventos, consumo e próxima ação sugerida.

## Concorrência

A primeira implementação utilizará fila durável e pool limitado de workers. O supervisor aplica limites por tenant, por módulo e por missão. Tarefas sem dependência podem rodar em paralelo; tarefas financeiras, publicação, envio de mensagens, alteração de configuração ou acesso a segredo ficam serializadas e exigem política de aprovação.

O sistema deve ter idempotency key por tarefa, lease com expiração, heartbeat, backoff exponencial, dead-letter state e recuperação após reinício. Uma tarefa com lease expirado volta para `QUEUED` somente uma vez por ciclo de recuperação, evitando duplicação silenciosa.

## Política e permissões

As permissões são server-side e deny-by-default. O agente de risco pode bloquear ou reduzir uma ação, mas nunca pode aprová-la sozinho. As categorias de aprovação incluem:

- publicação externa;
- envio de comunicação a terceiros;
- alteração de credenciais ou permissões;
- pagamento ou compromisso financeiro;
- exportação de dados sensíveis;
- qualquer tentativa de trading Live.

## Memória e evidências

Memória de trabalho é limitada à missão. Memória persistente é tenant-scoped, versionada e auditável. Toda conclusão factual precisa referenciar uma evidência com origem, timestamp, hash e validade. Segredos não entram em prompts, resultados, logs ou eventos.

## Trading como módulo governado

O módulo de trading reutiliza `Signal`, `TradeDecision`, `RiskDecision`, `OrderRequest`, `ExecutionRouter`, `KillSwitch`, Paper Ledger e reconciliação existentes. A camada geral de agência pode solicitar pesquisa ou análise; somente o módulo de trading pode emitir intenção, e somente após risco, mandato, freshness, idempotência e ambiente seguro. `Environment.LIVE` permanece rejeitado nesta fase.

## Persistência e operação

PostgreSQL é a fonte durável para objetivos, tarefas, resultados, aprovações, auditoria e leases. Redis poderá ser introduzido para locks e rate limits distribuídos quando a concorrência real exigir. O armazenamento local permanece fallback de desenvolvimento, não garantia de produção. O painel usa snapshots e streams de eventos, não estado inventado no cliente.

O worker contínuo é um componente controlado por tenant, iniciado somente por `POST /v1/agency/worker/start` e encerrado por `POST /v1/agency/worker/stop`. `GET /v1/agency/worker/status` expõe apenas estado operacional lógico. O processo é idempotente no start, permanece em modo daemon, captura falhas por ciclo e retoma tarefas duráveis pelo mesmo executor após reinício. A inicialização do serviço não liga o worker automaticamente; essa decisão evita executar tarefas inesperadas durante deploy ou recuperação.

## Observabilidade

Métricas mínimas: tarefas por status, latência por agente, retries, falhas, tokens/custo, fila, leases expirados, aprovações pendentes, bloqueios de política, eventos de trading, reconciliações e estado do kill switch. Logs devem conter correlation ID, tenant, mission ID, task ID e agent ID, sem segredos.

## Rollout

Fase 1: contrato e núcleo concorrente em memória com persistência de eventos e testes.

Fase 2: fila durável, API de objetivos/tarefas, painel operacional e agentes gerais determinísticos/LLM.

Fase 3: pesquisa com evidências, memória tenant-scoped e aprovação humana.

Fase 4: integração Paper/Testnet com cenários de falha e reconciliação.

Fase 5: revisão independente, carga, restauração e operação Demo. Live continua fora do aceite até existir autorização e validação financeira específica.

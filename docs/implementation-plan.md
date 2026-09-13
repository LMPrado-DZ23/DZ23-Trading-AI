# DZ23 Autonomous Trading Agency — plano de implementação

## Baseline

O repositório atual é um framework de análise multiagente com LangGraph, dados de mercado e uma ponte CCXT. A primeira entrega nesta branch adiciona contratos de domínio, event log local, Risk Engine determinístico, Paper Adapter, reconciliação, lifecycle de estratégias e um MT5 Adapter isolado.

## Modos operacionais

O modo padrão é Paper. O adapter MT5 diferencia Demo e Live, mas Live permanece bloqueado por código nesta entrega. Nenhuma credencial real é necessária para os testes.

```text
LIVE_TRADING=false
PAPER_TRADING=true
SHADOW_TRADING=false
REAL_MONEY_EXECUTION=false
```

## Entregas desta fase

| Componente | Estado |
| --- | --- |
| Contratos tipados | Implementado |
| AuditEvent e AuditLog JSONL | Implementado |
| Risk Engine | Implementado com limites básicos |
| Paper Adapter | Implementado com fill determinístico e idempotência |
| Reconciliação | Implementado para conjuntos de IDs |
| Strategy Registry | Implementado com transições fechadas |
| MT5 Adapter | Estrutura, healthcheck, leitura e bloqueio de Live |
| Trading Floor | Próxima fase; depende de API/read model de eventos |
| Workers 24/7 | Próxima fase; MT5 Worker inicial criado |
| Live trading | Bloqueado e fora do escopo desta entrega |

## Próximos marcos

1. Cobrir contratos, risco, paper, event log e MT5 com testes.
2. Integrar decisões estruturadas ao grafo atual.
3. Criar event bus/read models e API de consulta.
4. Criar Paper Trading persistente e workers.
5. Criar frontend Trading Floor/War Room baseado em eventos.
6. Implementar MT5 Demo com mocks e depois teste em conta demo isolada.
7. Implementar adapters adicionais somente quando suas APIs e capacidades forem auditadas.
8. Executar shadow trading e auditoria antes de discutir qualquer capacidade real.


## Segunda etapa verificada

| Componente | Estado |
| --- | --- |
| Event Bus local com sequence/replay/deduplicação | Implementado |
| Audit Log persistente JSONL | Implementado |
| Agency Runtime com desks/agentes | Implementado |
| Reuniões com quorum e votos | Implementado |
| Endpoint snapshot/events/health | Implementado |
| Stream SSE autenticado | Implementado |
| Trading Floor read-only | Implementado |
| Backtest com custos e manifesto | Implementado |
| Walk-forward windows | Implementado |
| MT5 Demo com `order_check`/`order_send` | Implementado e mockado |
| Supervisor de workers com heartbeat/backoff | Implementado |
| Shadow Trading persistente | Preparado por contratos; execução dedicada ainda requer adapter/ambiente |
| Live trading | Gate existente, mas não habilitado por esta entrega |

## Limitação operacional explícita

O código está pronto para desenvolvimento, Paper, Demo e integração controlada. Não existe garantia de lucro ou crescimento de ativos. A execução em conta real não é ativada automaticamente e não deve ser testada com credenciais reais nesta branch. Antes de uso real, ainda são necessárias validação da conta/broker, supervisão operacional, revisão de segurança, limites de capital, reconciliação em ambiente real e aprovação explícita.

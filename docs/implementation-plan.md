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

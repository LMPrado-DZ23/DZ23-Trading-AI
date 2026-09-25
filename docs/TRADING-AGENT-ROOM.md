# Sala de agentes de trading

A agência possui **15 agentes especializados de trading** executáveis em paralelo no control plane, com um pool limitado de **16 workers**. A concorrência é voltada a análise, evidência, risco, planejamento e reconciliação em Paper/Testnet; nenhum agente individual envia ordens Live.

| Grupo | Agentes | Função |
| --- | --- | --- |
| Mercado | Market Scanner, Sentiment Analyst, Fundamental Analyst, Macro Analyst, Alternative Data Analyst | Coletar e comparar sinais e fontes |
| Técnica e quantitativa | Technical Analyst, Quant Researcher, Strategy Agent | Formular hipóteses, indicadores e estratégias |
| Risco e governança | Risk Advisor, Trading Compliance | Bloquear violações de mandato e risco |
| Portfólio e execução | Portfolio Manager, Execution Planner, Order Book Analyst | Analisar alocação, liquidez e planos Paper/Testnet |
| Operação | Trade Journal Agent, Reconciliation Agent | Registrar, reconciliar e detectar divergências |

## Regra de coordenação

Os agentes podem trabalhar simultaneamente, mas o fluxo mantém uma separação de funções: **risco pode bloquear, nunca aprovar a si próprio; execução apenas planeja; o roteador governado é o único caminho para Paper/Testnet; Live permanece bloqueado**. A aprovação humana continua obrigatória para ações sensíveis.

O runtime utiliza `ThreadPoolExecutor` com limite configurável no construtor, atualmente 16 no serviço. O executor aplica exclusão mútua por runtime, idempotência por tenant, dependências tenant-scoped e claim durável quando o store SQLite está habilitado.

## Operação

O worker contínuo não inicia automaticamente. O operador inicia, consulta e encerra por tenant usando:

- `POST /v1/agency/worker/start`
- `GET /v1/agency/worker/status`
- `POST /v1/agency/worker/stop`

Antes de iniciar com tarefas pendentes, o serviço valida a configuração LLM do tenant. Falhas permanentes deixam o worker em estado `degraded`, com backoff e mensagem de erro redigida.

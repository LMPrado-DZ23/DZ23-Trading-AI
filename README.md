# TradingAgents DZ23 — análise de trading com IA

Múltiplos agentes de IA analisam um ativo (ações ou cripto), debatem e
entregam uma decisão estruturada. O núcleo analítico e os controles de
evidência/transporte possuem uma suíte adversarial extensa.

## Estado desta branch

O endpoint legado de ordem foi **desabilitado**. Ele aceitava símbolo, lado e
quantidade sem um mandato de carteira e não oferecia idempotência/reconciliação
suficientes. Nenhuma pessoa deve usar a v1.1.0 para operar dinheiro real.

O trabalho A5 transforma o projeto em uma plataforma SaaS multiusuário com
suitability, perfis de risco, carteira, sizing determinístico, ledger,
aprovações e execução CCXT reconciliada. Consulte:

- [`design A5`](docs/specs/2026-09-08-a5-saas-trading-platform-design.md)
- [`plano de execução`](docs/plans/2026-09-08-a5-execution-plan.md)
- [`estado da missão`](audit/AUTONOMOUS_MISSION_STATE.md)

## Segurança preservada
- O contrato Gate A cobre redação de segredos, envelopes de evidência,
  política de transporte e gate fail-closed.
- A linha de base reproduzida contém 897 testes aprovados, 2 ignorados e
  71 subtests.
- Esse núcleo não equivale a autorização financeira. LIVE permanece bloqueado
  até os gates objetivos do plano A5 e auditoria independente.

## Créditos
Fork endurecido de [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
(Apache-2.0 — ver `LICENSE`). Pesquisa original: TradingAgents:
Multi-Agents LLM Financial Trading Framework.

> ⚠️ O sistema não garante lucro ou ausência de perdas. Nada aqui é
> recomendação de investimento.

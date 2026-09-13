# ADR 0003 — Coordenação multiagente, multicarteria e multicorretora

- Status: aceita para implementação; não habilita LIVE
- Data: 2026-09-12
- Decisor de produto: Prado
- Escopo: M6–M8

## Contexto

Um tenant precisa operar várias equipes de análise, carteiras e contas de
corretora ao mesmo tempo. Paralelismo irrestrito, porém, permitiria que duas
equipes reutilizassem o mesmo saldo, ultrapassassem limites agregados ou
enviassem a mesma ordem depois de um timeout.

Agentes LLM produzem somente votos, evidências e recomendações. Eles nunca
definem quantidade executável, aprovam risco, alteram limites, movimentam
credenciais ou enviam ordens diretamente.

## Decisão

Adotaremos paralelismo na inteligência e serialização nas fronteiras de
capital:

1. `AgentTeamRun` pode executar em paralelo por equipe, estratégia e carteira.
2. Um agregador determinístico produz uma única `TargetAllocation` por
   carteira, preservando todos os votos e conflitos.
3. O `TenantRiskGovernor` serializa reservas agregadas do tenant antes de
   qualquer reserva de carteira ou conta.
4. Um coordenador econômico serializa mutações por carteira.
5. Um dispatcher com lease e fencing token monotônico serializa chamadas de
   rede por conta externa. Contas distintas podem executar simultaneamente.
6. Uma conta externa pertence a uma única carteira ativa na primeira versão;
   uma carteira pode possuir várias contas e corretoras.
7. `UNKNOWN` após timeout mantém as reservas e bloqueia retry ou reroute até a
   reconciliação provar o resultado na corretora.

## Agregados persistidos

- `AgentTeamDefinition`, `AgentTeamVersion`, `AgentVote`, `TeamDecision`;
- `PortfolioAccount`, `PortfolioAccountSnapshot`;
- `TenantRiskPolicy`, `TenantRiskState`, `GlobalRiskDecision`,
  `RiskReservationLine`;
- `RebalanceProposal`, `ExecutionApproval`, `ExecutionIntentV2`,
  `ExecutionLease`, `ExecutionOutbox`;
- `BrokerOrderObservation`, `BrokerFillObservation`,
  `ReconciliationRun` e `ReconciliationItem`.

Todos repetem `tenant_id`; relações financeiras repetem também `portfolio_id`
e `account_id`. FKs compostas, RLS forçado e portas de operação validam o
escopo no PostgreSQL. Eventos, decisões, intents, fills e ledger são
append-only.

## Ordem global de locks

Toda operação de execução, cancelamento e reconciliação usa exatamente:

1. `tenant_risk_state`;
2. carteiras ordenadas por UUID;
3. conexões ordenadas por UUID;
4. contas ordenadas por UUID;
5. reservas de risco e caixa ordenadas por UUID;
6. intents, outbox e ordens ordenados por UUID;
7. posições ordenadas por `(account_id, symbol)`;
8. fills e linhas de ledger.

Nenhuma transação ou lock permanece aberto durante uma chamada de rede. O
fluxo é `lock → validar → reservar e gravar outbox/fence → commit → rede → nova
transação com o mesmo fence`.

## Idempotência e reconciliação

- A chave persistida é HMAC server-side sobre tenant, operação, agregado,
  request digest e chave bruta; a chave bruta não é persistida.
- `client_order_id` deriva do digest imutável da ordem-filho, nunca de worker ou
  job.
- Reuso da chave com payload diferente falha fechado.
- Fills duplicados, atrasados ou fora de ordem não duplicam ledger.
- Worker com lease expirado não escreve usando fencing token antigo.
- Divergência de saldo, ordem ou fill abre hold/circuit breaker persistente.

## Consequências

O throughput máximo de uma mesma conta é deliberadamente limitado. O sistema
ganha paralelismo real entre equipes, carteiras e contas sem enfraquecer os
limites de capital. Sharding futuro por grupo de risco só poderá ocorrer com
provas de concorrência equivalentes.

## Gate mínimo

- duas equipes na mesma carteira não reutilizam caixa ou risco;
- duas carteiras não ultrapassam conjuntamente o limite do tenant;
- duas contas distintas executam em paralelo sem misturar ledger;
- accepted-then-timeout produz uma única ordem e reconcilia sem novo POST;
- partial fills duplicados e fora de ordem convergem exatamente uma vez;
- crash em cada fronteira do outbox converge após restart;
- testes PostgreSQL reais demonstram RLS, fencing, ausência de deadlock e
  ordem global de locks;
- SHADOW não envia ordem; PAPER nunca usa conta live; LIVE segue bloqueado por
  promoção, MFA e autorização humana vinculada ao digest.

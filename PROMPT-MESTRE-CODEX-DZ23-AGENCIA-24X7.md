# Prompt mestre para o Codex — DZ23 Autonomous Trading Agency 24/7

Você é o arquiteto e implementador principal de uma plataforma de trading algorítmico multiagente, distribuída, observável e segura.

Sua missão é evoluir o repositório existente:

```text
https://github.com/LMPrado-DZ23/tradingagents-dz23
```

para uma plataforma chamada:

```text
DZ23 Autonomous Trading Agency
```

A plataforma deve permitir que múltiplos agentes especializados pesquisem mercados, desenvolvam estratégias, participem de comitês, proponham operações, passem por gestão de risco, executem em paper/shadow/demo e, somente depois de gates formais, possam operar em contas reais de múltiplas corretoras.

## 1. Premissa fundamental

Não prometa crescimento da carteira, lucro ou preservação de capital. Nenhum agente, modelo de IA, backtest, taxa de acerto ou sistema autônomo garante retorno futuro. O objetivo do software é construir uma infraestrutura de pesquisa, decisão, risco, execução, monitoramento e governança.

O sistema não pode assumir que “autonomia” significa ausência de controle. Autonomia operacional deve significar que o sistema executa tarefas aprovadas dentro de políticas explícitas, limites de risco, orçamento, kill switch, auditoria e reconciliação.

A implementação deve iniciar e permanecer, por padrão, em:

```text
LIVE_TRADING=false
PAPER_TRADING=true
SHADOW_TRADING=false
REAL_MONEY_EXECUTION=false
```

Nenhum código desta missão pode enviar ordem real por padrão.

## 2. Objetivo funcional

Construir uma agência virtual que represente visualmente um escritório de trading real e que tenha operação efetiva por trás:

```text
Trading Floor
  → desks por mercado
  → agentes especializados
  → reuniões e comitês
  → sinais estruturados
  → Risk Engine determinístico
  → Portfolio Manager
  → Execution Manager
  → monitoramento
  → reconciliação
  → revisão pós-trade
  → memória e aprendizado controlado
```

A agência deve operar continuamente, com workers persistentes, filas, heartbeats, recuperação após falha e monitoramento 24/7. A operação contínua não pode depender de uma sessão interativa de terminal, de um navegador aberto ou de uma animação frontend.

## 3. Estado atual do projeto

Primeiro inspecione o repositório real. Não confie apenas nesta descrição.

A base conhecida contém:

- `TradingAgentsGraph` em `tradingagents/graph/trading_graph.py`;
- workflow LangGraph em `tradingagents/graph/setup.py`;
- analistas de mercado, sentimento, notícias e fundamentos;
- bull researcher, bear researcher e research manager;
- trader e debatedores de risco;
- portfolio manager;
- `AgentState`, `InvestDebateState` e `RiskDebateState`;
- memória e checkpoints;
- ferramentas de dados;
- ponte CCXT em `tradingagents/brokers/ccxt_bridge.py`;
- gate de segurança `assert_secure_for_capital`;
- journal e idempotência de ordens;
- testes de segurança, agentes, dados, configuração e broker;
- configuração Docker.

Preserve o núcleo atual quando possível. Não reescreva tudo sem necessidade. Faça mudanças incrementais, compatíveis, testadas e documentadas.

## 4. Processo obrigatório antes de codificar

Na Fase 0:

1. Clone ou abra o repositório.
2. Leia `README.md`, `HANDOFF.md`, `DEPLOY.md`, `pyproject.toml`, `docker-compose.yml`, `.env.example` e todos os módulos ligados a agentes, estado, grafo, dados, brokers e segurança.
3. Mapeie a árvore real do projeto.
4. Identifique o frontend existente, se houver.
5. Verifique todos os entrypoints e serviços.
6. Execute os testes existentes.
7. Se uma dependência estiver ausente, registre a ausência e instale somente a dependência de desenvolvimento necessária para verificar o projeto.
8. Identifique conflitos entre documentação e código.
9. Crie `docs/implementation-plan.md`.
10. Não implemente live trading nesta fase.

Ao final de cada fase, mostre:

```text
FASE
OBJETIVO
ARQUIVOS CRIADOS
ARQUIVOS ALTERADOS
TESTES EXECUTADOS
RESULTADO DOS TESTES
RISCOS
PENDÊNCIAS
PRÓXIMA FASE
```

Não diga que algo foi concluído se não foi executado ou testado.

## 5. Arquitetura-alvo

Implemente uma arquitetura separando control plane, data plane, execution plane e frontend.

```text
┌──────────────────────────────────────────────────────────────┐
│ DZ23 Trading Floor / War Room / Chat / Admin UI             │
└───────────────────────────────┬──────────────────────────────┘
                                │ REST + WebSocket/SSE
┌───────────────────────────────▼──────────────────────────────┐
│ Control Plane API                                             │
│ usuários · desks · agentes · estratégias · políticas · runs  │
└─────┬───────────────┬──────────────┬─────────────────────────┘
      │               │              │
┌─────▼────┐   ┌──────▼─────┐  ┌─────▼────────────────────────┐
│ Agent    │   │ Risk       │  │ Strategy Lifecycle            │
│ Runtime  │   │ Engine     │  │ research→champion             │
└─────┬────┘   └──────┬─────┘  └──────────────┬─────────────────┘
      │               │                       │
┌─────▼───────────────▼───────────────────────▼─────────────────┐
│ Event Bus + Event Log + Versioned Domain Contracts            │
└─────┬───────────────────────┬────────────────────────────────┘
      │                       │
┌─────▼──────────┐    ┌───────▼────────────────────────────────┐
│ Data Plane     │    │ Execution Plane                         │
│ market/news/   │    │ Paper · Shadow · Demo · Multi-broker    │
│ macro/features │    │ adapters · OMS · reconciliation         │
└─────┬──────────┘    └──────────────┬─────────────────────────┘
      │                              │
┌─────▼──────────────────────────────▼─────────────────────────┐
│ PostgreSQL / Redis / Object Storage / Metrics / Audit Storage  │
└──────────────────────────────────────────────────────────────┘
```

A interface visual nunca deve ser a fonte de verdade. O browser deve consumir read models e eventos.

## 6. Domínio de desks e agentes

Implemente entidades reais para:

```text
Agency
Desk
Agent
Strategy
Account
Broker
Instrument
MarketSnapshot
Signal
Committee
TradeDecision
RiskDecision
Order
Fill
Position
PortfolioSnapshot
AuditEvent
Incident
```

Desks iniciais:

```text
Crypto Desk
Forex Desk
Futures Desk
Equities Desk
Macro Desk
Research Desk
Risk Desk
Execution Desk
Monitoring Desk
```

Agentes mínimos:

```text
ChiefTraderAgent
MarketScannerAgent
RegimeDetectorAgent
TechnicalAnalystAgent
PriceActionAgent
OrderFlowAgent
SentimentAgent
NewsAgent
MacroAgent
FundamentalsAgent
StrategyAgent
PortfolioManagerAgent
RiskAdvisorAgent
ExecutionPlannerAgent
TradeMonitorAgent
ReconciliationAgent
PostTradeReviewerAgent
StrategyLifecycleAgent
LearningMemoryAgent
IncidentResponseAgent
```

O `ChiefTraderAgent` coordena e prioriza. Ele não deve enviar ordens diretamente.

O `Risk Engine` não deve ser um LLM.

O `Execution Manager` só pode executar uma decisão já aprovada por política e risco.

## 7. Reuniões entre agentes

As reuniões devem ser workflows estruturados e rastreáveis, não apenas conversas infinitas.

Tipos de reunião:

```text
MarketOpenBriefing
RegimeReview
StrategyCommittee
PreTradeReview
PortfolioAllocationMeeting
RiskReview
IncidentWarRoom
EndOfDayReview
PostTradeReview
StrategyPromotionReview
```

Cada reunião deve possuir:

```text
meeting_id
meeting_type
run_id
desk_id
participants
agenda
inputs
agent_votes
dissenting_views
consensus
risk_summary
decision
created_at
closed_at
status
```

Estados:

```text
SCHEDULED
RUNNING
WAITING_FOR_AGENT
QUORUM_REACHED
DECISION_RECORDED
CANCELLED
FAILED
```

Regras:

- reunião possui limite de tempo;
- reunião possui limite de tokens/custo;
- agentes não podem chamar outros agentes indefinidamente;
- máximo de participantes por tipo de reunião;
- quorum configurável;
- opiniões divergentes preservadas;
- consenso não substitui o Risk Engine;
- falha de agente não deve ser escondida;
- ausência de evidência deve reduzir confiança ou bloquear a decisão.

## 8. Contratos tipados

Use dataclasses imutáveis ou Pydantic, conforme as dependências já presentes. Não usar texto livre ou dicionário solto como contrato principal.

### MarketSnapshot

```text
snapshot_id
instrument_id
venue
bid
ask
last
volume
spread
observed_at
available_at
source_id
sequence
timezone
```

### Evidence

```text
evidence_id
source_id
source_type
instrument_id
content_hash
observed_at
available_at
valid_until
provenance
payload_reference
```

Dados sem `available_at` conhecido devem ser excluídos do histórico.

### Signal

```text
signal_id
strategy_id
agent_id
desk_id
instrument_id
side
regime
confidence
thesis
evidence_ids
created_at
expires_at
status
```

### TradeDecision

```text
decision_id
run_id
strategy_id
desk_id
instrument_id
venue
account_id
side
order_type
entry_price_reference
stop_loss
take_profit
risk_amount
risk_pct_equity
quantity
time_in_force
signal_expiry
confidence
regime
evidence_ids
agent_votes
meeting_id
created_at
status
```

### RiskDecision

```text
risk_decision_id
decision_id
status
approved_quantity
approved_risk_amount
reason_codes
limits_checked
portfolio_snapshot_id
engine_version
created_at
```

### Order, Fill, Position

Devem suportar:

- ordem de mercado;
- limit;
- stop;
- stop-loss;
- take-profit;
- cancelamento;
- rejeição;
- partial fill;
- preço médio;
- comissão;
- swap;
- slippage;
- posição netting;
- posição hedging;
- timestamps de submissão, aceitação, fill e reconciliação.

## 9. Event Log e barramento

Implemente um event log append-only com:

```text
event_id
event_type
schema_version
sequence
aggregate_type
aggregate_id
run_id
desk_id
agent_id
account_id
strategy_id
occurred_at
available_at
actor
payload_hash
payload_redacted
previous_event_hash
```

Eventos mínimos:

```text
agent.status.changed
desk.status.changed
meeting.started
meeting.vote.recorded
meeting.closed
market.snapshot.created
signal.created
risk.decision.created
order.submitted
order.accepted
order.rejected
order.cancelled
fill.received
position.updated
pnl.updated
strategy.promoted
strategy.demoted
risk.locked
broker.health.changed
incident.created
chat.message
```

Requisitos:

- ordenação;
- replay;
- snapshot;
- idempotência;
- detecção de corrupção;
- hash encadeado;
- deduplicação;
- retenção configurável;
- auditoria de leitura e escrita;
- nenhum trade sem eventos correspondentes.

## 10. Risk Engine

Implemente um motor de risco determinístico, independente dos agentes.

Limites obrigatórios:

- risco por trade;
- risco por estratégia;
- risco por agente;
- risco por desk;
- risco por conta;
- risco agregado entre contas;
- exposição por instrumento;
- exposição líquida e bruta;
- concentração por moeda, setor, broker e venue;
- correlação;
- margem;
- perda diária;
- perda semanal;
- drawdown;
- número máximo de posições;
- número máximo de ordens;
- spread;
- slippage;
- latência;
- idade do feed;
- eventos macroeconômicos;
- estado da estratégia;
- estado da conta;
- estado do broker;
- kill switch;
- circuit breaker.

O sizing deve ser determinístico:

```text
risk_amount = equity * approved_risk_pct
stop_distance = abs(entry_price - stop_loss)
raw_quantity = risk_amount / (stop_distance * contract_value)
approved_quantity = min(max(raw_quantity, min_qty), max_qty)
```

O sistema deve recalcular e limitar qualquer quantidade proposta por agente.

Saídas:

```text
APPROVE
REDUCE
WAIT
REJECT
```

Nenhum texto gerado por LLM pode contornar o resultado do Risk Engine.

## 11. Multi-corretora e adapters

Criar uma interface uniforme:

```python
class BrokerAdapter(Protocol):
    def capabilities(self): ...
    def healthcheck(self): ...
    def get_accounts(self): ...
    def get_instruments(self, query): ...
    def get_quote(self, instrument_id): ...
    def get_positions(self, account_id): ...
    def get_open_orders(self, account_id): ...
    def submit(self, order_request): ...
    def cancel(self, order_id): ...
    def reconcile(self, account_id): ...
```

Implementar primeiro:

```text
PaperAdapter
ShadowAdapter
CCXTAdapter
MT5Adapter
```

Depois preparar adapters adicionais conforme APIs autorizadas e documentadas. Não alegar suporte a “todas as corretoras” sem testar cada adapter e documentar sua capacidade.

Cada broker deve declarar:

```text
supports_market_orders
supports_limit_orders
supports_stop_orders
supports_trailing_stop
supports_hedging
supports_netting
supports_partial_fill
supports_client_order_id
supports_sandbox
supports_streaming
supports_reconciliation
```

Um broker sem sandbox não deve ser usado em testnet como se tivesse sandbox.

## 12. MetaTrader 5

Usar a biblioteca oficial `MetaTrader5` como integração nesta fase, encapsulada em `MT5Adapter` e `MT5ExecutionWorker`.

Nunca permitir que um agente ou frontend chame `order_send` diretamente.

Requisitos:

- initialize;
- login seguro;
- terminal_info;
- account_info;
- versão do terminal;
- seleção de símbolo;
- tick e barras;
- Market Depth quando disponível;
- order_calc_margin;
- order_calc_profit;
- order_check antes de envio;
- envio somente após aprovação;
- leitura de ordens;
- leitura de posições;
- histórico de ordens e deals;
- heartbeat;
- reconciliação;
- suporte explícito a hedging/netting;
- conta demo por padrão;
- bloqueio de conta real nesta entrega.

Nunca colocar credenciais em logs, eventos, browser, fixtures ou mensagens de agentes.

## 13. Execução e OMS

Criar um Order Management System com estados:

```text
CREATED
RISK_PENDING
RISK_APPROVED
SUBMITTING
SUBMITTED
ACCEPTED
PARTIALLY_FILLED
FILLED
CANCEL_PENDING
CANCELLED
REJECTED
EXPIRED
RECONCILIATION_REQUIRED
UNKNOWN
```

Requisitos:

- client order ID determinístico;
- retries limitados;
- não repetir automaticamente em estado desconhecido sem reconciliação;
- timeout explícito;
- cancelamento auditado;
- stop-loss obrigatório conforme política;
- reconciliação periódica;
- detecção de posição inesperada;
- circuit breaker para divergência;
- impossibilidade de duplicar ordem por retry.

## 14. Operação 24/7

Criar workers independentes para:

```text
MarketDataWorker
AgentSchedulerWorker
MeetingWorker
SignalWorker
RiskWorker
ExecutionWorker
BrokerHeartbeatWorker
ReconciliationWorker
PnLWorker
PostTradeWorker
StrategyLifecycleWorker
IncidentWorker
```

Cada worker deve possuir:

```text
healthcheck
heartbeat
retry policy
backoff
shutdown gracioso
idempotência
métricas
logs estruturados
dead letter queue
```

A operação 24/7 deve sobreviver a:

- reinício do processo;
- reinício da máquina;
- perda de rede;
- broker indisponível;
- feed atrasado;
- LLM indisponível;
- Redis indisponível;
- banco indisponível;
- evento duplicado;
- evento fora de ordem;
- fill parcial;
- resposta desconhecida;
- relógio divergente;
- vazamento de memória;
- estratégia travada;
- drawdown excedido.

## 15. Estratégias e lifecycle

Criar registry com:

```text
strategy_id
name
version
artifact_hash
owner_agent
desk
universe
timeframes
parameters
required_data
regime
cost_model
risk_profile
status
created_at
```

Estados:

```text
RESEARCH
BACKTEST
WALK_FORWARD
PAPER
SHADOW
CHALLENGER
CHAMPION
PROBATION
DISABLED
```

Uma estratégia só pode receber orçamento conforme estado, evidência e política.

Avaliar:

- PnL líquido;
- expectativa;
- profit factor;
- drawdown;
- duração do drawdown;
- estabilidade;
- out-of-sample;
- walk-forward;
- sensibilidade a spread, slippage e comissão;
- performance por regime;
- número de trades;
- taxa de sinais inválidos;
- rejeição pelo risco;
- divergência paper/shadow;
- correlação com estratégias existentes.

Não promover por win rate isolado.

## 16. Backtest e walk-forward

Implementar ou preservar backtesting sem look-ahead bias.

Cada dado precisa possuir:

```text
observed_at
available_at
source_id
```

O relatório deve separar:

```text
Backtest
Walk-Forward
Paper
Shadow
Live
```

Nunca misturar as curvas.

Adicionar custos, spread, slippage, comissão e latência.

Gerar manifesto reproduzível contendo:

```text
dataset_hash
strategy_id
strategy_version
parameters
seed
cost_model
time_window
code_revision
```

## 17. Trading Floor visual e escritório virtual

Construir ou integrar uma interface visual inspirada no vídeo fornecido:

```text
Trading Floor
War Room
Committee Room
Agents
Desks
Strategies
Positions
Orders
Backtesting
Walk-Forward
Paper
Shadow
Risk
Portfolio
Brokers
MT5
Audit Logs
```

Cada mesa deve representar uma entidade real do backend.

Estados visuais:

```text
OPERATING
ANALYZING
WAITING_SETUP
BACKTEST
WALK_FORWARD
PAPER_TRADING
SHADOW
TRAINING
PAUSED
RISK_LOCKED
ERROR
DISCONNECTED
```

A interface pode reutilizar Claw3D ou OpenClaw Office como referência/camada visual, mas:

- não copiar código sem verificar licença;
- não misturar as duas stacks inicialmente;
- não usar o frontend como fonte de verdade;
- não usar animação como prova de execução;
- não colocar credenciais no browser;
- não enviar ordens diretamente do frontend;
- usar snapshot inicial e eventos incrementais;
- separar estado financeiro de presença e chat.

Componentes mínimos:

```text
TradingFloor
DeskCard
AgentStation
StrategyCard
CommitteeRoom
WarRoom
PositionPanel
OrderBlotter
RiskPanel
PnLPanel
StrategyLeaderboard
BacktestPanel
WalkForwardPanel
BrokerHealthPanel
AuditTimeline
AgentChat
```

Cada visual deve mostrar claramente se o dado é:

```text
DEMO
PAPER
SHADOW
LIVE
```

A seção LIVE deve ficar bloqueada até aprovação de todos os gates.

## 18. Chat e comunicação

Implementar chat de duas camadas:

### Chat operacional

Mensagens de agentes, reuniões, evidências e conclusões.

### Comandos transacionais

Objetos estruturados para decisão, risco, ordem, cancelamento e reconciliação.

Nunca confundir os dois.

Perguntas suportadas:

```text
Por que esta operação foi proposta?
Quais agentes discordaram?
Quais evidências existiam naquele momento?
Por que o risco foi aprovado?
Por que a ordem foi rejeitada?
Qual foi o slippage?
Qual é a exposição da agência?
Qual estratégia recebeu ou perdeu orçamento?
```

Se não houver evidência, responder:

```text
Não há evidência registrada suficiente para responder.
A explicação não será inventada.
```

## 19. Capital allocation

O Portfolio Manager deve distribuir orçamento apenas dentro de limites determinados pelo Risk Engine.

Implementar:

- orçamento por desk;
- orçamento por estratégia;
- capital virtual por ambiente;
- risco agregado;
- correlação;
- rebalanceamento;
- limites de concentração;
- suspensão por drawdown;
- redução de capital por degradação;
- promoção por evidência.

O sistema nunca deve “fazer a carteira crescer” como promessa. Deve registrar retorno, perda, risco, custos e incerteza.

## 20. Segurança e governança

Implementar:

- autenticação;
- autorização por usuário, desk, conta e capacidade;
- separação de credenciais;
- secrets manager ou variáveis seguras;
- redaction fail-closed;
- audit log;
- rate limits;
- aprovação de comandos sensíveis;
- kill switch;
- circuit breaker;
- proteção contra replay;
- proteção contra prompt injection em evidências;
- isolamento de ferramentas;
- allowlist de brokers;
- limites de orçamento;
- bloqueio de conta não autorizada;
- estado de segurança persistente;
- verificação de configuração no boot.

Nunca usar o LLM para interpretar autorização, credencial ou política de segurança.

## 21. Testes obrigatórios

Criar e executar testes para:

### Contratos

- campos obrigatórios;
- enums;
- versões;
- timestamps;
- evidências;
- schemas inválidos.

### Agentes e reuniões

- quorum;
- timeout;
- agente indisponível;
- divergência;
- deduplicação;
- limite de rounds;
- limite de tokens;
- decisão registrada.

### Risk Engine

- limites por trade, agente, estratégia, desk e conta;
- drawdown;
- perda diária;
- exposição;
- correlação;
- margem;
- spread;
- feed velho;
- NaN, infinito, valores negativos e tipos errados;
- kill switch;
- circuit breaker.

### OMS e adapters

- ordem duplicada;
- retry;
- timeout;
- fill parcial;
- rejeição;
- cancelamento;
- estado desconhecido;
- reconciliação;
- broker desconectado;
- conta errada;
- símbolo inválido.

### MT5

Mockar a biblioteca nos testes. Não exigir terminal real para a suíte.

Testar:

- `order_check` antes de `order_send`;
- recusa sem Risk Approval;
- recusa em conta real nesta fase;
- heartbeat;
- posições hedging/netting;
- histórico;
- reconciliação;
- perda de conexão.

### Backtest

- ausência de look-ahead;
- `available_at`;
- janela forward;
- custos;
- seed;
- manifesto;
- dados futuros excluídos.

### 24/7

- restart;
- replay;
- reconnect;
- backoff;
- worker travado;
- dead letter;
- alerta;
- corrupção de evento;
- falha de banco;
- falha de Redis.

### Frontend

- Trading Floor renderiza estados reais;
- mesa bloqueada aparece como bloqueada;
- ordem não aparece como preenchida antes do fill;
- Paper não aparece como Live;
- reconexão mantém consistência;
- eventos duplicados não duplicam cards;
- acessibilidade e keyboard navigation.

## 22. Observabilidade

Implementar:

- logs JSON estruturados;
- métricas Prometheus ou equivalente;
- traces por `run_id`, `decision_id`, `order_id` e `event_id`;
- latência de market data;
- latência de agente;
- latência de risco;
- latência de envio;
- slippage;
- reconciliação;
- PnL;
- drawdown;
- custo de LLM;
- tokens;
- taxa de erro;
- eventos atrasados;
- workers offline;
- broker health;
- alertas operacionais.

## 23. Deploy 24/7

Preparar:

- Docker Compose de desenvolvimento;
- configuração de produção documentada;
- workers separados;
- healthcheck;
- readiness;
- liveness;
- migrations;
- backup;
- restore;
- rotação de logs;
- retenção de eventos;
- atualização segura;
- rollback;
- monitoramento;
- alertas;
- operação sem navegador.

Não alegar que Docker em Linux sozinho executa o terminal MT5 se o ambiente exigir Windows. Documentar a topologia real do worker MT5.

## 24. Fases de implementação

Execute nesta ordem:

```text
Fase 0 — auditoria, baseline e plano
Fase 1 — contratos de domínio e schemas
Fase 2 — event log, replay e auditoria
Fase 3 — Risk Engine determinístico
Fase 4 — Paper Adapter, OMS e reconciliação
Fase 5 — reuniões e comunicação estruturada entre agentes
Fase 6 — integração dos agentes atuais aos contratos
Fase 7 — lifecycle de estratégias e alocação
Fase 8 — backtest e walk-forward
Fase 9 — MT5 Adapter em conta demo
Fase 10 — demais adapters multi-corretora
Fase 11 — Trading Floor, War Room e Committee Room
Fase 12 — workers 24/7 e observabilidade
Fase 13 — shadow trading
Fase 14 — auditoria final e preparação de gates
```

O live trading real não deve ser ativado automaticamente nesta missão. A Fase 14 deve produzir um documento de requisitos e checklist para futura liberação, não executar ordens reais.

## 25. Gates para futura operação real

Antes de qualquer habilitação futura de conta real, exigir aprovação explícita do proprietário e evidência de:

```text
1. Testes automatizados passando.
2. Backtest reproduzível.
3. Walk-forward fora da amostra.
4. Paper Trading prolongado.
5. Shadow Trading prolongado.
6. Conta demo reconciliada.
7. Nenhuma divergência não explicada.
8. Risk Engine testado adversarialmente.
9. Kill switch testado.
10. Backup e restore testados.
11. Secrets protegidos.
12. Credenciais separadas por broker.
13. Limites de capital definidos.
14. Auditoria de segurança concluída.
15. Monitoramento e alertas ativos.
16. Runbook de incidentes aprovado.
17. Política de desligamento automático.
18. Aprovação humana explícita para cada conta, broker e orçamento.
```

Mesmo após aprovação, a capacidade real deve possuir:

- orçamento mínimo;
- limite diário;
- limite de perda;
- limite de exposição;
- conta separada;
- kill switch externo;
- auditoria;
- revisão periódica;
- desligamento automático em caso de inconsistência.

## 26. Documentação obrigatória

Criar ou atualizar:

```text
README.md
docs/implementation-plan.md
docs/architecture.md
docs/domain-contracts.md
docs/event-model.md
docs/agent-meetings.md
docs/risk-engine.md
docs/execution-oms.md
docs/broker-adapters.md
docs/mt5.md
docs/backtesting.md
docs/strategy-lifecycle.md
docs/trading-floor.md
docs/operations-24x7.md
docs/security.md
docs/live-readiness-gates.md
docs/runbooks/restart.md
docs/runbooks/broker-disconnected.md
docs/runbooks/reconciliation-failure.md
docs/runbooks/risk-lock.md
docs/runbooks/kill-switch.md
```

## 27. Comandos finais de verificação

Executar, quando aplicável:

```bash
python -m compileall tradingagents cli service
python -m pytest -q
ruff check .
```

Se houver frontend:

```bash
npm run build
npm run lint
npm test
```

Se algum comando não puder ser executado, informar:

```text
comando
motivo
resultado
impacto
próximo passo
```

## 28. Critérios de aceite

A entrega será aceita somente se:

1. O sistema iniciar sem credenciais reais.
2. Paper Trading for o modo padrão.
3. O núcleo atual continuar funcionando.
4. Toda decisão tiver `decision_id` e evidências.
5. Toda ordem passar por Risk Engine.
6. Toda ordem puder ser reconciliada.
7. Nenhuma ordem duplicar por retry.
8. O Event Log permitir replay.
9. Reuniões entre agentes forem estruturadas e limitadas.
10. Falhas de agentes forem visíveis.
11. Brokers declararem capacidades reais.
12. MT5 usar conta demo e mocks nos testes.
13. Backtest separar períodos e não usar dados futuros.
14. Estratégias possuírem lifecycle.
15. A UI refletir eventos reais.
16. Paper, Shadow e Live forem visualmente separados.
17. Workers possuírem heartbeat e recovery.
18. O sistema puder operar sem navegador aberto.
19. Não houver caminho acidental para dinheiro real.
20. A documentação informar claramente o que foi testado e o que permanece pendente.

## 29. Regra final

Não construa uma simulação visual que pareça uma agência.

Construa:

```text
um motor financeiro controlado,
com agentes especializados,
reuniões rastreáveis,
risk engine determinístico,
execução reconciliada,
workers 24/7,
e uma Trading Floor visual que represente a verdade do sistema.
```

A prioridade é segurança, auditabilidade, resiliência e controle de risco. A estética do escritório deve servir à observabilidade, não mascarar incerteza ou criar a impressão de lucro garantido.

Comece agora pela Fase 0. Não habilite live trading. Não solicite credenciais reais. Não envie ordens reais.


## 30. Integração visual com Claw3D/OpenClaw Office

Use `https://github.com/iamlukethedev/Claw3D` como principal referência visual e possível base da Trading Floor 3D. Use `https://github.com/wickedapp/openclaw-office` apenas como referência complementar ou alternativa para a interface isométrica.

Não misture as duas stacks visuais na primeira implementação. Escolha um runtime visual principal, preferencialmente Claw3D, e mantenha o motor TradingAgents DZ23 separado.

O TradingAgents DZ23 deve permanecer como fonte autoritativa para:

- market data;
- agentes;
- sinais;
- decisões;
- Risk Engine;
- ordens;
- fills;
- posições;
- PnL;
- estratégias;
- backtest;
- walk-forward;
- paper trading;
- shadow trading;
- auditoria;
- reconciliação.

Criar um `TradingRuntimeAdapter` entre o DZ23 e a interface visual.

O adapter deve fornecer snapshot inicial e eventos incrementais versionados via WebSocket ou SSE.

Eventos mínimos:

```text
agent.status.changed
desk.status.changed
signal.created
committee.vote.recorded
risk.decision.created
order.submitted
order.accepted
order.rejected
fill.received
position.updated
pnl.updated
strategy.promoted
strategy.demoted
risk.locked
broker.health.changed
chat.message
```

A interface deve consumir eventos oficiais. Não inferir ordens, fills, posições ou PnL a partir de textos, timers, animações, transcripts ou mensagens entre agentes.

Criar no frontend:

```text
TradingFloor
TradingDesk
StrategyStation
PositionPanel
OrderBlotter
RiskPanel
PnLPanel
CommitteeRoom
WarRoom
StrategyLeaderboard
BacktestPanel
WalkForwardPanel
BrokerHealthPanel
AuditTimeline
```

Começar com uma integração read-only usando dados `DEMO` ou `PAPER`. Depois adicionar Paper Trading, sempre passando pelo Risk Engine.

Não permitir que o frontend envie ordem diretamente para o broker. Qualquer comando deve passar pelo backend DZ23, autenticação, autorização, idempotência, validação de risco, auditoria e reconciliação.

Não misturar as animações cinematográficas com o estado real. Uma estação só deve mostrar uma ordem como enviada, aceita ou preenchida quando existir o respectivo evento oficial.

Manter obrigatoriamente:

```text
LIVE_TRADING=false
PAPER_TRADING=true
REAL_MONEY_EXECUTION=false
```

O Codex deve auditar a licença, os copyrights, os assets, os modelos 3D, as fontes, as dependências e os plugins de Claw3D/OpenClaw Office antes de copiar qualquer código ou recurso visual. Preservar os avisos de licença aplicáveis.

A primeira entrega visual deve ser um vertical slice read-only com:

1. Trading Floor;
2. desks e estações de agentes;
3. snapshot inicial;
4. feed de eventos incrementais;
5. estados `ANALYZING`, `PAPER_TRADING`, `RISK_LOCKED`, `ERROR` e `DISCONNECTED`;
6. painel de estratégia;
7. painel de posições e ordens claramente marcado como `DEMO` ou `PAPER`;
8. War Room;
9. Committee Room;
10. AuditTimeline;
11. reconexão e deduplicação de eventos;
12. testes de que a UI não apresenta fill antes do evento `fill.received`.

Não habilitar execução real nesta fase e não criar uma rota frontend para envio direto de ordens.

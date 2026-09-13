# Prompt completo para o Codex — Evolução do TradingAgents DZ23 para uma Trading Agency Multiagente

Você é um engenheiro de software sênior especializado em sistemas distribuídos, trading algorítmico, controle de risco, execução de ordens, Python, LangGraph, MetaTrader 5 e segurança de sistemas financeiros.

Sua missão é evoluir o repositório abaixo para uma plataforma operacional de agentes trader, sem destruir o núcleo existente e sem permitir execução de dinheiro real por padrão.

```text
Repositório: LMPrado-DZ23/tradingagents-dz23
Diretório local esperado: /home/ubuntu/tradingagents-dz23
Branch de trabalho: feat/dz23-trading-agency
Idioma de documentação e comentários novos: português brasileiro, quando isso não prejudicar APIs ou convenções técnicas
```

## 1. Objetivo do produto

Transformar o atual framework de análise financeira multiagente em uma plataforma chamada **DZ23 Autonomous Trading Agency**, composta por:

- Trading Floor visual;
- War Room operacional;
- múltiplos desks por mercado;
- agentes especializados de pesquisa;
- estratégias versionadas e concorrentes;
- Chief Trader para coordenação;
- Risk Engine determinístico e independente de LLM;
- execução Paper, Shadow, MT5 e CCXT;
- reconciliação de ordens, fills e posições;
- backtesting e walk-forward;
- promoção e rebaixamento automático de estratégias;
- telemetria e auditoria completa;
- explicação das decisões baseada em evidências registradas antes da ordem;
- suporte posterior a execução live controlada.

O resultado não deve ser apenas uma interface com muitos agentes conversando. Deve ser um sistema operacional de trading com contratos tipados, eventos imutáveis, controles de risco e execução reconciliada.

## 2. Estado atual conhecido

O repositório atual já possui:

- `TradingAgentsGraph` em `tradingagents/graph/trading_graph.py`;
- workflow LangGraph em `tradingagents/graph/setup.py`;
- analistas de mercado, sentimento, notícias e fundamentos;
- bull researcher, bear researcher e research manager;
- trader e debatedores de risco agressivo, neutro e conservador;
- portfolio manager;
- `AgentState`, `InvestDebateState` e `RiskDebateState`;
- memória em `tradingagents/agents/utils/memory.py`;
- checkpoints do LangGraph;
- ferramentas de dados com yfinance, FRED, notícias e Polymarket;
- `CCXTBridge` em `tradingagents/brokers/ccxt_bridge.py`;
- gate de segurança `assert_secure_for_capital`;
- journal redigido e idempotência de ordens;
- testes de segurança, dados, agentes, configuração e broker.

Não reescreva o projeto inteiro. Preserve o comportamento atual e faça a evolução em módulos novos, com compatibilidade retroativa sempre que possível.

## 3. Regras de segurança obrigatórias

Estas regras são absolutas:

1. O sistema deve iniciar com:

```text
LIVE_TRADING=false
PAPER_TRADING=true
REAL_MONEY_EXECUTION=false
```

2. Nenhum teste pode enviar ordem real.

3. Nenhuma chave real deve ser solicitada, impressa, armazenada em logs ou incluída em fixtures.

4. O código novo não pode interpretar a saída textual de um LLM como autorização direta de ordem.

5. Toda ordem deve passar por um Risk Engine determinístico.

6. Toda ordem deve possuir `run_id`, `decision_id`, `strategy_id`, `client_order_id`, `schema_version` e evidências vinculadas.

7. O LLM pode sugerir sinal, tese, regime e níveis. O LLM não pode decidir sozinho tamanho, exposição, risco ou autorização final.

8. Falha de dados, terminal, broker, reconciliação, Risk Engine, heartbeat, schema ou política deve resultar em bloqueio fechado.

9. Não adicionar fallback silencioso de paper para live.

10. Não criar endpoint HTTP que permita ativar live apenas com `true`, query string ou variável desacompanhada de política explícita.

11. A capacidade live deve permanecer desabilitada nesta fase. Implementar somente interfaces, validações e testes de recusa.

12. Não usar dados futuros em backtest ou walk-forward.

13. Não registrar no log payloads contendo API keys, secrets, tokens, senhas ou credenciais MT5.

14. Ordens duplicadas devem ser evitadas por idempotência determinística.

15. O sistema deve continuar funcionando quando um agente ou fornecedor de dados falhar, mas deve marcar a execução como incompleta e bloquear a ordem quando a evidência exigida não estiver disponível.

## 4. Resultado esperado da primeira grande entrega

Implemente uma primeira versão funcional do núcleo operacional com estes componentes:

```text
tradingagents/domain/contracts.py
tradingagents/domain/enums.py
tradingagents/domain/errors.py
tradingagents/telemetry/events.py
tradingagents/telemetry/audit_log.py
tradingagents/risk/engine.py
tradingagents/risk/limits.py
tradingagents/risk/sizing.py
tradingagents/execution/protocol.py
tradingagents/execution/paper_adapter.py
tradingagents/execution/reconciliation.py
tradingagents/lifecycle/registry.py
tradingagents/lifecycle/promotion.py
tradingagents/mt5/adapter.py
tradingagents/mt5/worker.py
```

Se a estrutura existente for diferente, adapte os caminhos sem criar duplicidade de conceitos.

## 5. Processo de trabalho obrigatório

Antes de alterar código:

1. Leia `README.md`, `HANDOFF.md`, `DEPLOY.md`, `pyproject.toml`, `docker-compose.yml` e os módulos de broker, agentes, estado, grafo e configuração.
2. Execute uma inspeção da árvore do projeto.
3. Execute a suíte atual, se as dependências estiverem disponíveis.
4. Se uma dependência estiver ausente, registre claramente o bloqueio e instale apenas dependências de desenvolvimento necessárias, sem alterar o comportamento de produção silenciosamente.
5. Crie um plano em `docs/implementation-plan.md`.
6. Não implemente todas as fases em uma única mudança sem testes intermediários.
7. Faça commits lógicos ou, se commits não forem desejados, deixe cada fase separada e documentada.

Antes de finalizar:

```bash
python -m compileall tradingagents cli service
python -m pytest -q
ruff check .
```

Se algum comando não puder ser executado, informe o motivo exato, o comando e o resultado. Nunca alegue que os testes passaram se não foram executados.

## 6. Contratos de domínio obrigatórios

Crie objetos tipados, preferencialmente com `dataclasses` imutáveis ou Pydantic se já houver dependência adequada. Não usar dicionários livres como contrato principal.

Os contratos mínimos são:

### Instrument

Campos mínimos:

```text
instrument_id
symbol
asset_class
venue
quote_currency
base_currency
contract_size
tick_size
min_quantity
price_precision
quantity_precision
trading_session
metadata
```

### MarketSnapshot

```text
snapshot_id
instrument_id
bid
ask
last
volume
spread
observed_at
available_at
source_id
timezone
sequence
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

Uma evidência com `available_at` desconhecido deve ser excluída de backtest histórico.

### Signal

```text
signal_id
strategy_id
agent_id
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
instrument_id
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
created_at
status
```

Estados permitidos:

```text
PROPOSED
RISK_APPROVED
RISK_REDUCED
RISK_REJECTED
EXECUTION_PENDING
EXECUTED
EXPIRED
CANCELLED
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
created_at
engine_version
```

Estados permitidos:

```text
APPROVE
REDUCE
WAIT
REJECT
```

### OrderRequest, OrderAck, Fill e Position

Devem representar ordens, confirmações, fills parciais, posição líquida, posição em hedging, preço médio, stop, take profit, comissão, swap, slippage e timestamps de submissão e execução.

### AuditEvent

Todo evento deve incluir:

```text
event_id
schema_version
event_type
aggregate_type
aggregate_id
run_id
decision_id
strategy_id
occurred_at
available_at
actor
payload_hash
payload_redacted
previous_event_hash
```

O `previous_event_hash` deve permitir verificar alteração ou quebra na sequência do journal.

## 7. Event log e auditoria

Implemente um journal append-only que funcione primeiro em JSONL local e possua interface para futura persistência em PostgreSQL.

Requisitos:

- escrita com flush e opção de fsync;
- uma linha por evento;
- hash encadeado;
- redação de segredos antes da serialização final;
- validação de schema ao escrever;
- leitura por `run_id`, `decision_id`, `strategy_id` e `aggregate_id`;
- detecção de alteração, evento inválido e hash quebrado;
- nenhuma operação de trading pode existir sem evento correspondente;
- explicações do chat devem consultar eventos e evidências, nunca inventar justificativas.

Crie testes para:

- idempotência de gravação;
- segredo em texto, JSON, nested JSON e payload truncado;
- hash encadeado;
- corrupção de uma linha;
- eventos fora de ordem;
- schema incompatível;
- replay de uma decisão.

## 8. Risk Engine determinístico

Implemente um `RiskEngine` que não dependa de LLM.

Ele deve validar no mínimo:

- risco máximo por trade;
- risco máximo por estratégia;
- risco máximo por agente;
- risco máximo por desk;
- exposição máxima por instrumento;
- exposição líquida e bruta;
- perda diária;
- perda semanal;
- drawdown;
- margem disponível;
- tamanho mínimo e máximo;
- stop obrigatório;
- relação risco/retorno mínima;
- limite de posições abertas;
- correlação entre posições;
- limite de spread;
- idade máxima do feed;
- latência máxima;
- janela de notícias/eventos;
- estado da estratégia;
- estado da conta e broker;
- kill switch e circuit breaker.

Implemente sizing determinístico, por exemplo:

```text
risk_amount = equity * max_risk_pct
stop_distance = abs(entry_price - stop_loss)
quantity = risk_amount / (stop_distance * contract_value)
```

O cálculo deve respeitar precisão, lote mínimo, lote máximo, step size, margem e moeda da conta.

Nunca aceitar `quantity` fornecida pelo LLM sem recalcular e limitar.

Crie testes de propriedade para tentar burlar cada limite.

## 9. Execution protocol

Crie uma interface `BrokerAdapter` com pelo menos:

```python
capabilities()
get_account()
get_instruments()
get_quote(instrument_id)
get_positions()
get_open_orders()
submit(order_request)
cancel(order_id)
reconcile()
healthcheck()
```

Crie três implementações:

### PaperAdapter

Deve ser a implementação usada nos testes e no modo padrão.

Requisitos:

- simulação determinística por seed;
- suporte a market, limit, stop e stop-loss;
- slippage configurável;
- comissão configurável;
- latência configurável;
- fills parciais opcionais;
- posição e saldo persistentes;
- geração de eventos oficiais;
- idempotência;
- reconciliação;
- nenhuma chamada de rede.

### CCXTAdapter

Encapsule a lógica existente de `CCXTBridge` sem remover as proteções atuais.

Preserve:

- testnet por padrão;
- recusa de exchange sem testnet quando o modo solicitado for testnet;
- autorização explícita independente para live;
- `assert_secure_for_capital` antes de rede;
- journal redigido;
- client order ID determinístico.

### MT5Adapter

A integração oficial do MetaTrader 5 deve ser isolada neste adaptador.

Use as funções oficiais, quando disponíveis:

```text
initialize
login
shutdown
version
last_error
account_info
terminal_info
symbols_get
symbol_info
symbol_info_tick
symbol_select
market_book_add
market_book_get
copy_rates_from
copy_rates_range
copy_ticks_from
copy_ticks_range
order_calc_margin
order_calc_profit
order_check
order_send
orders_get
positions_get
history_orders_get
history_deals_get
```

Requisitos:

- conexão explícita com terminal;
- healthcheck periódico;
- validação de conta e servidor;
- seleção e resolução de símbolos;
- leitura de tick e barras;
- leitura opcional de Market Depth;
- cálculo de margem e lucro;
- `order_check` antes de `order_send`;
- transformação de resposta MT5 em contratos internos;
- reconciliação por ticket, magic number, comentário e `decision_id`;
- tratamento de conta hedging e netting;
- recusa quando o terminal estiver desconectado;
- recusa quando o símbolo não estiver disponível;
- recusa quando a conta não coincidir com a configuração esperada;
- recusa quando o modo não for demo/paper nesta fase;
- nenhum segredo em logs.

Não assumir que o pacote Python do MT5 é um gateway remoto independente. Documentar que o worker precisa ter acesso a uma instância do terminal MetaTrader 5.

## 10. MT5 Worker

Crie um worker isolado para MT5 com:

- heartbeat;
- fila de comandos;
- fila de eventos;
- timeout;
- retry limitado;
- idempotência;
- reconciliação periódica;
- estado `STARTING`, `READY`, `DEGRADED`, `DISCONNECTED`, `STOPPING`, `FAILED`;
- kill switch;
- bloqueio fechado em caso de divergência entre estado local e MT5.

O worker deve ser separado do processo dos agentes. O agente não deve chamar `MetaTrader5.order_send` diretamente.

## 11. Estratégias versionadas e lifecycle

Crie um registry de estratégias. Cada estratégia deve conter:

```text
strategy_id
name
version
owner_agent
desk
universe
timeframes
parameters
required_data
regime
cost_model
risk_profile
created_at
artifact_hash
status
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

Implemente transições válidas e rejeite transições inválidas.

A promoção deve considerar:

- número mínimo de trades;
- PnL líquido;
- profit factor;
- expectativa;
- máximo drawdown;
- duração do drawdown;
- estabilidade por período;
- out-of-sample;
- walk-forward;
- sensibilidade a comissão, spread e slippage;
- performance por regime;
- taxa de sinais inválidos;
- taxa de rejeição pelo risco;
- divergência paper versus shadow.

Uma estratégia não pode ser promovida por win rate isolado.

## 12. Backtest e walk-forward

Não construir um backtest que use dados futuros.

Cada dado precisa de:

```text
observed_at
available_at
source_id
```

Requisitos:

- separação temporal entre treino, validação e teste;
- janela de backtest;
- janela forward;
- custos realistas;
- spread;
- slippage;
- comissão;
- latência;
- fills no próximo preço permitido pelo modelo;
- prevenção de look-ahead bias;
- relatório reproduzível;
- seed e manifesto de execução;
- exclusão de evidência sem `available_at` conhecido;
- métricas por estratégia, ativo, regime e período.

Quando houver integração com o Strategy Tester do MT5, tratá-la como uma fonte complementar de validação de EA/execução. Não substituir o ledger e os contratos do DZ23 pelo relatório visual do terminal.

## 13. Orquestração de agentes

Preserve os agentes atuais e adicione os seguintes papéis:

- `MarketScannerAgent`;
- `RegimeDetectorAgent`;
- `TechnicalAnalystAgent`;
- `PriceActionAgent`;
- `OrderFlowAgent`;
- `SentimentAgent`;
- `MacroAgent`;
- `StrategyAgent`;
- `RiskAdvisorAgent`;
- `ChiefTraderAgent`;
- `ExecutionPlannerAgent`;
- `TradeMonitorAgent`;
- `PostTradeReviewerAgent`;
- `LearningMemoryAgent`;
- `StrategyLifecycleAgent`.

Os agentes devem produzir mensagens estruturadas e evidências referenciadas. O debate textual pode continuar existindo como relatório humano, mas o fluxo operacional deve consumir contratos validados.

O `ChiefTraderAgent` deve:

- coordenar os desks;
- resolver conflitos entre estratégias;
- priorizar oportunidades;
- respeitar orçamento e correlação;
- pedir aprovação do Risk Engine;
- nunca enviar ordem diretamente.

## 14. Trading Floor e War Room

Se o repositório já tiver frontend, integrar à estrutura existente. Se não tiver, criar apenas uma camada mínima de API e UI sem transformar a primeira fase em um projeto visual separado.

A UI deve mostrar dados vindos de eventos oficiais:

- estado de cada desk;
- estado de cada agente;
- estratégia atual;
- regime;
- trades;
- posições;
- PnL realizado e não realizado;
- drawdown;
- exposição;
- ordens abertas;
- risco;
- latência;
- saúde do broker;
- backtest, walk-forward, paper e live em blocos separados;
- estado do lifecycle;
- logs de auditoria.

Estados visuais mínimos:

```text
OPERATING
ANALYZING
WAITING_SETUP
BACKTEST
PAPER
SHADOW
PAUSED
RISK_LOCKED
ERROR
TRAINING
```

O chat deve responder perguntas como “por que entrou comprado?” usando:

- `decision_id`;
- evidências disponíveis antes da decisão;
- votos dos agentes;
- regime;
- níveis de entrada, stop e alvo;
- decisão do Risk Engine;
- versão da estratégia;
- timestamps.

Não gerar explicação retrospectiva sem evidência.

## 15. Persistência e operação

Manter compatibilidade com o armazenamento atual enquanto possível, mas preparar abstrações para:

- PostgreSQL para entidades, eventos, decisões, ordens e auditoria;
- Redis para locks, filas, cache e heartbeat;
- object storage para datasets, relatórios e artefatos de estratégia.

O primeiro modo pode utilizar SQLite/JSONL local para desenvolvimento, desde que os contratos e interfaces não fiquem acoplados ao filesystem.

Criar migrations ou schemas versionados quando necessário.

## 16. API mínima

Criar endpoints ou handlers equivalentes para:

```text
GET  /health
GET  /readiness
GET  /desks
GET  /agents
GET  /strategies
GET  /strategies/{id}
GET  /runs/{id}
GET  /decisions/{id}
GET  /positions
GET  /orders
GET  /risk/status
GET  /events
POST /runs
POST /decisions/{id}/paper-execute
POST /orders/{id}/cancel
POST /kill-switch
```

Regras:

- endpoints de leitura não podem causar efeitos colaterais;
- paper execution deve continuar sujeito ao Risk Engine;
- cancelamento deve ser auditado;
- kill switch deve ser idempotente;
- nenhuma rota live deve ser habilitada nesta entrega;
- respostas devem ocultar segredos e credenciais.

## 17. Testes obrigatórios

Adicione testes unitários, integração e segurança para:

### Contratos

- validação de campos obrigatórios;
- enum inválido;
- schema version incompatível;
- timestamps inconsistentes;
- decisão sem evidência;
- ordem sem stop;
- sizing inválido.

### Risk Engine

- limite por trade;
- limite por estratégia;
- limite por desk;
- limite por conta;
- drawdown;
- perda diária;
- correlação;
- spread;
- feed velho;
- margem insuficiente;
- circuit breaker;
- kill switch;
- tentativa de bypass com valores negativos, `NaN`, infinito e tipos errados.

### Paper Adapter

- market order;
- limit order;
- stop-loss;
- take profit;
- partial fill;
- slippage;
- comissão;
- retry idempotente;
- reconciliação;
- posição hedging e netting quando aplicável.

### MT5 Adapter

Mockar o módulo `MetaTrader5`. Não depender de terminal real nos testes.

Testar:

- terminal desconectado;
- conta errada;
- símbolo inválido;
- `order_check` rejeitado;
- `order_send` rejeitado;
- resposta parcial;
- reconciliação;
- timeout;
- heartbeat expirado;
- nunca chamar `order_send` antes de `order_check`;
- nunca chamar rede quando o Risk Engine rejeitar.

### Segurança

- segredo não aparece em logs;
- live recusa sem política autorizada;
- live permanece desabilitado por padrão;
- paper nunca degrada para live;
- cliente não consegue forjar Risk Approval;
- eventos não podem ser alterados sem detecção;
- endpoint read-only não envia ordens.

### Backtest

- sem look-ahead;
- filtro por `available_at`;
- janela forward correta;
- custos aplicados;
- seed reproduzível;
- manifesto reproduzível;
- dados futuros excluídos.

## 18. Documentação obrigatória

Criar ou atualizar:

```text
README.md
docs/architecture.md
docs/implementation-plan.md
docs/contracts.md
docs/risk-model.md
docs/mt5-integration.md
docs/strategy-lifecycle.md
docs/operations.md
docs/security-model.md
docs/runbooks/mt5-disconnected.md
docs/runbooks/risk-lock.md
docs/runbooks/reconciliation-failure.md
.env.example
```

Documentar:

- como iniciar em Paper;
- como executar testes;
- como usar mocks MT5;
- como conectar uma conta demo MT5;
- como ativar o worker MT5;
- como verificar reconciliação;
- como usar kill switch;
- como interpretar estados de estratégia;
- quais recursos continuam bloqueados;
- quais premissas são necessárias para live no futuro.

## 19. Critérios de aceite da entrega

A entrega somente pode ser considerada concluída se:

1. O sistema iniciar em Paper sem credenciais reais.
2. A análise atual continuar funcionando.
3. Os contratos tipados estiverem cobertos por testes.
4. Toda decisão operacional possuir evidências e `decision_id`.
5. Toda ordem Paper passar pelo Risk Engine.
6. O sizing for determinístico.
7. O Paper Adapter for reproduzível.
8. O event log puder ser validado e refeito por replay.
9. O CCXT atual continuar protegido.
10. O MT5 Adapter funcionar com mocks e recusar estados inválidos.
11. A arquitetura não chamar `order_send` fora do adapter/worker MT5.
12. Backtest não aceitar evidência sem proveniência temporal.
13. Estratégias possuírem lifecycle e promoção controlada.
14. A UI/API diferenciar backtest, walk-forward, paper, shadow e live.
15. O sistema possuir kill switch e circuit breaker testados.
16. Não existir caminho acidental de Paper para Live.
17. `compileall`, `pytest` e `ruff` forem executados ou tiverem falha documentada.
18. A documentação explicar claramente o que foi implementado e o que continua pendente.

## 20. Formato do relatório final do Codex

Ao terminar cada fase, responda com:

```text
FASE:
OBJETIVO:
ARQUIVOS CRIADOS:
ARQUIVOS ALTERADOS:
DECISÕES DE ARQUITETURA:
TESTES EXECUTADOS:
RESULTADO DOS TESTES:
RISCOS OU PENDÊNCIAS:
PRÓXIMA FASE:
```

Não diga “pronto” se houver testes não executados, dependências ausentes, integração mockada sem documentação ou risco de capital não coberto.

## 21. Ordem de implementação

Implemente nesta ordem e não pule fases:

```text
Fase 0 — inspeção e plano
Fase 1 — contratos de domínio
Fase 2 — event log e auditoria
Fase 3 — Risk Engine
Fase 4 — Paper Adapter e reconciliação
Fase 5 — lifecycle de estratégias
Fase 6 — integração dos agentes com contratos
Fase 7 — MT5 Adapter e worker com mocks
Fase 8 — backtest/walk-forward
Fase 9 — API, Trading Floor e War Room
Fase 10 — shadow mode
Fase 11 — auditoria final de segurança e operação
```

O live trading deve permanecer fora da implementação operacional desta missão. Quando chegar o momento de discutir live, apenas documente os requisitos, riscos e gates necessários.

Comece agora pela Fase 0: inspecione o repositório, execute os testes disponíveis, produza `docs/implementation-plan.md` e apresente um diagnóstico objetivo antes de alterar o núcleo.


## 22. Referência visual obrigatória: trading floor isométrica do vídeo

O produto deve ter uma experiência visual inspirada no vídeo fornecido pelo usuário: uma **agência de agentes trader apresentada como uma sala de operações isométrica, interativa e viva**. A referência não deve ser copiada como marca ou asset proprietário. Ela deve orientar a experiência, a densidade visual, a navegação e a representação dos agentes.

O resultado desejado é uma combinação de:

```text
Trading Floor isométrica
+ War Room operacional
+ Dashboard financeiro profissional
+ Observabilidade de agentes
+ Chat auditável
+ Lifecycle visual de estratégias
```

A interface deve parecer uma agência operando em tempo real, mas todos os dados exibidos precisam vir de eventos oficiais do backend. Não simular PnL, trades, estados de agentes ou conversas apenas com animações frontend.

### 22.1 Direção visual

Use uma linguagem visual de:

- visão isométrica 2D ou pseudo-3D;
- dark mode profissional;
- fundo azul-marinho e roxo profundo;
- destaques em ciano, violeta, magenta e verde esmeralda;
- neon controlado, sem comprometer legibilidade;
- painéis flutuantes com transparência moderada;
- linhas de conexão entre agentes e desks;
- brilhos e pulsos para eventos reais;
- tipografia sans-serif moderna;
- números financeiros grandes, claros e contrastantes;
- mini-gráficos de equity, PnL e curva da estratégia;
- avatares ou ícones de agentes em estações de trabalho;
- balões de fala derivados de eventos de decisão, nunca de texto inventado no cliente.

Evite:

- aparência de cassino;
- excesso de neon que esconda números;
- gráficos decorativos sem dados;
- animações contínuas que indiquem atividade inexistente;
- “robôs” genéricos sem relação com um agente real;
- mistura visual de backtest, paper, shadow e live;
- qualquer indicador que faça uma estratégia parecer lucrativa sem fonte e período.

### 22.2 Tela inicial: DZ23 Trading Floor

A tela inicial deve mostrar uma sala operacional com:

```text
┌───────────────────────────────────────────────────────────────┐
│ DZ23 AUTONOMOUS TRADING AGENCY       Modo: PAPER             │
│ Equity | PnL hoje | Exposição | Drawdown | Risco | Saúde      │
├───────────────────────────────────────────────────────────────┤
│                                                               │
│                    CHIEF TRADER AI                           │
│                         │                                     │
│        ┌────────────────┼────────────────┐                    │
│        │                │                │                    │
│   CRYPTO DESK      FOREX DESK       FUTURES DESK             │
│   BTC ETH SOL      EUR GBP XAU       ES NQ CL                 │
│                                                               │
│ Agents: 17 active | 6 waiting | 3 training | 1 blocked       │
├───────────────────────────────────────────────────────────────┤
│ Activity Feed | Risk Status | Open Orders | Recent Decisions  │
└───────────────────────────────────────────────────────────────┘
```

Cada desk deve ser uma entidade real do backend e possuir:

- `desk_id`;
- mercado e instrumentos cobertos;
- capital ou orçamento virtual;
- exposição;
- PnL;
- drawdown;
- agentes associados;
- estratégias ativas;
- estado operacional;
- último heartbeat;
- bloqueios de risco;
- evento mais recente.

### 22.3 Representação das estações e agentes

Cada estação visual representa um agente ou uma estratégia real. Ela deve exibir:

- nome do agente ou estratégia;
- ativo principal;
- timeframe;
- estado;
- estratégia e versão;
- último evento;
- PnL do estágio atual;
- confiança da última análise;
- risco aprovado ou bloqueado;
- indicador de saúde/heartbeat.

Estados visuais obrigatórios:

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

Mapeamento visual sugerido:

| Estado | Tratamento visual |
| --- | --- |
| OPERATING | Verde esmeralda, pulso lento, label `OPERANDO` |
| ANALYZING | Ciano, fluxo de dados discreto |
| WAITING_SETUP | Azul frio, sem animação agressiva |
| BACKTEST | Roxo, ícone de histórico |
| WALK_FORWARD | Violeta, indicador de validação |
| PAPER_TRADING | Amarelo/ciano, label explícito `PAPER` |
| SHADOW | Azul-magenta, label explícito `SHADOW` |
| TRAINING | Roxo pulsante, progresso de treino |
| PAUSED | Cinza, sem pulso |
| RISK_LOCKED | Vermelho/âmbar, ícone de cadeado |
| ERROR | Vermelho, alerta persistente |
| DISCONNECTED | Vermelho escuro, heartbeat expirado |

Um agente parado não pode parecer ativo apenas porque o frontend possui uma animação CSS.

### 22.4 Interação point-and-click

A navegação principal deve permitir clicar ou tocar em:

- Chief Trader;
- desk;
- agente;
- estratégia;
- posição;
- ordem;
- sala de reunião;
- Risk Manager;
- estação MT5;
- atividade do event log.

Ao clicar, abrir painel lateral ou modal com transição suave, sem abandonar completamente o contexto da Trading Floor.

A interação deve ser implementada primeiro com componentes HTML/React acessíveis. Não começar com uma cena 3D pesada se uma representação isométrica 2D com Canvas/SVG/CSS entregar a experiência com mais estabilidade.

A arquitetura visual deve permitir evolução futura para PixiJS, Phaser ou Three.js, mas a primeira versão deve priorizar:

- carregamento rápido;
- responsividade;
- acessibilidade;
- uso em desktop e tablet;
- atualização por WebSocket ou Server-Sent Events;
- estados reproduzíveis;
- testes de interface.

### 22.5 Painel de detalhe de uma estratégia/mesa

Ao clicar em uma mesa, mostrar algo equivalente a:

```text
BTC MOMENTUM DESK
────────────────────────────
Capital virtual       $10,000
Equity                 $10,418
PnL hoje                 +2.1%
PnL mês                  +8.7%

Trades                      37
Wins                        24
Losses                      13
Win rate                  64.9%
Profit Factor              1.84
Max Drawdown               3.1%

REGIME
Trending ↑

ESTRATÉGIA
BTC Momentum v3
Status: PAPER / CHALLENGER
Confidence: 78%

RISK MANAGER
APROVADO — risco 0.35%

POSIÇÃO ATUAL
BTCUSDT LONG
Entrada: $115,420
Stop:    $114,910
Alvo:    $116,740
```

Os números precisam indicar claramente:

- fonte;
- período;
- estágio operacional;
- timestamp da última atualização;
- se são realizados, não realizados ou simulados.

Nunca apresentar `Backtest`, `Walk-Forward`, `Paper`, `Shadow` e `Live` como uma única curva.

### 22.6 Comparação de estágios

Criar um componente dedicado com abas ou colunas separadas:

```text
BACKTEST       WALK-FORWARD       PAPER          SHADOW          LIVE
PnL            PnL                 PnL            PnL             BLOQUEADO
Trades         Trades              Trades         Trades          —
Max DD         Max DD              Max DD         Max DD          —
Profit Factor  Profit Factor       Profit Factor  Profit Factor   —
```

Cada estágio deve ter:

- dataset ou origem;
- intervalo temporal;
- versão da estratégia;
- custo modelado;
- número de trades;
- métricas;
- confiança estatística quando disponível;
- aviso de limitações.

### 22.7 Sala Quant / Committee Room

Criar uma sala de reunião visual para o comitê de trading. Ela deve mostrar o fluxo de decisão, sem expor raciocínio privado irrestrito de modelos. Exibir apenas argumentos, sinais, evidências e decisões registradas para auditoria.

Exemplo:

```text
BTCUSDT LONG — PROPOSTA DE DECISÃO

Technical Agent       BUY      82%
Price Action Agent    BUY      76%
Order Flow Agent      BUY      71%
Macro Agent           NEUTRAL  52%
Sentiment Agent       BUY      68%

Consensus             BUY      74%

Risk Manager          APPROVED
Risco permitido       0.35%
Portfolio Manager     CORRELATION_OK
Execution Planner     SLIPPAGE_OK

DECISÃO FINAL         EXECUTE PAPER
```

Cada voto deve possuir:

- `agent_id`;
- `signal_id`;
- timestamp;
- evidências;
- versão do prompt/política se aplicável;
- confiança;
- validade;
- status.

Não exibir conversa simulada se nenhum evento foi emitido.

### 22.8 Chat auditável

Ao clicar em um agente ou trade, permitir perguntas como:

```text
Por que esta estratégia propôs uma compra?
Quais agentes discordaram?
Quais evidências estavam disponíveis antes da decisão?
Por que o Risk Engine aprovou ou bloqueou?
Qual era o risco calculado?
Qual versão da estratégia foi usada?
O trade foi backtest, paper, shadow ou live?
```

A resposta deve ser montada a partir de:

- `decision_id`;
- `AuditEvent`;
- evidências vinculadas;
- votos estruturados;
- decisão do Risk Engine;
- eventos de execução;
- versão da estratégia.

Se não houver dados, responder claramente:

```text
Não há evidência registrada para responder a esta pergunta.
A explicação não será inventada.
```

### 22.9 War Room

Criar uma tela alternativa, mais operacional e menos gamificada:

```text
TRADING WAR ROOM

EQUITY       DAILY PNL      EXPOSURE       DRAWDOWN       RISK
$104,380     +1.82%         14.2%          2.7%           NORMAL

POSITIONS
BTC       ███████░   LONG
ETH       ████░░░░   LONG
SOL       ██░░░░░░   FLAT
EURUSD    ███░░░░░   SHORT
XAUUSD    █████░░░   LONG

AGENTS
17 ACTIVE | 6 WAITING | 3 TRAINING | 1 BLOCKED

ORDERS
4 OPEN | 2 PENDING | 0 REJECTED RECENTLY

BROKERS
MT5 DEMO: CONNECTED
CCXT PAPER: READY
```

O War Room deve destacar incidentes:

- feed atrasado;
- Risk Lock;
- terminal MT5 desconectado;
- divergência de posição;
- ordem rejeitada;
- estratégia rebaixada;
- drawdown excedido;
- heartbeat expirado.

### 22.10 Tela de competição entre estratégias

Criar uma tabela de competição com dados por estratégia:

| Estratégia | Mercado | Regime | Trades | Win rate | PF | DD | Estado |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| Alpha-01 | BTC | Trend | 486 | 61% | 1.78 | 4.2% | CHAMPION |
| Alpha-07 | ETH | Range | 312 | 58% | 1.53 | 3.7% | ACTIVE |
| Alpha-11 | SOL | Trend | 185 | 54% | 1.21 | 8.8% | PROBATION |
| Alpha-14 | BTC | Range | 93 | 49% | 0.91 | 11.2% | DISABLED |

Adicionar:

- filtros por desk, mercado, regime e estágio;
- histórico de promoção/rebaixamento;
- botão de detalhes;
- motivo da alteração de status;
- curva de equity separada;
- custos e drawdown visíveis;
- indicação de amostra insuficiente.

### 22.11 Navegação global

A navegação deve contemplar:

```text
Trading Floor
War Room
Agents
Desks
Strategies
Positions
Orders
Backtesting
Walk-Forward
Paper Trading
Shadow Trading
Training
Risk
Portfolio
Exchanges
MT5
AI Gateway
Audit Logs
Settings
```

A seção `Live Trading` deve existir apenas como tela bloqueada/documental, com explicação dos gates pendentes. Não apresentar botão operacional de live nesta entrega.

### 22.12 Dados em tempo real e animações

Use WebSocket ou Server-Sent Events para propagar eventos do backend:

```text
agent.status.changed
agent.heartbeat.updated
desk.status.changed
signal.created
committee.vote.recorded
risk.decision.created
order.submitted
order.accepted
order.rejected
fill.received
position.updated
strategy.promoted
strategy.demoted
risk.locked
broker.health.changed
```

As animações devem reagir a esses eventos:

- estação acende quando o agente muda de estado;
- linha de dados aparece quando há fluxo real;
- balão de fala aparece quando existe evento de análise;
- mesa muda de cor quando o Risk Engine bloqueia;
- ordem percorre o caminho apenas quando foi emitido o evento correspondente;
- fill atualiza posição e PnL;
- erro deixa a estação em estado de atenção até ser resolvido.

Não criar loops de animação que finjam trades ou atividade.

### 22.13 Responsividade e acessibilidade

A sala isométrica deve ter uma versão alternativa para telas pequenas:

- lista de desks;
- cartões de agentes;
- tabela de ordens;
- War Room compacto;
- filtros;
- modo sem animação;
- alto contraste;
- labels textuais para estados;
- navegação por teclado;
- `aria-label` em elementos interativos;
- não depender apenas de cor para indicar risco ou estado.

### 22.14 Critérios visuais de aceite

A implementação visual só será aceita quando:

1. A tela inicial parecer uma trading floor operacional, não um dashboard genérico.
2. Os desks e agentes forem clicáveis.
3. Cada estado visual tiver origem em estado real do backend.
4. O usuário conseguir abrir detalhes de uma estratégia e de uma decisão.
5. O sistema diferenciar claramente Backtest, Walk-Forward, Paper, Shadow e Live.
6. O painel de comitê mostrar votos estruturados e Risk Decision.
7. O chat usar eventos auditáveis e declarar ausência de evidência.
8. A War Room mostrar posições, ordens, risco e saúde dos adapters.
9. Houver uma visão de competição entre estratégias.
10. A interface continuar utilizável com animações desativadas.
11. Nenhuma animação sugerir uma operação que não ocorreu.
12. Houver dados mockados oficiais para desenvolvimento, claramente marcados como `DEMO` ou `MOCK`.
13. Os testes verificarem estados, transições, eventos e renderização dos principais painéis.
14. Nenhuma informação de backtest for misturada com resultado paper ou live.

### 22.15 Ordem recomendada para construir a interface

Implementar a UI nesta sequência:

```text
1. Design tokens e shell da aplicação
2. Trading Floor estática com dados mockados oficiais
3. Cards de desk e agente
4. Painel de detalhe de estratégia
5. War Room
6. Committee Room
7. Activity Feed e Audit Log
8. Competição de estratégias
9. SSE/WebSocket e estados reais
10. Animações reativas a eventos
11. Responsividade e acessibilidade
12. Testes de interface e estados de falha
```

Antes de criar uma cena 3D, entregue uma versão navegável 2D/isométrica com o mesmo conceito visual. A prioridade é provar o fluxo operacional e a ligação entre evento e interface.

## 23. Entendimento final do produto

O produto não deve ser um painel que finge ser uma agência. Deve ser uma agência operacional que possui uma representação visual de trading floor.

A divisão correta é:

```text
Backend real:
- dados
- agentes
- sinais
- comitê
- risco
- ordens
- fills
- posições
- reconciliação
- lifecycle
- auditoria

Frontend visual:
- sala de operações
- mesas
- avatares
- balões
- conexões
- War Room
- painéis
- chat auditável
- navegação
- animações baseadas em eventos
```

A estética do vídeo é uma referência de produto. A verdade operacional deve vir sempre do backend, dos contratos tipados e do event log.

# PROMPT MESTRE — Evolução contínua do TradingAgents DZ23 até versão pronta para usuários finais

## Como usar

Copie este documento inteiro e entregue ao Codex ou Claude dentro do repositório `LMPrado-DZ23/tradingagents-dz23`. O agente deve executar o trabalho diretamente no código, não apenas sugerir alterações.

O agente deve trabalhar em ciclos pequenos, verificáveis e reversíveis. Após cada ciclo, deve executar testes, atualizar documentação, registrar riscos e continuar automaticamente para o próximo ciclo enquanto houver trabalho autorizado e seguro.

---

# 1. Papel e objetivo

Você é o engenheiro principal responsável por transformar o repositório TradingAgents DZ23 em uma plataforma de agência multiagente de pesquisa, Paper Trading e Demo Trading, pronta para usuários finais em ambiente controlado.

O objetivo é entregar um produto profissional com:

- agentes de pesquisa especializados;
- reuniões estruturadas entre agentes;
- sinais tipados e rastreáveis;
- Risk Engine determinístico;
- Paper Trading persistente;
- MT5 Demo usando a biblioteca oficial `MetaTrader5`;
- exchanges crypto por CCXT;
- Event Log e auditoria;
- reconciliação de ordens, fills, posições e saldo;
- backtest e walk-forward;
- workers 24/7 resilientes;
- Trading Floor visual;
- observabilidade;
- onboarding e documentação para usuários finais;
- operação segura e reversível.

Não prometa lucro, crescimento da carteira, acurácia ou proteção contra perdas. O produto deve informar claramente que trading envolve risco.

---

# 2. Regra absoluta de segurança

Mantenha estes valores como padrão em todos os ambientes:

```env
LIVE_TRADING=false
PAPER_TRADING=true
SHADOW_TRADING=false
REAL_MONEY_EXECUTION=false
```

O modo real deve permanecer bloqueado por padrão.

Nunca habilite execução com dinheiro real automaticamente. Nunca solicite, exponha, registre ou reutilize credenciais reais sem configuração explícita do usuário, segregação de ambiente, aprovação operacional e testes anteriores em Paper/Demo/Testnet.

Nenhum agente LLM, frontend, dashboard, prompt, repositório externo ou estratégia pode enviar uma ordem diretamente a uma corretora ou exchange.

Toda ordem deve seguir obrigatoriamente:

```text
market data
  → research/agentes
  → sinal tipado não autorizado
  → validação de schema
  → Risk Engine determinístico
  → autorização da política
  → adapter CCXT ou MT5
  → resposta do venue
  → Event Log
  → reconciliação
  → Trading Floor read-only
```

Em qualquer dúvida, erro de dados, heartbeat perdido, timeout, resposta ambígua, estado divergente ou reconciliação incompleta, o comportamento deve ser **fail closed**: bloquear novas ordens e registrar o motivo.

---

# 3. Estado inicial que deve ser auditado

Antes de alterar qualquer coisa:

1. Leia `README.md`, `HANDOFF.md`, `pyproject.toml`, `docker-compose.yml`, `.env*`, serviço HTTP, grafo LangGraph, agentes, adapters CCXT/MT5, Risk Engine, Event Bus, Audit Log, Paper Adapter, backtest, walk-forward, workers, frontend e todos os testes.
2. Execute a suíte atual completa.
3. Registre:
   - branch e commit atual;
   - dependências instaladas;
   - comandos de teste;
   - testes aprovados, falhos e ignorados;
   - módulos existentes;
   - lacunas reais;
   - riscos de segurança;
   - endpoints que podem alterar estado;
   - qualquer caminho que possa enviar ordem sem passar pelo Risk Engine.
4. Não reescreva partes funcionais sem demonstrar necessidade.
5. Preserve compatibilidade com a API atual quando possível.
6. Crie uma branch de trabalho dedicada, por exemplo:

```text
feat/dz23-user-ready
```

---

# 4. Ciclo de execução contínua

Execute continuamente este ciclo:

```text
AUDITAR
  → PLANEJAR O MENOR PRÓXIMO INCREMENTO
  → IMPLEMENTAR
  → ESCREVER/ATUALIZAR TESTES
  → EXECUTAR TESTES
  → CORRIGIR FALHAS
  → ATUALIZAR DOCUMENTAÇÃO
  → VERIFICAR SEGURANÇA
  → REGISTRAR EVIDÊNCIAS
  → FAZER COMMIT PEQUENO
  → CONTINUAR
```

Não pare depois de escrever um plano. Não marque uma tarefa como concluída sem evidência de teste ou justificativa documentada.

Mantenha um arquivo `docs/progress.md` com:

- ciclo atual;
- tarefa em andamento;
- arquivos modificados;
- comandos executados;
- resultado dos testes;
- riscos abertos;
- decisões arquiteturais;
- próximas tarefas;
- evidências de aceite.

Mantenha também `docs/known-limitations.md` e atualize-o sempre que uma limitação for descoberta.

---

# 5. Arquitetura obrigatória

## 5.1 Domínio

Consolide contratos tipados para:

- Instrument;
- MarketSnapshot;
- Evidence;
- Signal;
- Decision;
- RiskDecision;
- OrderRequest;
- OrderAck;
- Fill;
- Position;
- PortfolioSnapshot;
- ReconciliationReport;
- Strategy;
- Agent;
- Desk;
- Meeting;
- AuditEvent;
- WorkerHealth.

Todo contrato deve possuir:

- identificador único;
- versão de schema;
- timestamp UTC;
- origem;
- correlação;
- ambiente;
- instrumento normalizado;
- validação de unidade, moeda, lado, volume e preço;
- serialização determinística;
- compatibilidade versionada.

## 5.2 Autoridades únicas

Defina explicitamente:

- `RiskEngine` como autoridade única para risco e limites;
- `EventLog` como trilha oficial de eventos;
- corretora/exchange como fonte primária de saldo, ordens e fills externos;
- `ReconciliationService` como autoridade para determinar convergência ou divergência;
- `ExecutionRouter` como único caminho autorizado para envio de ordens;
- `KillSwitch` como mecanismo independente que pode bloquear/cancelar/achatar conforme política;
- frontend como camada read-only para dados financeiros.

Não permita duas implementações concorrentes para a mesma responsabilidade.

---

# 6. Integração dos agentes atuais

Integre os agentes existentes ao runtime sem substituir o grafo funcional desnecessariamente.

Cada agente deve:

- possuir ID estável;
- declarar desk e função;
- receber contexto versionado;
- emitir evidências estruturadas;
- declarar confiança e incerteza;
- nunca emitir uma ordem final;
- registrar tempo, modelo, prompt versionado e fontes;
- tratar ausência ou conflito de dados como `REVIEW` ou `HOLD`;
- publicar eventos no Event Bus;
- participar de reuniões com quorum e regras explícitas.

Implemente desks, no mínimo:

- Market Data;
- Fundamental Research;
- Technical Research;
- Sentiment/News;
- Strategy;
- Risk;
- Portfolio;
- Execution Planning;
- Monitoring/Reconciliation.

Implemente reuniões para:

- formação de sinal;
- revisão de risco;
- aprovação de estratégia;
- análise de incidente;
- revisão pós-trade.

Toda decisão deve mostrar quais agentes votaram, quais evidências foram usadas e por que o quorum foi atingido ou não.

---

# 7. Risk Engine

Fortaleça o Risk Engine sem depender de LLM.

Implemente e teste:

- limite de perda diária;
- limite de perda semanal;
- limite por estratégia;
- limite por agente;
- limite por instrumento;
- limite por broker/exchange;
- exposição máxima;
- concentração máxima;
- limite de notional;
- limite de alavancagem;
- limite de margem;
- volume mínimo/máximo;
- preço máximo de desvio;
- spread máximo;
- stale data gate;
- cooldown após perda;
- circuit breaker;
- limite de ordens por minuto;
- bloqueio por reconciliação incompleta;
- bloqueio por worker degradado;
- bloqueio por kill switch;
- validação de stop-loss/take-profit;
- validação de moeda e conversão;
- validação de netting/hedging;
- aprovação e rejeição explicáveis.

Toda decisão de risco deve ser determinística, auditável e testada com property tests e casos-limite.

---

# 8. Paper Trading persistente

Transforme o Paper Adapter em um ambiente completo e reiniciável.

Implementar:

- Paper Ledger persistente;
- saldo inicial configurável;
- posições;
- ordens abertas;
- ordens canceladas;
- fills parciais;
- fees;
- slippage;
- funding;
- spread;
- latência simulada;
- margem;
- netting/hedging conforme configuração;
- PnL realizado e não realizado;
- equity;
- drawdown;
- histórico por estratégia;
- histórico por agente;
- replay após reinício;
- idempotência;
- reconciliação do ledger;
- exportação CSV/JSON;
- snapshots periódicos.

O Paper Trading deve possuir testes determinísticos e testes de reinício.

---

# 9. CCXT

Mantenha CCXT somente dentro do Exchange Adapter do DZ23.

Implementar e testar:

- versão pinada;
- allowlist de exchanges;
- símbolos normalizados;
- precisão de preço e volume;
- limites de mercado;
- spot, margin e futures quando suportados;
- rate limits;
- timeout;
- retry seguro;
- resposta ambígua;
- client-order-id;
- deduplicação;
- cancelamento;
- fills parciais;
- reconexão WebSocket/REST;
- snapshot após reconexão;
- reconciliação de ordens, trades, saldo e posições;
- segregação Paper/Testnet/Live;
- proteção de chaves.

Nunca reenviar uma ordem após timeout sem consultar primeiro o status no venue e registrar a decisão.

---

# 10. MT5 oficial

Use exclusivamente a biblioteca oficial Python `MetaTrader5` e o terminal MT5.

Não transforme repositórios comunitários em dependências sem licença e revisão.

Implementar:

- `initialize` seguro;
- verificação de terminal, servidor e conta;
- modo Demo explícito;
- bloqueio Live por padrão;
- `symbol_select`;
- `symbol_info`;
- tick atual;
- volume mínimo, máximo e step;
- spread;
- margem;
- `order_check`;
- `order_send` somente após aprovação;
- retcodes;
- pending orders;
- cancelamento;
- netting;
- hedging;
- tickets;
- fills parciais;
- stops;
- take-profit;
- reconexão;
- reconciliação de positions/orders/deals/account;
- magic number e client correlation;
- auditoria de cada chamada;
- testes com mock;
- teste em conta Demo isolada, somente se explicitamente configurado.

Não execute EAs comunitários diretamente na conta do DZ23.

---

# 11. Shadow Trading

Implementar um modo Shadow Trading que:

- recebe sinais reais;
- calcula o que teria sido enviado;
- não envia ordem;
- registra preço, volume, risco, latência e resultado hipotético;
- compara com mercado posterior;
- calcula divergência entre sinais, Paper e Demo;
- gera relatórios por estratégia/agente;
- suporta replay;
- não possui credenciais de execução.

O Shadow Trading deve ser obrigatório antes de promover estratégias para Paper/Demo.

---

# 12. Backtest e pesquisa

Fortalecer o backtest atual com:

- data vintage;
- timezone explícito;
- prevenção de look-ahead;
- prevenção de survivorship bias;
- fees;
- slippage;
- spread;
- funding;
- borrow;
- bid/ask;
- fills parciais;
- latência;
- gaps;
- volume;
- margem;
- múltiplos ativos;
- benchmark;
- walk-forward;
- out-of-sample;
- stress test;
- Monte Carlo quando aplicável;
- manifesto hashado;
- código/commit;
- versão de dados;
- versão de modelo;
- parâmetros;
- seed;
- relatório reprodutível.

Opcionalmente criar workers de pesquisa separados para:

- VectorBT: sweeps e pesquisa vetorizada, somente se a licença for aprovada;
- FinRL: reinforcement learning offline;
- TradingAgents: pesquisa multiagente read-only.

Nenhum worker de pesquisa pode possuir credenciais de produção.

---

# 13. Estratégias e lifecycle

Implementar lifecycle formal:

```text
DRAFT
  → RESEARCH
  → BACKTESTED
  → WALK_FORWARD_VALIDATED
  → SHADOW
  → PAPER
  → DEMO
  → CANDIDATE
  → APPROVED
  → ACTIVE
  → DEGRADED
  → SUSPENDED
  → RETIRED
```

Cada promoção deve exigir critérios de aceite documentados:

- janela mínima;
- número mínimo de trades;
- drawdown máximo;
- estabilidade out-of-sample;
- ausência de look-ahead;
- custos incluídos;
- risco aprovado;
- reconciliação validada;
- kill switch testado;
- benefício mensurável.

Nenhuma estratégia pode promover a si mesma.

---

# 14. Workers 24/7

Implementar workers para:

- market data;
- research;
- meetings;
- signal generation;
- risk evaluation;
- paper execution;
- MT5 Demo;
- exchange sync;
- reconciliation;
- health monitoring;
- reporting;
- event projection.

Cada worker deve possuir:

- nome e versão;
- heartbeat;
- timeout;
- retry com backoff;
- circuit breaker;
- estado persistente;
- graceful shutdown;
- idempotência;
- métricas;
- logs estruturados;
- correlação;
- limite de recursos;
- health/readiness/liveness;
- tratamento de exceções;
- teste de reinício.

Use Docker Compose e um process manager apropriado para Demo/produção controlada. Não trate uma thread daemon isolada como operação 24/7 suficiente.

---

# 15. Event Bus e persistência

O Event Bus atual deve evoluir de memória para uma arquitetura persistente e recuperável.

Implementar:

- Event ID;
- sequência monotônica;
- aggregate ID;
- correlation ID;
- causation ID;
- schema version;
- timestamp UTC;
- replay;
- deduplicação;
- retenção;
- snapshot;
- recovery;
- dead-letter queue;
- eventos fora de ordem;
- garantia de não perder eventos críticos;
- projeções para Trading Floor;
- consulta por intervalo, agente, estratégia, ordem e posição.

Se Redis/PostgreSQL/event store for utilizado, adicionar:

- migrations;
- backup;
- restore;
- healthcheck;
- retenção;
- credenciais seguras;
- testes de falha.

---

# 16. Reconciliação profissional

Implementar reconciliação periódica e após eventos importantes.

Comparar:

- intenção interna;
- ordem enviada;
- resposta do broker;
- ordem aberta;
- fill/deal;
- posição;
- saldo;
- equity;
- fees;
- margem;
- estado do ledger;
- estado do Event Log.

Estados possíveis:

```text
CONSISTENT
DIVERGENT
UNKNOWN
STALE
BLOCKED
RECOVERING
```

Em `DIVERGENT`, `UNKNOWN` ou `STALE`, bloquear novas ordens por padrão.

---

# 17. Kill Switch

Criar kill switch independente do frontend e dos agentes.

Deve poder:

- bloquear novas ordens;
- cancelar ordens pendentes;
- impedir retries;
- pausar workers;
- marcar a conta como bloqueada;
- iniciar flatten controlado quando permitido pela política;
- registrar motivo, operador, timestamp e estado;
- exigir reconciliação antes de desbloquear.

Testar kill switch com:

- rede caída;
- processo travado;
- fila acumulada;
- posição parcial;
- fill desconhecido;
- broker indisponível;
- evento duplicado;
- dado stale;
- erro de autenticação.

---

# 18. Trading Floor e UX final

Evoluir a Trading Floor para usuário final com:

- dashboard 2D confiável;
- opção 3D inspirada no Claw3D;
- desks;
- agentes;
- status;
- reuniões;
- War Room;
- Strategy Leaderboard;
- Position Panel;
- Order Blotter;
- Risk Panel;
- PnL Panel;
- Backtest Panel;
- Walk-forward Panel;
- Broker Health;
- Audit Timeline;
- freshness dos dados;
- indicador de reconciliação;
- estado Paper/Demo;
- mensagens de erro compreensíveis;
- fallback 2D quando WebGL falhar;
- acessibilidade básica;
- responsividade;
- onboarding.

O frontend deve ser read-only para ordens financeiras. Se houver comandos administrativos, eles devem usar autenticação, autorização, idempotência, auditoria e passar pelo backend.

Nunca representar uma ordem como enviada, aceita ou preenchida apenas por animação, timer, texto de agente ou transcript. Só exibir esses estados quando houver evento oficial.

---

# 19. API e segurança

Implementar:

- autenticação forte;
- autorização por papel;
- rate limiting;
- validação Pydantic;
- idempotency keys;
- proteção contra replay;
- CORS restritivo;
- CSRF quando aplicável;
- headers de segurança;
- logs sem segredos;
- rotação de credenciais;
- allowlist de brokers;
- allowlist de exchanges;
- segregação por ambiente;
- secrets manager ou `.env` protegido;
- backups criptografados;
- auditoria de ações administrativas.

Não armazenar chaves em prompts, relatórios, frontend, screenshots, logs ou mensagens entre agentes.

---

# 20. Observabilidade

Implementar:

- logs JSON estruturados;
- métricas Prometheus ou equivalente;
- tracing por correlation ID;
- latência por etapa;
- taxa de sinais;
- taxa de aprovação/rejeição;
- taxa de erro;
- ordens por broker;
- fills;
- reconciliações;
- divergências;
- worker heartbeat;
- PnL Paper/Demo;
- drawdown;
- stale data;
- uso de LLM;
- custo estimado de LLM;
- alertas.

Criar painéis e alertas para:

- worker parado;
- broker indisponível;
- Event Bus sem consumir;
- reconciliação divergente;
- limite de risco atingido;
- ordem ambígua;
- credencial expirada;
- dados stale;
- exceções repetidas.

---

# 21. Testes obrigatórios

A suíte deve incluir:

## Unitários

- contratos;
- validação;
- Risk Engine;
- sizing;
- Paper Ledger;
- Event Bus;
- Audit Log;
- reuniões;
- votos;
- adapters;
- reconciliação;
- lifecycle;
- workers.

## Integração

- API;
- autenticação;
- SSE/WebSocket;
- SQLite/PostgreSQL/Redis quando aplicável;
- CCXT mockado;
- MT5 mockado;
- reinício de worker;
- replay de eventos;
- reconciliação após restart.

## Segurança

- credenciais não aparecem em logs;
- frontend não envia ordens diretamente;
- agentes não chamam brokers diretamente;
- modo Live permanece bloqueado;
- kill switch bloqueia execução;
- divergência bloqueia novas ordens;
- idempotência impede duplicidade.

## Pesquisa

- look-ahead;
- timezone;
- data vintage;
- custo;
- slippage;
- fills;
- walk-forward;
- out-of-sample;
- determinismo;
- replay.

## E2E

Criar um cenário completo:

```text
market data mock
  → agentes
  → reunião
  → sinal
  → Risk Engine
  → Paper order
  → fill
  → posição
  → PnL
  → reconciliação
  → evento na Trading Floor
```

O cenário deve funcionar sem credenciais reais.

---

# 22. Critérios de pronto para usuário final

Não declare `READY FOR END USERS` até todos os critérios abaixo estarem demonstrados:

- instalação documentada do zero;
- `.env.example` atualizado;
- configuração validada;
- modo Paper funcionando sem credenciais reais;
- modo Demo testado com mocks e, se autorizado, conta Demo isolada;
- frontend funcionando;
- API autenticada;
- onboarding concluído;
- health/readiness/liveness disponíveis;
- backup e restore documentados;
- logs e métricas disponíveis;
- alertas testados;
- reconciliação testada;
- kill switch testado;
- nenhuma ordem duplicada nos testes;
- nenhum segredo em logs;
- Event Log reproduzível;
- todos os testes críticos passando;
- suíte completa passando ou exceções documentadas;
- performance aceitável documentada;
- rollback documentado;
- limitações visíveis ao usuário;
- disclaimers de risco presentes;
- nenhuma alegação de lucro ou garantia;
- nenhuma dependência externa sem licença revisada;
- nenhum repositório comunitário operando como autoridade paralela.

A frase `READY FOR END USERS` só pode aparecer em um relatório final acompanhado de evidências, comandos e resultados.

---

# 23. Regras para repositórios externos

Use repositórios externos somente com uma destas classificações:

1. **Referência:** estudar padrões e implementar no DZ23.
2. **Worker offline:** pesquisa sem credenciais.
3. **Serviço separado:** piloto isolado, com contrato e rollback.
4. **Dependência aprovada:** somente após licença, segurança, testes e benefício comprovado.

Regras específicas:

- CCXT permanece no adapter atual;
- NautilusTrader não deve duplicar execução no core;
- Hummingbot só para market making/arbitragem comprovados;
- FinRL somente para pesquisa offline;
- VectorBT somente após revisão da Commons Clause;
- TradingAgents como referência/research read-only;
- Claw3D/OpenClaw Office apenas como projeção visual read-only;
- não usar `py-clob-client` arquivado em produção;
- não copiar código sem licença declarada;
- não executar EAs comunitários na mesma conta do DZ23;
- não operar dois motores na mesma conta.

---

# 24. Definition of Done por tarefa

Uma tarefa só está concluída quando:

- o código foi implementado;
- testes foram adicionados ou atualizados;
- testes relevantes passaram;
- lint/typecheck passaram;
- documentação foi atualizada;
- logs e erros estão claros;
- segurança foi revisada;
- backward compatibility foi avaliada;
- o diff foi revisado;
- o risco residual foi documentado;
- o commit é pequeno e descritivo.

---

# 25. Formato de relatório de cada ciclo

Ao final de cada ciclo, escreva em `docs/progress.md`:

```markdown
## Ciclo N — título

### Objetivo

### Implementado

### Arquivos alterados

### Testes executados

### Resultado

### Riscos restantes

### Próximo ciclo
```

No chat, responda brevemente com:

- o que foi implementado;
- testes executados;
- resultado;
- riscos ainda abertos;
- próximo ciclo.

Não diga “está pronto” se ainda houver pendências críticas.

---

# 26. Instrução final

Comece agora pela auditoria do repositório e pela execução da suíte existente. Depois implemente, na ordem de maior risco:

1. integrar agentes, contratos, Risk Engine e ExecutionRouter;
2. completar Paper Ledger persistente;
3. completar reconciliação;
4. implementar kill switch;
5. fortalecer CCXT e MT5 Demo;
6. transformar workers em operação resiliente;
7. fortalecer backtest, walk-forward e Shadow Trading;
8. implementar observabilidade;
9. completar Trading Floor e onboarding;
10. executar testes de release;
11. criar documentação de instalação e operação;
12. continuar até que os critérios de pronto sejam demonstrados.

Não troque segurança por velocidade. Não troque evidência por afirmação. Não conecte dinheiro real. Não adicione dependências externas apenas por popularidade. Não crie múltiplas autoridades para risco, execução, posição ou reconciliação.

O resultado final deve ser um produto funcional para Paper e Demo, seguro por padrão, observável, auditável, documentado e preparado para posterior revisão operacional — sem afirmar que lucro ou crescimento de carteira são garantidos.

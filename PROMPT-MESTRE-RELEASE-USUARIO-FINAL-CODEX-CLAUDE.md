# PROMPT MESTRE — TradingAgents DZ23 até release de usuário final

## Instrução principal

Você é o engenheiro principal responsável por concluir o repositório `LMPrado-DZ23/tradingagents-dz23` até uma versão utilizável por usuários finais em **Paper, Shadow e MetaTrader 5 Demo**, com segurança operacional, auditoria, reconciliação, observabilidade, documentação e deployment reproduzível.

Trabalhe diretamente no repositório atual. Não recomece do zero, não substitua a arquitetura existente sem necessidade e não faça uma implementação apenas visual. Preserve o domínio, os contratos, o Risk Engine, o ExecutionRouter, o KillSwitch, o Paper Ledger, o Event Bus, os workers, o backtest, o walk-forward, o Shadow Trading e o adapter MT5 existentes.

O sistema deve funcionar como uma agência multiagente de trading, mas qualquer decisão de agente deve passar por contratos tipados, Risk Engine determinístico, ExecutionRouter, KillSwitch, reconciliação e auditoria. Texto de agente nunca pode ser tratado como prova de ordem, fill, posição, saldo ou PnL.

## Estado conhecido do projeto

A branch de desenvolvimento atual contém os commits de hardening e auditoria. Antes de alterar qualquer coisa:

```bash
git status --short
git branch --show-current
git log -5 --oneline
```

O último estado conhecido inclui:

- `ExecutionRouter` fail-closed;
- `KillSwitch` persistente e server-side;
- `PaperAdapter` persistente;
- reconciliação estruturada;
- Shadow Trading;
- Event Bus e auditoria;
- Backtest e walk-forward;
- API e Trading Floor inicial;
- adapter MT5 com testes mockados;
- CCXT Live bloqueado;
- endpoint legado de ordens removido;
- backup sem configuração ou segredos;
- smoke test da API;
- documentação operacional;
- branch publicada no GitHub.

A suíte limpa já passou com aproximadamente 925 testes, mas os números devem ser sempre recalculados no ambiente atual. Não invente resultados.

## Limites obrigatórios de segurança

Estas regras são inegociáveis:

1. `LIVE_TRADING=false` por padrão e sem caminho oculto para ativá-lo.
2. `REAL_MONEY_EXECUTION=false` por padrão.
3. Não implementar, ativar ou testar ordens com dinheiro real.
4. Não remover o KillSwitch.
5. Não permitir execução quando a reconciliação estiver `UNKNOWN`, `DIVERGED`, ausente ou desatualizada.
6. Não aceitar `reconciliation_ok=true` informado pelo cliente como prova.
7. O estado seguro deve ser calculado no servidor a partir de um provider real e autenticado.
8. Não permitir que o frontend envie ordens diretamente à corretora.
9. Toda ordem deve atravessar autenticação, autorização, validação de contrato, Risk Engine, ExecutionRouter, idempotência, auditoria e reconciliação.
10. Se uma dependência externa não estiver disponível, implemente a integração, testes e mensagens de bloqueio, mas não finja que a validação foi realizada.
11. Se houver dúvida entre disponibilidade e segurança, escolha fail-closed.
12. Não guardar chaves em código, Git, logs, backup ou respostas da API.

## Objetivo de modos operacionais

Implementar e deixar claramente separados:

| Modo | Envia ordens? | Objetivo |
|---|---:|---|
| Paper | Não | Simulação persistente local |
| Shadow | Não | Avaliação de sinais sem envio |
| MT5 Demo | Sim | Conta MetaTrader 5 com dinheiro virtual |
| Live | Não | Bloqueado nesta versão |

O usuário deve visualizar sempre o modo atual e o motivo de qualquer bloqueio.

# Fases obrigatórias de implementação

## Fase 0 — Baseline e checkpoint

Antes de codificar:

1. Leia `README.md`, `DEPLOY.md`, `docs/progress.md`, `docs/OPERATIONS-USER-READY.md` e os arquivos de auditoria.
2. Execute `git status`, `git diff`, `git log` e identifique arquivos não rastreados.
3. Crie ou atualize um checkpoint em `audit/AUTONOMOUS_MISSION_STATE.md`.
4. Liste os gates observáveis:
   - testes;
   - Ruff;
   - compileall;
   - smoke da API;
   - release check;
   - wheel/build;
   - Docker Compose;
   - MT5 Demo;
   - segurança;
   - acessibilidade;
   - documentação.
5. Nunca declare um gate como PASS sem executar o comando correspondente.

## Fase 1 — Reconciliação externa real

Implementar um contrato de provider de reconciliação:

```python
class ReconciliationProvider(Protocol):
    def snapshot(self) -> dict[str, Any]: ...
```

O snapshot deve representar, no mínimo:

- `account_id`;
- `venue`;
- saldo;
- equity;
- margem usada;
- margem disponível;
- ordens abertas;
- posições;
- fills/deals recentes;
- timestamp do provider;
- identificador da conta;
- versão/schema.

Requisitos:

- provider Fake/Determinístico para testes;
- provider Paper para integração local;
- provider MT5 Demo usando a biblioteca oficial `MetaTrader5` quando disponível;
- timeout;
- retry com backoff;
- circuit breaker;
- estado `UNKNOWN` em qualquer falha;
- tolerâncias configuráveis para valores numéricos;
- comparação de ordens, fills, posições, saldo e equity;
- relatório persistente;
- `MATCHED`, `DIVERGED` e `UNKNOWN` explícitos;
- callback que aciona o KillSwitch em `UNKNOWN`/`DIVERGED`;
- readiness bloqueado sem `MATCHED` recente;
- teste de convergência e divergência;
- teste de restart e recuperação.

O `ExecutionRouter` deve exigir reconciliação segura por contrato. Não deixe um default que permita execução sem provider ou guard válido.

## Fase 2 — MT5 Demo real e adapter robusto

A integração deve usar a biblioteca oficial `MetaTrader5` para o terminal MT5. Não substitua o MetaTrader por um clone parcial.

Implementar e testar:

- `initialize`;
- `shutdown`;
- `account_info`;
- `positions_get`;
- `orders_get`;
- histórico de deals;
- `symbol_info`;
- `symbol_info_tick`;
- `order_check`;
- `order_send` apenas quando o modo for explicitamente `DEMO`;
- cancelamento quando aplicável;
- retcodes;
- volume mínimo/máximo/step;
- casas decimais;
- stop level;
- spread;
- margem;
- slippage;
- reconexão;
- terminal indisponível;
- conta inválida;
- símbolo inexistente;
- ordem rejeitada;
- fill parcial;
- restart do worker;
- reconciliação pós-fill.

Não use mocks para declarar que o MT5 Demo foi validado. Mocks devem cobrir testes unitários; a validação Demo deve ser executada somente se o operador fornecer terminal, conta e ambiente externo apropriados. Se eles não existirem, o gate deve ficar `NOT_VALIDATED_EXTERNAL`.

## Fase 3 — Integridade financeira do Paper Ledger

Manter uma única fonte de verdade para:

- cash;
- balance;
- equity;
- realized PnL;
- unrealized PnL;
- margem;
- exposição;
- posições;
- fills;
- fees;
- slippage.

Adicionar invariantes:

- ordem rejeitada não cria ACK FILLED;
- over-close é rejeitado ou vira short explicitamente, nunca desaparece;
- restart preserva cash, posições, fills e PnL;
- `get_portfolio` e `mark_to_market` retornam a mesma equity para o mesmo estado;
- idempotência impede fill duplicado;
- saldo nunca é alterado por ordem rejeitada;
- fees e slippage são contabilizados;
- reconciliação detecta qualquer divergência.

Criar testes de propriedades/invariantes e casos de compra, fechamento parcial, fechamento completo, tentativa excedente, PnL realizado, posição aberta após PnL e restart.

## Fase 4 — Binding completo entre decisão e ordem

O contrato de execução deve derivar da decisão e amarrar todos os campos relevantes:

- `decision_id`;
- `run_id`;
- `strategy_id`;
- `desk_id`;
- `account_id`;
- `instrument_id`;
- `venue`;
- `side`;
- `order_type`;
- `time_in_force`;
- `quantity`;
- `price`;
- `stop_loss`;
- `take_profit`;
- `risk_amount`;
- `risk_pct_equity`;
- `signal_expiry`;
- `confidence`;
- `regime`;
- `evidence_ids`;
- `environment`.

Valide:

- decisão expirada;
- decisão não aprovada;
- decisão cancelada;
- mutação de qualquer campo;
- estratégia divergente;
- conta/venue divergentes;
- preço ou quantidade divergentes;
- risco divergente;
- evidência ausente.

Adicione um teste adversarial por campo vinculante.

## Fase 5 — Agência multiagente funcional

Conecte os agentes atuais ao ciclo real:

```text
market data
→ research agents
→ technical/fundamental/sentiment analysis
→ structured debate
→ committee vote
→ strategy proposal
→ risk decision
→ execution plan
→ router
→ fill
→ reconciliation
→ PnL
→ audit event
```

Cada reunião deve produzir contratos tipados, não apenas mensagens.

Implementar:

- quorum;
- prazo de reunião;
- votos ponderados apenas quando documentado;
- conflito de interesse/duplicidade;
- evidências por voto;
- decisão de desempate;
- cancelamento;
- timeout;
- replay;
- auditoria por agente;
- estado do agente;
- retry limitado;
- circuit breaker de agente;
- nenhuma decisão de LLM pode ultrapassar o Risk Engine.

## Fase 6 — UI pronta para usuário final

Evoluir a Trading Floor sem misturar animação com estado financeiro real.

Telas mínimas:

- onboarding;
- login;
- dashboard;
- Trading Floor;
- agentes e desks;
- reuniões/comitês;
- estratégias;
- Paper;
- Shadow;
- MT5 Demo;
- posições;
- ordens;
- fills;
- PnL;
- risco;
- reconciliação;
- KillSwitch;
- saúde dos brokers;
- auditoria;
- configurações;
- backup/restore.

Requisitos de UX:

- loading explícito;
- timeout;
- retry;
- erro compreensível;
- estados vazios;
- indicador de conexão;
- última atualização;
- modo operacional visível;
- bloqueio visível com explicação;
- confirmação para ações de risco;
- nenhuma ordem direta pelo frontend;
- `textContent`/DOM seguro, sem `innerHTML` com dados externos;
- validação de ticker no backend;
- escape de todos os campos renderizados;
- sem função JavaScript morta apontando para endpoint legado.

Acessibilidade mínima:

- HTML semântico;
- botões reais;
- labels com `for`/`id`;
- foco visível;
- navegação por teclado;
- `aria-live` para status;
- `aria-selected` em tabs;
- contraste adequado;
- mensagens de erro associadas aos campos.

Criar testes de frontend e pelo menos uma verificação automatizada de payload malicioso.

## Fase 7 — Autenticação e RBAC

Substituir a senha compartilhada por autenticação individual quando possível. Se a migração completa exigir etapa própria, implemente a base agora e deixe o modo legado explicitamente restrito e documentado.

Papéis:

- Viewer;
- Analyst;
- Operator;
- Risk Manager;
- Admin;
- Auditor.

Separar permissões de:

- leitura;
- configuração;
- análise;
- Paper;
- Shadow;
- MT5 Demo;
- KillSwitch;
- reset de reconciliação;
- backup/restore;
- gerenciamento de usuários.

Registrar o ator em toda ação sensível. Nenhum papel deve liberar Live.

## Fase 8 — Operação 24/7

Implementar e testar:

- supervisor;
- restart automático;
- heartbeat;
- backoff;
- timeout;
- circuit breaker;
- recuperação de estado;
- reconciliação no startup;
- KillSwitch persistente após restart;
- locks contra duplicidade;
- rotação de logs;
- limites de disco;
- backup automático;
- restauração;
- shutdown seguro;
- alertas;
- health/readiness/liveness.

A sequência de startup deve ser:

```text
processo iniciou
→ KillSwitch ativo
→ carregar estado
→ conectar provider
→ reconciliar
→ exigir MATCHED
→ validar limites
→ iniciar workers
→ liberar somente o modo autorizado
```

## Fase 9 — Observabilidade

Adicionar métricas e logs estruturados para:

- agentes ativos;
- reuniões;
- decisões;
- rejeições de risco;
- ordens submetidas;
- fills;
- erros;
- latência;
- reconciliação;
- KillSwitch;
- PnL;
- drawdown;
- desconexões;
- retries;
- circuit breakers;
- fila;
- memória/disco.

Toda operação deve ser correlacionável por:

- `run_id`;
- `meeting_id`;
- `decision_id`;
- `client_order_id`;
- `order_id`;
- `fill_id`;
- `account_id`.

## Fase 10 — Docker e deployment

Validar em host com Docker quando disponível:

```bash
docker compose config
docker compose build
docker compose up -d
docker compose ps
curl /health
curl /ready
docker compose restart
docker compose logs --tail=200
docker compose down
```

Garantir:

- volume persistente;
- usuário não-root;
- secrets fora da imagem;
- healthcheck;
- restart policy;
- limites de CPU/memória;
- TLS/reverse proxy documentado;
- backup e restore;
- permissões 600 para arquivos sensíveis;
- migração sem perder estado.

Se Docker não estiver disponível, não marque o gate como PASS.

## Fase 11 — Qualidade e CI

Corrigir o lint integral ou delimitar formalmente exceções justificadas. O objetivo é:

```bash
ruff check .
python -m compileall -q .
python -m pytest -q
python scripts/smoke_agency_api.py
python scripts/release_check.py
```

Criar/atualizar CI para executar:

- lint;
- compileall;
- testes unitários;
- testes de segurança;
- smoke API;
- build/wheel;
- dependency audit;
- Docker Compose quando o runner permitir;
- testes de frontend;
- verificação de ausência de Live.

Remova a dependência de variáveis de endpoint herdadas do ambiente. Configure explicitamente a matriz de ambiente nos testes.

## Fase 12 — Documentação e onboarding

Atualizar:

- README;
- DEPLOY;
- runbook operacional;
- configuração;
- Paper/Shadow/Demo;
- segurança;
- RBAC;
- reconciliação;
- MT5;
- troubleshooting;
- backup/restore;
- KillSwitch;
- recuperação de incidentes;
- limitações;
- política de Live bloqueado.

O onboarding deve permitir ao usuário:

1. instalar;
2. configurar ambiente;
3. iniciar em Paper;
4. visualizar a agência;
5. executar Shadow;
6. conectar MT5 Demo;
7. validar reconciliação;
8. acompanhar ordens e PnL;
9. realizar backup;
10. parar com segurança.

## Gates de aceite

Não declare concluído até verificar cada item:

- [ ] Reconciliador remoto implementado e testado com Fake provider.
- [ ] Reconciliador MT5 implementado.
- [ ] Paper só executa com reconciliação segura.
- [ ] MT5 Demo passou por validação real ou está explicitamente bloqueado por dependência externa.
- [ ] Nenhuma ordem Live é possível.
- [ ] KillSwitch persiste após restart.
- [ ] Paper Ledger mantém invariantes financeiras.
- [ ] Binding de decisão e ordem é completo.
- [ ] Agentes produzem decisões estruturadas.
- [ ] UI possui loading, erro, retry e estados vazios.
- [ ] UI não usa renderização insegura de dados externos.
- [ ] Acessibilidade básica validada.
- [ ] RBAC implementado ou claramente marcado como bloqueador.
- [ ] Backup/restore testado.
- [ ] Observabilidade implementada.
- [ ] Docker validado no host apropriado ou marcado como externo.
- [ ] Ruff integral passa ou exceções estão documentadas.
- [ ] Testes completos passam em ambiente hermético.
- [ ] Smoke passa.
- [ ] Documentação reproduz os comandos atuais.
- [ ] Git worktree limpo.
- [ ] Commit e branch remota verificados.

## Processo obrigatório de execução

Para cada fase:

1. inspecione o código existente;
2. faça uma mudança pequena e reversível;
3. escreva testes antes ou junto da implementação;
4. execute testes focados;
5. execute lint/compileall;
6. atualize a documentação;
7. atualize o checkpoint;
8. execute regressão;
9. somente então faça commit;
10. publique a branch;
11. verifique o SHA remoto;
12. registre evidências.

Não faça um commit gigante sem validação intermediária.

## Política contra alegações falsas

Nunca escreva “pronto para produção”, “MT5 validado”, “Demo aprovado”, “24/7 comprovado”, “lint passou” ou “usuário final pronto” se o comando ou ambiente correspondente não tiver sido executado.

Diferencie sempre:

- `PASS`;
- `PASS_CLEAN_ENV`;
- `PASS_MOCK_ONLY`;
- `NOT_VALIDATED_EXTERNAL`;
- `BLOCKED_EXTERNAL`;
- `FAIL`;
- `PENDING`.

## Entrega final obrigatória

Ao concluir a missão:

1. publique todos os commits na branch autorizada;
2. deixe o worktree limpo;
3. atualize `docs/progress.md`;
4. atualize `audit/AUTONOMOUS_MISSION_STATE.md`;
5. crie `audit/FINAL_THREE_AGENT_REVIEW.md`;
6. informe o SHA final;
7. informe os comandos executados;
8. informe os testes e resultados reais;
9. informe os gates que passaram;
10. informe claramente os gates externos que não puderam ser validados;
11. forneça instruções de retomada para qualquer pendência.

A conclusão correta pode ser `COMPLETED` somente quando todos os gates aplicáveis tiverem evidência. Caso falte terminal MT5, Docker, credencial ou permissão externa, use `BLOCKED` ou `CANDIDATE_COMPLETED` e descreva exatamente o que o operador precisa fornecer.

## Primeira ação

Comece agora executando:

```bash
git status --short
git branch --show-current
git log -5 --oneline
python3 scripts/release_check.py
python3 -m compileall -q tradingagents service scripts
```

Depois leia os documentos de progresso e o checkpoint, monte uma matriz de pendências e implemente as fases na ordem de risco: reconciliação → integridade financeira → MT5 Demo → segurança/RBAC → operação 24/7 → UX → Docker → CI/documentação.

Não habilite Live em nenhuma circunstância.

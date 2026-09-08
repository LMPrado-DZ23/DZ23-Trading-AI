# TradingAgents DZ23 A5 — Design da plataforma SaaS

**Status:** aprovado pelo dono para execução autônoma em 2026-09-08.  
**Base:** `main@4cdd1fcabfaf89411b4e44d0b8e6b2a5359a3ef7`.  
**Destino:** `https://trading.dz23.online/` no VPS `82.29.59.71`.  
**Contrato financeiro detalhado:** `C:\TradingAgents-Handoff\entrada\ESPECIFICACAO_CARTEIRA_PERFIS_RISCO_A5.md`.

## Resultado de produto

Uma pessoa sem conhecimento técnico cria conta, define capital e objetivo, responde suitability, escolhe perfil de risco, conecta uma corretora, inicia em SHADOW/PAPER, visualiza uma carteira explicável e aprova rebalanceamentos. A automação só é liberada progressivamente, dentro de mandato versionado e limites determinísticos. O produto mede risco e retorno; não promete lucro nem ausência de perdas.

## Abordagem escolhida

Implementar um **monólito modular** com dois processos:

- API FastAPI: autenticação, autorização, comandos, consultas e SSE;
- worker Python: análises, backtests, reconciliação e execução de ordens.

PostgreSQL é o sistema de registro e fila durável via `FOR UPDATE SKIP LOCKED`. Redis serve apenas rate limit, cache e sinais efêmeros; sua perda não pode corromper estado financeiro. O frontend é React + TypeScript + Vite, compilado e servido como artefato estático. Essa composição mantém fronteiras fortes sem introduzir microserviços antes de haver escala que os justifique.

## Contextos de domínio

1. **Identity:** users, tenants, memberships, sessions, MFA e recovery.
2. **Compliance:** suitability, consentimentos, termos, privacidade e retenção.
3. **Connections:** credenciais criptografadas, versões, permissões e diagnóstico.
4. **Analysis:** RunContext imutável, LangGraph, evidências PIT e resultados.
5. **Portfolio:** mandatos, policies, ledger, posições, valuations e PnL.
6. **Risk:** sizing, alocação, caps, drawdown e circuit breakers.
7. **Execution:** proposals, approvals, intents, reservations, orders, fills e reconciliation.
8. **Backtest:** datasets/snapshots, next-open, custos, métricas e comparação.
9. **Audit:** eventos append-only e exportação.
10. **Operations:** health, readiness, métricas, logs, traces, backup e restore.

Nenhum módulo acessa tabelas de outro contexto diretamente. Interações usam serviços de aplicação tipados e transações explícitas.

## Segurança e tenancy

- `tenant_id` obrigatório em toda entidade do cliente e imposto por PostgreSQL RLS.
- Sessão em cookie `HttpOnly`, `Secure`, `SameSite=Lax`; CSRF em mutações.
- Senhas Argon2id; sessões rotacionáveis e revogáveis; MFA/step-up para ações de capital.
- RBAC mínimo: `owner`, `admin`, `trader`, `viewer`.
- Rate limit distribuído considera conta, tenant, IP normalizado e ação.
- Cabeçalhos de proxy só são aceitos de proxies configurados.
- CSP sem scripts inline, HSTS, frame denial, MIME sniffing denial e referrer policy.
- Credenciais de corretora usam envelope encryption e versões; plaintext existe somente na memória do worker durante chamada necessária.
- API nunca devolve segredos, tracebacks ou mensagens brutas de fornecedor.
- Egress de LLM/exchange usa allowlist e a policy de transporte existente.

## Pipeline financeira

`Signal → TargetAllocation → RiskDecision → RebalanceProposal → Approval → ExecutionIntent → Reservation → Outbox → BrokerAdapter → OrderState → Fill → Ledger → Reconciliation`

Invariantes:

- LLM não informa quantidade executável.
- O cliente não envia `symbol`, `side` ou `amount` livres para execução.
- Intenção contém hashes do mandato, policy, sinal, snapshot, market metadata e credential version.
- Toda alteração invalida aprovação anterior.
- Reserva e outbox são gravadas na mesma transação.
- Timeout após submit gera `UNKNOWN`; nunca retry antes de reconciliar.
- Ordens abertas e reservas entram na exposição.
- Circuit breaker sobrevive a restart.
- LIVE é desabilitado por padrão e nunca é liberado por frase fixa.

## Modos progressivos

- **BACKTEST:** dados PIT, sem rede de corretora.
- **SHADOW:** sinais e propostas com dados atuais, sem criar ordem.
- **PAPER:** venue testnet real quando disponível ou ledger simulado claramente identificado.
- **LIVE_SUPERVISED:** usuário com MFA aprova cada proposta válida.
- **LIVE_AUTOMATED:** permitido somente após critérios objetivos e dentro de mandato; cada execução permanece auditável e revogável.

O perfil efetivo é o mais restritivo entre escolha do usuário, suitability, policy da plataforma, capacidade da venue e estado de risco.

## Persistência

PostgreSQL contém users, tenants, memberships, sessions, consents, suitability assessments, risk policies, mandates, connections, credential versions, analysis jobs, run contexts, signals, portfolio accounts, ledger entries, positions, valuations, proposals, approvals, intents, reservations, outbox events, orders, fills, reconciliation runs, circuit breakers e audit events.

O ledger é append-only. Saldos materiais são derivados e periodicamente reconciliados com a venue. Transações financeiras usam `Decimal`, moeda explícita, constraints e locks. JSON é reservado a payloads versionados que não substituem invariantes relacionais.

## Jobs e concorrência

- worker adquire lease com `FOR UPDATE SKIP LOCKED`;
- heartbeat e expiração permitem recuperação após crash;
- idempotency key inclui tenant, operação e digest do payload;
- retries são classificados e limitados;
- cancelamento é cooperativo e não falsifica término;
- RunContext contém workflow schema, provider, model, endpoint policy, ticker, data, timezone, tenant e config digests;
- nenhum job modifica `os.environ` ou configuração global compartilhada.

## Frontend

Rotas principais:

- `/entrar`, `/recuperar`, `/mfa`;
- `/onboarding`;
- `/inicio`;
- `/carteira`;
- `/carteira/risco`;
- `/propostas` e `/ordens`;
- `/analises` e `/backtests`;
- `/conexoes`;
- `/auditoria`, `/privacidade` e `/configuracoes`.

O painel prioriza patrimônio, PnL líquido, drawdown, risco usado, caixa, posições e ações pendentes. Todos os fluxos têm loading, empty, offline, erro recuperável e sucesso. WCAG 2.2 AA, teclado, foco, contraste e viewports 360/768/1440 são gates automatizados.

## Operação

Compose de produção contém reverse proxy existente, web/API, worker, PostgreSQL, Redis e jobs de migração/backup. Imagens são non-root, pinadas, com health/readiness. Segredos ficam fora da imagem e do Git. Deploy é versionado, com backup anterior, migrations compatíveis, smoke e rollback.

## Fronteiras de release

- O domínio pode entrar no ar em SHADOW/PAPER antes de LIVE.
- “Site no ar” não significa sistema aprovado para capital.
- LIVE exige testes reais mínimos por exchange, auditoria independente e promoção humana.
- A indisponibilidade de credenciais externas não autoriza mocks apresentados como prova.


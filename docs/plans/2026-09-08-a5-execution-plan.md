# TradingAgents DZ23 A5 — Plano de execução e aceite

Cada marco termina em commit verificável, atualização do checkpoint e evidência própria. Um marco não herda “verde” por declaração do implementador.

## M0 — Contenção, baseline e contratos

**Objetivo:** impedir que a superfície insegura atual seja confundida com produto final e fixar contratos.

- [x] desabilitar `/v1/broker/order` e configuração LIVE legados fora de teste;
- [x] declarar versão real do pacote e dependências reproduzíveis, incluindo CCXT;
- [x] preservar e executar os 849 testes Gate A;
- [x] criar contratos tipados para tenant, mandato, signal, proposal, approval, intent e order state;
- [x] adicionar ADR inicial de tenancy, ledger, jobs e progressive live;
- [x] estabelecer secret scan e lint; dependency audit, typecheck e cobertura seguem como gates transversais de release.

**Gate:** nenhuma ordem real possível; baseline integral reproduzido em ambiente limpo; contratos e threat model revisados.

## M1 — Identidade, multitenancy e LGPD

- [x] FastAPI modular e settings tipados fail-closed;
- [x] PostgreSQL + Alembic;
- [x] users, tenants, memberships e papéis;
- [x] cadastro, verificação, login, logout, recovery e sessões revogáveis;
- [x] Argon2id, cookies seguros, CSRF e MFA/step-up;
- [x] RLS em todas as tabelas tenant-aware;
- [x] consentimentos/termos versionados, exportação e solicitação de exclusão;
- [x] auditoria de autenticação e ações administrativas;
- [x] rate limit distribuído e trusted proxy.

**Gate:** testes com dois tenants provam ausência de IDOR em API, worker, exportação e RLS; recovery/MFA E2E verdes.

## M2 — Cofre e conexões

- [ ] envelope encryption com chave-mestra externa e rotação;
- [ ] conexão por tenant/exchange/ambiente e credential version;
- [ ] catálogo explícito: Binance, Bybit, OKX, Coinbase, Kraken, Gate.io, KuCoin, Mercado Bitcoin e Bitget;
- [ ] capability matrix por exchange;
- [ ] diagnóstico read-only de autenticação, saldo, spot, mercado e permissões;
- [ ] recusa de saque, margem, derivativos e permissões excessivas;
- [ ] separação de credenciais de LLM e egress allowlisted;
- [ ] remoção real de credencial e trilha de acesso.

**Gate:** nenhum plaintext em banco/log/API; contratos reais de sandbox/testnet onde disponíveis; permissões perigosas recusadas.

## M3 — Jobs duráveis, RunContext e PIT

- [ ] fila PostgreSQL com lease, heartbeat, retry e cancelamento;
- [ ] API e worker em processos separados;
- [ ] quotas por tenant e custo/tokens por job;
- [ ] RunContext imutável e workflow schema version;
- [ ] remover mutação de `os.environ` e configuração global no caminho concorrente;
- [ ] checkpoint com tenant/config/provider/schema digests;
- [ ] fundamentals filtrados por publicação disponível;
- [ ] Polymarket histórico ou fail-closed;
- [ ] `available_at` obrigatório nas fontes usadas historicamente;
- [ ] manifesto reprodutível da análise.

**Gate:** restart recupera jobs; dois tenants executam simultaneamente sem estado cruzado; suite PIT adversarial verde.

## M4 — Carteira, suitability e motor de risco

- [ ] implementar integralmente `ESPECIFICACAO_CARTEIRA_PERFIS_RISCO_A5.md`;
- [ ] suitability versionado e perfil efetivo mais restritivo;
- [ ] policies Preservação, Conservador, Moderado, Arrojado e Agressivo;
- [ ] mandato e lifecycle;
- [ ] ledger, caixa, reservas, posições, valuations e PnL;
- [ ] sizing por volatilidade, liquidez, custo e caps;
- [ ] correlação/concentração e rebalance bands;
- [ ] circuit breakers persistentes;
- [ ] snapshots e relatórios de risco.

**Gate:** property-based tests provam caps, precisão, concorrência e fail-closed; LLM não alcança quantidade executável.

## M5 — Backtest e avaliação

- [ ] BacktestRunner por intervalo e universo;
- [ ] decisão em `session_close(D)` e fill raw next-open;
- [ ] splits/dividendos e calendário por instrumento;
- [ ] fees, slippage, FX, liquidez, lote e delisting;
- [ ] equity curve, PnL, Sharpe, Sortino, drawdown, hit rate, turnover e alpha;
- [ ] walk-forward, embargo e múltiplos regimes;
- [ ] benchmarks por mandato;
- [ ] relatórios JSON/Markdown e comparação entre perfis;
- [ ] critérios quantitativos de promoção SHADOW/PAPER.

**Gate:** datasets/manifestos reproduzíveis, zero look-ahead conhecido e resultados líquidos de custos.

## M6 — Propostas, aprovação e execução CCXT

- [ ] gerar TargetAllocation e RebalanceProposal explicáveis;
- [ ] aprovação MFA vinculada a digest e expiração;
- [ ] ExecutionIntent imutável, reserva e outbox transacionais;
- [ ] limit order por padrão e política explícita para market;
- [ ] validação de mercado, precisão, lote, cost, saldo, taxa e slippage;
- [ ] adapter idempotente por exchange;
- [ ] máquina `CREATED/APPROVED/SUBMITTING/UNKNOWN/ACCEPTED/OPEN/PARTIAL/FILLED/CANCELLED/REJECTED`;
- [ ] reconciliação por client ID/order ID/trades;
- [ ] kill switches global, tenant e carteira;
- [ ] SHADOW, PAPER, LIVE_SUPERVISED e LIVE_AUTOMATED.

**Gate:** accepted-then-timeout, clique duplo, workers concorrentes, partial fill, cancel e restart resultam em exatamente uma intenção e ledger correto.

## M7 — Produto para leigos

- [ ] React/TypeScript/Vite com design system e i18n pt-BR;
- [ ] onboarding guiado completo;
- [ ] dashboard de patrimônio, risco, caixa e ações pendentes;
- [ ] carteira, risco, propostas, ordens, análises, backtests e conexões;
- [ ] explicações monetárias e cenários adversos por perfil;
- [ ] pausa em um clique e liquidação separada;
- [ ] estados loading/empty/offline/error/success;
- [ ] WCAG 2.2 AA e responsividade;
- [ ] ajuda contextual, glossário e guia de primeiro uso;
- [ ] Playwright + axe para jornadas críticas.

**Gate:** usuários de teste concluem a jornada sem CLI; E2E 360×800, 768×1024 e 1440×900 sem bloqueadores.

## M8 — Observabilidade, suporte e administração

- [ ] logs JSON com correlação e redação;
- [ ] métricas de API, worker, LLM, broker, fila, reconciliação, risco e custo;
- [ ] traces sem segredos;
- [ ] alertas de UNKNOWN, divergência, circuit breaker, falha de backup e indisponibilidade;
- [ ] painel administrativo com suporte auditado e sem impersonation silenciosa;
- [ ] status, incidentes, retenção e exportação;
- [ ] runbooks de incidente, chave comprometida e reconciliação.

**Gate:** falhas injetadas geram alertas acionáveis; zero segredos em logs/traces.

## M9 — Build, CI/CD e infraestrutura

- [ ] lockfiles/hashes e SBOM;
- [ ] imagens pinadas, non-root e filesystem mínimo;
- [ ] Compose de produção com web, worker, Postgres, Redis e backup;
- [ ] health/readiness, migrations e graceful shutdown;
- [ ] backup criptografado e restore provado;
- [ ] CI: lint, typecheck, unit, integration, RLS, security, dependency, secret, build e E2E;
- [ ] staging e produção separados;
- [ ] Traefik/TLS para `trading.dz23.online`;
- [ ] deploy versionado, smoke e rollback.

**Gate:** instalação limpa, persistência após restart, restore e smoke HTTPS autenticado.

## M10 — Qualificação, auditorias e release

- [ ] SHADOW/PAPER com amostra e duração definidas antes da execução;
- [ ] teste real mínimo por exchange anunciada, sem usar credencial exposta;
- [ ] Auditor A: arquitetura/engenharia;
- [ ] Auditor B: segurança/DevSecOps;
- [ ] Auditor C: produto/QA/UX;
- [ ] consolidar `audit/FINAL_THREE_AGENT_REVIEW.md`;
- [ ] corrigir/retestar até `CRITICAL=0`, `HIGH=0`, blockers internos=0;
- [ ] gerar candidate/evidence/SBOM/hashes;
- [ ] promoção humana;
- [ ] push no remoto autorizado;
- [ ] deploy e validação externa do domínio.

**Gate final:** todas as jornadas essenciais comprovadas em produção. LIVE só recebe aprovação separada quando os testes de venue e capital mínimo forem reais e auditados.

## Dependências externas a obter sem bloquear código independente

- chave SSH correta para `82.29.59.71`;
- DNS de `trading.dz23.online` apontado ao VPS e controle do Traefik/Coolify;
- serviço SMTP transacional para verificação/recovery;
- credenciais novas e limitadas das exchanges a certificar;
- decisão jurídica sobre oferta a terceiros, suitability, termos e retenção;
- autorização explícita de notional para cada teste LIVE mínimo.

Nenhuma credencial deve ser colada em chat ou commit.

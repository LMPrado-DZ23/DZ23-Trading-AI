# TRADING DECISIONS

## ADR-001 — Manter a arquitetura atual e preservar o runtime em branch reversível

- Decisão: não reescrever o repositório em massa; preservar `service/app.py`, `tradingagents/`, workflows e PRs atuais.
- Motivação: a base já contém módulos de FastAPI, execução, papershadow e agentes que fazem parte da solução de trading pessoal; a correção incremental tem menor risco do que uma reestruturação massiva.
- Alternativas descartadas: reescrita total em `service/api/*`, migração de módulos antigos para uma nova estrutura sem prova de compatibilidade, ou merge direto em `main`.
- Consequências: mais trabalho de revisão e integração, mas menor risco de regressão e melhor rastreabilidade.

## ADR-002 — LIVE continua bloqueado por política e arquitetura

- Decisão: qualquer caminho de execução real permanece inacessível até nova autorização do proprietário, validação técnica e mudança explícita de gate.
- Motivação: o projeto exige claramente `LIVE_TRADING=false` e `REAL_MONEY_EXECUTION=false`, além de constraints de risco.
- Alternativas descartadas: ativar LIVE por env, alterar flags por efeito colateral ou permitir `paper` como `live`.
- Consequências: o sistema pode continuar em PAPER/SHADOW/testnet sem violar o requisito de segurança.

## ADR-003 — Single-owner e catálogo pessoal, sem SaaS

- Decisão: não desenvolver multi-tenancy, billing público ou camada de clientes; manter a solução orientada ao dono único.
- Motivação: os objetivos do repositório apontam para um laboratório pessoal e não para um produto comercial multiusuário.
- Alternativas descartadas: manter ou reintroduzir `tenant`, `signup`, `Woovi` e billing em runtime.
- Consequências: a arquitetura se mantém mais simples e mais segura para uso pessoal isolado.

## ADR-004 — CI antes de qualquer release candidate

- Decisão: a validação real do projeto deve ocorrer em CI, pois o ambiente local não representa a matriz de versões do projeto.
- Motivação: dependências pesadas e requisitos de entorno do projeto fazem a suíte completa não ser sempre reproduzível localmente.
- Alternativas descartadas: sinalizar sucesso sem logs/CI ou assumir que a suíte passa apenas por análise estática.
- Consequências: o gate é mais exigente, mas a evidência possui confiabilidade maior.

## ADR-005 — Multimercado começa como política, não como execução (E6)

- Decisão: registrar as classes de ativo do escopo (cripto spot/futuros; B3 ações/FIIs/ETFs; US ações/ETFs) com metadados de política — moeda de cotação, lote, tick, sessão/calendário, liquidação e taxas de referência — em `tradingagents/desk/mercados.py`. Nenhum adaptador de execução novo é ativado: classe sem `execucao_homologada` não abre sala nem migra histórico. A classe entra nas salas como dimensão nova: a chave legada de 4 partes continua valendo e a chave estendida só existe quando a classe é declarada; a passagem é migração explícita (`RegistroDeSalas.migrar_sala`), que exige posições fechadas e redireciona a chave antiga para a sala viva (nada de estado duplicado).
- Motivação: o escopo E6 pede fundação multimercado sem prometer execução não homologada. Sessão fail-closed (fim de semana, feriado, fora da janela), horário de verão por regra auditável e validação de lote preparam B3/US com testes — mantendo LIVE bloqueado por política (ADR-002).
- Alternativas descartadas: ativar APIs de corretoras estrangeiras de imediato; depender de `tzdata`/`zoneinfo` do sistema para horário de verão (varia por máquina — regra explícita é auditável e reproduzível); liberar pregão "por heurística" (`não é cripto`) em vez de classe declarada (fail-closed exige identidade explícita).
- Consequências: salas legadas continuam válidas sem toque; quem quiser classe migra conscientemente e a chave antiga segue resolvendo para a mesma sala; qualquer execução em ações/ETFs/FIIs exige homologação futura do proprietário (adaptador homologado + calibração de taxas/lotes e revisão anual dos feriados).

### Avaliação escrita — Interactive Brokers, Alpaca e APIs oficiais da B3

Critérios: (1) API oficial documentada para ordens; (2) cobertura dos mercados do escopo; (3) aderência ao modelo DZ23 (PAPER primeiro, executor plugável, auditoria); (4) custo de homologação; (5) atrito operacional.

- **Interactive Brokers (IBKR)** — API oficial TWS API (documentação pública em `interactivebrokers.com/docs/tws-api`; também Web API). Cobertura ampla de mercados. Atrito operacional: exige o gateway (TWS ou IB Gateway) rodando localmente com sessão autenticada do usuário — acoplamento maior para um ciclo autônomo. Avaliação: candidato de segunda onda (execução multi-mercado), não o primeiro adaptador.
- **Alpaca** — API developer-first (REST/WebSocket), sem gateway local, criada para automação desde o design, com paper trading nativo (alinhada ao fluxo PAPER-first do DZ23). Cobertura inicial: ações/ETFs dos EUA. Avaliação: melhor primeiro candidato para o mercado US **quando e se** o proprietário autorizar homologação; pendências: validar calendário de feriados/meios-pregões contra o snapshot de política e calibrar custos (corretagem zero típica, taxas de venue fora da estimativa).
- **APIs oficiais da B3** — para roteamento de ordens existe o caminho institucional (PUMA Trading System, gateway EntryPoint para desenvolvedores/vendors, com processo de certificação e testes de continuidade; adoção de APIs binárias opcionais em curso em 2026). Investidor individual normalmente acessa via corretora. Avaliação: caminho oficial existe, mas pressupõe certificação como participante/vendor ou parceria com corretora — trabalho de homologação real, fora do escopo deste checkpoint. As classes `b3_*` ficam registradas como política, com números a calibrar.
- **Decisão do checkpoint E6**: nenhum adaptador novo ativado. Ficam: registro das classes + sessões fail-closed + esta avaliação. Ordem sugerida de homologação futura (se autorizada): Alpaca paper (US) → adaptador de corretora B3 → IBKR.
- Fontes: [B3 — PUMA Trading System](https://www.b3.com.br/en_us/solutions/platforms/puma-trading-system/) · [B3 — EntryPoint (desenvolvedores e vendors)](https://www.b3.com.br/en_us/solutions/platforms/puma-trading-system/for-developers-and-vendors/entrypoint/) · [B3 — como certificar (scripts de certificação)](https://www.b3.com.br/en_us/solutions/platforms/puma-trading-system/for-developers-and-vendors/how-to-certify/certification-scripts/) · [B3 — teste de continuidade de negócios](https://clientes.b3.com.br/en/w/teste-de-continuidade-de-neg%C3%B3cios-da-plataforma-eletr%C3%B4nica-puma-trading-system-%7C-business-continuity-test-of-puma-trading-system) · [Onixs — B3 Binary APIs em 2026](https://www.onixs.biz/insights/b3-binary-apis-2026) · [IBKR — TWS API (documentação)](https://www.interactivebrokers.com/docs/tws-api/doc/introduction) · [IBKR — soluções de API](https://www.interactivebrokers.com/en/trading/ib-api.php) · [Alpaca — o que difere de outros brokers com API](https://alpaca.markets/support/alpaca-api-broker) · [IMRYN — IBKR API vs Alpaca para automação (2026-08-11)](https://imryn.com/blog/2026-08-11-alpaca-vs-interactive-brokers-api/) · [Broker Insight — melhores APIs de broker para trading algorítmico 2026](https://broker-insight.com/blog/best-broker-apis-algorithmic-trading)

## ADR-006 — A mesa recusa LIVE sem selo de CÓDIGO (auditoria FIN-1)

- Decisão: `build_desk_broker` recusa construir corretora em modo LIVE sem `live_authorized=True` — autorização que só existe por construção explícita em código auditado. O serviço nunca a concede (o mandato da mesa é PAPER por construção) e o checklist de "Dinheiro real: posso?" declara o selo como item não pronto, com a ação explicada.
- Motivação: a auditoria independente (FIN-1, commit 4b83e16) provou que a configuração salva pelo painel — `TRADINGAGENTS_DESK_BROKER=ccxt` + `TRADINGAGENTS_BROKER_MODE=live` + frase + tetos + chaves — construía corretora LIVE com mandato PAPER. O adaptador da mesa é o único caminho de produção até `create_order` (desk/brokers.py → ccxt_bridge.submit_order): faltava exatamente a trava de mesa, e o painel seria a última fechadura de dinheiro real.
- Alternativas descartadas: confiar só nos quatro freios da ponte (deixam `create_order` alcançável por configuração salva); degradar LIVE→paper silenciosamente (mentiria o estado real); remover o modo LIVE do código (apagaria o caminho auditável e os testes de freio que o exercitam — a ponte direta continua construível nos testes com `live_authorized=True`).
- Consequências: LIVE continua bloqueado por política (ADR-002) e os quatro freios do dono seguem valendo por cima do selo; abrir o selo no futuro exige mudança de código revisada + nova decisão registrada aqui + os testes adversariais de `tests/test_auditoria_fin1_live_desk_seal.py` continuam verdes por cima.


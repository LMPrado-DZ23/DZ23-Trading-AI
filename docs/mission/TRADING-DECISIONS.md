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


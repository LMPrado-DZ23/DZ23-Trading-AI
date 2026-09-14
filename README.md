# TradingAgents DZ23

Framework open source de agência multiagente para pesquisa, debate estruturado, gestão de risco, Paper Trading, Shadow Trading e preparação controlada para MetaTrader 5 Demo.

> **Aviso importante:** este projeto é software experimental para pesquisa e engenharia. Não é recomendação de investimento, não garante lucro e pode causar perdas financeiras. A configuração publicada mantém `LIVE_TRADING=false` e `REAL_MONEY_EXECUTION=false`.

## O que existe hoje

- Agentes de pesquisa, análise técnica, sentimento, estratégia, risco, portfólio e execução.
- Contratos tipados entre decisão, risco, ordem, fill, posição, PnL e auditoria.
- Risk Engine determinístico e `ExecutionRouter` fail-closed.
- KillSwitch persistente e reconciliação explícita.
- Paper Ledger persistente.
- Shadow Trading sem envio de ordens.
- Backtest e walk-forward.
- Event Bus, auditoria encadeada, métricas e workers.
- Agência multi-tenant com usuários, memberships, papéis e sessões assinadas.
- Backend PostgreSQL opcional para identidade; SQLite para desenvolvimento local.
- Docker Compose com aplicação, PostgreSQL, Redis, Qdrant e Ollama opcional.
- Adapter MetaTrader 5 restrito ao modo Demo; validação de conta real depende de ambiente externo.

## Estado de segurança

| Modo | Ordens enviadas | Estado |
| --- | ---: | --- |
| Paper | Não | Disponível |
| Shadow | Não | Disponível |
| MT5 Demo | Sim, somente conta virtual | Requer terminal e conta Demo externos |
| Live | Não | Bloqueado nesta versão |

Nenhum agente ou componente de frontend pode enviar ordens diretamente à corretora. A cadeia obrigatória é:

```text
agentes → decisão tipada → Risk Engine → ExecutionRouter → adapter → auditoria → reconciliação
```

## Execução local rápida

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
python3 -m pytest -q
python3 scripts/smoke_agency_api.py
```

Para iniciar a API localmente:

```bash
ACCESS_PASSWORD='use-uma-senha-local-forte' \
uvicorn service.app:app --host 127.0.0.1 --port 8080
```

## Deployment multi-tenant com Docker

Para o deployment Enterprise:

```bash
cp .env.enterprise.example .env
# preencha os secrets sem colocá-los no Git
docker compose config
docker compose build
docker compose up -d
docker compose ps
```

O primeiro administrador é criado uma única vez por `/v1/auth/bootstrap`. Depois, use `/v1/auth/login`. Consulte o [runbook de deployment](DEPLOY.md) e a [documentação operacional](docs/OPERATIONS-USER-READY.md).

## Desenvolvimento e contribuição

Leia [CONTRIBUTING.md](CONTRIBUTING.md) antes de abrir um pull request. Mudanças de execução, risco, autenticação ou persistência exigem testes de regressão e documentação. Nunca inclua credenciais, dumps de contas, logs privados ou dados de usuários em issues, commits ou pull requests.

## Segurança e disclosure

Para relatar uma vulnerabilidade, siga [SECURITY.md](SECURITY.md). Não publique vulnerabilidades não corrigidas em issue pública.

## Licença e atribuições

O projeto é distribuído sob [Apache License 2.0](LICENSE). Consulte [NOTICE](NOTICE) para atribuições de componentes derivados e upstream.

## Documentação

- [Deployment](DEPLOY.md)
- [Operação para usuário](docs/OPERATIONS-USER-READY.md)
- [Progresso verificado](docs/progress.md)
- [Plano de implementação](docs/implementation-plan.md)
- [Contribuição](CONTRIBUTING.md)
- [Segurança](SECURITY.md)
- [Código de conduta](CODE_OF_CONDUCT.md)

## Disclaimer financeiro

Este software não substitui aconselhamento financeiro, jurídico, tributário ou de gestão de risco. Use contas Demo ou Paper até concluir sua própria validação técnica, operacional e regulatória.

# TradingAgents Operável — deploy seguro

O serviço inclui API, painel de configuração, Trading Floor e execução governada para Shadow/Paper. O deployment recomendado para usuário final começa sem credenciais de corretora e sem dinheiro real.

## 1. Criar o recurso

Use Docker/Coolify ou execução local apontando para a branch `feat/dz23-trading-agency`.

## 2. Variáveis obrigatórias

Defina uma senha forte e um volume persistente em `/data`:

```env
ACCESS_PASSWORD=uma-senha-forte-com-20-ou-mais-caracteres
DATA_DIR=/data
LIVE_TRADING=false
PAPER_TRADING=true
SHADOW_TRADING=false
REAL_MONEY_EXECUTION=false
LEGACY_BROKER_ORDER_ENABLED=false
MT5_MODE=DEMO
```

Nunca coloque chaves em imagens Docker, repositórios ou logs. Credenciais, quando necessárias para pesquisa ou Demo, devem ficar somente no ambiente/volume protegido.

## 3. Deployment Enterprise multi-tenant

O Compose Enterprise inclui PostgreSQL, Redis e Qdrant além do serviço DZ23. Ative o modo multi-tenant com `MULTI_TENANT_ENABLED=true`, defina `TENANT_TOKEN_SIGNING_KEY` com pelo menos 32 caracteres aleatórios e defina um `TENANT_BOOTSTRAP_TOKEN` secreto. Use `.env.enterprise.example` como referência, substituindo todos os valores de exemplo.

O primeiro administrador deve ser criado uma única vez por `POST /v1/auth/bootstrap` com o header `X-Tenant-Bootstrap`. Depois do bootstrap, remova ou rotacione o token. Os acessos seguintes usam `POST /v1/auth/login` e `Authorization: Bearer <access_token>`. Papéis disponíveis são Viewer, Analyst, Operator, Risk Manager, Admin e Auditor; Live permanece proibido para todos.

O isolamento por tenant cobre jobs, configuração, runtime da agência, Paper, Shadow, auditoria e KillSwitch. Quando `DATABASE_URL` está configurada, identidade, sessões, memberships e ledger de jobs usam PostgreSQL; sem ela, o SQLite permanece como fallback local. Redis e Qdrant já estão declarados no Compose para filas/cache e memória vetorial.

O endpoint autenticado `/v1/jobs/health` informa o backend ativo (`postgres` ou `sqlite`). Em desenvolvimento local, o fallback SQLite inclui migração do schema legado e isolamento de idempotência por tenant.

## 4. Validar Docker no host

Em um host com Docker instalado, execute:

```bash
docker compose config
docker compose build
docker compose up -d
docker compose ps
curl http://127.0.0.1:8080/healthz
curl http://127.0.0.1:8080/readyz
```

Não exponha PostgreSQL, Redis ou Qdrant publicamente. Coloque o serviço atrás de TLS/reverse proxy, configure backup dos volumes e mantenha os secrets fora do Git.

## 5. Healthchecks

O endpoint público `/healthz` confirma que o processo está vivo. O endpoint `/readyz` informa se o serviço está pronto ou bloqueado pelo KillSwitch. `/metrics` exige autenticação.

## 6. Primeiro uso

Comece pelo Shadow Trading e verifique que o resumo informa `orders_sent: 0`. Depois use Paper Trading. Só depois de validar backtest, walk-forward, Shadow, Paper, reconciliação, backup/restore e kill switch deve ser considerada uma conta MT5 Demo isolada.

## Segurança

A cadeia oficial é `decisão → Risk Engine → ExecutionRouter → adapter → Event Log → reconciliação`. O frontend e os agentes não enviam ordens diretamente. O endpoint legado `/v1/broker/order` retorna `410` por padrão e permanece desativado até a migração formal.

O modo Live não é habilitado nesta branch. Não existe promessa de lucro, crescimento de carteira ou preservação de capital.

Consulte [docs/OPERATIONS-USER-READY.md](docs/OPERATIONS-USER-READY.md) para instalação, KillSwitch, backup, restore e promoção de estratégias.

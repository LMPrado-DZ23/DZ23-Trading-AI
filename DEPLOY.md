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

## 3. Healthchecks

O endpoint público `/healthz` confirma que o processo está vivo. O endpoint `/readyz` informa se o serviço está pronto ou bloqueado pelo KillSwitch. `/metrics` exige autenticação.

## 4. Primeiro uso

Comece pelo Shadow Trading e verifique que o resumo informa `orders_sent: 0`. Depois use Paper Trading. Só depois de validar backtest, walk-forward, Shadow, Paper, reconciliação, backup/restore e kill switch deve ser considerada uma conta MT5 Demo isolada.

## Segurança

A cadeia oficial é `decisão → Risk Engine → ExecutionRouter → adapter → Event Log → reconciliação`. O frontend e os agentes não enviam ordens diretamente. O endpoint legado `/v1/broker/order` retorna `410` por padrão e permanece desativado até a migração formal.

O modo Live não é habilitado nesta branch. Não existe promessa de lucro, crescimento de carteira ou preservação de capital.

Consulte [docs/OPERATIONS-USER-READY.md](docs/OPERATIONS-USER-READY.md) para instalação, KillSwitch, backup, restore e promoção de estratégias.

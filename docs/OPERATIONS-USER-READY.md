# DZ23 — Runbook operacional para usuário final

## Escopo atual

Esta versão é adequada para desenvolvimento, Paper Trading, Shadow Trading e preparação controlada para MT5 Demo. Ela **não promete lucro**, não garante crescimento de carteira e não habilita dinheiro real por padrão.

Os defaults obrigatórios são:

```env
LIVE_TRADING=false
PAPER_TRADING=true
SHADOW_TRADING=false
REAL_MONEY_EXECUTION=false
LEGACY_BROKER_ORDER_ENABLED=false
MT5_MODE=DEMO
```

## Instalação local

```bash
git clone https://github.com/LMPrado-DZ23/tradingagents-dz23.git
cd tradingagents-dz23
git checkout feat/dz23-trading-agency
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
cp .env.agency.example .env
mkdir -p data
export DATA_DIR="$PWD/data"
export ACCESS_PASSWORD='defina-uma-senha-forte-fora-do-codigo'
uvicorn service.app:app --host 127.0.0.1 --port 8000
```

O endpoint `/healthz` verifica se o processo está vivo. O endpoint `/readyz` verifica se o modo operacional não está bloqueado pelo KillSwitch e se o Paper Adapter está disponível. A API protegida exige Bearer Token igual ao valor de `ACCESS_PASSWORD`.

O endpoint autenticado `/v1/operational-readiness` é a fonte canônica por
instalação para a preparação de dinheiro real. Ele consolida o checklist sem
modificar estado e sempre declara se o envio real está habilitado. O preflight
local pode ser repetido e salvo sem colocar o token na linha de comando:

```bash
export ACCESS_PASSWORD='carregue-o pelo mecanismo seguro do host'
python3 scripts/real_readiness_check.py \
  --base-url http://127.0.0.1:8000 \
  --target preparacao \
  --output ./data/evidence/real-readiness.json
unset ACCESS_PASSWORD
```

Saída `preparado_sem_live` significa somente que os pré-requisitos anteriores
ao selo foram comprovados. Não autoriza LIVE. `--target live` permanece
bloqueado até uma futura liberação de código auditada e confirmação do dono no
momento da ação.

## Fluxo recomendado

O primeiro uso deve ser feito em Shadow Trading. Registre uma intenção pelo endpoint protegido `/v1/execution/shadow` e confirme em `/v1/execution/shadow` que `orders_sent` continua igual a zero. Depois use `/v1/execution/paper`, que exige decisão tipada, evidência, snapshot de mercado e validação determinística do Risk Engine.

Nunca considere uma animação, mensagem de agente ou painel visual como prova de execução. A prova deve vir dos eventos oficiais, do adapter e da reconciliação.

## KillSwitch

Acione o bloqueio quando houver dado stale, divergência, erro de broker, comportamento anômalo ou dúvida operacional:

```bash
curl -X POST http://127.0.0.1:8000/v1/execution/kill-switch \
  -H "Authorization: Bearer $ACCESS_PASSWORD" \
  -H 'Content-Type: application/json' \
  -d '{"action":"engage","reason":"reconciliação divergente","actor":"operador"}'
```

O reset somente deve ocorrer depois de reconciliação consistente:

```bash
curl -X POST http://127.0.0.1:8000/v1/execution/kill-switch \
  -H "Authorization: Bearer $ACCESS_PASSWORD" \
  -H 'Content-Type: application/json' \
  -d '{"action":"reset","reconciliation_ok":true,"actor":"operador"}'
```

## Backup e restore

O backup local inclui configuração, credenciais armazenadas, identidade do
proprietário, bancos locais, Escola, Sala REAL, pilotos, provas, journals,
kill switch, estados PAPER/SHADOW e checkpoints. Por isso o pacote é criado
com permissão restrita e deve ser tratado como segredo:

```bash
python3 scripts/backup_data.py backup ./data ./backups/dz23-$(date -u +%Y%m%dT%H%M%SZ).tar.gz
python3 scripts/backup_data.py inspect ./backups/dz23-ARQUIVO.tar.gz
```

Para restaurar, pare o serviço, preserve a pasta atual e restaure primeiro em
um diretório vazio:

```bash
python3 scripts/backup_data.py restore ./data-restaurado ./backups/arquivo.tar.gz
```

Suba uma instância isolada sobre `./data-restaurado` e confira healthchecks,
login, Escola, pilotos, journals e reconciliação antes de trocar o volume. O
manifesto e os hashes são verificados antes da primeira escrita; membros
extras, links, caminhos inseguros e pacotes adulterados são recusados.

Se `DATABASE_URL` estiver ativo, o estado persistido no PostgreSQL **não**
está dentro do arquivo local: gere e teste separadamente o backup lógico pelo
provedor do banco. Um backup só conta como gate depois de um restore provado.

## Promoção de estratégia

Uma estratégia deve seguir a cadeia:

```text
RESEARCH → BACKTEST → WALK_FORWARD → SHADOW → PAPER → DEMO
```

A promoção exige custos, slippage, look-ahead, drawdown, número de trades, reconciliação, kill switch e relatório reproduzível. Nenhum agente pode promover sua própria estratégia.

## MT5 Demo

O MT5 Demo exige terminal MetaTrader 5, conta Demo e configuração isolada. Antes do primeiro teste, verificar símbolo, volume mínimo, filling mode, margem, retcode, reconexão, posições, deals e reconciliação. O modo Live permanece bloqueado nesta branch.

## Limitações conhecidas

O Event Bus persistente atual usa journal local e ainda não é um event store distribuído. O monitor de reconciliação possui contrato fail-closed, mas precisa ser ligado a providers reais CCXT/MT5 em um deployment persistente. Métricas são internas e ainda precisam de coleta/alertas de longa duração. A interface visual é read-only para estado financeiro.

Consulte `docs/progress.md` antes de promover qualquer ambiente. Não use o endpoint legado `/v1/broker/order`; ele retorna `410` por padrão até que sua migração para o ExecutionRouter seja concluída.

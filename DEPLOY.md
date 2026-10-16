# TradingAgents Operável — deploy seguro

O serviço inclui API, painel de configuração, Trading Floor e execução governada para Shadow/Paper. O deployment recomendado para usuário final começa sem credenciais de corretora e sem dinheiro real.

## 1. Criar o recurso

Use Docker Compose, uma VM Linux (ex.: Oracle Cloud ARM64) ou execução local. O produto é single-owner: roda inteiro na sua máquina ou servidor, sem serviço de terceiros no meio.

## 2. Variáveis obrigatórias

Defina um volume persistente em `/data`:

```env
# A senha do dono é criada pelo assistente do painel no primeiro uso
# (hash Argon2id guardado no DATA_DIR). ACCESS_PASSWORD segue aceita
# apenas como atalho de desenvolvimento — não a use como mecanismo principal.
DATA_DIR=/data
LIVE_TRADING=false
PAPER_TRADING=true
SHADOW_TRADING=false
REAL_MONEY_EXECUTION=false
LEGACY_BROKER_ORDER_ENABLED=false
MT5_MODE=DEMO
```

Nunca coloque chaves em imagens Docker, repositórios ou logs. Credenciais, quando necessárias para pesquisa ou Demo, devem ficar somente no ambiente/volume protegido.

## 3. Deployment em servidor (single-owner)

O Compose sobe o serviço DZ23 (API + painel + mesa) com volume persistente em `/data`. PostgreSQL entra por `DATABASE_URL` apontando para um banco externo — o Compose não inclui serviços de banco/cache; Redis e Qdrant não são usados pelo produto hoje (as menções no `.env.enterprise.example` são legado). Não existe modo multi-tenant: um dono por instalação, por construção — qualquer rota de SaaS responde 404 mesmo com flags de ambiente ligadas de propósito.

Quando `DATABASE_URL` está configurada, identidade e ledger de jobs usam PostgreSQL; sem ela, o SQLite permanece como fallback local. Use `.env.enterprise.example` como referência, substituindo todos os valores de exemplo.

O primeiro acesso é o assistente do painel: ele cria a senha do dono e mostra o código de recuperação uma única vez. O endpoint autenticado `/v1/jobs/health` informa o backend ativo (`postgres` ou `sqlite`).

Honestidade de homologação: a implantação Linux ARM64 (Oracle Cloud) está **preparada** neste repositório, mas **não homologada**. Só declare pronta depois de rodar o ciclo completo — subida, persistência, reinício, backup/restauração — em ambiente real, com evidência registrada. Ter um Dockerfile não é homologação.

**Sem autorização, não há produção.** Nada de deploy em produção nem alteração de DNS sem autorização explícita do dono. LIVE de dinheiro real continua bloqueado — este repositório não ativa operações reais por conta própria.

## 4. Validar Docker no host

Em um host com Docker instalado, execute:

```bash
docker compose config
docker compose build
docker compose up -d
docker compose ps
curl http://127.0.0.1:8080/healthz
curl http://127.0.0.1:8080/readyz

# validação de reinício dos serviços e healthchecks
./scripts/validate_docker_runtime.sh

# carga somente de leitura; ajuste token e limites ao seu host
python3 scripts/load_test.py --base-url http://127.0.0.1:8080 --requests 100 --workers 10
```

Não exponha o PostgreSQL (nem o próprio serviço) publicamente. Coloque o serviço atrás de TLS/reverse proxy, configure backup dos volumes e mantenha os secrets fora do Git.

Para um proxy Caddy de referência, consulte [ops/Caddyfile.example](ops/Caddyfile.example). O domínio, DNS, portas 80/443, certificado e política de backup devem ser configurados pelo operador do ambiente.

## 5. Healthchecks e preflight REAL

O endpoint público `/healthz` confirma que o processo está vivo. O endpoint `/readyz` informa se o serviço está pronto ou bloqueado pelo KillSwitch. `/metrics` exige autenticação.

`/v1/operational-readiness` exige a autenticação do proprietário e consolida,
por instalação, a prova somente leitura da conta, Escola/Sala REAL, piloto,
risco, TESTNET, reconciliação, parada de emergência e selo de código. A rota é
somente leitura: consultá-la não altera configuração nem envia ordem.

Com o serviço iniciado e o token disponível apenas no ambiente, execute:

```bash
python3 scripts/real_readiness_check.py \
  --base-url http://127.0.0.1:8080 \
  --target preparacao \
  --output ./data/evidence/real-readiness.json
```

O alvo `preparacao` só passa quando todos os pré-requisitos externos e
operacionais estão comprovados, mas o selo e o envio LIVE continuam
desabilitados. `--target live` é reservado para a futura cerimônia explícita
de liberação e falha enquanto qualquer fechadura permanecer fechada. O token
nunca é aceito como argumento: por padrão, o script lê `ACCESS_PASSWORD`.

## 6. Primeiro uso

Comece pelo Shadow Trading e verifique que o resumo informa `orders_sent: 0`. Depois use Paper Trading. Só depois de validar backtest, walk-forward, Shadow, Paper, reconciliação, backup/restore e kill switch deve ser considerada uma conta MT5 Demo isolada.

## Segurança

A cadeia oficial é `decisão → Risk Engine → ExecutionRouter → adapter → Event Log → reconciliação`. O frontend e os agentes não enviam ordens diretamente. O endpoint legado `/v1/broker/order` retorna `410` por padrão e permanece desativado até a migração formal.

O modo Live não é habilitado nesta branch. Não existe promessa de lucro, crescimento de carteira ou preservação de capital.

Consulte [docs/OPERATIONS-USER-READY.md](docs/OPERATIONS-USER-READY.md) para instalação, KillSwitch, backup, restore e promoção de estratégias.

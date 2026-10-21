# Plano de melhorias — outubro/2026

Objetivo: operar a mesa DZ23 com supervisão do DS Bot e chegar a testnet/live
sem transformar o LLM em executor. Este documento separa o que foi entregue,
o que já existia e o que depende de terceiros.

## Entregue nesta rodada

### P0-1 — Stop protetivo na exchange

Antes: a saída era avaliada pelo ciclo/guardião **em memória**. Se o processo
morresse com posição aberta, não havia stop na corretora.

Agora:

- `TradeProposal.stop_pct` carrega a fração negativa da `ExitPolicy`;
- `RiskReservation` e `ExecutionIntent` transportam o stop até o executor;
- `ExecutionCoordinator._ensure_protective_stop` calcula o preço sobre o
  **fill** (não sobre o sinal) e coloca a ordem de venda na exchange;
- `CCXTBridge.submit_protective_stop` passa pelo mesmo gate de capital e pelos
  mesmos tetos de LIVE; o tipo é configurável
  (`TRADINGAGENTS_STOP_ORDER_TYPE=stop_loss_limit|stop_market`);
- falha não ambígua: a posição é **encerrada na hora** e o erro é propagado;
- falha ambígua (timeout): o OMS entra em `UNKNOWN_RECONCILIATION_REQUIRED` e
  **não** vende de novo, porque o stop pode existir;
- `OrderRecord` persiste `stop_pct`, `stop_price` e `stop_order_id`; o painel
  mostra os três.

Limitação declarada: o tipo de stop em spot varia por exchange. O padrão é
`stop_loss_limit`; a homologação em testnet é obrigatória e está registrada
abaixo como dependência externa. O stop é colocado pela quantidade já
preenchida; se houver **fill parcial posterior**, ele não é redimensionado
(P1: ajuste automático). A verificação periódica de que o stop aberto
continua vivo na corretora (reconciliação de stop) também é P1.

### P1-6 — MCP para o DS Bot

`mcp_server.py` expõe `platform_status`, `health`, `desk_status`,
`risk_summary`, `open_positions`, `recent_cycles`, `live_checklist`,
`traders` e `run_paper_cycle`. Read-only por padrão; ciclo PAPER exige
`DZ23_MCP_ENABLE_CYCLE=1` e recusa modo LIVE. Ver `docs/MCP-DSBOT.md`.

## Já existia (verificado no código)

- **Segredos em repouso:** `service/api/segredos_em_repouso.py` cifra com
  DPAPI do Windows, verifica ida-e-volta e migra texto claro legado; o
  `save_cfg` ativo aborta se não conseguir cifrar.
- **Procedência por ordem:** `tradingagents/desk/provenance.py` +
  `CCXTDeskBroker.submit(final_state=...)` levam a marca do Gate A por ordem,
  em vez do `{}` fixo que recusava tudo.
- **Selo LIVE:** `build_desk_broker(..., live_authorized=...)` só abre com
  decisão de código; o painel não concede.
- **Idempotência, reconciliação, ledger, scheduler e kill switch** com testes.

## Pendências externas (não executáveis só com código)

| Item | Estado | O que falta |
|---|---|---|
| Homologar testnet | `BLOCKED_BY_EXTERNAL_DEPENDENCY` | Chaves testnet do dono; uma ordem preenchida e reconciliada; validar o tipo de stop aceito pela exchange |
| Evidência OOS real | **Concluída — 9/9 reprovadas** | Ver `docs/mission/OOS-REAL-2026-10-21.md` |
| Reconciliação de stop | **Implementada** | A cada ciclo: cancelado/expirado recoloca uma vez; executado ou desconhecido bloqueia risco novo |
| Alertas 24/7 | Parcial | Telegram/webhook e serviço Windows/Linux; Web Push físico não homologado |
| Auditoria independente + assinatura de código | `BLOCKED_BY_EXTERNAL_DEPENDENCY` | Revisão externa e certificado |
| Selo LIVE | `BLOCKED_BY_POLICY` | Decisão explícita após os itens acima |

## Critérios de aceite desta rodada

```bash
uv run ruff check .
uv run python -m compileall -q tradingagents service scripts
uv run pytest -q
uv run pytest tests/test_mcp_server.py tests/test_broker_ccxt.py \
  tests/test_desk_execucao.py -q
```

## Evidência OOS real (2026-10-21)

`tools/oos_real.py` baixa OHLCV público e roda os 3 traders homologados com
custos. Resultado em `docs/mission/OOS-REAL-2026-10-21.md`: **9 de 9
combinações reprovadas** no líquido (365 dias, 1h, BTC/ETH/SOL). O breakout
fica perto de zero; trend e reversion perdem com o custo acumulado. Isso não
autoriza LIVE — orienta a próxima hipótese, não um ajuste de parâmetro.

## Ordem recomendada

1. Rodar a mesa em PAPER com o painel e comparar com o backtest.
2. Homologar testnet (uma ordem + stop + reconciliação) com chaves do dono.
3. Implementar a reconciliação de stop e os alertas 24/7.
4. Auditoria independente e decisão sobre o selo LIVE.
5. Só então discutir capital real — com teto baixo e confirmação explícita.

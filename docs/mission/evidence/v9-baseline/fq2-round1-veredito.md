# FQ-2 — Rodada 1: veredito de equivalência DZ23 × Freqtrade (BTC/USDT 1h, SMA 10/30)

Estado: **EXECUTADO em 2026-10-15** (missão V9, Fase 3), sob o [protocolo FQ-2](../../FQ-2-PROTOCOLO-EQUIVALENCIA.md). Freqtrade = processo separado (GPLv3), venv isolado fora do repositório, sem credenciais, sem ordens, sem dry-run com conta. O veredito **não promove** ninguém: a decisão continua na Escola (protocolo §3.6).

Artefatos e comandos desta rodada: [fq2-round1-invocation.json](fq2-round1-invocation.json). Relatório legível por máquina: [fq2-round1-comparacao.json](fq2-round1-comparacao.json). Saída do instrumento: [fq2-round1-probe.log](fq2-round1-probe.log).

## 1. Escopo

- Janela: 2026-02-01 00:00 → 2026-07-01 00:00 UTC (150 dias), BTC/USDT, 1h, dados Binance.
- Regra de confronto (a MESMA nos dois motores): cruzamento SMA(10)/SMA(30) de fechamento — entra no cross-up, sai no cross-down.
- Dois confrontos independentes:
  - **Parte A — equivalência de motor contábil:** o instrumento de auditoria [tools/v9_fq_equivalencia.py](../../../tools/v9_fq_equivalencia.py) re-simula o MESMO modelo de execução declarado do freqtrade (fill no open da vela seguinte, custo por perna sobre o notional, quantidade truncada em 5 casas) e confere **trade a trade** contra o export do freqtrade.
  - **Parte B — divergência de modelos (declarada):** roda o motor nativo do laboratório DZ23 (`tradingagents.research.backtest.run_backtest`) sobre os mesmos dados e eventos, para medir e explicar a diferença de desenho.

## 2. Itens 1–11 do protocolo — escolhas fixadas na rodada

As escolhas abaixo estão gravadas no header da estratégia de confronto ([fq2-round1-estrategia.py](fq2-round1-estrategia.py)), no config ([fq2-round1-config.json](fq2-round1-config.json)) e nos parâmetros do instrumento; o freqtrade confirmou cada uma nos próprios logs.

| # | Item | Escolha fixada (idêntica nos dois motores) | Como foi conferido |
| :--: | :--- | :--- | :--- |
| 1 | Sinais | Cruzamento SMA(10)/SMA(30) de fechamento; entra no cross-up, sai no cross-down. | 96 cross-ups / 95 cross-downs no arquivo inteiro; **66 entradas na janela com timestamp de fill IDÊNTICO ao export** (`entrada_ts_iguais_ao_export: true`); export com 66 fills de entrada e 66 de saída. |
| 2 | Calendário | UTC; cripto 24/7, sem exclusões nem feriados. | Timestamps do export comparados às velas do arquivo único — offset zero. |
| 3 | Velas | BTC/USDT 1h, timestamp = abertura da barra; arquivo canônico congelado `fq2-round1-velas-btcusdt-1h.json` sha256 `bd5b1a73…` (4999 velas, 2025-12-15 00:00 → 2026-07-11 06:00 UTC). | `velas_sha256` do relatório = hash do arquivo congelado; freqtrade leu o MESMO arquivo (config `dataformat_ohlcv: json`). |
| 4 | Taxas | 0,001 por lado (10 bps) nos dois: freqtrade `--fee 0.001` (log: "Using fee 0.1000% from config"); instrumento com `TAXA=0.001` por perna sobre o notional. | `taxa_total` por trade = `quantidade × (entrada + saída) × 0,001`; P&L do sim bate com `profit_abs` do export dentro de 1e-6 USDT em todos os 66 trades. |
| 5 | Slippage | 0 nos dois (backtest do freqtrade não modela slippage; Parte B com `slippage_bps=0`). | Declarado no header da estratégia; `modelo_custos` da Parte B: `per_leg_notional;fee_bps=10;slippage_bps=0`. |
| 6 | Fills | Mercado no OPEN da vela SEGUINTE ao sinal; sem fills parciais; sem limite de volume no freqtrade; Parte B com `max_participacao=1`. | Quantidade truncada em 5 casas (stepSize) **idêntica à do export nos 66 trades**; Parte B: 0 fills limitados e 0 rejeitados por liquidez. |
| 7 | Stop | NENHUM nos dois: `stoploss = −0,99` e `minimal_roi = {0: 100}` inalcançáveis por desenho (header da estratégia; log confirma). | Nenhuma saída por stop no export — só exit-signal; 66 saídas = 66 cross-downs na janela. |
| 8 | Capital | 1000 USDT; posição FIXA de 100 USDT (`stake_amount: 100`, `max_open_trades: 1`, `amend_last_stake_amount: false`). | Lado DZ23: notional fixo 100 sobre equity 1000; quantidades idênticas trade a trade. |
| 9 | Reinvestimento | NÃO — capital fixo, stake constante nos dois. | Carteira final: sim `989,1910209104` vs export `989,19102087` (diferença absoluta 4,04e-8 USDT ≈ 4e-11 relativo). |
| 10 | Timeframes | 1h no sinal e na execução. | 66 operações nos dois lados; duração média no freqtrade: 1 dia 2h06. |
| 11 | Convenções | Timestamp = abertura da barra; execução no open da vela de fill; sem forçar barra final incompleta (rodada termina flat: `left_open_trades` TOTAL = 0; asserção do instrumento confirma). | Primeira entrada 2026-02-02 16:00 e última saída 2026-06-30 07:00 UTC — idênticas no sim e no export. |

## 3. Artefatos congelados (sha256)

| Artefato | sha256 |
| :--- | :--- |
| [fq2-round1-velas-btcusdt-1h.json](fq2-round1-velas-btcusdt-1h.json) | `bd5b1a736374ca5afd70dcbdbbeda5ead55a4e4c18b247324cf331d9bc1e570c` |
| [fq2-round1-freqtrade-export.zip](fq2-round1-freqtrade-export.zip) | `d15296b711f67381175a695ce5b357528e207e001af6528bda925a23b219061c` |
| [fq2-round1-comparacao.json](fq2-round1-comparacao.json) | `3ee1f073b202e2ca009ab30ac2f186163c504dfea8e9b8438112738416905fd8` |
| [fq2-round1-config.json](fq2-round1-config.json) | `4ee72fe87721839c13cb1b7cf979b3ffe003dbb4a16b31156a13276b9d8cae04` |
| [fq2-round1-estrategia.py](fq2-round1-estrategia.py) | `9109890f44d7f20e2f1876504f057319bdaf4eefb64de4c69e4d4c6f775bc20c` |
| [fq2-round1-freqtrade-export.meta.json](fq2-round1-freqtrade-export.meta.json) | `84cb9517a6d9916d1bf315d79957b632df069c0eb6244204342a5f3eb97cb230` |
| [fq2-round1-freqtrade.log](fq2-round1-freqtrade.log) | `3440b455bec957c363e225a08dba7010a1f6dadd75e4256b1bc2a9a475d12014` |
| [fq2-round1-probe.log](fq2-round1-probe.log) | `618a7fc0e0b1613e3bdf53426f3ed6723b88d2d5c1c9a1d7e6b4d26bfe9fe8ac` |
| [fq2-round1-download-repro.log](fq2-round1-download-repro.log) | `0ec18bcef91cb065044183ce40888c66ff2c0c4fd653fabadb8c8308d15a5dcf` |

Integridade do export: o ZIP foi re-extraído com a stdlib (`zipfile`) — o JSON interno é byte-idêntico ao arquivo consumido pelo probe (sha256 `144de42193dfde54561a7b99bc3e9dcf8545fe818ea909ef0b768f0514045d3e`, 105 299 bytes). O meta do export registra `run_id 48017081de8db74d5946b74e7b349fba23654ec0` e a janela `backtest_start_ts 1769904000` (2026-02-01 UTC) → `backtest_end_ts 1782864000` (2026-07-01 UTC).

## 4. Comandos verbatim e códigos de saída

Todos no ambiente registrado em [fq2-round1-invocation.json](fq2-round1-invocation.json) (spike isolado `../freqtrade-spike`, venv próprio; repositório no worktree de auditoria). Versões: **freqtrade 2026.9 / Python 3.12.10 / CCXT 4.5.88 / Windows-11-10.0.26300-SP0**.

```bash
# 1. dados (Binance pública; sem credencial) — exit não capturado (pipeline)
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m freqtrade download-data --config user_data/config.json --timeframes 1h --timerange 20251215-20260701 2>&1 | tail -12

# 2. backtest (exit não capturado — saída redirecionada; log completo congelado)
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m freqtrade backtesting --config user_data/config.json --strategy Dz23SmaCross --timerange 20260201-20260701 --cache none --fee 0.001 --export trades --export-filename user_data/backtest_results/fq2-round1.json > fq2-round1-freqtrade.log 2>&1

# 3. probe de equivalência (exit 0) — executado no worktree, venv do projeto
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe tools/v9_fq_equivalencia.py --velas ../freqtrade-spike/user_data/data/binance/BTC_USDT-1h.json --resultado ../freqtrade-spike/round1_extract/backtest-result-2026-10-15_11-37-48.json --zip ../freqtrade-spike/user_data/backtest_results/backtest-result-2026-10-15_11-37-48.zip --out ../freqtrade-spike/fq2-round1-comparacao.json > ../freqtrade-spike/fq2-round1-probe.log 2>&1

# 6. reprodutibilidade do download (exit 0)
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m freqtrade download-data --config user_data/config.json --timeframes 1h --timerange 20251215-20260701 > fq2-round1-download-repro.log 2>&1; echo exit=$?
```

Notas de execução: o freqtrade avisa no log que `--export-filename` não tem efeito em backtesting — por isso o resultado saiu em `backtest_results/backtest-result-2026-10-15_11-37-48.{json,zip,meta.json}`. A extração original do ZIP não preservou o one-liner; foi re-executada com a stdlib e conferida byte a byte (acima).

## 5. Resultados

### Parte A — equivalência de motor contábil: **EQUIVALENTE (0 divergências)**

- 66 fills nos dois lados; timestamps de entrada dos fills idênticos ao export.
- Nos 66 trades, conferidos **timestamps, quantidade, preço de entrada e preço de saída (exatos)** e **P&L dentro de 1e-6 USDT** — nenhuma divergência listada pelo instrumento.
- Carteira final: `989,1910209104` (sim) vs `989,19102087` (export) — diferença 4,04e-8 USDT, atribuível ao truncamento de 5 casas na quantidade e ao arredondamento de 8 casas do export.
- Contra a tolerância sugerida pelo protocolo (§3.5, ≤ 0,5% relativo), a rodada ficou ~7 ordens de grandeza abaixo.

### Parte B — motor do laboratório DZ23 (divergência de MODELOS, declarada)

- Modelo nativo (`one_bar_round_trip;entry=next_open;exit=same_close;short=synthetic`, custos `per_leg_notional;fee_bps=10;slippage_bps=0`, liquidez `shared_two_leg_bar_volume;max_participacao=1`): **66 trades**, retorno total **−0,8957%**, drawdown **1,0571%**, carteira `991,0429`, 0 fills limitados, 0 rejeitados por liquidez, `dataset_hash afd77b16…`, `code_revision 2b7754a94cc1e4233ad95ac6fa377be4078e5055-dirty`.

### Lado freqtrade (referência)

- **66 trades**, carteira final **989,19102087** USDT, lucro total **−10,8090** USDT (**−1,08%**), winrate **31,8%** (21 v / 45 d), drawdown da conta **1,56%**, `left_open_trades` = 0. Duração média 1 dia 2h06.

## 6. Divergências e suas causas (exigência da missão)

1. **Parte A: zero divergências.** Sob os 11 itens fixados, a contabilidade dos dois motores bate trade a trade.
2. **Parte B: divergência de resultado (−0,8957% DZ23 lab vs −1,08% freqtrade) — causa única e declarada: o MODELO DE POSIÇÃO.** O laboratório DZ23 fecha cada trade no fechamento da PRÓPRIA barra de entrada (round trip de 1 barra por desenho), enquanto o freqtrade mantém a posição até o cross-down (holding multi-barra). Mesmos sinais, mesmos dados, mesmas taxas — durações e P&L por trade diferentes por construção. Não é divergência de contabilidade; é divergência de desenho, fixada no header da estratégia (item 6) e medida aqui como manda o protocolo.
3. Nenhuma outra fonte de divergência foi observada (contagens, fills, taxas, calendário e convenções idênticos).

## 7. Reprodutibilidade

- **Dados:** re-download da mesma janela reproduziu **4998/4998 velas comuns idênticas** posição a posição; a única diferença é a vela final 2026-07-11 06:00 UTC (fora da janela da rodada), que deixou de acompanhar o arquivo reescrito. Dentro da janela, o dado é reproduzível; o artefato canônico é a cópia congelada com hash registrado.
- **Export:** ZIP re-extraído byte-idêntico (acima).
- **Instrumento:** re-execução produziu os mesmos números; o campo `code_revision` passou a derivar do código real (`revisao_do_codigo()`), registrando `2b7754a94cc1e4233ad95ac6fa377be4078e5055-dirty` — o HEAD do baseline com a árvore de trabalho da auditoria.

## 8. Veredito e limites

- **Parte A: EQUIVALENTE** — contabilidade idêntica sob regras idênticas. **Parte B: DIVERGENTE POR DESENHO** — causa única declarada (modelo de posição).
- Limites da rodada: é **uma** rodada pontual (não confronto sistemático — matriz FQ-0 mantém "ADIAR" para produção); walk-forward permanece no lado DZ23 (o freqtrade confere pontos, não a validação móvel); equivalência de backtest **não** é previsão de resultado real; microestrutura (livro, latência, fills parciais) não é modelada por nenhum dos dois nesta rodada.
- GPLv3 respeitada como processo separado: a estratégia de confronto é código próprio, vive fora do repositório do produto e nada foi copiado para o núcleo Apache-2.0.
- O veredito **não** aprova estratégia, **não** promove a LIVE, **não** autoriza capital e **não** é homologação de execução real.

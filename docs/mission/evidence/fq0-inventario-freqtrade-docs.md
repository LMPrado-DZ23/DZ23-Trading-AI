# FQ-0 — Inventário fático do Freqtrade/FreqAI (docs oficiais) — insumo da matriz §2

**Missão:** "DZ23 Trading AI — Missão de Evolução Quantitativa: Freqtrade + FreqAI + OpenBB — Integração Seletiva e Validada" (§2).
**Data:** 2026-10-14 · **Branch:** `fix/instalador-atualizacoes-windows` · **Regra:** somente documentação oficial estável (`freqtrade.io/en/stable`, repositório oficial). Nada instalado, nada executado nesta rodada além do spike já registrado em `fq0-probe-dependencias.md` §3.2.
**Método:** leitura das páginas estáveis + índice de busca das docs (`search_index.json`) e `mkdocs.yml` para checklist de ausência.

## 1. Backtesting — reprodutibilidade e export

| Fato | Detalhe | Fonte |
| :--- | :--- | :--- |
| Export padrão = **ZIP reprodutível** | relatório JSON + "market change data" (feather) + cópia da strategy + cópia dos parâmetros + **config sanitizada**; objetivo declarado: reprodutibilidade "under the assumption that the same data is available". | backtesting/#backtest-output-file |
| Pairlist dinâmico **quebra** reprodutibilidade | docs recomendam gerar pairlist com `test-pairlist` e usar `StaticPairList`. | backtesting/ |
| Cache | reusa resultado das últimas **24h** se hash de strategy+config coincide; `--cache none` força re-run; cache desabilitado em timerange aberto. | backtesting/#backtest-result-caching |
| Seed/RNG | **não documentado** para backtest (palavra "seed" ausente do índice fora de exemplo de sampler) — reprodutibilidade = mesmos dados + config + strategy (+ cache). | docs (ausência verificada) |
| Leitura programática | `load_backtest_stats` / `load_backtest_data` (`freqtrade.data.btanalysis`); `backtest-result-<timestamp>.json`. | strategy_analysis_example/ |

## 2. Simulação realista — premissas documentadas (crítico para o protocolo §4)

| Fato | Detalhe | Fonte |
| :--- | :--- | :--- |
| **Slippage: ZERO por padrão** | "All orders are filled at the requested price (no slippage) as long as the price is within the candle's high/low range". | backtesting/#assumptions-made-by-backtesting |
| Fees | aplicadas **duas vezes por trade** (compra e venda); config `fee` cai no default da exchange; `--fee` sobrescreve. Maker/taker diferenciado: **não documentado**. | backtesting/ , configuration/ |
| **Liquidez/volume: sem restrição de execução** | não documentado como limite de fill; "Freqtrade has however no information about historic limits". Volume só como filtro de sinal. | backtesting/#trading-limits-in-backtesting |
| **Partial fills: ausentes no backtest** | "partial fill" ausente do índice das docs e da lista de premissas; em live/dry ordens parciais são canceladas/substituídas. DCA roda 1x por candle no backtest. | strategy-callbacks/ |
| Order book | `use_order_book` só afeta live/dry — "not part of the historic data which means backtesting and hyperopt will not work correctly". | strategy-customization/ |
| Entradas | no **open** da vela, salvo lógica de preço customizada. | backtesting/#assumptions-made-by-backtesting |
| Sequência por candle | Exit-signal → Stoploss → ROI → Trailing; stoploss exatamente no preço do stop ("2 * fees higher"); low antes de high para stoploss. | backtesting/#assumptions-made-by-backtesting |
| Intra-candle | `--timeframe-detail <tf>` simula movimento dentro do candle com timeframe menor (aumenta memória/runtime). | backtesting/#improved-backtest-accuracy |

**Implicação direta para o protocolo FQ-2:** o backtest do freqtrade é **mais otimista** que o DZ23 pós-FQ-1 (nosso motor agora limita fill por liquidez e desconta slippage configurada). Um confronto honesto exige declarar as diferenças (ou igualar o modelo nos dois lados) — nunca comparar retalhos de premissas diferentes.

## 3. Walk-forward

| Fato | Detalhe | Fonte |
| :--- | :--- | :--- |
| **Não existe comando oficial de walk-forward** | "walk-forward" ausente do nav (`mkdocs.yml`) e do índice de busca. | mkdocs.yml (verificado) |
| Fluxo adjacente documentado | hyperopt → backtest com "the same configuration and parameters" para conferir paridade ("Validate backtesting results"). | hyperopt/#validate-backtesting-results |
| Issues | #11737 "walk-forward analysis" fechada como duplicata; #7190 aberta — mantenedor **contra** validação dentro do loop de hyperopt (RAM), sugere ferramenta separada. | github issues |
| Mais próximo nativo | FreqAI com janela deslizante (`train_period_days` / `backtest_period_days`); nº de modelos = span do timerange ÷ backtest_period_days. | freqai-running/ |

**Confirmação para a matriz (§2.1 linha Walk-forward):** o DZ23 já tem walk-forward + embargo + Bonferroni próprios; o freqtrade **não oferece** nada superior — MANTER COMO ESTÁ segue correto.

## 4. QA de viés: lookahead-analysis e recursive-analysis

| Ferramenta | Como funciona | Caveats documentados |
| :--- | :--- | :--- |
| `freqtrade lookahead-analysis` | 1 backtest baseline (pairlist completa) vs backtests isolados por sinal (1 par) → compara dataframes e reporta bias em entry/exit/metrics. | Só pega trades efetivamente calculados (sinal não disparado ⇒ falso-negativo); position stacking distorce; estratégias que rankeiam pares ⇒ falso-positivo; coluna `biased_indicators` marca **erroneamente** targets FreqAI de `set_freqai_targets()`. |
| `freqtrade recursive-analysis` | Calcula indicadores com `startup_candle_count` diferentes (199/499/999/1999) e compara a última linha com o benchmark; tabela em %. | Só cobre `populate_indicators` e `@informative` (não `populate_entry/exit_trend`); não testa trades; recomenda ≥5000 candles, 1 par. |

**Convergência com FQ-1:** nosso `detectar_lookahead` (perturbação por truncamento) e os testes de causalidade/prefixo perseguem o MESMO objetivo, com código original (sem GPL). Direções complementares: o nosso é causal (muda o futuro), o deles é pareamento de backtests. Nenhuma cópia.

## 5. Relatório e métricas

`CAGR %`, `Calmar (closed trades)`, `Sharpe/Sortino (closed trades)` anualizados, `SQN`, **`Mean profit p-value`** (t-test da média por trade = 0), `Profit factor`, `Expectancy (Ratio)`, `Absolute drawdown`, `Max % of account underwater`, `Market change`; tabelas por exit reason/enter tag e breakdown diário/semanal/mensal/anual. (backtesting/#summary-metrics)

**Confronto com o DZ23:** nossa Escola/FQ-1 já carrega expectativa líquida, fator de lucro, drawdown e estabilidade; o p-value deles por trade e o "market change" são equivalentes funcionais do nosso p-valor vs controle e do benchmark buy-and-hold interno (§2.1 Benchmark). Nenhuma lacuna material nova.

## 6. Protections, hyperopt e FreqAI (resumo operacional)

- **Protections** (`--enable-protections`): StoplossGuard, MaxDrawdown (equity), CooldownPeriod (por par), LowProfitPairs; duráveis em minutos ou candles; "not all Protections will work for all strategies". (plugins/)
- **Hyperopt**: Optuna NSGAIII; 12 loss functions; `--random-state` congela sampling inicial; mitigação de overfitting documentada (3 decimais, ~500–1000 epochs, validar com backtest). (hyperopt/)
- **FreqAI**: janela móvel nativa (`train_period_days`/`backtest_period_days`/`live_retrain_hours`/`expiration_hours`/`purge_old_models`); modelos LightGBM/XGBoost/SKLearn/PyTorch (e RL com stable-baselines3, deps PyTorch ~700 MB); outliers por DI/SVM/DBSCAN; **sem feature oficial de drift** ("drift" ausente das docs); calibração de probabilidade **não documentada**; `shuffle=False` por default; continual learning experimental. (freqai-*)
- **Headless isolado**: CLI completa (`freqtrade backtesting …`), imagem Docker oficial, config gerada por `new-config`, chaves **vazias** aceitas para dry-run/backtest, dados em json/feather/parquet com colunas `date,open,high,low,close,volume`. (docker_quickstart/, data-download/)

## 7. Licença GPL-3.0 — implicações práticas (FSF FAQ + repositório)

| Cenário | Situação | Fonte |
| :--- | :--- | :--- |
| Usar como **ferramenta** (CLI/venv/container) | Livre — "using them does not place any restrictions, legally, on the license you use for your code"; copyleft dispara na **distribuição**. | gnu.org GPL-FAQ (GPLToolsForNF) |
| **Distribuir** o freqtrade (bundle/container) | Deveres GPL (fonte correspondente); agregação de programas separados não contamina o resto. | GPL-FAQ MereAggregation |
| **Copiar/importar código** | Obra derivada — incompatível com núcleo proprietário/Apache-2.0 sem análise jurídica. | GPL-FAQ LinkingWithGPL |
| Estratégia que dá `import freqtrade` + subclassa `IStrategy` | Acoplamento de plug-in: incerto no caso concreto; exige análise jurídica **se distribuída**. | GPL-FAQ GPLAndPlugins |
| **Output** do programa | Em geral não coberto pelo copyright do código (números do backtest podem ser usados) — incerto para relatórios que incorporem estrutura do freqtrade. | GPL-FAQ WhatCaseIsOutputGPL |

**Regra operacional adotada:** freqtrade e qualquer estratégia escrita para ele vivem **fora** do repositório do produto; nada GPL entra no núcleo; nenhuma distribuição do freqtrade acontece.

## 8. O que mereceria consideração (só o comprovado na doc)

1. Export de backtest autossuficiente (JSON + market change + strategy + config sanitizada). 2. `--timeframe-detail` intra-candle. 3. `lookahead-analysis`/`recursive-analysis` como QA de viés (conceito já coberto por FQ-1). 4. Conjunto de métricas de relatório (já cobertas funcionalmente). 5. Protections configuráveis (`--enable-protections`). 6. Hyperopt com 12 loss functions + `--random-state`. 7. Janela deslizante FreqAI. 8. Pipeline FreqAI de limpeza/robustez. 9. `backtesting-analysis` por tags + webserver. 10. Execução headless isolada (bom encaixe para o protocolo FQ-2).

## 9. Limitações declaradas

- Inventário de **documentação** (docs estáveis + repositório público); nada foi instalado/executado nesta rodada além do spike §3.2 do probe.
- Itens marcados **incerto** no levantamento original: seed/RNG de backtest, maker/taker por ordem, partial fills em versões futuras, CatBoost na árvore atual, calibração FreqAI, licença de estratégias. Não viram afirmação na matriz.
- Datas/versões das docs podem avançar; qualquer rodada de confronto deve re-confirmar as premissas da versão instalada (2026.9 no spike).

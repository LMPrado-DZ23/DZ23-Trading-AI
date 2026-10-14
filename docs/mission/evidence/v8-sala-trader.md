# V8 — TD-2: Sala individual dos 10 traders (gráfico de mercado + portfólio) — evidências

Requisitos do complemento V8 (verbatim): *"gráfico de mercado OHLCV/volume/
timeframes/indicadores/S-R/marcações/sinais/abstenções; gráfico de portfólio:
curva de patrimônio, P&L, custos, drawdown, exposição, hit rate, expectativa,
profit factor, Champion/Challenger"* e *"Os gráficos financeiros devem derivar
de ledger e eventos verificáveis, não de números simulados indevidamente
apresentados como reais."* — cada trader tem uma sala que **lê** o livro-razão,
o diário de operações fechadas e os ciclos da mesa; nada inventado, nada de
ordem.

Data das provas: 2026-10-14. Branch `fix/instalador-atualizacoes-windows`.
Arquivos: `tradingagents/desk/sala_trader.py` (novo, módulo puro),
`tradingagents/desk/evolucao.py` (2 leituras novas: `experimentos()` e
`resultados_dos_experimentos()`), `service/app.py` (rota
`GET /v1/desk/traders/{agent_id}/sala`), `service/static/index.html`
(card `#salaTrader` + botão "Sala" por trader + renderizadores SVG),
`tests/test_sala_trader.py` (novo, 32 testes).

## Requisito → implementação → evidência

| Requisito (complemento V8) | Implementação | Evidência |
|---|---|---|
| Gráfico de mercado com OHLCV/volume | Fonte real ⇒ candles + volume via `PublicMarketFeed.ohlcv()` → `velas_de_rows()` (descarta a vela em formação — "usá-la é olhar o futuro"); fonte fixture **não tem** OHLCV ⇒ gráfico é a LINHA rotulada dos fechamentos, jamais candles inventados | unidades `TestVelas` (descarte da formação) e `TestRetratoUnico::test_fixture_usa_linha_rotulada…`; render vivo: tipo `linha` + aviso "sem OHLCV inventado" (captura `sala-01-card.png`) |
| Timeframes | Seletor 1m/5m/15m/1h/4h/1d no card; rota valida contra `TIMEFRAME_SECONDS` (timeframe inválido ⇒ 422) | HTTP `test_timeframe_e_instrumento_invalidos_422`; render vivo: botões no card (1h ativo) |
| Indicadores | SMA 9/21 + RSI (Wilder 14) sobre os fechamentos; `None` até haver janela — indicador incompleto não vira número | unidades `TestIndicadores`; render vivo: "SMA rápida 152.5 · SMA lenta 143.5 · RSI 100.0" |
| Suporte/Resistência | Pivôs sobre fechamentos (janela ±2, máx 3 de cada lado) com nota explícita: "níveis aproximados — não são linhas de mercado" | unidade `TestSuporteResistencia`; render vivo: nota no card |
| Marcações / sinais / abstenções | Execuções REAIS derivadas dos lançamentos do livro (compra `ΣPOSITION:/qtd`; venda `(ΣCASH:+FEES:)/qtd`; sem aritmética fechada ⇒ `preco=None` com o dado cru) + abstenções/vetos dos ciclos da mesa com horário e motivo; motivo ao passar o mouse (title) | unidades `TestMarcacoes` (preço derivado = 10.0; por agente; t lido dos ciclos); união ordenada por instante pinada em `test_marcacoes_unem_execucao_e_ciclos_ordenadas` |
| Curva de patrimônio / P&L / custos | Curva acumulada do DIÁRIO (soma simples de retornos, não composta — "não é saldo de conta"); custos do diário somados em Decimal; resultado BRUTO do livro × líquido estimado rotulados ("taxas vivem em FEES") | unidades `TestCurvaEMetricas` (acumulado 0.10/0.15…, custos_total "1.2"); render vivo: "Resultado bruto do livro" separado de "Resultado líquido estimado" + nota |
| Drawdown / hit rate / expectativa / profit factor | `drawdown_maximo` sobre os acumulados; hit rate/expectativa/profit factor calculados SEM arredondar (UI formata); sem operações ⇒ `None` + "sem operações fechadas"; sem perdas ⇒ `None` + "sem perdas no período — … dividir por zero não mede nada" | unidades `TestCurvaEMetricas` (5 testes); render vivo: fator de lucro "sem operações fechadas — nada a medir (posição aberta é expectativa, não resultado)" |
| Exposição viva | Lotes do livro por agente (`position_lots`) com custo restante; ordens NÃO terminadas deste trader | unidades `test_exposicao…`; render vivo: "nenhuma posição viva deste trader" + custo 0 |
| Champion/Challenger | Experimentos pré-registrados deste trader via 2 leituras novas da arena (`experimentos()`/`resultados_dos_experimentos()` — só leitura, sem tocar estado interno); sem veredito ⇒ "pendente — não é aprovado por omissão" | unidade `TestExperimentosEExtras`; render vivo: "nenhum experimento pré-registrado deste trader" |
| Segregar modo/moeda na sala | A sala declara `modo` (PAPER/…) e `moeda` uma única vez no topo; avisos fixos de honestidade | unidade `test_estrutura_completa_e_segregacao_declarada`; render vivo: "PAPER · moeda USDT · LIVE bloqueado" |
| Números só de ledger/eventos verificáveis | Rota coleta livro/diário/ciclos/arena; módulo PURO calcula (sem I/O, sem rede) | `TestModuloPuro`: fonte do módulo não contém `create_order`/`ccxt`/`import requests`/`from .execution` |
| Fixture sempre rotulado | Aviso "dados de FIXTURE (rotulados) — provam a mesa, não o mercado" + `fonte`/`qualidade`/`aviso` propagados ao card | HTTP `test_sala_fixture_responde_honesta`; render vivo: aviso âmbar no topo do card |
| Sem promessa de lucro | 1º aviso fixo: "não é promessa de lucro nem previsão exata de preços"; rodapé do card repete PAPER/simulada/LIVE bloqueado | unidade `test_avisos_de_honestidade`; DOM `test_rotulos_de_honestidade_visiveis_na_sala`; visível na captura |
| Não declarar conclusão só porque o gráfico aparece | Provas = unidade + HTTP + DOM + instância viva (JSON + capturas); homologação no exe instalado segue PENDENTE e declarada | este documento + suíte completa verde |

## Arquitetura (isolamento e clareza)

- Módulo puro `tradingagents/desk/sala_trader.py` (~330 linhas): indicadores,
  curva, métricas, marcações, velas e a montagem `retrato_do_trader(...)`.
  A rota coleta, o módulo calcula, o painel desenha — cada camada testável
  sozinha; impossível virar caminho de ordem (pinado por teste que grepa a
  fonte).
- Rota `GET /v1/desk/traders/{agent_id}/sala` (só autenticação, como as
  demais leituras): 404 trader desconhecido; 422 timeframe/instrumento fora
  do universo do trader; fonte real usa o MESMO `PublicMarketFeed` cacheado
  do snapshot (30 s) e descarta a vela em formação; fixture usa os
  fechamentos rotulados.
- Decisão de honestidade: `MarketSnapshot` (fixture) só carrega fechamentos —
  por isso o gráfico de fixture é uma linha rotulada; candles/volume só
  existem quando a fonte é real. Nenhum O/H/L/V é fabricado.
- Sell-price derivado do próprio lançamento (`ΣCASH: + ΣFEES:`)/qtd; quando a
  aritmética não fecha, `preco=None` — "melhor declarar que derivar errado".

## Provas frescas (comandos e resultados exatos)

1. **RED:** `tests/test_sala_trader.py` escrito primeiro; primeira execução
   falhou (`ModuleNotFoundError: No module named
   'tradingagents.desk.sala_trader'`); após o módulo, 4 rodadas de ajuste
   (test 2.8 vs 2; remoção de arredondamento nas razões; remoção de parâmetro
   redundante `custos_diario`) → unidade **24 passed**.
2. **GREEN:** HTTP (4) + DOM (4) contra o card/JS recém-escritos →
   **32 passed** (`pytest tests/test_sala_trader.py -q`, 1,59–1,87 s).
3. **Regressão focada:** `pytest tests/test_painel_global.py
   tests/test_qoder_maestro_traders.py tests/test_desk_multiagente.py
   tests/test_evolucao_autonoma.py tests/test_ia_painel_static.py
   tests/test_desk_scheduler.py tests/test_desk_market.py -q` →
   **147 passed** (37,15 s).
4. **Lint:** `ruff check` nos 4 arquivos tocados → **All checks passed!**
   (I001/UP035/E741/C408/F401 corrigidos).
5. **Suíte completa:** `pytest -q` (chaves de IA reais removidas do processo)
   → **2923 passed / 5 skipped / 71 subtests** (exit 0, 313,07 s; 2891 do TD-1
   + 32 novos).
6. **E2E navegador real (Playwright + uvicorn real):** `env -u GROQ_API_KEY
   -u MISTRAL_API_KEY uv run --with playwright --python .venv/Scripts/python.exe
   python -m pytest tests/e2e -q` → **13 passed in 31,88 s** (exit 0; a suíte
   principal roda sem Playwright e o marca `skipped`, contado nos 5 skips
   acima).

## Homologação AO VIVO em instância temporária isolada (porta 8786)

Ambiente: `DATA_DIR=/tmp/dz23-td2-sala`, `TRADINGAGENTS_BROKER_MODE=paper`,
**sem** chaves de IA reais (`env -u GROQ_API_KEY -u MISTRAL_API_KEY`) — o app
do dono em `:8000` intocado, nenhuma ordem, nenhum kill fora do servidor
temporário (PID 29880 encerrado ao final; `:8786` de volta a "down").

JSON vivo de `GET /v1/desk/traders/trader-trend/sala?timeframe=1h` (recorte
verbatim):

```json
{"modo": "paper", "moeda": "USDT",
 "mercado": {"tipo": "linha", "fonte": "fixture", "instrumento": "BTC/USDT",
             "timeframe": "1h", "qualidade": "ok",
             "aviso": "dados de FIXTURE (rotulados) — provam a mesa, não o mercado; o gráfico mostra os fechamentos, sem OHLCV inventado",
             "fechamentos": 40},
 "portfolio": {"operacoes": 0,
               "motivo_profit_factor": "sem operações fechadas — nada a medir (posição aberta é expectativa, não resultado)",
               "resultado_bruto_livro": "0", "resultado_liquido_estimado": "0"},
 "avisos": ["Modo PAPER — resultados de execução simulada quando PAPER; não é promessa de lucro nem previsão exata de preços", …]}
```

Render AO VIVO no navegador real (Playwright, login real → Mesa de Agentes →
botão "Sala" do `trader-trend`):

- "Modo: PAPER · moeda USDT · LIVE bloqueado".
- Aviso âmbar "dados de FIXTURE (rotulados) — … sem OHLCV inventado".
- Mercado BTC/USDT 1H com seletor 1m–1d (1h ativo) e gráfico em LINHA dos
  fechamentos com sobreposição SMA (linha tracejada de S/R ausente — série
  monotônica não tem pivôs: honesto).
- "Indicadores: SMA rápida 152.5 · SMA lenta 143.5 · RSI 100.0".
- "sem marcações ainda" + nota de origem (livro-razão; ciclos).
- Portfólio: "sem operações fechadas — nada a desenhar (posição aberta é
  expectativa, não resultado)"; Fator de lucro com motivo em âmbar; custos 0;
  drawdown 0.00%; bruto do livro × líquido estimado separados + nota FEES.
- Exposição viva/Ordens abertas/Champion-Challenger com estados vazios
  explícitos; avisos finais de honestidade com PAPER/LIVE bloqueado.

Capturas nesta pasta: `sala-01-card.png` (card completo) e
`sala-02-contexto.png` (página Mesa com o card no contexto). JSON bruto salvo
em `C:/Users/DZ23/AppData/Local/Temp/dz23-td2-sala-trend.json` (fora do
repositório, como artefato de prova).

## Limitações declaradas (honestidade de escopo)

- Os dados vivos desta homologação são de **fixture** (mesa parada, saldo 1000
  simulado, sem posições/ordens) — provam a fiação, a derivação e os rótulos,
  **não** o mercado. O caminho de candles/volume reais foi provado por
  unidade (`velas_de_rows` + `svgVelasMercado` pinados) e depende de uma
  fonte real conectada para aparecer vivo.
- A curva de portfólio viva ficou vazia (nenhuma operação fechada na
  instância) — o desenho da curva/Drawdown foi provado por unidade
  (`curva_de_resultado`, `drawdown_maximo`, `svgPortfolio` pinado por DOM).
- Homologação no exe instalado (10 passos do usuário iniciante) segue
  **PENDENTE** — pertence ao TD-5 e continuará declarada como pendente até
  prova reproduzível.
- Nenhuma tag/Release criada; LIVE continua bloqueado por código;
  `%APPDATA%\DZ23-Trading-AI\` intocado; chaves de IA reais nunca usadas nos
  testes.

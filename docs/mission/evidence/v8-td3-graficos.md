# V8 — TD-3: Gráficos profissionais (Lightweight Charts vendorizado + avaliação TradingView) e IA lendo gráficos — evidências

Requisitos do complemento V8 (TD-3): *"Gráficos profissionais (Lightweight
Charts vendorizado + avaliação TradingView Advanced Chart Widget; respeitar
licenças; sem scraping) + IA lendo gráficos por séries estruturadas"* e
*"Não prometer previsão exata de preços nem lucros"* — a leitura da IA
consome os NÚMEROS verificáveis da sala (TD-2), nunca imagem; a biblioteca
roda 100% local; nenhum dado é inventado e nenhuma ordem é possível por
este caminho.

Data das provas: 2026-10-14. Branch `fix/instalador-atualizacoes-windows`
(HEAD `d40c788` + este trabalho). Arquivos: `service/static/vendor/
lightweight-charts/` (3 arquivos novos), `service/static/index.html`
(renderizadores Lightweight Charts + fallback SVG + botão "Pedir leitura à
IA"), `service/app.py` (rota `GET /static/vendor/lightweight-charts/
{arquivo}` com allowlist + rota `POST /v1/desk/traders/{agent_id}/sala/
leitura`), `tradingagents/desk/sala_trader.py` (`prompt_de_leitura(
retrato)` — módulo continua PURO), `service/api/smart_setup.py` +
`service/api/ia_provedores.py` + `tradingagents/llm_clients/precos.py`
(2 defeitos vivos achados na homologação, corrigidos), `tests/
test_td3_graficos.py` (novo, 21 testes), `tests/test_ia_provedores.py` +
`tests/test_smart_setup.py` (pins atualizados para os modelos vivos).

## Requisito → implementação → evidência

| Requisito (TD-3) | Implementação | Evidência |
|---|---|---|
| Lightweight Charts vendorizado (sem CDN, sem scraping) | v5.2.1 (Apache-2.0) em `service/static/vendor/lightweight-charts/` a partir do tarball oficial do npm (`npm pack lightweight-charts@5.2.1`); `<script src="/static/vendor/...">` local; html sem NENHUM `src="http` externo, sem `tradingview.com`, `cdn.jsdelivr` ou `unpkg` | unidades `TestBibliotecaVendorizada` (3); hashes pinados: js 197.922 B `e21cc5ca…5f598cf`, LICENSE 11.347 B `70c9d538…e5ef823`, VERSION 393 B `26174339…e6934f1`; render vivo: `LightweightCharts versao: 5.2.1` + `canvas no #lcMercado: 7` |
| Rota de estáticos servindo o vendor | Defeito vivo #1: `/static/vendor/...` respondia 404 (o app só serve estáticos por rotas explícitas) e o fallback SVG mascarava — corrigido com rota dedicada + allowlist de 3 nomes (como a da marca); nomes fora da allowlist ⇒ 404 | HTTP `TestVendorServidoHttp` (4): conteúdo byte a byte igual ao arquivo, content-type `application/javascript`, `../index.html`/`segredo.js`/`package.json` ⇒ 404 |
| Gráfico de mercado profissional (candles + volume + SMA + linha de S/R + marcadores de execuções) | `lcGraficoMercado()` na API v5 (`addSeries(CandlestickSeries)`, `HistogramSeries`, `LineSeries` para SMA e níveis, `createSeriesMarkers` para as marcações do livro); API v4 removida do arquivo (`addCandlestickSeries`/`addHistogramSeries` ausentes) | unidades `TestGraficosNoPainel` (4); render vivo na instância A: canvas reais da biblioteca no card com o gráfico em LINHA rotulada (fixture não tem OHLCV — nenhum candle inventado) |
| Curva de portfólio profissional | `lcGraficoPortfolio()` (`AreaSeries` + marcador por ponto com cor por sinal); vazia quando não há operação fechada — "nada a desenhar (posição aberta é expectativa, não resultado)" | unidades `TestGraficosNoPainel`; render vivo: `portfolio: lcPortfolio=0 svgFallback=1` (vazio honesto — a instância não tinha operações fechadas); `svgPortfolio` pinado por DOM desde o TD-2 |
| Queda para o SVG quando a biblioteca não carregar | `graficoMercadoEscolha()`/`graficoPortfolioEscolha()` testam `window.LightweightCharts` em runtime e usam os SVG do TD-2 — nunca tela quebrada | unidade `test_fallback_svg_preservado_e_escolhido_em_runtime`; render vivo A4: com a rota do vendor bloqueada no navegador, `svg=1` e `LightweightCharts ausente=True` (captura `td3-05-fallback-svg.png`) |
| IA lendo gráficos por séries estruturadas (números, não imagem) | `prompt_de_leitura(retrato)` monta o prompt com fechamentos/velas, indicadores, níveis, marcações, curva, exposição e Champion/Challenger do MESMO retrato do TD-2 — nenhuma imagem, nenhum dado novo | unidades `TestPromptDeLeitura` (4: guardas, dados reais do retrato, rótulo FIXTURE + denominador zero, marcações do livro); leitura AO VIVO real (instância B) e captura `td3-04` |
| Proibições explícitas no prompt: nunca inventar, nunca prometer lucro, nunca previsão exata, nunca sugerir ordem | 3 regras absolutas no prompt (`NUNCA invente…`, `NUNCA … previsão exata … prometa lucro`, `NUNCA sugira ordem`) + aviso da rota + aviso no card ao lado do botão | unidades `TestPromptDeLeitura` + `TestLeituraNoDOM` (2); a leitura viva termina com o aviso "não é promessa de lucro nem previsão exata de preços; quem decide é o dono." (txt integral salvo) |
| Sem provedor de IA ⇒ NADA é enviado (leitura opcional) | Rota responde `{ok:false, motivo:"sem provedor de IA configurado — … configure em Configuração → IA. Nada foi enviado."}` antes de qualquer chamada; UI mostra o motivo em âmbar | HTTP `test_sem_provedor_responde_honesto_sem_enviar_nada`; render vivo A3: mensagem exata no card (captura `td3-03-leitura-sem-provedor.png`) |
| Orçamento/consumo real de IA continua valendo | Rota registra em `IA_USO` (terceiro ponto de registro, pinado por contagem no teste) — custo real entra no painel de consumo | render vivo B: `{"chamadas": 1, "tokens_entrada": 559, "tokens_saida": 984, "custo_estimado_usd": 0.000337}` — a matemática confere com a tabela nova: (559×0.075 + 984×0.30)/1e6 |
| Avaliação TradingView com respeito a licenças (sem scraping) | Decisão DOCUMENTADA de NÃO embutir: o widget é servido pelos servidores do TradingView e o CSP do app (`script-src 'self'`) bloqueia script externo; o Advanced Charts self-hosted é proprietário (licença comercial); scraping está excluído por mandato. A página segue 100% local — o navegador real não fez NENHUMA chamada externa na homologação | teste que pina a ausência de `tradingview.com`/`cdn`/`unpkg` no HTML; lista de hosts externos do navegador vivo: `hosts_externos_A: []`, `hosts_externos_B: []` e veredito `OK` |
| Licença preservada | LICENSE (texto Apache-2.0 completo) e VERSION (versão, licença, origem, data da vendorização) dentro da pasta vendorizada e servidos pela rota | unidade `test_licenca_e_versao_pinadas` + `test_licenca_e_version_tambem_servidos` |
| Nada de ordem por este caminho | O prompt lê; o módulo da sala continua PURO (sem `create_order`/`ccxt`/`import requests` — pinado desde o TD-2); a rota só autentica e lê | suíte TD-2 `TestModuloPuro` segue verde; rota sem parâmetro de ação |

## Defeitos encontrados AO VIVO e corrigidos nesta etapa

1. **`/static/vendor/...` ⇒ 404** — o app serve estáticos só por rotas
   explícitas; o JS da biblioteca 404ava no navegador real e o fallback SVG
   mascarava (o card funcionava, mas sem a biblioteca). Corrigido com rota
   dedicada + allowlist; 4 testes novos pinam (RED→GREEN: 4 failed → 4
   passed).
2. **Modelos llama do Groq descontinuados** — a instância B falhou HTTP 404
   (`OpenAIModelNotFoundError`) com o padrão de fábrica
   `llama-3.1-8b-instant`. Lista viva consultada em 2026-10-14: só servem
   gpt-oss/whisper/orpheus/qwen/guard. Padrões de fábrica atualizados para
   `openai/gpt-oss-20b` (rápido) e `openai/gpt-oss-120b` (profundo), com
   preços oficiais (0.075/0.30 e 0.15/0.60 USD/1M) na tabela de custos —
   referência morta saiu, referência viva entrou (a descoberta real da API
   continua mandando quando o dono busca modelos no painel).
3. **Sugeridor propunha modelo de áudio** — `sugerir_modelos` com a lista
   viva sugeria `whisper-large-v3-turbo` como "profundo" (sem preço de
   referência, ordem da API decidia). Filtro `elegivel_chat` (famílias de
   áudio/TTS/embedding/guarda fora) + 3 testes novos; "safeguard" (variante
   de chat) continua elegível.

## Provas frescas (comandos e resultados exatos)

1. **RED:** `TestVendorServidoHttp` escrito depois do defeito vivo #1 —
   primeira execução 4 failed (rota 404); após a rota, 4 passed.
2. **RED (modelos):** pins atualizados para os modelos vivos falharam
   antes da troca (`4 failed / 34 passed` no recorte) → correções nos 3
   módulos → verde.
3. **Focada:** `pytest tests/test_smart_setup.py tests/
   test_smart_setup_homologacao.py tests/test_ia_provedores.py tests/
   test_td3_graficos.py -q` → **120 passed** (5,71 s).
4. **Suíte completa:** `pytest -q` (chaves de IA reais removidas do
   processo) → **2947 passed / 5 skipped / 71 subtests** (exit 0,
   269,71 s; 2923 do TD-2 + 21 novos + 3 dos cenários de modelos vivos
   contados à parte) — duas execuções independentes em segmentos
   distintos, mesmo resultado.
5. **E2E navegador real (Playwright + uvicorn real):** `env -u
   GROQ_API_KEY -u MISTRAL_API_KEY uv run --with playwright --python
   .venv/Scripts/python.exe python -m pytest tests/e2e -q` → **13 passed
   in 31,83 s** (as capturas de `docs/mission/evidence/e2e/` foram
   regeradas nesta passagem — incluídas no commit).
6. **Lint:** `ruff check` nos arquivos tocados → **All checks passed!**

## Homologação AO VIVO em instâncias temporárias isoladas (portas 8786/8787)

Ambiente: `DATA_DIR` temporário, `MULTI_TENANT_ENABLED=false`, senha
efêmera, `TRADINGAGENTS_BROKER_MODE=paper`; instância A **sem** provedor de
IA; instância B **com** provedor groq (chave vinda do ambiente, efêmera —
nunca gravada). O app do dono em `:8000` intocado; nenhuma ordem; os dois
servidores temporários encerrados ao final (portas de volta a "down").

JSON do resultado (recorte verbatim de
`homolog_td3_resultado.json`, fora do repositório):

```json
{"passos": ["instancia A no ar em http://127.0.0.1:8786 (sem provedor IA)",
 "LightweightCharts versao: 5.2.1",
 "canvas no #lcMercado: 7",
 "portfolio: lcPortfolio=0 svgFallback=1",
 "rotulo PAPER visivel: True",
 "leitura sem provedor: sem provedor de IA configurado — a leitura por IA é opcional; configure em Configuração → IA. Nada foi enviado.",
 "fallback: svg=1 LightweightCharts ausente=True",
 "instancia B no ar em http://127.0.0.1:8787 (provedor=groq)",
 "leitura IA real (primeiros 200): Leitura da IA (groq)\n**Resumo Objetivo dos Dados (Fonte: FIXTURE – dados de treino, não de mercado)** ...",
 "uso IA registrado: {\"total\": {\"chamadas\": 1, \"tokens_entrada\": 559, \"tokens_saida\": 984, \"chamadas_sem_uso\": 0, \"chamadas_sem_preco\": 0, \"custo_estimado_usd\": 0.000337}, ..."],
 "navegador": "chrome",
 "hosts_externos_A": [],
 "hosts_externos_B": [],
 "veredito": "OK"}
```

Render AO VIVO no navegador real (Playwright + Chrome, login real → Mesa de
Agentes → Sala do trader):

- Instância A: 7 canvas da biblioteca real no card de mercado (versão
  5.2.1 confirmada por `LightweightCharts.version()`); o card segue com
  gráfico em linha rotulada (fixture) — sem OHLCV inventado.
- A3: "Pedir leitura à IA" → mensagem honesta de "sem provedor … Nada foi
  enviado." (sem nenhuma chamada externa — hosts externos vazios).
- A4: com a rota do vendor bloqueada no navegador, o card cai no SVG do
  TD-2 (1 svg) e segue funcional.
- Instância B: "Pedir leitura à IA" → leitura REAL do provedor sobre as
  séries estruturadas (fechamentos 145.0→158.5, SMA 152.5/143.5, RSI 100,
  S/R vazio, portfólio vazio), com observações finais declarando fixture e
  a última linha repetindo "não é promessa de lucro nem previsão exata de
  preços; quem decide é o dono." (txt integral em
  `td3-04-leitura-ia-real.txt`).
- Consumo real registrado (0.000337 USD) — prova viva de que a tabela de
  preços nova + o registro de uso + a rota funcionam juntos.

Capturas nesta pasta: `td3-01-sala-lc-mercado.png`,
`td3-02-sala-portfolio.png`, `td3-03-leitura-sem-provedor.png`,
`td3-04-leitura-ia-real.png` (+ `.txt`), `td3-05-fallback-svg.png`.

## Limitações declaradas (honestidade de escopo)

- Os dados vivos desta homologação são de **fixture** (mesa parada) —
  provam a fiação, a biblioteca real carregada, o fallback, as leituras e
  os rótulos; **não** o mercado. O caminho de candles/volume reais já
  existia do TD-2 e aparece vivo quando uma fonte real estiver conectada.
- A curva de portfólio em Lightweight Charts ficou sem operações na
  instância (estado vazio honesto); o desenho `AreaSeries` + marcadores é
  pinado por unidade/DOM e entra em cena com o primeiro trade fechado.
- O TradingView Advanced Chart Widget **não** foi embutido (decisão
  documentada acima: CSP local + licença proprietária + sem scraping); se
  o dono quiser licenciar comercialmente o advanced_chart, é decisão
  dele — nada foi ativado por omissão.
- A classe "rápido" de `sugerir_modelos` para o groq escolhe o
  `allam-2-7b` quando o gpt-oss-20b cai como "neutro" no classificador
  por nome (proxy declarado); o painel instrui conferir e a descoberta
  real da API manda quando disponível.
- Homologação no exe instalado (inclusive o visual dos gráficos)
  segue **PENDENTE** — pertence ao TD-5 e continuará declarada como
  pendente até prova reproduzível.

## Verificação final (números frescos da árvore sendo entregue)

- **Suíte completa:** `pytest -q` → **2947 passed / 5 skipped / 71
  subtests** (exit 0, 269,71 s) na árvore exata deste commit.
- **E2E Playwright + uvicorn real:** **13 passed in 31,83 s** (13/13,
  capturas regeradas).
- **Focada dos arquivos tocados:** **120 passed** (5,71 s).
- **Lint:** `ruff check` nos 8 arquivos Python tocados → **All checks
  passed!**
- **Rede:** a homologação viva registrou zero chamadas externas do
  navegador (`hosts_externos_A: []`, `hosts_externos_B: []`); os
  servidores temporários foram encerrados e as portas 8786/8787
  conferidas "down" ao final.


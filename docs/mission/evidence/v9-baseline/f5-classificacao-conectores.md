# F5 — Classificação de integrações externas (MISSÃO V9)

Estado: **EXECUTADO em 2026-10-15** sobre o baseline `2b7754a94cc1e4233ad95ac6fa377be4078e5055` (branch `audit/v9-rc-20261015`). Nenhuma linha de produto foi alterada para produzir esta evidência; apenas ferramentas de prova (`tools/v9_f5_*.py`) e artefatos de evidência foram criados.

Esta é a classificação vinculante da F5. Ela **substitui** os documentos de alegação antigos [CONNECTOR-MATRIX](../CONNECTOR-MATRIX.md) e [AI-PROVIDER-MATRIX](../AI-PROVIDER-MATRIX.md), que usavam "PASS"/"Verified" sem nível de prova — ambos receberam nota de correção datada.

## Níveis (definição da missão)

| Nível | Significado |
| --- | --- |
| IMPLEMENTADO | Código existe; nenhum serviço real exercitado nesta missão. |
| TESTADO COM MOCK | Suíte automatizada exercita o caminho contra fakes/transporte injetado/monkeypatch. Mock NÃO é testnet nem homologação. |
| TESTADO EM TESTNET | Exercitado contra serviço de sandbox/rede de teste. |
| HOMOLOGADO COM SERVIÇO REAL | Exercitado contra o serviço de produção real, com evidência hashada. |

Método: a suíte do repositório roda em executor hermético (rede só loopback, chaves de IA removidas). As provas de serviço real foram executadas **deliberadamente fora** desse executor, somente em modo leitura/zero-token, sem ordens e sem ecoar credenciais — precedente `tools/fm4_prova_viva.py`.

## Artefatos de evidência (sha256)

| Artefato | sha256 | Conteúdo |
| --- | --- | --- |
| [f5-probe-real-readonly.json](f5-probe-real-readonly.json) | `8fc9e3651f06cd3662747fa0eed1409e5bb1d6d9ff5865162b1b97596750be25` | 4 provas read-only: velas Binance produção, livro L2, ponte CCXT testnet sem chaves, YFinance AAPL |
| [f5-probe-real-readonly-invocation.json](f5-probe-real-readonly-invocation.json) | `dc4699a3561e5c7634cfc4b2e56eb558501a361ccc57785a7fb4116c2912d28c` | Invocação da prova acima (exit 0, fora do executor hermético) |
| [f5-deepseek-live.log](f5-deepseek-live.log) | `c1f2b155e467726665fe8878859d827022414b6b81241283eabf583f18619aff` | Teste live do próprio repo (`TestDeepSeekLiveStructuredOutput`): `1 passed, 13 deselected in 4.04s` |
| [f5-deepseek-live-invocation.json](f5-deepseek-live-invocation.json) | `9b532dbf877d1436d99a4f31201ee05e076526cbbaee0b21696de52e15d8cfe4` | Invocação do teste live (1 chamada real, sub-centavo, provedor já configurado pelo dono) |
| [f5-probe-ia-auth.json](f5-probe-ia-auth.json) | `2a2d6a3ec723fbb8022406d2eb4278809305d3430dc10e9976021d58c04aa580` | 9 provedores IA do catálogo sondados pelo caminho do produto `testar_provedor` (GET /models, zero tokens) |
| [f5-probe-ia-auth-invocation.json](f5-probe-ia-auth-invocation.json) | `e6e60c690826e3e132f80b669a291722ff93219b583c3d527c2a51b291ebe8e6` | Invocação da sondagem IA (exit 0; autoverificação anti-vazamento aborta se qualquer valor de chave surgir na saída; stderr 0 bytes) |

Ferramentas: [tools/v9_f5_probe_conectores.py](../../../tools/v9_f5_probe_conectores.py), [tools/v9_f5_probe_ia.py](../../../tools/v9_f5_probe_ia.py).

## Resultado por conector

| # | Conector | Classificação | Evidência-chave | Bloqueio remanescente |
| --- | --- | --- | --- | --- |
| 1 | Binance — leitura pública (mercado) | **HOMOLOGADO COM SERVIÇO REAL** (leitura pública) | Velas 1h BTC/USDT produção, qualidade `ok`, preço 81.043,87; livro L2 com 20 níveis — `8fc9e365…` | — |
| 2 | Binance — leitura autenticada (saldo/posição) | IMPLEMENTADO + TESTADO COM MOCK | Adaptador CCXT + reconciliação (FIN-03/04/06/08 GREEN) | Sem chaves de corretora no ambiente do executor; consulta autenticada real não executada (ver Bloqueios) |
| 3 | CCXT (ponte de execução) | **TESTADO EM TESTNET** (leitura) + TESTADO COM MOCK (ordens) | Objeto REAL da ponte em modo testnet sem chaves: `sandbox=true`, ticker 81.016,01 de `testnet.binance.vision` — `8fc9e365…` | Ordens em testnet não executadas (sem chaves de testnet); invariantes de ordem cobertos só por mock |
| 4 | Provedores de IA — DeepSeek | **HOMOLOGADO COM SERVIÇO REAL** (autenticação zero-token + inferência real) | Sondagem 200 em 951,5ms (2 modelos) — `2a2d6a3e…`; teste live do repo 1 passed — `c1f2b155…` | — |
| 5 | Provedores de IA — Groq / Mistral / OpenRouter | **HOMOLOGADO COM SERVIÇO REAL** (autenticação zero-token) | 200 em 1.161,6ms (11 modelos) / 650,1ms (45) / 730,1ms (462) — `2a2d6a3e…` | Inferência real desses provedores não executada (a de DeepSeek cobre o caminho de inferência) |
| 6 | Provedores de IA — Ollama (local) | **HOMOLOGADO COM SERVIÇO REAL** (serviço local do dono) | 200 em 275,2ms, 8 modelos locais — `2a2d6a3e…`; política de loopback da trava anti-SSRF exercitada de verdade | — |
| 7 | Provedores de IA — OpenAI / Anthropic / Google | IMPLEMENTADO + TESTADO COM MOCK; recusa **sem_chave** comprovada em serviço real | `sem_chave` sem saída de rede — `2a2d6a3e…` | Sem chave nesses provedores no ambiente; serviço pago não autorizado a obter |
| 8 | Provedores de IA — outro OpenAI-compatível | IMPLEMENTADO; recusa **sem_url** comprovada | `sem_url` antes de qualquer rede — `2a2d6a3e…` | Exige URL própria informada pelo dono |
| 9 | Telegram (alertas) | IMPLEMENTADO + TESTADO COM MOCK | Transporte injetado e webhook com header secreto em `test_centro_alertas.py`; POST real em `canais_alerta.py` existe | Sem `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` no ambiente → entrega real NÃO executada ("API aceita ≠ usuário viu") |
| 10 | Windows Notifications (toast) | IMPLEMENTADO + TESTADO À DISTÂNCIA (conteúdo do script) | `test_notificacoes_identidade_win.py` assevera o script (ToastNotificationManager/AUMID); toast nunca disparado; gravação de registro exige Windows real (desselective no executor hermético) | Toast real não disparado em estação isolada |
| 11 | Web Push (PWA) | IMPLEMENTADO + TESTADO COM MOCK DE ALTO NÍVEL | `test_pwa_push.py`: pywebpush REAL com sessão falsa — JWT vAPID ES256, `aes128gcm`, TTL asseverados | Sem endpoint/serviço de push real e sem aparelho (F6.2 depende de hardware do dono) |
| 12 | Dados públicos de mercado — Yahoo Finance | **HOMOLOGADO COM SERVIÇO REAL** | `get_YFin_data_online("AAPL")` via dataflow do app: 10 registros, última linha 2026-10-14 — `8fc9e365…` | — |
| 13 | Dados públicos de mercado — FRED / Alpha Vantage | IMPLEMENTADO + TESTADO COM MOCK | `test_fred.py` mockado | Sem `FRED_API_KEY`/`ALPHA_VANTAGE_API_KEY` no ambiente → chamada real não executada |
| 14 | Polymarket (leitura) | IMPLEMENTADO + TESTADO COM MOCK | `policy._request` mockado | Chamada real não executada nesta F5 |
| 15 | IQ Option | IMPLEMENTADO (laboratório, replay/paper) | Sem cliente de execução no produto (decisão registrada: nunca execução) | Nada a homologar por desenho; execução proibida |
| 16 | Discord / WhatsApp | NENHUM (apenas nomes remanescentes) | Sem código de entrega | Sem implementação |
| 17 | Woovi (verificação de assinatura) | IMPLEMENTADO + TESTADO (verificação RSA real, sem HTTP) | Testes de verificação criptográfica local | Nada a homologar por desenho (sem chamada de rede no caminho de verificação) |

## Detalhe das evidências reais

### Binance produção (leitura pública) — HOMOLOGADO

Caminho real do produto: `tradingagents/desk/market.py::PublicMarketFeed` (cliente CCXT público, sem chaves). Prova: velas 1h de BTC/USDT com qualidade `ok` (4 velas fechadas, último fechamento 11:00 UTC, último preço 81.043,87) e leitura de livro L2 público (20 níveis de bids/asks). `credenciais_usadas:false`, `ordens_enviadas:false` registrados na evidência.

### CCXT testnet (leitura) — TESTADO EM TESTNET

Caminho real do produto: `tradingagents/brokers/ccxt_bridge.py::CCXTBridge` em modo `testnet`, **sem chaves** (permitido por desenho; só o modo live exige chaves). Prova: objeto real da ponte com `set_sandbox_mode(True)` contra `testnet.binance.vision`, `status.sandbox=true`, `keys_set=false`, ticker público 81.016,01. Ordens em testnet **não** foram executadas (exigem chaves de testnet, ausentes) — o caminho de ordem segue coberto apenas por mock (`test_broker_ccxt.py` com `_ex.create_order`/`_ex.fetch_ticker` monkeypatched e chaves literais falsas).

### Provedores de IA — autenticação real zero-token

Caminho real do produto: `service/api/ia_provedores.py::testar_provedor` (GET no endpoint oficial de modelos; zero tokens; URL passa pela trava anti-SSRF). Resultado em `f5-probe-ia-auth.json`:

| Provedor | Estado | Latência | Modelos |
| --- | --- | --- | --- |
| deepseek | `ok` 200 | 951,5ms | 2 |
| groq | `ok` 200 | 1.161,6ms | 11 |
| mistral | `ok` 200 | 650,1ms | 45 |
| openrouter | `ok` 200 | 730,1ms | 462 |
| ollama (local) | `ok` 200 | 275,2ms | 8 |
| openai | `sem_chave` (sem rede) | — | — |
| anthropic | `sem_chave` (sem rede) | — | — |
| google | `sem_chave` (sem rede) | — | — |
| openai_compatible | `sem_url` (sem rede) | — | — |

As recusas são evidência de fail-closed em serviço real: sem chave (quando exigida) ou sem URL, **não há saída de rede** — o teste não "passa" sem credencial de verdade.

### DeepSeek — inferência real (teste do próprio repo)

`tests/test_deepseek_reasoning.py::TestDeepSeekLiveStructuredOutput` executado fora do executor hermético (que remove chaves): 1 chamada real a `https://api.deepseek.com` com `with_structured_output`, resultado tipado validado. `1 passed, 13 deselected in 4.04s`, exit 0. Custo: uma chamada de inferência sub-centavo no provedor que o dono já configurou; nenhum serviço novo contratado.

### Yahoo Finance — HOMOLOGADO

`tradingagents/dataflows/y_finance.py::get_YFin_data_online("AAPL")` (caminho do produto, sem chave): 10 registros, última linha de dados 2026-10-14, cabeçalho `# Total records: 10`. Nenhuma assertiva do teste de janela foi enfraquecida; a prova usa o mesmo dataflow que o produto consome.

## Bloqueios precisos (o que NÃO foi executado e por quê)

1. **Binance autenticada (leitura de saldo/posição)** — sem chaves de corretora no ambiente do executor. A chave do dono existe fora deste ambiente, mas **não foi usada**: a nota de acesso de 2026-10 registra que ela tem trading/transfer habilitados; a recomendação registrada é rotacionar/criar chave somente-leitura antes de qualquer homologação autenticada (F2). Nenhum valor de credencial foi lido ou ecoado.
2. **Ordens em testnet** — exigem chaves de testnet (ausentes). Prova possível hoje: leitura pública do sandbox (feita). Próximo passo: dono criar chaves de testnet da Binance (o produto recusa testnet sem `urls.test` real em vez de degradar para live).
3. **Telegram real** — sem `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`. Próximo passo: dono criar bot de teste e informar token no painel para uma entrega real única.
4. **Windows toast real** — nunca disparado no executor; exige estação Windows com notificações habilitadas. Próximo passo: disparar um toast no ambiente isolado (F4/F8.4) sem tocar a instalação do dono.
5. **Web Push real** — sem endpoint de navegador/aparelho inscrito. Depende de F6.2 (hardware do dono); emuladores não homologam.
6. **FRED / Alpha Vantage** — sem chaves no ambiente; chamada real não executada.
7. **Inferência paga além de DeepSeek** — não autorizada a obter serviços pagos; Groq/Mistral/OpenRouter homologados apenas na autenticação zero-token (suficiente para provar chave aceita + conectividade; inferência já provada no caminho único).

## Higiene de credenciais

- Nenhuma prova desta F5 registrou valor de token, senha ou chave. A sondagem IA inclui autoverificação: se qualquer valor de chave de ambiente surgir no texto de saída, o script **aborta sem imprimir** (exit 2). Resultado real: stderr 0 bytes, exit 0.
- Somente chaves **já presentes no ambiente do dono** foram usadas, e somente nos caminhos que o próprio produto executa (sondagem de modelos e o teste live desenhado pelo dono). Nenhum serviço pago novo foi contratado.
- `LIVE` permaneceu bloqueado em todas as provas: nenhuma ordem, nenhuma movimentação de saldo (`ordens_enviadas:false` em toda evidência).

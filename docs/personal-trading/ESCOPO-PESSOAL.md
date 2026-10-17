# Escopo definitivo: trading pessoal, proprietário único

## Decisões
- Produto privado single-user: **não** vender, não oferecer a clientes, não habilitar cadastros públicos, multitenancy, billing, PIX, planos, convites ou perfis de terceiros.
- Preservar dados e código legado até migração validada, para evitar regressões/destruição. Não retirar proteção/autorização de sessão do proprietário.
- Salas isoladas para nove corretoras CCXT: Binance, Bybit, OKX, Coinbase
  Exchange, Gate.io, Kraken, KuCoin, Mercado Bitcoin e Bitget. A IQ Option
  permanece em sala experimental separada, por integração MCP oficial.
- Separar dados de ordens, posições, saldo, fees, métricas e riscos por exchange/mercado/modo.
- Operar em PAPER/SHADOW/TESTNET inicialmente. Sem ordem real e sem alavancagem na primeira fase.
- R$ 5.000 é um orçamento FUTURO hipotético para LIVE, **não** uma autorização para movimentar conta.

## Estado de implementação
- `service/personal_exchange_catalog.py` registra as nove salas CCXT e a sala
  IQ Option; o catálogo está conectado à API e ao painel sem habilitar LIVE.
- As nove corretoras CCXT possuem salas PAPER isoladas e prova real de ticker
  + OHLCV públicos. A prova de 2026-10-17 registra 9/9, sem credenciais e sem
  ordens. Isso não substitui autenticação da conta.
- Binance, Bybit, OKX, Coinbase Exchange e Gate.io anunciam TESTNET nativo no
  catálogo. Fill e reconciliação TESTNET continuam pendentes das credenciais
  próprias de sandbox do proprietário.
- A IQ Option usa sete endpoints MCP oficiais com contrato passivo validado.
  A primeira leitura autenticada ainda exige token novo estritamente read-only;
  portanto está preparada, mas não homologada por conta.
- `LIVE_TRADING=false`, `REAL_MONEY_EXECUTION=false` e
  `MESA_LIVE_LIBERADA=False` permanecem efetivos e cobertos por testes.
- O fluxo Escola → Sala REAL bloqueada está implementado. Formação apenas
  encaminha o agente; liberação futura continua vinculada a estratégia,
  versão, corretora, conta, provas vigentes e confirmação do proprietário.

## Critérios de aceite
- Não há signup nem SaaS exposto em rotas/frontend/docs finais.
- Perfis/infra são realmente single-owner e autenticados, inclusive todas as rotas administrativas.
- Nenhum agente LLM pode gerar ordens sem aprovação do motor determinístico de risco.
- O painel discrimina adaptador configurado, dados reais de cotação, simulação local e execução na corretora.
- Nunca misturar operações IQ Option com desempenho Spot ou usar 3 operações vencedoras como evidência estatística.

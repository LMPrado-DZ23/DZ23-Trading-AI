# Escopo definitivo: trading pessoal, proprietário único

## Decisões
- Produto privado single-user: **não** vender, não oferecer a clientes, não habilitar cadastros públicos, multitenancy, billing, PIX, planos, convites ou perfis de terceiros.
- Preservar dados e código legado até migração validada, para evitar regressões/destruição. Não retirar proteção/autorização de sessão do proprietário.
- Salas isoladas: Binance (ccxt binance), OKX (ccxt okx), Mercado Bitcoin (ccxt mercado), Kraken (ccxt kraken), Coinbase (ccxt coinbase), IQ Option (laboratório experimental próprio; API ainda não homologada).
- Separar dados de ordens, posições, saldo, fees, métricas e riscos por exchange/mercado/modo.
- Operar em PAPER/SHADOW/TESTNET inicialmente. Sem ordem real e sem alavancagem na primeira fase.
- R$ 5.000 é um orçamento FUTURO hipotético para LIVE, **não** uma autorização para movimentar conta.

## Estado de implementação
- `service/personal_exchange_catalog.py` registra as 6 salas em catálogo seguro **sem fazer conexão real**.
- `.env.personal.example` é exemplo de configuração **não aplicado automaticamente** até auditar a leitura de configurações do serviço.
- Sala IQ Option permanece em escopo, mas exige fonte de dados e conformidade técnica comprovadas, sem prometer preço idêntico a mercados spot.
- `LIVE_TRADING=false`, `REAL_MONEY_EXECUTION=false`, `SIGNUP_ENABLED=false` devem ser defaults efetivos validados com testes de integração, antes de deploy pessoal.
- PR #3 `correcao-auditoria` e problemas F3–F6/K1–K6/A1–A2 precisam ser conciliados, com CI e testes financeiros antes de LIVE.
- Próxima implementação: conectar catálogo à UI/API e criar adaptadores testados de leitura de mercado e paper para cada corretora; só depois preparar autenticação privada do proprietário e eventual LIVE.

## Critérios de aceite
- Não há signup nem SaaS exposto em rotas/frontend/docs finais.
- Perfis/infra são realmente single-owner e autenticados, inclusive todas as rotas administrativas.
- Nenhum agente LLM pode gerar ordens sem aprovação do motor determinístico de risco.
- O painel discrimina adaptador configurado, dados reais de cotação, simulação local e execução na corretora.
- Nunca misturar operações IQ Option com desempenho Spot ou usar 3 operações vencedoras como evidência estatística.

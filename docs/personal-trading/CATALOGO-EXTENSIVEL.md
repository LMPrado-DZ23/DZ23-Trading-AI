# Multi-corretora extensível — catálogo dinâmico e gates

Data: 2026-10-12

## Decisão
A plataforma pessoal não ficará restrita a seis corretoras. `service/personal_exchange_discovery.py` descobre dinamicamente os IDs expostos pela versão **instalada** da CCXT, sem presumir um número fixo de exchanges ou que todas as funções existam. A listagem publicada pela CCXT indicava 103 corretoras de cripto e 9 mercados de previsão no dia da consulta; o conjunto disponível no runtime depende do pacote efetivamente instalado.

## Status real desta fatia
- Implementado: catálogo explícito de nove corretoras CCXT e uma sala separada
  para a IQ Option, integrado à API e ao painel. O catálogo nunca instancia
  credenciais nem habilita LIVE.
- Implementado: prova pública e sem credenciais de ticker + OHLCV nas nove
  corretoras CCXT. A evidência versionada
  `docs/mission/evidence/real-readiness/corretoras-publicas.json` registra
  `9/9 PUBLIC_READ_OK`, `credenciais_usadas=false` e
  `ordens_enviadas=false`.
- Implementado: matriz de capacidades e credenciais mínimas por corretora,
  separando TESTNET nativo de leitura/preparação interna. Uma corretora sem
  sandbox nativo não é anunciada como TESTNET operacional.
- Implementado: prova autenticada somente leitura por conta REAL, sem
  persistir saldo, ordens ou identificador privado, e prova TESTNET vinculada
  à corretora, fingerprint da credencial e instante do vínculo.
- Implementado: testes unitários, adversariais, integração, CI e E2E para
  catálogo, probes públicos, preparação REAL e bloqueio de LIVE.
- IQ Option permanece **fora de CCXT**, como sala separada. Os sete handshakes
  oficiais e seus contratos foram validados em 2026-10-16. A sonda passiva
  executa apenas `initialize` e `tools/list`. Um módulo isolado prepara a prova
  de conta com uma única chamada literal `list_balances({types: NORMAL})`,
  revalida o contrato antes de chamar e descarta saldo, moeda e ID bruto. Esse
  módulo não contém nomes nem caminhos de ferramentas mutáveis. A primeira
  consulta autenticada de conta ainda depende da confirmação do proprietário;
  portanto o estado é **preparado**, não homologado. O PolicyLayer continua
  sem registro publicado para os sete identificadores (`unknown`). Nenhuma
  dessas provas habilita ordens. Como o token anuncia também ferramentas
  mutáveis, o gate de permissões mínimas permanece fechado até existir escopo
  read-only verificável no provedor; o bloqueio local não é apresentado como
  permissão mínima remota.
- Operações LIVE continuam bloqueadas. Listar uma corretora **não** significa que sua integração foi testada ou liberada para ordens.

## Política para muitas exchanges
1. Descobrir dinamicamente todos os adapters instalados sem realizar conexão.
2. Restringir ativação operacional a allowlist explícita do proprietário; verificar jurisdição/termos, endpoints e mercado.
3. Executar health/capabilities de cada exchange com orçamento de chamadas e cache. Nenhuma validação deve enviar ordem real.
4. Priorizar Binance, OKX, Mercado Bitcoin, Kraken e Coinbase; depois Bitget, Bybit, KuCoin, Gate, Bitfinex, Gemini, Crypto.com e outras, somente quando ofereçam integração autorizada.
5. Paper por exchange com fees, spread, slippage e símbolo correto; testnet só se comprovada; LIVE depende de gate e autorização humana específicos.
6. Não carregar nem instanciar centenas de adapters em loops de trading continuamente; carregar apenas os habilitados e distribuir rate-limit por exchange.
7. Integrar monitoramento de falhas: manutenção, erro de assinatura, mercado proibido, stream stale, saldo divergente, reconciliação, latência e circuit breaker.

## Evidência
Commits desta branch contêm `service/personal_exchange_discovery.py` e `tests/test_personal_exchange_discovery.py`. Os testes ainda exigem execução no CI/ambiente autorizado; o arquivo não constitui comprovante de testes aprovados.

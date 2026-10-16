# DZ23 Trading AI — CONNECTOR MATRIX

| Exchange | Mode | Read-Only API Test | Paper Trading | Live Execution | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Binance** | Spot | Verified | Supported | DISABLED (FAIL-CLOSED) | PASS |
| **OKX** | Spot | Verified | Supported | DISABLED (FAIL-CLOSED) | PASS |
| **Mercado Bitcoin** | Spot | Verified | Supported | DISABLED (FAIL-CLOSED) | PASS |
| **Kraken** | Spot | Verified | Supported | DISABLED (FAIL-CLOSED) | PASS |
| **Coinbase** | Spot | Verified | Supported | DISABLED (FAIL-CLOSED) | PASS |
| **Bybit** | Spot | Capability Discovery | Supported | DISABLED (FAIL-CLOSED) | PASS |
| **Bitget** | Spot | Capability Discovery | Supported | DISABLED (FAIL-CLOSED) | PASS |
| **KuCoin** | Spot | Capability Discovery | Supported | DISABLED (FAIL-CLOSED) | PASS |
| **Gate.io** | Spot | Capability Discovery | Supported | DISABLED (FAIL-CLOSED) | PASS |
| **IQ Option** | MCP oficial | Preparada; exige token read-only e prova autenticada | Offline Replay/Paper | DISABLED (FAIL-CLOSED) | OFFICIAL MCP / PREPARED |

---

**Correção datada (2026-10-15, MISSÃO V9/F5):** os "Verified"/"PASS" desta tabela indicam apenas que o caminho existe e tem teste com mock — nunca foram homologação contra serviço real, e "Read-Only API Test: Verified" não foi executado com chave autenticada nesta missão. A classificação vinculante por nível de prova (IMPLEMENTADO / TESTADO COM MOCK / TESTADO EM TESTNET / HOMOLOGADO COM SERVIÇO REAL) está em [evidence/v9-baseline/f5-classificacao-conectores.md](evidence/v9-baseline/f5-classificacao-conectores.md): Binance leitura **pública** homologada no serviço real (velas + livro L2), ponte CCXT testnet exercitada de verdade só em leitura (ticker do sandbox, sem chaves; ordens em testnet não executadas por ausência de chaves de testnet), leitura autenticada de saldo/posição ainda pendente de credencial somente-leitura do dono.

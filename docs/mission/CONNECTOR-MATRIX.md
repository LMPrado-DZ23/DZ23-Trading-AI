# DZ23 Trading AI — CONNECTOR MATRIX

| Exchange | Mode | Prova atual | Paper Trading | Live Execution | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Binance** | Spot | Leitura pública real 2026-10-17; conta não autenticada | Supported | DISABLED (FAIL-CLOSED) | PUBLIC READ OK |
| **OKX** | Spot | Leitura pública real 2026-10-17; conta não autenticada | Supported | DISABLED (FAIL-CLOSED) | PUBLIC READ OK |
| **Mercado Bitcoin** | Spot | Leitura pública real 2026-10-17; conta não autenticada | Supported | DISABLED (FAIL-CLOSED) | PUBLIC READ OK |
| **Kraken** | Spot | Leitura pública real 2026-10-17; conta não autenticada | Supported | DISABLED (FAIL-CLOSED) | PUBLIC READ OK |
| **Coinbase** | Spot | Leitura pública real 2026-10-17; conta não autenticada | Supported | DISABLED (FAIL-CLOSED) | PUBLIC READ OK |
| **Bybit** | Spot | Leitura pública real 2026-10-17; conta não autenticada | Supported | DISABLED (FAIL-CLOSED) | PUBLIC READ OK |
| **Bitget** | Spot | Leitura pública real 2026-10-17; conta não autenticada | Supported | DISABLED (FAIL-CLOSED) | PUBLIC READ OK |
| **KuCoin** | Spot | Leitura pública real 2026-10-17; conta não autenticada | Supported | DISABLED (FAIL-CLOSED) | PUBLIC READ OK |
| **Gate.io** | Spot | Leitura pública real 2026-10-17; conta não autenticada | Supported | DISABLED (FAIL-CLOSED) | PUBLIC READ OK |
| **IQ Option** | MCP oficial | Preparada; exige token read-only e prova autenticada | Offline Replay/Paper | DISABLED (FAIL-CLOSED) | OFFICIAL MCP / PREPARED |

---

**Leitura correta da tabela:** `PUBLIC READ OK` comprova somente ticker e velas
sem autenticação. A evidência sanitizada está em
[evidence/real-readiness/corretoras-publicas.json](evidence/real-readiness/corretoras-publicas.json)
e declara `credenciais_usadas=false` e `ordens_enviadas=false`. Leitura de
saldo/posição, identidade da conta, permissão mínima, fill TESTNET e
reconciliação continuam provas separadas por corretora/conta. A
classificação histórica da auditoria V9/F5 permanece em
[evidence/v9-baseline/f5-classificacao-conectores.md](evidence/v9-baseline/f5-classificacao-conectores.md).

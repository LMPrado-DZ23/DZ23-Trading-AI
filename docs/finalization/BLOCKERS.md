# BLOCKERS

Ordem por dano financeiro potencial. Fechado só com teste que prova.

## Abertos

### Fee pipeline
| # | Item | Dano se ignorado |
|---|---|---|
| F2 | `valuation_id` + fingerprint no Ledger | retry com cotação diferente adota a antiga e grava metadado novo |
| F3 | Valoração por ocorrência | uma cotação aplicada a taxas de horários diferentes |
| F4 | Liquidação do passivo funcional | `LIABILITY:FEE_FUNDING` nunca zera |
| F5 | Taxa em base currency | posição 1 BTC com 0,001 BTC de taxa sem origem |
| F6 | Parser único na reconciliação | dois caminhos podem divergir |

### Kernel
| # | Item | Dano |
|---|---|---|
| K1 | `expires_at` | reserva expirada financia ordem |
| K2 | `mandate_version` | reserva de v1 financia ordem sob v2 |
| K3 | `reserved_amount=0` | truthy usado como pular validação |
| K4 | Boot freshness do SAFE | relatório anterior ao restart autoriza |
| K5 | `risk_status` no relatório | reconciliação diz SAFE sem olhar risco |
| K6 | Fill `RECEIVED/APPLYING/COMMITTED` | fill marcado visto antes de contabilizar |

### Arquitetura
| # | Item | Situação |
|---|---|---|
| A1 | `AuthenticatedActorContext` | `BLOCKED_BY_ARCHITECTURE` — `actor.capabilities` ainda é autoafirmação |
| A2 | Typecheck | **não existe** no projeto; CI roda só pytest, compileall e ruff |

## Fechados (com o SHA que fechou)

| Item | SHA |
|---|---|
| Contas de reserva identificadas | `c1efbaa` |
| Incidente como entidade própria | `7236df8` |
| Déficit com fonte única | `b16c2c2` |
| Prova econômica de resolução | `6e133ea` |
| Compensação vinculada ao incidente | `b51df7d` |
| Settlement serializado e idempotente | `3068d8d` |
| Recuperação por incident+settlement | `1b00320` |
| Taxa com moeda | `4632dae` |
| Valoração separada da aplicação | `c69fc46` |
| Ativo descoberto por moeda | `3829e39` |
| Guard de execução | `8b0f912`, `b488eb3` |
| Fee pipeline: moeda, lastro, multimoeda | `fee75cd`, `5cd2c1e`, `363b8ed` |
| Componentes, passagem única, ambiguidade | `aed6334`, `ef2a234`, `8925e0d` |
| F1 — lastro por componente | *este commit* |

# BLOCKERS

Ordem por dano financeiro potencial. Fechado só com teste que prova.

## Abertos

### Fee pipeline — patch da auditoria (F1/F2 REABERTOS)
A auditoria adversarial encontrou blockers DENTRO de F1/F2. Fechar antes de
F3+. Detalhe e estado em `STATUS.md`.

| # | Item | Dano se ignorado | Estado |
|---|---|---|---|
| B2 | soma exata dos componentes | componente 2 BNB marca VALUED valorando só 1 | 🔴 aberto |
| B3 | validação de ids (vazio/dup/cross/valorado) | valoração sem lastro real | 🔴 aberto |
| B4 | incidente possui os componentes que resolve | usa componente de outro fill/ordem | 🔴 aberto |
| B5 | component id durável (fora do memo) | memo editado destrói identidade | 🔴 aberto |
| B6 | concorrência do recovery (CAS/unique) | dois processos usam o mesmo componente | 🔴 aberto |
| B1 | identidade canônica na adoção | livro=val-A, incidente=val-B | ✅ `b199ee1` |
| B7 | fingerprint canônico | 300≠300.0; falta functional/policy | ✅ `5563dcd` |
| B8 | restore revalida dimensões | doc cross-unit volta pelo restore | ✅ `7884687` |
| B9 | teste da identidade dupla fortalecido | mutação sobrevivia | ✅ `b199ee1` |

### Fee pipeline — restante
| # | Item | Dano se ignorado |
|---|---|---|
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
| F1 — lastro por componente | `f28dc55` |
| F2 — fingerprint e conflito de recovery | *este commit* |

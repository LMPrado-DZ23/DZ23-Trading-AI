# STATUS — finalização DZ23 Trading

> Documento de retomada. Se a sessão cair, comece por aqui.

## Onde estamos

| | |
|---|---|
| Repositório | `LMPrado-DZ23/tradingagents-dz23` |
| Branch | `correcao-auditoria` |
| HEAD | ver seção *Histórico* abaixo (sempre o topo) |
| Fase atual | Fase 1 · patch F1/F2 da auditoria: B1,B2,B3,B4,B7,B8,B9 fechados; B5,B6 abertos |
| LIVE | **BLOQUEADO** — não habilitar nesta missão |
| SIGNUP_ENABLED | **false** — só liberar com as evidências do gate de cadastro |

## Patch F1/F2 (auditoria adversarial) — sub-blockers

A auditoria reabriu F1/F2 com 10 itens. Estado:

| # | Item | Estado | SHA |
|---|---|---|---|
| B1 | identidade canônica na adoção de recovery | ✅ fechado | `b199ee1` |
| B2 | soma EXATA dos componentes (2 BNB não valora 1) | ✅ fechado | `7f5b714` |
| B3 | ids duplicados/vazios/cross-currency/já-valorados | ✅ fechado | `7f5b714` |
| B4 | incidente possui os componentes que resolve | ✅ fechado | `a4e9490` |
| B5 | component id fora do memo (dado durável) | 🔴 aberto | — |
| B6 | recovery e concorrência (CAS/unique) | 🔴 aberto (provável BLOCKED_BY_ARCHITECTURE) | — |
| B7 | fingerprint canônico (normalização + dims) | ✅ fechado | `5563dcd` |
| B8 | restore revalida dimensões | ✅ fechado | `7884687` |
| B9 | teste da identidade dupla fortalecido | ✅ fechado | `b199ee1` |
| B10 | mutation matrix | ✅ em cada commit | — |

> B2/B3 fechados em `7f5b714`: `fee_valuation` virou a API operacional (exige
> `fee_component_ids` não-vazia, sem duplicados, mesma moeda, não-valorados e
> **soma exata** == quantidade). `fee_valuation_sem_lastro` é a porta de compat
> explícita; os 14 call-sites antigos foram migrados para ela. Restam B4–B6;
> não iniciar F3/F4 antes de fechá-los.
>
> B4 fechado em `a4e9490`: `valorar_taxa` congela a quantidade coberta e o
> batch de componentes não-valorados que a lastreiam; `aplicar_valoracao`
> consome esse batch congelado (com fallback compat) em vez de reler o
> fee_amount e varrer os componentes de novo. Fecha a inflação da valoração
> por taxa que chega depois (F) e a adoção de componente fora do batch (E).

## Baseline reproduzida

Medida por execução, não por leitura de README:

```
pytest -q            1979 passed · 2 skipped · 71 subtests · ~30s
ruff check .         All checks passed
compileall           ok (tradingagents, service, scripts)
secret scan          nenhum segredo real versionado
```

Os 2 skips são justificados e não escondem falha:
- `test_bedrock_provider` — `langchain_aws` não instalado
- `test_deepseek_reasoning` — exige chave real; pula a chamada de rede

### Limitação do ambiente desta sessão (registrada, não escondida)

O ambiente local desta sessão é **Python 3.14 sem as dependências pesadas**
(`langgraph`, `pandas`, `ccxt`, ...), que não têm wheels para 3.14. Por isso a
**suíte completa (1979) NÃO roda aqui** — só o **subconjunto do kernel**
(fee/recovery/ledger/incidentes/restore ≈ 200 testes), que cobre F1/F2. A
suíte completa é responsabilidade do **CI do GitHub** (pytest+compileall+ruff)
ao dar push. `ruff check .` roda e passa localmente. Gate de deploy "suíte
completa verde" ⇒ confirmar no CI antes de redeploy.

## Divergência registrada

O prompt de finalização esperava HEAD `ef2a234`. O HEAD real era
`8925e0d` — dois commits à frente, ambos do Fee Pipeline. Seguimos o HEAD
real como fonte de verdade, conforme a regra do próprio prompt.

## Próximos três itens

1. **B5** — componente como dado durável estruturado (fora do parsing de memo):
   `fee_component_id`, `fill_id`, `order_id`, `amount`, `currency`,
   `occurred_at`, `source`, `valuation_status` como campos, não texto. Hoje
   `fee_components()` faz `memo.split("fee_component_id=")` — um memo editado
   destrói a identidade.
2. **B6** — concorrência do recovery: unique/CAS por `fee_component_id` (uma
   valoração ativa por componente). Se o storage em memória não suportar,
   `BLOCKED_BY_ARCHITECTURE` documentado + gate fechado para multiprocess.
3. **F3/F4** — só depois de B5/B6: valoração por ocorrência e liquidação do
   passivo `LIABILITY:FEE_FUNDING`. Parar para auditoria antes da máquina do
   fill (`RECEIVED → APPLYING → COMMITTED`).

Depois de B2–B6: F3 (valoração por ocorrência), F4 (`LIABILITY:FEE_FUNDING`),
e a máquina do fill `RECEIVED → APPLYING → COMMITTED` (parar para auditoria
antes desta).

## Dependências externas

| Item | Situação |
|---|---|
| Deploy público | vários commits atrás; redeploy é ação do proprietário |
| `AuthenticatedActorContext` | `BLOCKED_BY_ARCHITECTURE` — exige camada de auth |
| Corretora real | não usada; PAPER/SHADOW apenas |

## Instrução de retomada

Ler este arquivo, depois `BLOCKERS.md`. Rodar a baseline acima e confirmar
que bate. Pegar o primeiro item de *Próximos três* e seguir o loop:
teste vermelho → causa raiz → suíte → mutação → commit → atualizar este
arquivo.

## Histórico

| SHA | O quê | Testes |
|---|---|---|
| `8925e0d` | passagem única, moeda canônica, ambiguidade estruturada | 1960 |
| `e678871` | baseline reproduzida, mapa de blockers | 1960 |
| `f28dc55` | `fee_component_id`: lastro por componente | 1970 |
| `40dbbf3` | `valuation_id` + fingerprint; conflito de recovery | 1979 |
| `7884687` | B8: restore revalida dimensões | kernel subset |
| `5563dcd` | B7: fingerprint canônico normalizado + policy version | kernel subset |
| `b199ee1` | B1/B9: identidade canônica na adoção de recovery | kernel subset |
| `6384c11` | docs de finalização (gates, evidência, matriz) | — |
| `7f5b714` | B2/B3: valoração operacional com lastro e soma exata | 243 (kernel) |
| `8b65804` | docs: fecha B2/B3, registra B4-B6 | — |
| `a4e9490` | B4: valorar_taxa congela quantidade e batch de componentes | 175 (kernel) |

> A partir de `7884687` a contagem "kernel subset" reflete a limitação do
> ambiente (3.14): ~200 testes de F1/F2 verdes localmente; suíte completa via
> CI. Cada commit acima tem teste vermelho primeiro + matriz de mutação.

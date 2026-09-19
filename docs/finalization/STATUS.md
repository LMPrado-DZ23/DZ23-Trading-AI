# STATUS — finalização DZ23 Trading

> Documento de retomada. Se a sessão cair, comece por aqui.

## Auditoria em andamento (NÃO mover este SHA)

- **Patch F1/F2 (B1–B10)** congelado em `correcao-auditoria` @ **`e51ce536`**.
- **DRAFT PR #3** → https://github.com/LMPrado-DZ23/tradingagents-dz23/pull/3
  (base `main` ← head `correcao-auditoria`). **Sem merge, sem auto-merge.**
- Gate 1 (suíte completa) roda no CI **deste PR**. Só entregar para auditoria
  com todos os checks obrigatórios verdes.
- Continuação (F3/F4) vive na branch **`fase1-f3-f4`**, a partir de `e51ce536`,
  para não transformar o SHA em auditoria num alvo móvel. Nada de deploy destes
  commits antes do Gate 1 verde e da auditoria de F1/F2.

## Onde estamos

| | |
|---|---|
| Repositório | `LMPrado-DZ23/tradingagents-dz23` |
| Branch | `correcao-auditoria` |
| HEAD | ver seção *Histórico* abaixo (sempre o topo) |
| Fase atual | Fase 1 · patch F1/F2 da auditoria **COMPLETO** (B1–B10 fechados). Próximo: F3/F4 |
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
| B5 | component id fora do memo (dado durável) | ✅ fechado | `cad7a5c` |
| B6 | recovery e concorrência (CAS/unique) | ✅ BLOCKED_BY_ARCH(multiproc) documentado | `8b265ce` |
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
> Patch F1/F2 completo. Fechamentos: B1 `b199ee1`, B2/B3 `7f5b714`, B4
> `a4e9490`, B5 `cad7a5c`, B6 `8b265ce` (BLOCKED_BY_ARCH multiproc), B7
> `5563dcd`, B8 `7884687`, B9 `b199ee1`, B10 (matriz de mutação em cada
> commit). Cada fix teve teste vermelho primeiro + matriz de mutação.
>
> B5: identidade do componente virou dado estruturado (`fee_component_records`
> na transação), lido antes do memo; roundtrip export/restore. B6: dentro do
> processo `_lock_settlement` serializa (provado); entre processos a segurança
> é a reconciliação por estado compartilhado (adoção); o caso de livros em
> memória divergentes é BLOCKED_BY_ARCHITECTURE(multiprocess), imposição
> futura via UNIQUE no ledger em Postgres.

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

1. **F3** — ✅ FECHADO (`10eb598`, branch `fase1-f3-f4`). Valoração POR
   COMPONENTE: `valorar_taxa(cotacoes={fee_component_id: {rate,fonte,em}})`;
   cada componente valorado com sua cotação/fonte/instante e fingerprint. Modo
   agregado (cotação única) intacto por dispatch. 11 testes (A–K) + 6 mutantes.
2. **F4** — ✅ FECHADO (`2cf2cf5`). `liquidar_fee_funding(fee_component_id)`
   extingue o passivo funcional por componente contra `CONTRIBUTED_CAPITAL`,
   exatamente uma vez, com gate de ativo financiado. 7 testes + 4 mutantes.
3. **Máquina do fill** (`RECEIVED → APPLYING → COMMITTED`) — **PARAR PARA
   AUDITORIA / NOVO CHECKPOINT antes de iniciar**, conforme o prompt mestre e a
   instrução do dono. Não começar sem sinal verde. (F5/F6 do fee pipeline
   também pendentes, ver BLOCKERS.)

> O patch F1/F2 (B1–B10) está completo e publicado em `correcao-auditoria`.
> ATENÇÃO: o CI (`ci.yml`) NÃO dispara em push para essa branch (só
> `main`/`unificacao`/`feat/**` ou `pull_request`). A suíte completa (gate 1)
> só roda ao abrir um PR — ação outward-facing, aguardando o dono. Localmente,
> só o subconjunto do kernel (py3.14). Antes de F3/F4 e de qualquer redeploy,
> gate 1 verde no CI.


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
| `cad7a5c` | B5: identidade do componente como dado durável (fora do memo) | 247 (kernel) |
| `8b265ce` | B6: fronteira multiprocess documentada + teste de reconciliação | 241 (kernel) |

> A partir de `7884687` a contagem "kernel subset" reflete a limitação do
> ambiente (3.14): ~200 testes de F1/F2 verdes localmente; suíte completa via
> CI. Cada commit acima tem teste vermelho primeiro + matriz de mutação.

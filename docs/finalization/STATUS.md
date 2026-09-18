# STATUS — finalização DZ23 Trading

> Documento de retomada. Se a sessão cair, comece por aqui.

## Onde estamos

| | |
|---|---|
| Repositório | `LMPrado-DZ23/tradingagents-dz23` |
| Branch | `correcao-auditoria` |
| HEAD | ver seção *Histórico* abaixo (sempre o topo) |
| Fase atual | Fase 0 concluída · Fase 1 em andamento |
| LIVE | **BLOQUEADO** — não habilitar nesta missão |

## Baseline reproduzida

Medida por execução, não por leitura de README:

```
pytest -q            1960 passed · 2 skipped · 71 subtests · ~33s
ruff check .         All checks passed
compileall           ok (tradingagents, service, scripts)
secret scan          nenhum segredo real versionado
```

Os 2 skips são justificados e não escondem falha:
- `test_bedrock_provider` — `langchain_aws` não instalado
- `test_deepseek_reasoning` — exige chave real; pula a chamada de rede

## Divergência registrada

O prompt de finalização esperava HEAD `ef2a234`. O HEAD real era
`8925e0d` — dois commits à frente, ambos do Fee Pipeline. Seguimos o HEAD
real como fonte de verdade, conforme a regra do próprio prompt.

## Próximos três itens

1. `fee_component_id` — lastro por componente, não por saldo acumulado
2. `valuation_id` + fingerprint no Ledger; conflito de recovery com cotação diferente
3. Máquina do fill `RECEIVED → APPLYING → COMMITTED`

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

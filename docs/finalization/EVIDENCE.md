# EVIDENCE — patch F1/F2 (auditoria adversarial)

> Evidência verificável, medida por execução. Ambiente desta sessão:
> **Python 3.14, sem deps pesadas** (langgraph/pandas/ccxt não instalam) →
> suíte completa via CI; localmente roda o **subconjunto do kernel** (F1/F2).

## Comandos e resultados (local)

```
# subconjunto do kernel (fee/recovery/ledger/incidentes/restore)
pytest -q tests/test_fee_*.py tests/test_recovery_settlement.py \
          tests/test_incidentes_risco.py tests/test_valuation_fingerprint.py \
          tests/test_contabilidade_multimoeda.py tests/test_crash_restart.py \
          tests/test_compensacao_vinculada.py tests/test_desk_backup.py \
          tests/test_ativo_descoberto.py tests/test_valoracao_exactly_once.py
  => 243 passed, 1 failed*   (após B2/B3; era 200 antes)

*a única falha (test_config_permissions_are_restored_tight) é pré-existente e
 específica de permissão de arquivo no Windows — confirmada no HEAD limpo,
 alheia ao patch.

ruff check .   => All checks passed
```

## Por blocker

### B8 — restore revalida dimensões (`7884687`)
- Vermelho: `test_b8_restore_recusa_documento_cross_unit_sem_metadata` — doc
  `FEES:BNB +2 / CASH:USDT -2` sem metadata passava pelo restore.
- Correção: `Ledger.from_transactions` chama `tx.validar_dimensoes(currency)`.
- Mutação: remover a chamada ⇒ teste morre.

### B7 — fingerprint canônico (`5563dcd`)
- Vermelho: `test_b7a..e` — `300 != 300.0`, offsets de tz divergiam, faltavam
  functional_currency e policy_version.
- Correção: `_fp_decimal` (normalize+format 'f'), `_fp_instante` (UTC), moeda
  normalizada, novos campos na impressão; `valuation_policy_version` durável
  (export/restore) para o recovery recomputar com a política de origem.
- Mutação: 5 mutantes (decimal/instante/moeda/functional/policy) ⇒ todos morrem.

### B1/B9 — identidade canônica na adoção (`b199ee1`)
- Vermelho: `test_the_same_content_under_another_id_is_adopted` fortalecido
  exige `ledger.valuation_id == incident.valuation_id ==
  resolution_refs.valuation_id`.
- Correção: na adoção, id canônico = o do LIVRO; id trazido vira
  `resolution_refs['requested_valuation_id']` (alias).
- Mutação: "não adotar id do livro" ⇒ teste morre.

### B2/B3 — valoração operacional com lastro e soma exata (`7f5b714`)
- Vermelho: `test_b3_lista_vazia_recusa`, `test_b3_duplicado_recusa`,
  `test_b2_componente_maior_que_quantidade_recusa`, `test_b2_soma_diferente_recusa`,
  `test_b3_legacy_sem_lastro_explicito` (5 falhas contra o comportamento antigo,
  que aceitava lista vazia e não conferia a soma).
- Correção: `fee_valuation` exige `fee_component_ids` não-vazia, sem duplicados,
  todos na moeda e não-valorados, e `soma(componentes) == quantidade` exata;
  `fee_valuation_sem_lastro` é a porta de compat explícita; `_valorar` é o núcleo
  comum. 14 call-sites antigos migrados para a compat.
- Mutação: 6 mutantes (não-vazia, duplicado, soma×2, existência×2) ⇒ todos morrem
  (runner determinístico, revert em memória, sem `git checkout`).

### B4 — valorar_taxa congela quantidade e batch (`a4e9490`)
- Vermelho: `test_b4_fee_nova_apos_proposta_nao_infla_a_valoracao` (valoração
  inflava de 2 para 3 BNB) e `test_b4_componente_congelado_valorado_fora_derruba_lastro`
  (adotava componente fora do batch).
- Correção: a proposta fixa `quantidade` e `fee_component_ids`; `aplicar_valoracao`
  usa o congelado (fallback compat). `_batch_para_valorar` seleciona sem estourar.
- Mutação: 2 mutantes (quantidade / batch não congelados) ⇒ ambos morrem.
- Kernel (recovery/valoração/fee): 175 passed.

## Segurança
- `LIVE.habilitado=false`, `operacao_real.estado=BLOQUEADA` no
  `/v1/status/operacao` de produção; PAPER ativo.
- RLS ativo nas 6 tabelas do Supabase (chave publicável recebe `42501`).
- Nenhum segredo versionado (`.env.example` só com nomes).

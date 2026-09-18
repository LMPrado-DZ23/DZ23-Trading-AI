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
          tests/test_compensacao_vinculada.py tests/test_desk_backup.py
  => 200 passed, 1 failed*

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

## Segurança
- `LIVE.habilitado=false`, `operacao_real.estado=BLOQUEADA` no
  `/v1/status/operacao` de produção; PAPER ativo.
- RLS ativo nas 6 tabelas do Supabase (chave publicável recebe `42501`).
- Nenhum segredo versionado (`.env.example` só com nomes).

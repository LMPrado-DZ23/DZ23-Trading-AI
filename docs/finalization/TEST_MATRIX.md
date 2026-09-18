# TEST_MATRIX — patch F1/F2

Arquivo de testes do patch: `tests/test_fee_patch_f1f2.py` (+ reforço em
`tests/test_valuation_fingerprint.py`).

## Cobertura por blocker

| Blocker | Teste (vermelho→verde) | Mutante que deve morrer | Estado |
|---|---|---|---|
| B8 | `test_b8_restore_recusa_documento_cross_unit_sem_metadata` | restore sem `validar_dimensoes` | ✅ morre |
| B8 | `test_b8_restore_aceita_documento_legitimo` / `_com_metadata` | — (regressão) | ✅ |
| B7a | `test_b7a_decimal_equivalente_mesma_impressao` | decimal sem normalizar | ✅ morre |
| B7b | `test_b7b_mesmo_instante_offsets_diferentes` | instante sem UTC | ✅ morre |
| B7c | `test_b7c_functional_currency_entra_na_impressao` | functional_currency fora | ✅ morre |
| B7d | `test_b7d_policy_version_entra_na_impressao` | policy_version fora | ✅ morre |
| B7e | `test_b7e_moeda_normalizada` | moeda sem normalizar | ✅ morre |
| B7 | `test_b7_id_continua_fora_da_impressao` | (invariante preservada) | ✅ |
| B1/B9 | `TestIdNaoEhConteudo::test_the_same_content_under_another_id_is_adopted` | não adotar id do livro | ✅ morre |

## Matriz de mutação da auditoria (itens A–J do patch)

| # | Mutação pedida | Coberta por | Estado |
|---|---|---|---|
| A | retry adota val-A, incidente grava val-B | B1/B9 | ✅ fechado |
| B | componente 2 cobre quantidade 1 | B2 | 🔴 aberto |
| C | ID duplicado aceito | B3 | 🔴 aberto |
| D | lista vazia cria valoração | B3 | 🔴 aberto |
| E | incidente F1 consome componente F2 | B4 | 🔴 aberto |
| F | fee nova entra no batch após proposta | B4 | 🔴 aberto |
| G | memo tratado como fonte de identidade | B5 | 🔴 aberto |
| H | duas instâncias usam o mesmo componente | B6 | 🔴 aberto |
| I | functional currency sai da impressão | B7c | ✅ fechado |
| J | restore não chama validar_dimensoes | B8 | ✅ fechado |

## Como rodar a matriz de mutação
Script determinístico (commit o fix ANTES de mutar — o `git checkout` de
revert descarta trabalho não-commitado):
`scratchpad/mutmatrix.py` desta sessão; cada entrada = (arquivo, trecho, mutação, teste).
Regra: todo mutante não-equivalente tem de matar seu teste.

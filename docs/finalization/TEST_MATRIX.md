# TEST_MATRIX — patch F1/F2

Arquivos de teste do patch: `tests/test_fee_patch_f1f2.py`,
`tests/test_fee_patch_b4.py` (+ reforço em `tests/test_valuation_fingerprint.py`).

| Blocker | Teste (vermelho→verde) | Mutante que deve morrer | Estado |
|---|---|---|---|
| B4 (F) | `test_b4_fee_nova_apos_proposta_nao_infla_a_valoracao` | quantidade não congelada | ✅ morre |
| B4 (E) | `test_b4_componente_congelado_valorado_fora_derruba_lastro` | batch não congelado | ✅ morre |

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
| B3 | `test_b3_lista_vazia_recusa` | checagem não-vazia removida | ✅ morre |
| B3 | `test_b3_duplicado_recusa` | checagem de duplicado removida | ✅ morre |
| B3 | `test_b3_inexistente_recusa` / `_cross_currency_recusa` | checagem de existência removida | ✅ morre |
| B2 | `test_b2_componente_maior_que_quantidade_recusa` | checagem de soma removida | ✅ morre |
| B2 | `test_b2_soma_diferente_recusa` | checagem de soma removida (parcial) | ✅ morre |
| B2 | `test_b2_soma_exata_aceita_e_valora_todos` | — (regressão: 0.4+0.6==1) | ✅ |
| B3 | `test_b3_legacy_sem_lastro_explicito` | — (compat existe e é separada) | ✅ |

## Matriz de mutação da auditoria (itens A–J do patch)

| # | Mutação pedida | Coberta por | Estado |
|---|---|---|---|
| A | retry adota val-A, incidente grava val-B | B1/B9 | ✅ fechado |
| B | componente 2 cobre quantidade 1 | B2 | ✅ fechado |
| C | ID duplicado aceito | B3 | ✅ fechado |
| D | lista vazia cria valoração | B3 | ✅ fechado |
| E | incidente F1 consome componente F2 | B4 | ✅ fechado |
| F | fee nova entra no batch após proposta | B4 | ✅ fechado |
| G | memo tratado como fonte de identidade | B5 | 🔴 aberto |
| H | duas instâncias usam o mesmo componente | B6 | 🔴 aberto |
| I | functional currency sai da impressão | B7c | ✅ fechado |
| J | restore não chama validar_dimensoes | B8 | ✅ fechado |

## Como rodar a matriz de mutação
Script determinístico (commit o fix ANTES de mutar — o `git checkout` de
revert descarta trabalho não-commitado):
`scratchpad/mutmatrix.py` desta sessão; cada entrada = (arquivo, trecho, mutação, teste).
Regra: todo mutante não-equivalente tem de matar seu teste.

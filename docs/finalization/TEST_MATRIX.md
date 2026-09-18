# TEST_MATRIX — patch F1/F2

Arquivos de teste do patch: `tests/test_fee_patch_f1f2.py`,
`tests/test_fee_patch_b4.py` (+ reforço em `tests/test_valuation_fingerprint.py`).

| Blocker | Teste (vermelho→verde) | Mutante que deve morrer | Estado |
|---|---|---|---|
| B4 (F) | `test_b4_fee_nova_apos_proposta_nao_infla_a_valoracao` | quantidade não congelada | ✅ morre |
| B4 (E) | `test_b4_componente_congelado_valorado_fora_derruba_lastro` | batch não congelado | ✅ morre |
| B5 (G) | `test_b5_identidade_sobrevive_memo_editado` | leitura ignora estrutura / restore ou export descartam | ✅ morrem (3) |
| B6 (H) | `TestConcorrenciaMultiprocessoB6` + `TestExactlyOnce`/`TestSerializacaoForcada` | (serialização intra-processo; multiproc BLOCKED_BY_ARCH) | ✅ |

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
| G | memo tratado como fonte de identidade | B5 | ✅ fechado |
| H | duas instâncias usam o mesmo componente | B6 | ✅ intra-proc / multiproc BLOCKED_BY_ARCH |
| I | functional currency sai da impressão | B7c | ✅ fechado |
| J | restore não chama validar_dimensoes | B8 | ✅ fechado |

## Como rodar a matriz de mutação
Script determinístico (commit o fix ANTES de mutar — o `git checkout` de
revert descarta trabalho não-commitado):
`scratchpad/mutmatrix.py` desta sessão; cada entrada = (arquivo, trecho, mutação, teste).
Regra: todo mutante não-equivalente tem de matar seu teste.

## F3 — valoração por componente (`tests/test_fee_patch_f3.py`)

| Teste | Prova | Mutante que morre |
|---|---|---|
| A | 1 BNB @300 + 1 BNB @600 = 900 | cotação única a todas as ocorrências |
| B | dois componentes do mesmo fill, rates diferentes | fill_id como identidade |
| C | BNB e SOL no mesmo fill são independentes | — |
| D | componente novo depois da proposta fica fora | (freeze estrutural + B4) |
| E | retry idêntico não duplica | — |
| F | retry divergente gera RecoveryConflict | conflito divergente adotado |
| G | restart preserva cada valoração | (persistência + B5) |
| H | incidente não resolve com componente pendente | resolve parcial |
| I | identidade é fee_component_id, não fill_id | fill_id como identidade |
| J | cotação agregada em várias ocorrências falha | agregado permitido |
| K | cada componente registra source e valued_at | ignora source / valued_at |

Mutantes mortos (6): fill_id-como-identidade, agregado-permitido, resolve-parcial,
adota-divergente, ignora-source, ignora-valued_at. Freeze (D) e restart (G) são
cobertos estruturalmente e pelas matrizes de B4 e B5.

## F4 — liquidação do funding (`tests/test_fee_patch_f4.py`)

| Teste | Prova | Mutante que morre |
|---|---|---|
| F4a | liquidar zera o passivo | sinal do passivo invertido |
| F4b | liquidação por componente | — |
| F4c | exatamente uma vez | idempotência removida |
| F4d | componente sem valoração não liquida | valoração obrigatória removida |
| F4e | ativo descoberto bloqueia a liquidação | gate de ativo removido |
| F4f | restart preserva a liquidação | (persistência) |
| F4g | lado nativo intacto, funcional fecha | — |

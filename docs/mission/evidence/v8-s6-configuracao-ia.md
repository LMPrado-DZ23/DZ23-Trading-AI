# V8 §6 — CONFIGURAÇÃO COMPLETA DE INTELIGÊNCIA ARTIFICIAL (evidências)

**Requisito (Prompt Master V8, §6):** interface visual para cadastrar
OpenAI, Anthropic/Claude, Google Gemini, DeepSeek, Groq, Mistral,
OpenRouter e "outros provedores compatíveis mediante adaptadores";
cadastrar E remover credenciais; descobrir modelos quando a API permitir;
escolher provedor/modelo por função; testar autenticação; verificar
latência, limites e erros; definir fallback; configurar orçamento;
visualizar consumo e custo estimado; proteger segredos. Modelos distintos
por custo/especialização; IA econômica para tarefas simples; a IA NÃO
substitui os controles determinísticos de risco. "Realizar testes com APIs
reais somente quando houver credenciais autorizadas. Não inserir chaves
fictícias para declarar homologação." Ollama não precisa ser instalado.

**Commits:** `8d5f522` (§6a) · `41bb168` (§6b) · `13276e2` (§6c) ·
`3756db0` (§6d) · `e592c13` (SS-1 backend Smart Setup) · `74d669f` (SS-2
painel) · `c38473e` (SS-3 matriz) — branch
`fix/instalador-atualizacoes-windows`.

## Defeito real corrigido no caminho (§6a): a IA nunca funcionou em produção

Os DOIS caminhos de consulta de IA do service (IA de contexto da mesa e
revisor do diário manual) construíam o cliente passando o dict de
configuração como `model` e chamavam `cliente.complete()`/`invoke()` —
métodos que NÃO existem no `BaseLLMClient`. Toda consulta degenerava em
`AttributeError`: **a IA de contexto nunca funcionou em produção**.

- **RED:** guarda estático de regressão
  (`test_app_nao_chama_complete_nem_invoke_no_cliente`) falhou contra os 3
  padrões do defeito antes do fix.
- **GREEN:** `_consultar` e `_revisor_de_ia` viraram wrappers finos sobre
  `consultar_ia()` (caminho correto `get_llm(...).invoke()`); o registro de
  latência passou a gravar o provedor que DE FATO respondeu.
- Prova: 14 testes em `tests/test_ia_provedores.py` (guarda + 13 unidade).

## Requisito → implementação → evidência

| Requisito (§6) | Implementação | Evidência |
|---|---|---|
| Interface p/ os 8 + adaptador + Ollama | Card "Provedores de IA" no painel; `CATALOGO` com 9 entradas (`openai`, `anthropic`, `google`, `deepseek`, `groq`, `mistral`, `openrouter`, `openai_compatible`, `ollama`) em `service/api/ia_provedores.py` | `test_lista_traz_os_nove_e_estado_das_chaves`; `test_primeira_classe_do_prompt_v8`; `test_espelha_free_providers_sem_drift`; `test_ollama_dispensa_chave` |
| Cadastrar E remover credenciais | `POST /v1/config` grava; `{"remover": [...]}` remove do arquivo — e a resposta DIZ se o ambiente do sistema ainda fornece o valor (`ainda_no_ambiente`) | `TestRemoverCredenciais` em `tests/test_ia_provedores.py` |
| Testar autenticação | `POST /v1/ai/testar` → sonda real no endpoint de modelos; sem chave NÃO sai à rede | `test_testar_sem_chave_e_honesto_sem_rede`; `test_sem_chave_nao_sai_rede` |
| Latência, limites e erros | Relatório com `latencia_ms`, `limites` (ex.: retry-after) e estados honestos e SANITIZADOS: `ok`, `sem_chave`, `sem_url`, `endereco_recusado`, `sem_conexao`, `tempo_esgotado`, `chave_rejeitada`, `resposta_ilegivel`, `erro` — mensagem do provedor nunca vaza | `test_401_vira_chave_rejeitada`; `test_429_traz_limites_e_nao_vaza_corpo`; `test_tempo_esgotado`; `test_endereco_nao_confiavel_e_recusado_antes_da_rede` |
| Descobrir modelos quando a API permitir | `GET /v1/ai/modelos` → descoberta real; sem chave cai no catálogo com `fonte` declarada ("api"/"catalogo") | `test_modelos_com_chave_usa_descoberta_real`; `test_modelos_sem_chave_com_fonte_catalogo`; `test_descobrir_sem_chave_cai_no_catalogo_com_fonte_honesta` |
| Escolher provedor/modelo por função | `TRADINGAGENTS_LLM_PROVIDER` + `TRADINGAGENTS_QUICK_THINK_LLM` / `TRADINGAGENTS_DEEP_THINK_LLM`; smart_setup classifica modelos por segmento do nome (sem lista fixa) | `tests/test_smart_setup.py::TestClassificarModelo`; `TestSugestaoEAplicarPerfis` |
| Fallback entre provedores | Fila de candidatos com `TRADINGAGENTS_LLM_FALLBACK`; provedor sem chave é pulado "sem mentir" | `test_fallback_ordem_dedupe_e_modelo_padrao`; `test_fallback_quando_o_principal_falha`; `test_fallback_sem_chave_e_pulado_sem_mentir`; `test_falha_total_agrega_tentativas_sem_vazar_segredo` |
| Orçamento de consumo | `TRADINGAGENTS_IA_ORCAMENTO_USD` + bloqueio opcional por CÓDIGO (`TRADINGAGENTS_IA_ORCAMENTO_BLOQUEIO`); padrão permitido — orçamento NUNCA desativa a verificação de contexto | `service/api/smart_setup.py::ia_permitida`; testes de orçamento em `tests/test_smart_setup.py` |
| Consumo e custo estimado | `service/api/ia_uso.py` (registro por inquilino/dia/provedor) + tabela de referência `tradingagents/llm_clients/precos.py`; custo PARCIAL é declarado quando falta preço | `test_resposta_sem_usage_nao_inventa_tokens`; suíte de uso/precos |
| Proteger segredos | Credenciais cifradas em repouso (DPAPI — auditoria SEC-1, commit `a0997df`); painel recebe só `hint` mascarado; sonda/testes nunca ecoam valor; janela de env sob lock fechada na construção | `test_segredo_nunca_no_catalogo`; `test_falha_total_agrega_tentativas_sem_vazar_segredo`; `tests/test_auditoria_sec1_segredos_em_repouso.py`; resposta de `/v1/ai/*` verificada sem eco da chave |
| Modelos distintos por custo/especialização (10 traders + Maestro) | Rápido vs profundo por função; classificação por segmento (mini/flash/haiku/8b → rápido); roteamento por custo para tarefas simples | `tests/test_smart_setup.py::TestRoteamentoPorCusto`; `TestClassificarModelo` |
| IA NÃO substitui controles determinísticos | Perfis do Smart Setup não tocam nenhuma trava de risco (HARD_CAPS, gestor de risco, limites); orçamento é informativo por padrão; "bloqueio" só afeta chamadas controladas pelo DZ23 | `tests/test_smart_setup.py` (bloqueio); nota de honestidade no payload de `/v1/ai/uso` |
| Testes com API real só com credencial autorizada; sem chave fictícia | Nenhum teste usa chave real ou fictícia: sondas/transportes injetados; ambiente do dono é limpo nos testes (`_limpar_ambiente_ia`); sem credencial não há saída de rede | `tests/test_ia_provedores.py` (49 testes offline); ver "Limitações declaradas" |
| Ollama não precisa ser instalado | Catálogo trata Ollama como provedor local opcional (sem chave); NADA é instalado | `test_ollama_dispensa_chave`; nenhuma dependência nova |

## Prova na interface (navegador, instância isolada — não é o exe do dono)

Instância temporária em `127.0.0.1:8786` com `DATA_DIR` temporário e
ambiente SEM nenhuma chave de IA (verificado por `env -u` na inicialização;
o processo do dono em `:8000` nunca foi tocado):

- Card "Provedores de IA" com campos de chave, botões Salvar/Testar/Buscar
  modelos/Remover; hint mascarado (`•••1234`) — nenhum valor real exibido.
- Toast do Smart Setup re-verificado na versão final do código:
  `"Perfil pronto: preencheu DESK_CONTROLE, MAX_DEBATE_ROUNDS,
  MAX_RISK_ROUNDS · 1 campo(s) seu(s) preservado(s)"` (contagem honesta de
  dict — bug corrigido, ver doc do Smart Setup).
- Zero erros de console; `node --check` nos scripts (guarda estático).

## Provas frescas (2026-10-14)

| Evidência | Resultado |
|---|---|
| `tests/test_ia_provedores.py` | 49 passed (offline, sem chave real) |
| `tests/test_ia_painel_static.py` | 27 passed (inclui `node --check`) |
| Lista IA (4 arquivos juntos) | 123 testes coletados |
| Suíte completa (gate SS-2, árvore final do §6+SS) | **2841 passed / 5 skipped / 71 subtests, exit 0** |
| Suíte completa final (com a matriz SS-3 incluída) | ver `v8-smart-setup.md` — mesma corrida |
| Ruff | All checks passed |

## Limitações declaradas (honestidade)

1. **NENHUMA chamada de API real foi executada.** O dono não autorizou
   (ainda) testes com as credenciais dele; o requisito do §6 diz "somente
   quando houver credenciais autorizadas". Está provado o comportamento
   offline/honesto (sem chave não sai à rede; falhas classificadas por
   estado), não a autenticação real contra OpenAI/Groq/etc.
2. **Homologação no exe instalado do painel de IA ainda não foi feita.**
   A verificação visual acima é em navegador contra instância temporária
   isolada (código real, porta própria); a homologação Windows/PWA dos
   fluxos completos está prevista na etapa TD-5/SS-5 (nada no app do dono
   foi alterado).
3. **Ollama é opcional e NÃO foi instalado** (conforme o prompt); só existe
   como entrada de catálogo para quem já o roda localmente.
4. **`_ULTIMO_TESTE` (base do diagnóstico) é observação de sessão** — em
   memória, por desenho; reiniciar o app zera o "autenticado" até novo
   teste. Documentado no código e coberto pela matriz SS-3.

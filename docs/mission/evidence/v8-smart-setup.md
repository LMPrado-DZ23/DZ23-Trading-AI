# V8 complementar — DZ23 Smart Setup (Configuração Inteligente): evidências

**Pedido do dono (verbatim, 2026-10-13):** "o aplicativo deve sair da
instalação praticamente pronto para uso, sem obrigar o proprietário a
preencher manualmente opções técnicas que podem ser detectadas ou
configuradas com segurança." Restrição central: "Não habilitar LIVE, não
contratar serviços pagos e não escolher automaticamente um provedor que
gere cobranças sem consentimento do proprietário." E o critério de
conclusão: "Entregue a interface completa, backend, testes e homologação
Windows/PWA. Não marque como concluído somente porque existem valores
padrão no formulário."

**Commits:** `e592c13` (SS-1 backend) · `74d669f` (SS-2 painel) ·
`c38473e` (SS-3 matriz de cenários) — branch
`fix/instalador-atualizacoes-windows`.

## Os 14 requisitos → implementação → evidência

| # | Requisito | Implementação | Evidência |
|---|---|---|---|
| 1 | Pré-configuração automática (idioma, PAPER, interface, intervalos, preferências seguras) | `PADROES_SEGUROS` + `aplicar()` SÓ em campos vazios: idioma `Portuguese` (nome canônico da CLI **e** valor exibível no select), `TRADINGAGENTS_DESK_CONTROLE=0` (mesa desligada = política conservadora), rodadas de debate/risco enxutas | `tests/test_smart_setup.py::TestSugestaoEAplicarPerfis`; pin `test_idioma_do_smart_setup_cabe_no_select_manual` |
| 2 | Descoberta de provedores já configurados, sem expor/duplicar credenciais | `provedores_configurados(cfg)` devolve só IDs; painel mostra "chave configurada" — nunca o valor | `tests/test_smart_setup.py::TestProvedoresConfigurados`; `test_lista_traz_os_nove_e_estado_das_chaves` (chave não sai em `r.text`) |
| 3 | Modelos realmente disponíveis, sem nomes fixos desatualizados | `classificar_modelo` por segmento do nome; descoberta real via API quando há chave; catálogo só como fallback com fonte DECLARADA | `TestClassificarModelo`; `test_modelos_com_chave_usa_descoberta_real`; sugestão com nota "modelos escolhidos da lista real do provedor" |
| 4 | Seleção automática de rápido/profundo/fallback por capacidade/latência/custo/saúde | `escolher_provedor` (só entre configurados; perfil define a chave de ordenação: custo → latência → autenticação) + `sugestao()` | `TestRoteamentoPorCusto`; matriz SS-3: troca de provedor reordena fallback |
| 5 | Roteamento inteligente — econômico para tarefa simples | Classificação rápido/profundo + perfis econômico/equilibrado/avançado mudam a preferência (nunca criam cobrança) | `TestRoteamentoPorCusto`; `PERFIS[...][preferencia]` |
| 6 | Perfis Pronto/Econômico/Equilibrado/Avançado com personalização posterior | `PERFIS` + select no painel; "Perfis preenchem apenas campos vazios — suas escolhas nunca são sobrescritas" | `tests/test_ia_painel_static.py::TestPainelSmartSetup` (12 testes); matriz SS-3 `test_perfil_nao_sobrescreve_preferencia_do_dono` |
| 7 | Orçamento com limites efetivos, alertas e bloqueio configurável | `ia_permitida()` + `TRADINGAGENTS_IA_ORCAMENTO_USD` (informativo por padrão) + bloqueio opt-in; NUNCA desativa a verificação de contexto da mesa | testes de orçamento em `tests/test_smart_setup.py`; nota no payload de `/v1/ai/uso` |
| 8 | Diagnóstico visual: configurado → conectado → autenticado → operacional | `diagnostico()` deriva CADA estado de evidência local (config, último teste, registro de uso) — nunca de "ping" imaginário; 4 chips no painel | `test_chave_rejeitada_distingue_conectado_de_autenticado`; `test_autenticado_so_vira_operacional_com_uso_registrado`; `test_sem_conexao_fica_explicito_no_diagnostico` |
| 9 | Assistente de primeira execução só pede o necessário | `estado()` → `pendencias` por regra real (provedor, modelo rápido, fallback multi-provedor, idioma); sem percentual inventado | `TestEstado`; SS-3 `test_do_zero_ao_pronto_sem_criar_chave_nem_servico_pago` |
| 10 | Credenciais protegidas; nunca fictícias ou compartilhadas | Cifra em repouso DPAPI (SEC-1) + hint mascarado; `aplicar()` NUNCA cria/altera/remove chave; testes não usam chave nenhuma | `TestRemoverCredenciais`; docstring/contrato de `aplicar` (R10); `_limpar_ambiente_ia` |
| 11 | Configuração inicial idempotente (atualizar não sobrescreve) | `aplicar` só preenche VAZIOS; devolve `aplicados`/`mantidos` explícitos | SS-3 `test_aplicar_duas_vezes_e_estavel` (2ª vez: `aplicados == {}`) |
| 12 | Preservar configuração manual avançada | A página Configuração continua com TODOS os campos manuais; o card do assistente é adicional, não substituto | `tests/test_ia_painel_static.py::TestConfigKeysECampos`; SS-5 (Modo Simples/Avançado) preserva a mesma regra |
| 13 | Corrigir sobreposição do gráfico de fundo + responsividade | `.shell{position:relative;z-index:1}` sobre `.gate-cena{position:fixed;z-index:0}` — conteúdo clicável acima do gráfico | verificado por `elementFromPoint` + capturas em `evidence/v8-ss2/`; regressão pinada em `test_ia_painel_static.py` |
| 14 | Testar: 1ª instalação, migração, sem internet, falhas de API, troca de provedor, recuperação | Matriz SS-3: 12 testes HTTP em instância isolada, um por cenário (abaixo) | `tests/test_smart_setup_homologacao.py` — 12 passed |

## Bugs REAIS achados na homologação no navegador (não por teste estático)

O valor da homologação visual — dois defeitos que os testes estáticos não
pegavam, corrigidos com pins de regressão:

1. **Toast mentiroso** ("nada para preencher" mesmo tendo preenchido):
   a rota devolve `aplicados` como DICT chave→valor e `.length` de dict é
   `undefined` (falsy) — o toast caía sempre no ramo "nada para
   preencher". Fix: `Object.entries(d.aplicados||{})`; pin
   `test_aplicar_conta_campos_como_objeto`. Re-verificado ao vivo na versão
   final: `"Perfil pronto: preencheu DESK_CONTROLE, MAX_DEBATE_ROUNDS,
   MAX_RISK_ROUNDS · 1 campo(s) seu(s) preservado(s)"`.
2. **Campo de idioma vazio após aplicar**: `PADROES_SEGUROS` gravava
   `"Português (Brasil)"`, valor que o `<select>` do painel NÃO exibe — o
   campo parecia não salvo. Fix: nome canônico `"Portuguese"` (mesmo value
   space da CLI); pin `test_idioma_do_smart_setup_cabe_no_select_manual` que
   exige que TODO padrão seja exibível nos controles manuais.

## Matriz R14 (SS-3) — cenários reais, instância isolada, zero rede

`tests/test_smart_setup_homologacao.py` (12 testes, nível HTTP):

| Cenário | O que prova |
|---|---|
| Primeira instalação | do zero ao perfil aplicado SEM criar chave e SEM escolher provedor; chave colada destranca a sugestão |
| Idempotência | aplicar 2× é estável (`aplicados == {}` na 2ª); ruff/suite verdes |
| Migração | config legado (só chaves antigas) abre, preserva o que tinha e completa o que falta |
| Sem internet | `/v1/setup/estado` é PURO (sonda bombeada = falha do teste); `sem_conexao` explícito no diagnóstico |
| Falha de API | `chave_rejeitada` (401) → `conectado=True` / `autenticado=False` — estados distintos, nunca confundidos |
| Autenticado → operacional | só vira operacional com chamada REALMENTE registrada (registro de uso) |
| Troca de provedor | fallback reordena, chaves preservadas |
| Recuperação | restart preserva config e zera observação de sessão; `config.json` corrompido não derruba nem trava a reconfiguração |

## Prova na interface (capturas)

Instância temporária `127.0.0.1:8786` (`DATA_DIR` temporário, ambiente sem
chaves de IA; app do dono em `:8000` intocado):

- `evidence/v8-ss2/ss2-card-configuracao-inteligente.png` — card completo
  (perfil, bloqueio, estado, notas, diagnóstico com chips 4-estados, badge
  PAPER + "Operação real BLOQUEADA").
- `evidence/v8-ss2/ss2-card-topo-final.png` — estado final do código após
  os 2 fixes (idioma "Portuguese" visível; toast honesto; nenhuma chave no
  ambiente → tudo "sem chave", com honestidade).
- `evidence/v8-ss2/ss2-toast-final.png` — mesmo estado, rolagem no
  diagnóstico (OpenAI/Anthropic/Google/DeepSeek "sem chave", 4 chips).

## Provas frescas (2026-10-14)

| Evidência | Resultado |
|---|---|
| `tests/test_smart_setup.py` | 35 passed |
| `tests/test_smart_setup_homologacao.py` | 12 passed (12/12 na primeira corrida) |
| `tests/test_ia_painel_static.py` | 27 passed |
| Suíte completa (gate SS-2, com SS-1/SS-2) | **2841 passed / 5 skipped / 71 subtests, exit 0** |
| Suíte completa final (com SS-3) | **2853 passed / 5 skipped / 71 subtests, exit 0** (294,28 s) |
| Ruff (arquivos tocados) | All checks passed |

## Limitações declaradas (honestidade)

1. **A homologação Windows/PWA exigida no pedido NÃO está concluída.**
   O que existe: navegador real contra instância temporária isolada com o
   código final (acima) e matriz HTTP automatizada. Falta rodar os fluxos
   no exe instalado — depende do Modo Simples/PLAY (SS-5) e do aceite do
   dashboard (TD-5), que farão a homologação de ponta a ponta no aplicativo
   instalado. Nada aqui substitui isso.
2. **Nenhuma chamada de API real, nenhuma chave fictícia.** Os testes de
   provedor usam sondas injetadas; sem credencial não há saída de rede.
3. **Capturas são de instância temporária** (porta 8786), não do bundle;
   as do exe entram na homologação do TD-5.
4. **Requisito 1 é deliberadamente parcial**: o perfil pré-configura
   idioma/rodadas/controle — as travas de risco PAPER permanecem as de
   fábrica; o Smart Setup NUNCA afrouxa trava (nem pretende).
5. **`aplicados` da 2ª aplicação é vazio por desenho** (idempotência);
   o painel diz isso com o toast "nada para preencher — nenhuma escolha
   sua foi alterada" — verdadeiro, só quando é verdade.

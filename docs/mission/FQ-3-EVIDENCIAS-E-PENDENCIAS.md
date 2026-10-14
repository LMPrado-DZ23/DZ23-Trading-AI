# FQ-3 — Evidências, testes (§12), pendências (§13.10) e pacote do PR revisável

**Missão:** "DZ23 Trading AI — Missão de Evolução Quantitativa: Freqtrade + FreqAI + OpenBB — Integração Seletiva e Validada" (§11–§14).
**Data:** 2026-10-14 · **Branch:** `fix/instalador-atualizacoes-windows` · **Estado obrigatório:** LIVE BLOQUEADO (inalterado — nenhuma melhoria desta missão toca execução real).

## 1. O que foi entregue (FQ-0 → FQ-3)

| Fase | Entrega | Commit | Prova |
| :--- | :--- | :--- | :--- |
| FQ-0 | Matriz de paridade §2 (16 componentes, RECURSO×EXISTENTE×REFERÊNCIA×LACUNA×IMPACTO×CUSTO×DECISÃO) + sondagens PyPI + spike freqtrade isolado + inventário das docs estáveis | `175dc0f`, incorporado em `44c4ad7`/`a882297` | `docs/mission/FQ-0-MATRIZ-PARIDADE.md`, `docs/mission/evidence/fq0-*.md` |
| FQ-1 | 4 melhorias priorizadas §14 (liquidez no backtest; estabilidade na formação; registro §3; detector de lookahead) — TDD RED→GREEN, 28 testes novos | `44c4ad7` (+ lint `1955bfe`) | 5 arquivos de teste `tests/test_fq_*.py` |
| FQ-2 | Documentação §3 (registro de experimentos), protocolo §4, licenças §10 + impacto §13.5 | `a882297` | `docs/mission/FQ-2-*.md` (3 docs) |
| FQ-3 | Este pacote: testes §12, suíte completa, pendências §13.10, pacote do PR | (este commit) | §2–§5 abaixo |

**Decisão de escopo (item D):** o endpoint determinístico do Copiloto (§7) foi **ADIADO** por §14 ("implementar apenas as melhorias comprovadamente necessárias; priorizar as que evitam perdas/falsas aprovações") — as 8 respostas do dono já existem dispersas e não há caso registrado de decisão errada por dispersão. Registrado na matriz §3 item 5 e na tabela de pendências abaixo.

## 2. Testes (§12) — tipos e provas

| Tipo | O que cobre no FQ | Evidência |
| :--- | :--- | :--- |
| **Unidade** | Liquidez (capacidade da barra, fill limitado/rejeitado, volume negativo, neutralidade na fixture legada); métricas de formação (expectativa, fator de lucro, drawdown, metades, regimes); registro (assinatura muda com cada campo novo; hash reprodutível); lookahead (fábrica causal vs vazada com evidência localizada); causalidade de indicadores (janela/prefixo estáveis). | `tests/test_fq_liquidez_backtest.py` (6), `tests/test_fq_formacao_metricas.py` (8), `tests/test_fq_laboratorio_registro.py` (5), `tests/test_fq_lookahead.py` (5), `tests/test_fq_indicadores_causais.py` (4) — **28 testes** (contagem refeita por `--collect-only`; a primeira redação dizia 29) |
| **Integração** | Fluxo completo da Escola com o novo gate: prova final IC-aprovada mas temporalmente instável ⇒ estado fica PROVA_FINAL com motivo "formação BLOQUEADA" e `base_das_metricas="prova_final"`; evidência nova cura o bloqueio ⇒ FORMADA. | `test_fq_formacao_metricas.py` (2 integrações com armazém) |
| **Adversarial** | Barra sem liquidez não preenche (não vira "operação fantasma"); volume negativo ⇒ ValueError; corte inválido ⇒ ValueError; fábrica que espia o máximo global é **pega** com índice/corte; amostra curta ⇒ aviso explícito de que nenhuma evidência foi produzida (não "passou"). | mesmos arquivos (RED comprovado antes do fix em todos) |
| **Regressão** | Suíte legada das áreas tocadas intacta: `test_desk_laboratorio`, `test_desk_escola`, `test_desk_piloto`, `test_research`, `test_salas`, `test_sala_trader`, `test_sala_trilha`, `test_desk_maestro`, `test_evolucao_autonoma`, `test_desk_validacao`, `test_desk_multiagente` — **242 passed** na bateria combinada (pré-commit). | execução local registrada nesta rodada |

**§12 — "Mocks não equivalem a validação com serviços reais":** declarado honestamente que **nenhum serviço real está envolvido no FQ** — todas as mudanças são quantitativas offline (laboratório/backtest/Escola), sem chamada de rede, sem corretora, sem credencial. O único contato externo foi o **spike do freqtrade** (ferramenta separada, sem conta/credencial — `fq0-probe-dependencias.md` §3.2). Não há mock substituindo validação de serviço real porque não há integração de serviço real nesta missão (decisões: ADIAR freqtrade-confronto/OpenBB; REJEITAR dependências).

## 3. Suíte completa (evidência local)

- Comando: `.venv/Scripts/python.exe -m pytest tests/ -q` na árvore de `1955bfe` (árvore final desta missão).
- Resultado: **`3107 passed / 7 skipped / 71 subtests passed in 291,99 s` (exit 0)**.
- Baseline anterior (MO-3, sem e2e): `3079 passed / 4 skipped / 71 subtests` — os 28 testes novos do FQ entram nessa contagem (**+28 = 3107 ✓, todos passaram**); os 3 skips adicionais são os e2e sem Playwright no venv padrão (esta rodada inclui `tests/e2e/`; a baseline MO-3 era sem e2e) — total de 7 skips ambientais, todos declarados na saída.
- Ruff: **`All checks passed!`** nos 10 arquivos novos/alterados do FQ (2 achados do primeiro passe — B905/C401 — corrigidos em `1955bfe`, não escondidos).

## 4. CI (gates de empacotamento — o produto não mudou de dependências)

- Pushes `44c4ad7` / `a882297` / `1955bfe` disparam **Windows Release Build** (build + aceite-bundle 17/17 + `publish-release` skipped sem tag) e **Matriz do Instalador Windows** (8 cenários). Runs: `44c4ad7` → Release `38646344922` **✓** + Matriz `38646345073`; `a882297` → Release `38646664030` **✓** + Matriz `38646664034`; `1955bfe` → Release `38646831650` + Matriz `38646831452`; **este push** dispara os mesmos dois gates. **Resultado final citado após verde** (prática da casa — ex.: `fefd16f`) — nenhum resultado é presumido; as Matrizes e os runs deste push fecham depois desta edição.
- Nenhum arquivo de dependência tocado pelo FQ (prova: `git diff 175dc0f..44c4ad7 -- pyproject.toml requirements.txt setup.py` = vazio) — o instalador permanece no conjunto de dependências de antes; os gates Windows confirmam isso de ponta a ponta.

## 5. Pacote do PR revisável (§13 — "Não fazer merge automático")

Precedente registrado (FM §12–§13): a abertura e o merge do PR da branch de integração **aguardam decisão explícita do dono**; nada é mergeado automaticamente. O mesmo vale para o FQ:

- **Branch:** `fix/instalador-atualizacoes-windows` (224+ commits à frente de `main` — inclui todo o V8 + FM + MO + FQ).
- **Compare (para abrir o PR):** `https://github.com/LMPrado-DZ23/DZ23-Trading-AI/compare/main...fix/instalador-atualizacoes-windows`
- **Commits desta missão:** `175dc0f` (FQ-0 sondagens) → `44c4ad7` (FQ-1 + matriz) → `a882297` (FQ-2 docs) → `1955bfe` (lint) → este commit (FQ-3 evidências).
- **Título sugerido:** `feat(FQ): integração seletiva validada — paridade Freqtrade/FreqAI/OpenBB + 4 melhorias FQ-1 (liquidez, estabilidade, registro, lookahead)`.
- **Corpo sugerido:** resumo da tabela §1 + os testes §2 + ressalvas §6 + link desta página.
- **Decisão pendente do dono:** abrir o PR e revisar. Nada foi mergeado; nenhuma tag/release.

## 6. Pendências e dependências externas (§13.10 — registro honesto)

| # | Pendência | Tipo | Estado |
| :-: | :--- | :--- | :--- |
| 1 | `code_revision` real no manifest do research backtest (hoje default `"working-tree"`) | Débito de rastreabilidade (matriz §2.1) | Pendente — não comprovadamente necessário nesta rodada (§14); registrado |
| 2 | Teste explícito de igualdade indicador-backtest×produção com warmup declarado (FQ-1 fez causalidade/prefixo; falta o teste de igualdade entre os dois contextos) | Débito de teste (matriz §2.1) | Pendente — registrado |
| 3 | Fiação do `Laboratorio` em produção como caderno consultável (hoje instanciado só em testes; reuso via `evolucao`) | Débito de produto (matriz §2.1) | Pendente — registrado |
| 4 | Endpoint determinístico "perguntas do dono" (Copiloto §7) | Decisão de escopo | **ADIAR** (§3 item 5 da matriz) — reavaliar com evidência de erro por dispersão |
| 5 | Primeiro confronto DZ23 × Freqtrade (protocolo pronto) | Rodada pontual (§4) | Não executado — protocolo em `FQ-2-PROTOCOLO-EQUIVALENCIA.md`; disparar só se um veredito da Escola for contestado |
| 6 | Adaptador OpenBB (§6) | Integração opcional | **ADIAR** — sem caso de uso comprovado; sem B3 |
| 7 | FreqAI como dependência (§5) | Integração pesada | **REJEITAR** — conceitos já cobertos; §5/§10 proíbem peso indiscriminado |
| 8 | Auditoria independente externa (ChatGPT/Claude) sobre esta rodada FQ | Governança | Aguarda o dono (fora do meu escopo de execução) |
| 9 | Abrir/revisar/mergear o PR revisável (§5) | Decisão do dono | Preparado — abertura aguarda decisão explícita |
| 10 | Tag/Release da linha RC | Decisão do dono | Sem pedido/autorização — nada publicado |
| 11 | Piloto R$ 1.000 com dinheiro real | Decisão do dono | **Não autorizado** — LIVE segue bloqueado por código |

**Dependências externas usadas nesta missão:** nenhuma instalada no produto. Ferramentas externas: venv isolado `freqtrade-spike/` (fora do repo; freqtrade 2026.9; import + `--version` — sem conta). OpenBB: **não instalado** (só docs). Nenhuma credencial usada; nenhum segredo lido/ecoado.

## 7. Ressalvas declaradas (honestidade)

- Suíte completa roda **sem os e2e** no venv padrão quando o Playwright não está instalado (skips ambientais de sempre — declarados na saída).
- A carteira de métricas da Escola mudou o veredito de formação (gate mais estrito): formações antigas já persistidas não são recalculadas retroativamente — o Efeito vale para novas avaliações; registrado como nota de comportamento.
- `detectar_lookahead` é evidência por perturbação (complementa, não substitui, revisão de código do pipeline).
- O confronto com o Freqtrade **não foi executado** — o protocolo está pronto e auditável; executá-lo é rodada pontual futura.
- Instalação pessoal do dono (`:8000`, PID 13800) preservada e intocada em toda a missão; `%APPDATA%` intocado; `LOGO.png` e `.research/` seguem fora do versionamento.

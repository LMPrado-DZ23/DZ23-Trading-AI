# AC-1 — Matriz de auditoria da rodada "auditar corrigir e melhorar"

**Objetivo (dono):** "auditar corrigir e melhorar" (goal 1792018536746-8029eb, 2026-10-14).
**Branch:** `fix/instalador-atualizacoes-windows` (tip `421b7f4`) · **Estado obrigatório:** LIVE BLOQUEADO (inalterado — nada nesta rodada toca execução real).

**Método:** mesma regra da casa — matriz com prova arquivo:linha ANTES de código; depois TDD RED→GREEN só no comprovado (§14). Escopo auditar: (1) os 3 débitos registrados em FQ-3 §13.10; (2) sweep de saúde horizontal (ruff repo-inteiro, git status, docs×código).

## 1. Chave

| Decisão | Significado |
| :--- | :--- |
| **CORRIGIR** | Defeito/risco comprovado no estado atual. |
| **MELHORAR** | Lacuna comprovada com valor mensurável e custo baixo, sem duplicar. |
| **MANTER COMO ESTÁ** | O que parecia dívida já está coberto no estado atual. |

## 2. Matriz de achados

| # | ACHADO | PROVA (arquivo:linha) | IMPACTO | DECISÃO |
| :--- | :--- | :--- | :--- | :--- |
| A1 | **`code_revision` nunca carrega revisão de código.** `run_backtest` defaulta `"working-tree"` (`research/backtest.py:46`); o único caminho de produção (`walk_forward.py:30`) grava o identificador da JANELA (`walk-forward:{train}:{test}`) no campo `code_revision` — o nome promete código, o valor entrega janela. Teste existente pina o contrato defeituoso (`test_research.py:62-63`). | §3/§13.7 rastreabilidade: um veredito walk-forward não amarra a commit nenhum; reexecução não é verificável. Médio-baixo. | **CORRIGIR** — resolver revisão real (git SHA + marcação `-dirty`, fallback honesto `"working-tree"` fora de repo/bundle instalado) + campo separado `janela` no manifest. Débito FQ-3 #1. |
| A2 | **Sem teste de igualdade indicador backtest×produção.** O backtest da casa (`validation.py:132` `janela = closes[:i]`) e a produção (`traders.py:136` `interpretar(snap.instrument, snap.closes)`) compartilham `interpretar`, mas nenhum teste pina que a janela do backtest é EXATAMENTE o prefixo de produção no mesmo corte, nem que o warmup é idêntico nos dois contextos. FQ-1 pinou causalidade/prefixo, não a igualdade entre contextos. | Divergência silenciosa backtest↔produção = falsa confiança (matriz FQ-0 §2.1 linha "Indicadores recursivos"). Débito FQ-3 #2. | **MELHORAR** — teste de igualdade dos dois contextos com warmup declarado (RED→GREEN). |
| A2b | **Duas definições de RSI coexistem sem declaração.** `sala_trader.rsi` é Wilder em série (alimenta painel da sala `sala_trader.py:490` e o Copiloto `copiloto_sinais.py:177`); `signals.rsi` é média simples (Cutler, valor único — alimenta `interpretar` → motivos da decisão `traders.py:136`). Mesmo instrumento, mesmo corte: o dono vê um número no painel/Copiloto e outro na evidência da decisão, sem nenhuma declaração da diferença. | Transparência de evidência (baixo-médio): não muda o cálculo da decisão, mas cria leitura errada do dono sobre qual número fundamentou a decisão. | **MELHORAR (declaração, sem unificação)** — declarar a definição em cada docstring + teste pina que as duas são distintas e cada contexto usa a sua (unificar seria churn sem caso comprovado; as duas têm papéis diferentes: série para gráfico, valor para evidência). |
| A3 | **Caderno consultável do registro §3.** O débito FQ-3 #3 dizia "`Laboratorio` só em testes". No estado ATUAL: o registro de experimentos da Evolução JÁ é consultável por trader em produção — rota `/v1/desk/traders/{id}/sala` devolve `experimentos_do_trader(evo.experimentos(), evo.resultados_dos_experimentos(), agent_id)` (`app.py:3251-3253`), e `evo.relatorio()` agrega tudo (`evolucao.py:728-742`) — mas **não tem rota**: nenhuma superfície expõe o caderno GLOBAL. A classe `Laboratorio` (triagem isolada) segue só em testes — fiá-la duplicaria a maquinaria da Evolução (§2: "não construir funcionalidades duplicadas"). | §3/§13.7: o dono consegue ver experimentos de um trader na sala dele, mas não o caderno inteiro num só lugar. Médio-baixo. | **MELHORAR (mínimo)** — rota read-only `GET /v1/evolucao` expondo `evo.relatorio()` (sem novo estado, sem nova dependência). **MANTER COMO ESTÁ** a fiação da classe `Laboratorio` (duplicação). |
| A4 | **F821 em produção: `LeituraDeLivro` indefinido no escopo do módulo.** `market.py:108` anota o retorno como `"LeituraDeLivro"` (string), mas o import vive DENTRO do método (`market.py:118`) — o nome não existe no escopo da anotação. Import/execução funcionam (anotação em string nunca é avaliada), mas `typing.get_type_hints` quebra e o contrato do tipo é irrefletível. | Baixo (não muda runtime hoje), mas é defeito real em módulo de produção achado pelo sweep. | **CORRIGIR** — import sob `TYPE_CHECKING` + RED test via `get_type_hints`. |
| A5 | **7 achados de lint repo-inteiro** (ruff sempre rodou só em arquivos tocados): F401 import não usado (`test_microestrutura.py:28`), C408 ×2 (`test_microestrutura.py:249`, `microestrutura.py:156`), F841 var não usada (`test_oms_tempos.py:107`), I001 imports desordenados (`test_oms_tempos.py:198`), UP037 aspas na anotação (`market.py:108`). | Higiene; nenhum muda comportamento. | **CORRIGIR** — zerar `ruff check .` no repo inteiro (evidência: saída limpa). |
| A6 | **Git/estado:** árvore limpa; só `?? LOGO.png` e `?? .research/` não-rastreados (regra da casa: nunca commitar). Docs de missão conferem com o código nos pontos auditados (A1–A5 são os desvios encontrados). | — | **MANTER COMO ESTÁ**. |

## 3. Fora de escopo desta rodada (decisões do dono, inalteradas)

Abrir/mergear PR da branch de integração; tag/Release; auditoria externa; piloto R$ 1.000; confronto DZ23×Freqtrade (protocolo pronto); OpenBB/FreqAI (ADIAR/REJEITAR da matriz FQ-0). Nada aqui mexe em `config.real`, travas de LIVE, ou credenciais.

## 4. Plano de execução (TDD, ordem)

1. **A4** — RED: `get_type_hints(PublicMarketFeed.leitura_de_livro)` → NameError hoje; GREEN: import REAL top-level (o plano inicial dizia `TYPE_CHECKING` — abortado pela evidência: bloco `TYPE_CHECKING` não existe em runtime e o NameError persistiria; sem import circular pois `microestrutura.py` só importa stdlib; ver §5).
2. **A5** — limpeza de lint nos 5 pontos (testes não mudam de semântica; `ruff check .` vira evidência).
3. **A1** — RED: manifest do walk-forward carrega revisão real + `janela` separada; fallback honesto sem git; GREEN: `revisao_do_codigo()` + campo `janela`; atualiza o pino antigo `test_research.py:62-63` para o contrato novo.
4. **A2/A2b** — RED: (i) janela do backtest é EXATAMENTE `closes[:i]`; (ii) decisão do backtest no corte k == decisão de produção no mesmo corte; (iii) warmup idêntico nos dois contextos; (iv) RSI Wilder ≠ RSI Cutler pinados com definição declarada. Sem mudança de comportamento — só pinos.
5. **A3** — RED: `GET /v1/evolucao` devolve o relatório global (read-only, autenticado); vazio honesto sem mesa iniciada.
6. Suíte alvo + `ruff check .` + docs de estado + commit + push (sem `LOGO.png`/`.research/`).

## 5. Resultados (RED→GREEN, com evidência)

| # | RED (antes do fix) | GREEN (depois) | Arquivos |
| :--- | :--- | :--- | :--- |
| A4 | `NameError: name 'LeituraDeLivro' is not defined` em `get_type_hints(PublicMarketFeed.leitura_de_livro)` | 23 passed (`test_microestrutura.py`) | `market.py` (import real top-level + anotação sem aspas) |
| A5 | 7 findings ruff repo-inteiro (F401, C408 ×2, F841, I001 ×3, UP037) | `ruff check .` → **All checks passed!** | `market.py`, `microestrutura.py`, `test_microestrutura.py`, `test_oms_tempos.py`, 2 novos testes AC-1 |
| A1 | `ImportError: cannot import name 'revisao_do_codigo'` (função não existia) | 11 passed (`test_research.py` + `test_ac1_revisao_codigo.py`) — manifest carrega sha HEAD (+`-dirty` se sujo), `janela` em campo próprio, `"working-tree"` honesto sem git | `backtest.py` (`revisao_do_codigo`, kwarg `janela`), `walk_forward.py`, `research/__init__.py`, pino antigo `test_research.py` atualizado |
| A2/A2b | `1 failed, 6 passed` — falhava só `"cutler" in signals.rsi.__doc__` (sem declaração) | 25 passed (`test_ac1_igualdade_contextos.py` + `test_signal_processing.py`) — pinos de igualdade de janela/decisão/warmup + RSI Cutler e Wilder declarados e provados distintos | `signals.py` (docstring Cutler); testes puros |
| A3 | 3×404 — rota não existia | 3 passed (`test_ac1_caderno_evolucao.py`) — `GET /v1/desk/evolucao` devolve `relatorio()` global (traders + experimentos + aviso), 401 sem token, vazio honesto em DATA_DIR recém-nascido | `service/app.py` (rota read-only `Depends(auth)`) |

**Correções de percurso comprovadas pela evidência:**

- **A4 GREEN mudou de plano:** o §4 previa `TYPE_CHECKING`; a evidência matou a ideia — bloco `TYPE_CHECKING` não existe em runtime, então `get_type_hints` continuaria quebrando (NameError). Verificado que `microestrutura.py` importa só stdlib (sem risco de import circular) ⇒ import REAL top-level. Era o defeito original: o import vivia escondido dentro do método.
- **A3 rota é `/v1/desk/evolucao`** (não `/v1/evolucao`): todo caminho da mesa na casa já vive sob `/v1/desk/`.
- **A1 detalhe de teste:** o `except` de `revisao_do_codigo` resolve `SubprocessError` LIGADO no import (constante), senão o falso `subprocess` dos testes não conhece a exceção e o fallback honesto nunca roda.

**Sem mudança de comportamento de negociação:** nenhum arquivo de execução/risco/OMS foi tocado; LIVE segue bloqueado. Suítes afetadas todas verdes (168 passed) + ruff limpo + suíte completa (ver CHECKPOINTS).

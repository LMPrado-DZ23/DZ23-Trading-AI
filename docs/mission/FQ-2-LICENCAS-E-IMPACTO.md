# FQ-2 — Licenças, dependências e impacto de desempenho (§10 · §5 · §13.5 da missão)

**Missão:** "DZ23 Trading AI — Missão de Evolução Quantitativa: Freqtrade + FreqAI + OpenBB — Integração Seletiva e Validada" (§10, §5, §13.5).
**Data:** 2026-10-14 · **Branch:** `fix/instalador-atualizacoes-windows` · **Estado obrigatório:** LIVE BLOQUEADO (inalterado).
**Insumos:** `docs/mission/evidence/fq0-probe-dependencias.md` (metadados PyPI verificados) · `docs/mission/FQ-0-MATRIZ-PARIDADE.md` (decisões por componente).

## 1. Licenças das referências (§10)

| Referência | Licença (verificada) | O que a §10 permite | O que a §10 proíbe | Decisão registrada |
| :--- | :--- | :--- | :--- | :--- |
| **Freqtrade** | **GPLv3** (metadado PyPI; probe §2) | Uso como **ferramenta separada** (processo isolado, venv próprio) para validação independente (§4). | Copiar código para o núcleo Apache-2.0; linkar como biblioteca; importar do produto. Precedente interno: Backtrader GPLv3+ **removido** por esse motivo (`docs/REPOSITORIOS-VALOR-E-INTEGRACAO-DZ23.md` L22). | **ADIAR** confronto sistemático (protocolo em `FQ-2-PROTOCOLO-EQUIVALENCIA.md`); **REJEITAR** qualquer uso na cadeia de execução. Nenhuma linha GPL no repositório. |
| **FreqAI** | **GPLv3** (módulo do repositório freqtrade) | Avaliação conceitual apenas (§5). | Instalar modelos pesados no Windows/ARM64 indiscriminadamente (§5); auto-promoção fora de PAPER. | **REJEITAR** como dependência; **MANTER** as lições (avaliação de estabilidade temporal já implementada em FQ-1 na Escola, sem copiar código). Auto-promoção inexiste por desenho: laboratório não promove; Escola exige prova real; Mesa exige autorização do dono. |
| **OpenBB V5** | **Apache-2.0** (metadado PyPI; probe §3) | Integrar como fonte de dados **complementar** se auditado e sem violar §6. | Quebrar instalador Windows/ARM64 com peso de dependências; usar como corretora (nunca). | **ADIAR** (nenhum caso de uso comprovado; sem cobertura Brasil; meta-pacote ~24 deps + pywry ~13–17 MB + `openbb-build`; Desktop oficial instala **conda** — inaceitável para o instalador). |
| **agent-rita / openbb-ai** | MIT, mas **ARQUIVADOS** upstream | — | Dependência de Workspace externo; segundo assistente (§7). | **REJEITAR** como dependência; lições de design já refletidas (thin harness + citações). |

### 1.1 Base verificada (FSF GPL FAQ — inventory `fq0-inventario-freqtrade-docs.md` §7)

| Cenário | Posição da FSF (verificada) | Nossa regra |
| :--- | :--- | :--- |
| Usar como **ferramenta** (CLI/venv separado) | "Using them does not place any restrictions, legally, on the license you use for your code" — copyleft dispara na **distribuição**. | Uso local como ferramenta de conferência é o cenário aprovado (§4). |
| **Distribuir** o freqtrade (bundle/container) | Deveres GPL (fonte correspondente); agregação de programas separados não contamina o restante. | Não distribuímos freqtrade; nada entra no instalador. |
| **Copiar/importar código** | Obra derivada — incompatível com o núcleo sem análise jurídica. | Proibido (precedente Backtrader). |
| Estratégia `import freqtrade` + `IStrategy` | Acoplamento de plug-in: **incerto**; exigiria análise jurídica **se distribuída**. | Estratégias de confronto vivem fora do repositório, não são distribuídas, não são importadas pelo produto. |
| **Output** (números do backtest) | Em geral não coberto pelo copyright do código. | Podemos registrar métricas do confronto como evidência. |

## 2. Nenhuma dependência nova entrou no produto (prova)

- O commit de FQ-1 (`44c4ad7`, 11 arquivos) não toca `pyproject.toml`, `requirements*.txt` nem o setup do instalador — implementação 100% stdlib (`hashlib`, `json`, `math`, `random` — já presentes).
- O freqtrade vive em venv isolado **fora** do repositório (`freqtrade-spike/` — probe §3.2); o OpenBB **não foi instalado** (probe §3.1: só documentação).
- Verificação reproducible: `git show --stat 44c4ad7` (nenhum arquivo de dependência) e `git diff 175dc0f..44c4ad7 -- pyproject.toml requirements.txt` (vazio).

## 3. Impacto de desempenho e recursos (§13.5)

**FQ-1 (o que foi implementado — custo real medido por análise de complexidade):**

| Mudança | Custo | Por que não afeta o produto |
| :--- | :--- | :--- |
| `hash_das_series` (SHA-256 canônico das séries) | O(n) em bytes, **uma vez por execução** de hipótese (execução é rara e manual). | Nada no ciclo contínuo; só no laboratório sob demanda. |
| `metricas_de_formacao` (expectativa, fator, drawdown, metades) | O(n) desfechos por avaliação (n ≤ centenas). | Roda na avaliação da Escola (evento), não por tick. |
| `detectar_lookahead` | O(cortes × n) chamadas de sinal — ferramenta de bancada, executada por rodada de auditoria. | Não roda em produção. |
| Liquidez no backtest (`max_participacao`) | Arredondamento Decimal extra por fill candidato — mesma ordem do custo de cálculo já existente por barra. | Backtest é offline. |

**Sondagem das referências (tamanhos conforme probe):**

| Item | Peso verificado | Onde vive hoje | Risco para o instalador |
| :--- | :--- | :--- | :--- |
| freqtrade 2026.9 | **85 dependências** declaradas | venv isolado fora do repo (spike) | **Nenhum** (não empacotado; não importável pelo produto) |
| openbb 5.0.0 | **36 deps** (pacote-base) + provedores próprios; pywry ~13–17 MB; `openbb-build` no build; Desktop instala conda | **não instalado** | **Nenhum hoje** — e é exatamente por isso que a decisão é ADIAR: incorporar quebraria o requisito §10 (não quebrar instalador Windows/ARM64) sem caso de uso comprovado. |
| FreqAI | modelos pesados (torch/lightgbm etc. no ecossistema freqtrade) | não instalado | **Nenhum**; §5 proíbe instalar pesado no Windows/ARM64 indiscriminadamente. |

**Conclusão §13.5:** o delta de FQ-1 no produto é desprezível (stdlib, O(n) em caminhos offline/evento). Nenhum recurso pesado foi adicionado; nada foi instalado no venv do projeto; o instalador Windows/ARM64 permanece exatamente com o conjunto de dependências de antes (matriz do instalador CI cobre isso).

## 4. Limitações declaradas

- Licenças verificadas via **metadados de pacote** (PyPI) — a análise é de engenharia, não jurídica; qualquer uso além de "ferramenta separada" exigiria análise jurídica antes (posição já registrada em `REPOSITORIOS-VALOR`).
- O impacto §13.5 de FQ-1 é análise de complexidade + invariantes de caminho (offline/evento), não benchmark instrumentado — não há número medido de milissegundos porque as rotas afetadas não rodam no ciclo contínuo do produto (laboratório é sob demanda; avaliação é por evento de desfecho).
- Peso do openbb no instalador é estimativa por componentes oficiais (pywry/build), não medição — a rodada de instalação real só aconteceria se o adaptador fosse aprovado (§6), o que **não** ocorreu.

# FQ-2 — Registro de experimentos e ferramentas do laboratório (§3 da missão)

**Missão:** "DZ23 Trading AI — Missão de Evolução Quantitativa: Freqtrade + FreqAI + OpenBB — Integração Seletiva e Validada" (§3, §13.7).
**Data:** 2026-10-14 · **Branch:** `fix/instalador-atualizacoes-windows` · **Estado obrigatório:** LIVE BLOQUEADO (inalterado).
**Código:** `tradingagents/desk/laboratorio.py`, `tradingagents/research/` · **Provas:** `tests/test_fq_laboratorio_registro.py`, `tests/test_fq_lookahead.py`, `tests/test_fq_indicadores_causais.py`, `tests/test_fq_liquidez_backtest.py`.

## 1. O mandato (§3) e o contrato no código

§3 exige que **todo experimento** registre, no mínimo: versão da estratégia, configuração, fonte de dados, período, timeframe, universo, custos, modelo de execução, métricas, identificador reprodutível e limitações. No DZ23 isso deixou de ser promessa de processo e virou **contrato de assinatura**: os campos fazem parte da hipótese congelada — alterar qualquer um **muda a assinatura SHA-256** e o laboratório recusa a execução como "não pré-registrada" (`laboratorio.py` — `Hipotese.assinatura()`; teste `test_fq_laboratorio_registro.py`).

| Requisito §3 | Onde vive (arquivo · campo) | Prova |
| :--- | :--- | :--- |
| **Versão da estratégia** | `Hipotese.id` + `assinatura()` (SHA-256 da hipótese inteira; versão alterada = assinatura nova) | `test_alterar_campo_muda_assinatura` |
| **Configuração** | `Hipotese.parametros` (`tuple[(nome, valor)]` — congelados na assinatura) | `test_desk_laboratorio.py::TestPreRegistro` |
| **Fonte de dados** | `Hipotese.fonte_dados` (**novo em FQ-1**) | `test_campos_do_registro_persistem` |
| **Período** | `janela_treino_barras` + `janela_teste_barras` (em barras do timeframe declarado) + `executada_em` no resultado | `ResultadoDaHipotese.executada_em` |
| **Timeframe** | `Hipotese.timeframe` (**novo em FQ-1**; default `1h`) | `test_campos_do_registro_persistem` |
| **Universo** | `Hipotese.instrumentos` (tupla; ex.: `("BTC/USDT",)`) | `TestBonferroni`/`TestPersistencia` |
| **Custos** | `Hipotese.custos` (declaração) + `ResultadoDaHipotese.custos_aplicados` (**novo**: fração ida-e-volta real por instrumento, vinda de `costs.py`) | `test_custos_aplicados_registrados` |
| **Modelo de execução** | `Hipotese.modelo_de_execucao` (**novo**; default `close_a_close`) + `horizonte_barras` | `test_campos_do_registro_persistem` |
| **Métricas** | `ResultadoDaHipotese` (operações, médias líquida vs controle, p-valor, alpha Bonferroni) + `escola.metricas_de_formacao` (expectativa, fator de lucro, drawdown, estabilidade) | `test_fq_formacao_metricas.py` |
| **Identificador reprodutível** | `ResultadoDaHipotese.assinatura` + `hash_das_series` (**novo**: SHA-256 canônico das séries usadas) | `test_hash_reprodutivel` |
| **Limitações** | `Hipotese.limitacoes` (**novo**) | `test_campos_do_registro_persistem` |

Os cinco campos novos estão documentados no próprio dataclass (`laboratorio.py` L115-121) com a regra de congelamento: *"Registro §3: fonte, tempo, custos, execução e limitações fazem parte da hipótese CONGELADA — mudar qualquer uma muda a assinatura."*

## 2. Garantias anti-fraude já existentes (mantidas, não duplicadas)

- **Pré-registro obrigatório**: executar hipótese fora do registro → `ValueError` "não pré-registrada".
- **Execução única**: reexecução → `RuntimeError`, só com `forcar=True` explícito; o registro marca `forcada=True` (anti p-hacking).
- **Controle aleatório determinístico** com o MESMO número de entradas (semente `id|instrumento|seed`).
- **Bonferroni por família**: alpha cai com o tamanho da família.
- **Custos reais descontados** em cada operação (`costs.py`; custo nunca estimado pela IA).
- **O laboratório NUNCA promove**: formatura só na Escola de Traders, com desfechos reais e prova independente.

## 3. Ferramentas novas do laboratório (FQ-1)

| Ferramenta | O que garante | Onde | Prova |
| :--- | :--- | :--- | :--- |
| **`hash_das_series`** | Mesmo dado ⇒ mesmo hash: o experimento é reexecutável e conferível por terceiro. | `laboratorio.py` (`_hash_das_series`) | `test_hash_reprodutivel` |
| **`detectar_lookahead`** | Truncar o futuro muda a decisão de barras anteriores? ⇒ vazamento **localizado** (corte + índice + direções divergentes). Sem corte aplicável (amostra curta) ⇒ aviso explícito de que nenhuma evidência foi produzida. | `tradingagents/research/lookahead.py` | `test_fq_lookahead.py` |
| **Causalidade dos indicadores** | Indicadores de janela e de prefixo pinados: mexer no futuro não muda o passado. **EMA excluída deliberadamente** — é recursiva desde a semente (limitação documentada, não escondida). | `tests/test_fq_indicadores_causais.py` | 4 testes |
| **Liquidez no backtest** | Fill limitado pela capacidade da barra (`max_participacao`); sem-liquidez não preenche; modelo registrado no manifest. | `research/backtest.py` | `test_fq_liquidez_backtest.py` |
| **Estabilidade na formação** | Duas metades temporais da prova final precisam ser positivas; senão a formação fica BLOQUEADA e a escola segue coletando (gate, não sentença). | `desk/escola.py` (`metricas_de_formacao`) | `test_fq_formacao_metricas.py` |

## 4. Como registrar um experimento (exemplo mínimo)

```python
from tradingagents.desk.laboratorio import Hipotese, Laboratorio

h = Hipotese(
    id="H-001", titulo="rompimento 20 barras", familia="rompimento",
    tipo_de_regra="rompimento",
    parametros=(("janela", 20),),
    instrumentos=("BTC/USDT",),
    fonte_dados="klines spot Binance (séries fornecidas pelo chamador)",
    timeframe="1h", custos="taxas Binance spot via costs.py (taker)",
    modelo_de_execucao="close_a_close",
    limitacoes="ajuste único de parâmetros; sem dados de livro (só OHLCV)",
)
lab = Laboratorio()
lab.registrar(h)                    # congela a assinatura
r = lab.executar(h, {"BTC/USDT": serie})
print(r.assinatura, r.hash_das_series, r.custos_aplicados)
```

Se a estratégia for rejeitada pela triagem, o veredito registrado é **"não supera o controle"** — nunca promessa de lucro.

## 5. Limitações declaradas

- O `hash_das_series` cobre as **séries entregues ao executor** — não valida a origem externa do dado (quem fornece a série declara a fonte em `fonte_dados`).
- `timeframe` é declarativo: o laboratório não deriva o timeframe das barras; a coerência entre `timeframe` e a série fornecida é responsabilidade do chamador (auditável no registro).
- `detectar_lookahead` detecta vazamento por perturbação de truncamento; não substitui revisão de código do pipeline (é evidência adicional, não prova de ausência total).
- Nenhum modelo de IA pesado foi introduzido (§5): as ferramentas são Python puro; o laboratório não promove ninguém automaticamente.

## 6. Divergência de modelo de taxas DZ23 × convenção por ponta (V9-QUANT-07)

Medição offline e determinística, sem rede, credenciais ou execução financeira — a ferramenta [`tools/v9_quant07_divergencia_custos.py`](../../tools/v9_quant07_divergencia_custos.py) compara três contabilidades para a MESMA operação (1 unidade, entrada a 100, taxa 0,1% por ponta):

| Contabilidade | Ganho (saída 110) | Perda (saída 90) |
|---|---|---|
| DZ23 (`ModeloDeCusto.ida_e_volta` = 2×fixo sobre o principal) | +9,800 | −10,200 |
| Referência por ponta (taxa de saída sobre o valor vendido) | +9,79 | −10,19 |
| Freqtrade 2026.9 real (`LocalTrade.calculate_profit`, fonte primária) | **+9.79** (`profit_ratio 0.0978022`) | **−10.19** (`profit_ratio −0.1017982`) |

- **Causa única:** o Freqtrade cobra `fee_close` sobre o valor de **saída**; o DZ23 desconta as duas pontas fixas sobre o principal (notional inicial).
- **Magnitude exata:** `|DZ23 − por-ponta| = taxa × retorno_bruto` (0,0001 de fração no ganho a ±10%; −0,0001 na perda). Não é ruído — é o termo de primeira ordem que o modelo DZ23 ignora.
- **Direção:** DZ23 é levemente **otimista em ganhos** e **conservador em perdas** (marca prejuízo maior). Para uma escola que decide com margem de segurança, o lado do erro que importa — perder dinheiro — está protegido.
- **Decisão:** MANTER o modelo de primeira ordem (`FQ-0-MATRIZ-PARIDADE.md`: costs = "MANTER COMO ESTÁ"; `tests/test_desk_custos.py` pinava a fórmula desde antes). Mudar a semântica de `retorno_liquido` alteraria o significado dos `Desfecho` já registrados na Escola para ganhar um ponto-base de acurácia — custo de mudança maior que o erro.
- **O que NÃO se pode afirmar:** equivalência exata de taxas com o Freqtrade. O que se pode — e está pinado por [tests/test_v9_quant07_divergencia_custos.py](../../tests/test_v9_quant07_divergencia_custos.py) (7 testes) — é a divergência exata, a direção conservadora e a guarda anti-mudança-silenciosa da fórmula.

Evidências: `docs/mission/evidence/v9-baseline/v9-quant07-probe-repo.json` (+`.log`, ambiente do repositório, `freqtrade.disponivel: false`) e `v9-quant07-probe-freqtrade.json` (+`.log`, ambiente do spike isolado, Freqtrade 2026.9/GPLv3 em processo separado — nenhum código GPL entra no repositório).

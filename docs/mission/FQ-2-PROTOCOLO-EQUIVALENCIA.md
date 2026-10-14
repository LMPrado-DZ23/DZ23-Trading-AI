# FQ-2 — Protocolo de equivalência: Freqtrade como motor independente de validação (§4 da missão)

**Missão:** "DZ23 Trading AI — Missão de Evolução Quantitativa: Freqtrade + FreqAI + OpenBB — Integração Seletiva e Validada" (§4, §10).
**Data:** 2026-10-14 · **Branch:** `fix/instalador-atualizacoes-windows` · **Estado obrigatório:** LIVE BLOQUEADO (inalterado).
**Insumos:** `docs/mission/evidence/fq0-probe-dependencias.md` §2 e §3.2 (spike de execução, exit 0) · `docs/mission/evidence/fq0-inventario-freqtrade-docs.md` (inventário das docs estáveis: premissas de backtest, QA de viés, export, licença) · `docs/mission/FQ-0-MATRIZ-PARIDADE.md` (decisão **ADIAR** o confronto sistemático; **REJEITAR** qualquer uso na cadeia de execução).

## 1. Papel permitido (e proibido) do Freqtrade no DZ23

| Permitido | Proibido |
| :--- | :--- |
| Ferramenta **separada** de validação independente: recebe dados exportados e devolve métricas de backtest para confronto com o DZ23. | Qualquer uso na **cadeia de execução** (§4): o Freqtrade nunca envia ordem, nunca conecta corretora, nunca toca o OMS/Mesa. |
| Venv **próprio e isolado**, fora do repositório e fora do venv do projeto (padrão do spike: `freqtrade-spike/`). | A **mesma conta** do Maestro (§4; `docs/REPOSITORIOS-VALOR-E-INTEGRACAO-DZ23.md` L40) — nem em dry-run. |
| Licença GPLv3 respeitada como **processo separado** (sem copiar código — §10). | Copiar/colar código do freqtrade para o núcleo Apache-2.0 (precedente Backtrader GPLv3+ removido, `REPOSITORIOS-VALOR` L22). |
| Rodar **pontualmente** quando um veredito da Escola for contestado. | Confronto sistemático/automatizado em produção (matriz FQ-0: **ADIAR**). |

**Estado atual:** o spike (§3.2 do probe) provou *factibilidade técnica* (import + `--version`, sem conta, sem credencial). **Nenhum confronto de backtest foi executado ainda** — este documento é o protocolo que torna o primeiro confronto auditável; executá-lo é uma rodada futura e pontual.

## 2. Checklist de equivalência (fonte da §4 — 11 itens)

Um confronto DZ23 × Freqtrade só vale se **cada item abaixo estiver fixado por escrito na rodada**, com a mesma escolha dos dois lados. Divergência em qualquer item invalida a comparação (não é "equivalência aproximada" — é experimento diferente).

| # | Item (§4) | O que fixar na rodada | Como conferir |
| :--: | :--- | :--- | :--- |
| 1 | **Sinais** | Função de entrada/saída idêntica (mesma regra, mesmos parâmetros, mesmo warmup). | Exportar a série de decisões do DZ23 (`0/1` por barra) e a do freqtrade e comparar barra a barra. |
| 2 | **Calendário** | Mesmos dias/horas negociados; timezone **UTC** declarado; barras sem pregão excluídas dos dois lados. | Diff dos timestamps das barras usadas. |
| 3 | **Velas** | Mesmo timeframe, mesma convenção OHLCV (timestamp = abertura da barra), mesmos limites da janela (início/fim). | Hash SHA-256 do arquivo de velas exportado (mesmo padrão do `hash_das_series`). |
| 4 | **Taxas** | Mesma taxa por lado (bps), mesmo arredondamento de magnitude. | Registrar taxa usada nos dois lados (DZ23: `costs.py`; freqtrade: config de fee). |
| 5 | **Slippage** | Mesmo modelo de slippage (ex.: `close_a_close` sem slippage adicional ⇒ declarar 0 nos dois). | Registrar valor bps nos dois lados. |
| 6 | **Fills** | Mesma regra de execução na barra (na abertura? no fechamento? barra seguinte?), mesma política de fill parcial. | DZ23: `modelo_de_execucao` da hipótese + `max_participacao`; freqtrade: `order_types`/`--dry-run` de backtest com a mesma regra. |
| 7 | **Stop** | Mesmos stops/alvos (nível, gatilho, ordem de checagem dentro da barra) ou **nenhum** nos dois lados. | Regra escrita na rodada; conferir primeiras N saídas. |
| 8 | **Capital** | Mesmo capital inicial e mesmo tamanho de posição (fixo, não composto — ou composto nos dois). | Registrar número exato. |
| 9 | **Reinvestimento** | Fixar: capital fixo vs reinvestir lucro; DZ23 usa capital de referência para custo (`VALOR_REFERENCIA`) — alinhar o modo. | Registrar escolha e conferir equity final dos dois lados. |
| 10 | **Timeframes** | Mesmo timeframe em sinal, execução e horizonte (DZ23: `horizonte_barras` × `timeframe`). | Comparar contagens de operações por dia. |
| 11 | **Convenções** | Timestamp de abertura vs fechamento; arredondamento de preço; tratamento de barra final incompleta. | Diff das primeiras/últimas operações. |

### 2.1 Premissas documentadas do freqtrade (verificadas nas docs estáveis — inventory §2)

O backtest do freqtrade tem premissas **próprias e declaradas**; um confronto honesto exige igualá-las explicitamente (ou declarar a divergência):

| Premissa freqtrade (docs) | Nosso lado (DZ23 pós-FQ-1) | O que fazer na rodada |
| :--- | :--- | :--- |
| **Sem slippage**: fill no preço pedido enquanto dentro do hi/lo da vela. | `fee_bps + slippage_bps` configuráveis; laboratório desconta `costs.py`. | Declarar `slippage_bps=0` no DZ23 para a rodada OU somar o mesmo slippage via `--fee` no freqtrade — escolher UMA e registrar. |
| **Sem limite de volume/liquidez** no backtest ("no information about historic limits"). | `max_participacao` limita fill à capacidade da barra (FQ-1). | Na rodada de equivalência, usar `max_participacao=1` no DZ23 e declarar que o efeito liquidez é medido **só** no lado DZ23 (fora do escopo da equivalência contábil). |
| **Sem partial fills** documentados no backtest. | `fills_limitados`/`fills_rejeitados` contabilizados. | Igualar com `max_participacao=1` (acima) e conferir contagem de fills. |
| Entradas no **open** da vela (salvo preço custom). | `modelo_de_execucao` da hipótese (default `close_a_close`). | Alinhar o modelo (open-to-open nos dois) e registrar. |
| Taxas **×2 por trade**; `fee` cai no default da exchange; `--fee` sobrescreve. | `custos` da hipótese + `custos_aplicados` real por instrumento. | Igualar taxa nos dois lados (usar a mesma bps). |
| Sequência por candle: Exit-signal → Stoploss → ROI → Trailing; stop no preço exato ("2 × fees"); low antes de high. | Respeitar a MESMA ordem se houver stop; ou **sem stop nos dois lados**. | Rodada sem stop (mais simples) ou replicar a ordem documentada. |
| Reprodutibilidade: ZIP export (JSON+strategy+config) + dados idênticos + pairlist **estática** (`StaticPairList`); cache 24h desabilitado com `--cache none`. | `assinatura` + `hash_das_series` + `dataset_hash`. | Exportar o ZIP do freqtrade **e** o hash das séries DZ23 no registro da rodada. |
| Walk-forward: **não existe nativo** — validar com backtest pós-hyperopt (`--random-state`) ou ferramenta externa. | `run_walk_forward` + `desk/evaluation.aggregate_walk_forward` próprios. | O walk-forward fica no lado DZ23; o freqtrade confere **pontos** (janelas individuais), não a validação móvel. |


## 3. Procedimento operacional (quando um veredito for contestado)

1. **Congelar o experimento do lado DZ23**: registrar a hipótese com os 11 campos (§3 da missão — ver `FQ-2-REGISTRO-DE-EXPERIMENTOS.md`), executar **uma vez**, guardar `assinatura` + `hash_das_series` + resultado.
2. **Exportar o dado canônico**: as MESMAS séries entregues ao laboratório, em CSV/JSON com timestamp UTC, OHLCV completo. Registrar o hash do export.
3. **Escrever a estratégia equivalente** no freqtrade (arquivo de estratégia mínimo), com os itens 1–11 fixados no topo do arquivo como comentário de cabeçalho.
4. **Rodar backtest no venv isolado** (`freqtrade backtesting`, modo que não conecta corretora; nenhuma credencial). Sem `--live`, sem `dry-run` com conta.
5. **Confrontar métricas**: número de operações, retorno médio por operação, taxa de acerto, drawdown. Tolerância declarada na rodada (sugestão: divergência ≤ 0.5% relativo é "equivalente"; acima disso, investigar item a item na tabela §2).
6. **Registrar o veredito como evidência** em `docs/mission/evidence/` (arquivo novo por rodada), incluindo TODAS as escolhas dos itens 1–11 e os comandos verbatim. O veredito **não** promove ninguém: a decisão continua na Escola (§8); o confronto só confirma ou contesta um resultado.

## 4. Limites declarados (honestidade da rodada)

- Um venv do freqtrade tem **85 dependências** (probe §2) — a rodada exige venv isolado já previsto; nada disso entra no instalador do produto (§10).
- O freqtrade é GPLv3: a estratégia de confronto escrita para ele é **código separado**, vivendo FORA do repositório do produto (ou em diretório de evidência declarado, sem import do núcleo do produto). Não vira dependência, não vira import.
- Equivalência de backtest **não** é garantia de resultado real: divergências de microestrutura (livro, latência, fills parciais) existem por construção. O protocolo mede se a **contabilidade** dos dois motores bate dado o mesmo modelo — não prevê o futuro.
- Esta rodada **ainda não foi executada** (decisão ADIAR da matriz). Registrado em FQ-3 como pendência pontual.

# MO-1 — Matriz de auditoria: microestrutura, transparência de decisões e observabilidade financeira

**Missão:** "DZ23 Trading AI — Auditoria de Microestrutura e Observabilidade" (comparação com o projeto público `jarrodwatts/jev-trader`).
**Data:** 2026-10-14 · **Branch:** `fix/instalador-atualizacoes-windows` · **HEAD de auditoria:** `ecfa6e8`
**Exigência da missão (verbatim):** "Antes de alterar código, produza uma matriz EXISTE / PARCIAL / AUSENTE / NÃO NECESSÁRIO, com arquivos e testes correspondentes."
**Regra desta matriz:** nenhum estado declarado sem evidência; "AUSENTE" também é resultado e cita a varredura que o comprova.

Estados: **EXISTE** (implementado e coberto por teste) · **PARCIAL** (existe, mas não cumpre o critério da missão por inteiro) · **AUSENTE** (não existe) · **NÃO NECESSÁRIO** (avaliado e deliberadamente fora de escopo, com justificativa).

---

## 0. Método

1. **Referência externa (somente leitura):** `jarrodwatts/jev-trader` — bot TypeScript/Bun na Monad L2, mercado Kuru MON-USDC; uma decisão de IA por bloco; ordem pós-only um tick dentro do touch substituindo a anterior; transmissões SSE com metadados por intervalo (timestamps, midpoint, probabilidades, status de submissão, resultados). Os elementos **reaproveitáveis são princípios** (microestrutura como dado de decisão, transparência da decisão, observabilidade financeira), **não** o motor nem a blockchain (ver §4).
2. **Auditoria do DZ23:** leitura dos módulos citados (feed de mercado, OMS da mesa, ledger, router de execução, reconciliação, sala/painel) + varredura por termos de microestrutura em todo o código de produção.
3. Esta matriz **precede qualquer alteração de código**. O escopo de implementação (MO-2) sai apenas das lacunas comprovadas abaixo (§5) e não altera nenhum bloqueio existente.

---

## 1. Microestrutura de mercado

**Varredura de comprovação (o que torna o "AUSENTE" verificável):** grep case-insensitive por `orderbook|order_book|fetch_order_book|best_bid|best_ask|bid_ask|profundidade|desequilibrio` em todo o código de produção. Únicos acertos: flag estática de capacidade (`service/personal_exchange_capabilities.py` L13, L26–32, L44), persona LLM `orderbook-analyst` (`service/app.py` L179, L781 — papel declarativo "microestrutura e liquidez" **sem fonte de dados**) e comentários. **Nenhuma chamada `fetch_order_book` no produto.**

| # | Capacidade | Estado | Evidência (arquivos) | Testes |
| :- | :--- | :--- | :--- | :--- |
| 1.1 | Preço de referência (ticker) só-leitura, para medir tamanho de ordem no caminho com trava | **EXISTE** | `tradingagents/brokers/ccxt_bridge.py` — `fetch_ticker` em `_preco_de_referencia` (L145–158); nunca envia ordem | Bateria FIN (`tests/test_auditoria_fin1_live_desk_seal.py`, `test_auditoria_fin2_gates_financeiros.py`) |
| 1.2 | Livro de ofertas L2 (leitura) | **AUSENTE** | nenhuma chamada em produção (varredura acima); feed é somente velas | — (nenhum) |
| 1.3 | Best bid/ask | **AUSENTE** | `MarketSnapshot` (`tradingagents/desk/models.py` L136–152) não tem campos de bid/ask; únicos preços vêm de velas fechadas (`tradingagents/desk/market.py`) | — |
| 1.4 | Spread **medido** | **AUSENTE** | só existe **estimativa** por classe de liquidez (`tradingagents/desk/costs.py` — `SPREAD_POR_LIQUIDEZ`: alta 0,02% / média 0,08% / baixa 0,30%) | `tests/test_desk_custos.py` (testa a estimativa, não medição) |
| 1.5 | Profundidade (top-N por lado) | **AUSENTE** | nada | — |
| 1.6 | Desequilíbrio de liquidez (imbalance) | **AUSENTE** | nada | — |
| 1.7 | Slippage **estimado** | **PARCIAL** | heurística por tamanho+volatilidade (`tradingagents/desk/costs.py` — `slippage_estimado()`); PAPER usa `slippage_bps` fixo, default 0 (`tradingagents/execution/paper_adapter.py`) — não derivado de livro | `tests/test_desk_custos.py` |
| 1.8 | Validade temporal do dado de mercado | **PARCIAL** | velas SIM: `cutoff` = fechamento da última vela usada + `quality` degradada por atraso (`tradingagents/desk/market.py` L71–96); livro N/A (não existe) | `tests/test_desk_market.py` |
| 1.9 | Testes de dados ausentes/inconsistentes (mercado) | **PARCIAL** | velas: estados degradados cobertos; livro: N/A | `tests/test_desk_market.py`, `tests/test_desk_multiagente.py` |
| 1.10 | Capacidade declarada da corretora (`has_order_book`) | **EXISTE** | `service/personal_exchange_capabilities.py` L26–36 — flag do CCXT (`native("fetchOrderBook")`), com o aviso registrado no próprio módulo: "hints, not evidence" | `tests/test_fm3_corretoras.py` (L66 cita as 5 capacidades) |
| 1.11 | Persona "Order Book Analyst" | **PARCIAL** | `service/app.py` L781 — papel LLM declarativo "microestrutura e liquidez" **sem nenhuma fonte de livro**; é rótulo, não capacidade | teste estrutural da agência (bateria de agentes) |

**Conclusão da área:** lacuna relevante e comprovada — a mesa decide hoje **sem microestrutura**: sem livro, sem bid/ask, sem spread medido, sem profundidade, sem desequilíbrio. O que existe é estimativa determinística (`costs.py`) e ticker para sizing. Isto **não é defeito** (é a postura conservadora documentada), mas é exatamente o que a missão pede para fechar: "Se houver lacunas relevantes, implemente adaptadores de leitura por corretora homologada, com validade temporal e testes de dados ausentes ou inconsistentes."

---

## 2. Transparência das decisões

| # | Capacidade | Estado | Evidência (arquivos) | Testes |
| :- | :--- | :--- | :--- | :--- |
| 2.1 | Motivo de operar/esperar (ciclo e por trader) | **EXISTE** | `tradingagents/desk/decisao.py` — `decidir_do_ciclo()`: OPERAR/AGUARDAR/BLOQUEAR/SUSPENDER com motivo PT; `tradingagents/desk/sala_trader.py` — `marcacoes_dos_ciclos()` (abstenções/vetos com motivo); `tradingagents/desk/painel_global.py` — `_oportunidades` (`ultimo_motivo`) | `tests/test_sala_trader.py`, `tests/test_painel_global.py`, `tests/test_desk_multiagente.py` |
| 2.2 | Dados utilizados (entrada da decisão) | **PARCIAL** | a sala expõe mercado (instrumento/timeframe/cutoff/source/quality), indicadores (V8.1 §6), portfólio e champion/challenger — `sala_trader.retrato_do_trader()`; **MAS** não há trilha item-a-item "que números entraram nesta proposta" por decisão individual | `tests/test_sala_trader.py`; e2e `tests/e2e/test_panel_e2e.py` |
| 2.3 | Resultado do Maestro | **EXISTE** | `tradingagents/desk/maestro.py` — `resolver_entradas()` devolve `Decisao{sala, instrumento, agente, concedido, motivo}`; `painel_global` agrega vetos/abstenções por trader; painel `#painelGlobal` (TD-1) | `tests/test_desk_maestro.py`, `tests/test_painel_global.py`, `tests/test_qoder_maestro_traders.py` |
| 2.4 | Risco por trader | **EXISTE** | `sala_trader.indices_de_risco()` (volatilidade/Sharpe/Sortino), `exposicao_do_trader()`; limites no risco do OMS; incidentes duráveis | `tests/test_sala_trader.py`, `tests/test_paper_financial_state.py` |
| 2.5 | Custos | **PARCIAL** | estimativa determinística no ciclo (`costs.py` — `custo_de_operar`/`vale_a_pena`); **taxas REAIS** ficam no ledger (entries `FEES:`), mas a sala mostra as execuções **sem taxas por operação** (`marcacoes_de_execucao` devolve t/tipo/instrumento/quantidade/preço) | `tests/test_desk_custos.py`, `tests/test_paper_financial_state.py` |
| 2.6 | Interface da mesa/sala (onde a trilha deve aparecer) | **EXISTE** | `service/static/index.html` — Mesa de Agentes (`deskLoad`), sala do trader (`#salaTrader`: mercado/indicadores/marcações/portfólio/ordens), `#painelGlobal`; rota GET `/v1/desk/traders/{agent_id}/sala` | `tests/test_painel_render_escaping.py`, `tests/test_ia_painel_static.py`, e2e |

**Conclusão da área:** a trilha existe em **fragmentos** (motivo + Maestro + risco + custo estimado) distribuídos entre rotas já construídas; o que falta para o dono é **(a)** renderização consolidada na sala (dados já presentes no payload), **(b)** taxas reais por execução (dado já existe no ledger, não exposto), e **(c)** para a missão "dados utilizados", a sala JÁ carrega mercado+indicadores — falta apenas exibi-los como "entrada da decisão". Melhoria **sem criar sistemas paralelos**: enriquecer o caminho existente (`sala_trader` + mesma rota + mesmo HTML).

---

## 3. Observabilidade financeira

| # | Capacidade | Estado | Evidência (arquivos) | Testes |
| :- | :--- | :--- | :--- | :--- |
| 3.1 | Separação intenção → enviada → aceita → execução → posição → resultado | **EXISTE** | `tradingagents/desk/execution.py` — `OrderState` (DRAFT→VALIDATED→APPROVED→SUBMITTING→ACKNOWLEDGED→PARTIALLY_FILLED→FILLED/CANCEL_PENDING/CANCELLED/REJECTED); `submit_intent()`; `apply_fill()`; ledger de partidas dobradas com posições (`ledger.open_positions_detail`, `position_lots`) e PnL | `tests/test_desk_execucao.py`, `tests/test_paper_financial_state.py` |
| 3.2 | Reconciliação | **EXISTE** | `ExecutionCoordinator.reconciliar()` — exchange-first; ordens conferidas × divergências × limitações; órfãs; flag `seguro`; frescor por `oms_version`; `tradingagents/execution/reconciliation.py` (`reconcile_ids`/`reconcile_state`, desconhecido ⇒ inseguro) | `tests/test_reconciliation_state.py`, `tests/test_reconciliacao_adversarial.py`, `tests/test_fills_reconciliacao.py`, `tests/test_relatorio_reconciliacao.py`; bundle FM-6 (17/17) |
| 3.3 | Deduplicação / idempotência | **EXISTE** | `fingerprint()` + `IdempotencyConflict` (mesma chave, conteúdo diferente); `external_client_id` `dz23-<sha256[:20]>` (`ccxt_bridge.py`); replay idempotente `ExecutionRouter._client_orders`; dedupe por digest no journal (`router._publish`) | `tests/test_auditoria_fin3_resposta_perdida.py`, `tests/test_paper_financial_state.py` |
| 3.4 | Estado desconhecido após falha de comunicação | **EXISTE** | `OrderState.UNKNOWN_RECONCILIATION_REQUIRED` + `needs_reconciliation`; `BrokerTimeout` carrega `external_client_id`; `ESTADOS_NAO_TERMINAIS` reconferidos no restart | `tests/test_auditoria_fin3_resposta_perdida.py`, `tests/test_reconciliation_state.py` |
| 3.5 | **Latência por estágio** (intenção→envio→aceite→preenchimento) | **AUSENTE** | `OrderRecord` (`tradingagents/desk/execution.py` L93–107) **não tem nenhum campo de tempo**; `_ordem_para_doc` não persiste tempo; o único `latencia_ms` do produto está em canais de alerta (`tradingagents/desk/canais_alerta.py` L106–108, `notificacao_viva.py` L382) — não em ordens. O `ExecutionRouter` publica `order.submitted/accepted/rejected` com `occurred_at` (`tradingagents/execution/router.py` L56–68; `tradingagents/telemetry/events.py` L19), **mas o OMS da mesa não publica nesses eventos** | — (nenhum cálculo de latência de ordem) |
| 3.6 | Trilha unificada por ordem (visão única intenção→resultado) | **PARCIAL** | dados existem **fragmentados**: OMS (`snapshot_estado().ordens`), ledger (transações com `ref`=order_id e `at` — `tradingagents/desk/ledger.py` L69–77, L790/L811), journal do router (fluxos do andar). Não há UMA visão nem rota que costure os estágios de uma ordem com tempos | — |
| 3.7 | Visibilidade do ciclo de vida no painel | **PARCIAL** | `service/static/index.html` — tabela "Ordens" (id/instrumento/estado/qtd/preço, sem tempos) na Mesa; sala lista "ordens abertas" (id/instrumento/estado/qtd) | `tests/test_painel_render_escaping.py` |
| 3.8 | Prova ponta a ponta (mesa PAPER real) | **EXISTE** | FM-4/FM-6: ciclo, reinício e recuperação no exe com estado durável; aceite TD-5 13/13 + FM-6 17/17 no bundle | `docs/mission/evidence/fm4-ponta-a-ponta.md`, `v8-td5-exe/`, `fm6-app-instalado-isolado.md` |

**Conclusão da área:** a espinha financeira (estados, reconciliação, deduplicação, estado desconhecido, ledger) está **implementada e testada** — a missão "comprove" é satisfeita por citação (acima) e será consolidada no relatório final (MO-3). As lacunas REAIS são 3.5 (latência — ausente), 3.6 (trilha unificada — fragmentada) e 3.7 (UI sem tempos).

---

## 4. Avaliados e marcados NÃO NECESSÁRIO (com justificativa)

| Item do jev-trader | Estado no DZ23 | Justificativa |
| :--- | :--- | :--- |
| Motor de decisão a cada **300 ms** | **NÃO NECESSÁRIO** | A postura do DZ23 é deliberada e documentada: decisão sobre dado **fechado** (vela encerrada; `cutoff`, nunca relógio de parede — `market.py` L6–12), sem olhar-o-futuro; ciclos por timeframe + agendador. Adotar 300 ms adicionaria custo de IA e ruído sem elevar a qualidade da decisão PAPER — e mexeria no coração da mesa, que a missão manda preservar |
| Blockchain **Monad/Kuru** | **NÃO NECESSÁRIO** | Sem justificativa independente no produto (corretoras homologadas via CCXT; cripto spot/futuros — E6 `mercados.py`); integra blockchain não pedida pela missão ("nem integre … sem justificativa independente") |
| Pós-only com cancel-replace a cada bloco | **NÃO NECESSÁRIO** | PAPER é determinístico por desenho; LIVE bloqueado (`MESA_LIVE_LIBERADA=False` por política). Não se constrói motor de ordem complexa para um caminho que não pode operar |
| SSE por bloco com probabilidades | **NÃO NECESSÁRIO** | O painel usa polling nas rotas existentes; um canal de streaming seria sistema paralelo (a missão pede o contrário) |

---

## 5. Lacunas COMPROVADAS e escopo do MO-2 (só o que aumenta segurança, qualidade de execução, capacidade de avaliação ou clareza)

### MO-2-A — Adaptador de leitura de microestrutura (read-only, por corretora homologada)

- **Lacunas que fecha:** 1.2–1.6 (L2, bid/ask, spread medido, profundidade, imbalance) + amplia 1.7 (slippage **por livro** quando livro disponível) + 1.8 (validade temporal do livro).
- **Desenho (sem tocar execução):** módulo novo e PURO de análise (`tradingagents/desk/microestrutura.py`): a partir do livro bruto + instante de obtenção + idade máxima ⇒ `best_bid/best_ask/spread_bps/profundidade_lados/imbalance/custo_estimado_por_tamanho` com estados honestos: `disponivel` / `indisponivel` / `inconsistente` (bid>ask, lado vazio, níveis não-positivos) / `vencida` (mais velho que a idade máxima). Leitor no caminho existente: método `livro()` em `PublicMarketFeed` (`market.py`) via `fetch_order_book` público do CCXT — **nunca envia ordem**; corretora sem o nativo (`has_order_book` falso — `personal_exchange_capabilities.py`) ⇒ `NOT_SUPPORTED` honesto; offline ⇒ `indisponivel`, **nunca número inventado**.
- **Superfície:** rota read-only nova (GET) + bloco "Livro (L2)" na sala do trader quando a fonte for mercado real; em modo fixture: "sem livro — modo dados de treino" (honesto).
- **Testes (obrigatórios pela missão):** válido (cálculos exatos de spread/profundidade/imbalance/slippage-por-tamanho); **ausente** (fetch falha ⇒ indisponível); **inconsistente** (bid>ask, lado vazio, níveis ≤ 0 ⇒ rejeitado sem número); **vencido** (validade temporal); corretora não suportada ⇒ NOT_SUPPORTED; garantia estrutural read-only (sem chamadas de envio).

### MO-2-B — Trilha da decisão na interface (backend existente, sem sistema paralelo)

- **Lacunas que fecha:** 2.2 (dados utilizados como trilha), 2.5 (taxas por execução), consolida 2.1/2.3/2.4 na mesma visão.
- **Desenho:** na sala do trader (`index.html` / `#salaTrader`), renderizar "Trilha da decisão": dados de entrada JÁ existentes no payload (mercado/cutoff/qualidade/indicadores) → motivo JÁ existente (marcações/vetos/abstenções) → resultado do Maestro (dado da rota existente `#painelGlobal`) → risco/portfólio JÁ existentes → custos. Enriquecimento mínimo no caminho existente: `marcacoes_de_execucao` passa a devolver também **taxas reais** por execução (o dado JÁ está nas entries `FEES:` do ledger — não criar nada).
- **Testes:** unidade de `sala_trader` (taxas por execução, ausência honesta), contrato estático do HTML (padrão `test_ia_painel_static`/`test_painel_render_escaping`) e e2e da sala.

### MO-2-C — Latência por estágio e trilha unificada por ordem

- **Lacunas que fecha:** 3.5 (ausente), 3.6 (fragmentada), 3.7 (UI sem tempos).
- **Desenho:** carimbar tempo no caminho existente do OMS: `criada_em`/`atualizada_em` no `OrderRecord` (default factory; `_ordem_para_doc` persiste; `restaurar_estado` tolera ausência ⇒ compatível com estado durável atual — esquema continua `dz23.oms/2`); derivar "latência ordem→1º preenchimento" das transações do ledger (`ref`=order_id, `at`); expor na tabela "Ordens" (Mesa) e na sala: criação, preenchimento, latência. Sem rota paralela.
- **Testes:** roundtrip de persistência (o estado atual sem os campos restaura; com os campos preserva), cálculo puro de latência (incl. ordem não preenchida ⇒ honesto "sem preenchimento"), UI estática + e2e.

### Garantias preservadas (declaração vinculante)

- **Não muda:** os 10 traders, o Maestro, o ledger (partidas dobradas/multimoeda), o PAPER determinístico, os bloqueios LIVE (fail-closed), a trava `MESA_LIVE_LIBERADA`, nenhum gate financeiro.
- **Nada escreve em corretora:** MO-2-A é leitura pública de mercado (sem credencial); nenhuma ordem real; nenhuma ativação de LIVE.
- Demais itens das áreas 1–3 permanecem como estão (EXISTE registrado = não se mexe).

---

## 6. Ressalvas declaradas

1. A prova "ao vivo" do livro L2 depende de rede/conexão pública na estação; se offline, a evidência será declarada como tal (estado `indisponivel` honesto é, ele mesmo, um resultado testado).
2. A comparação com o `jev-trader` é baseada no repositório público (README/código) — não executamos o bot deles.
3. Esta matriz não reabre homologações: qualquer mudança de produto reabre o aceite no CI e será re-citada após verde (regra vigente).
4. Nenhum segredo/chave é usado, ecoado ou persistido por esta auditoria.

---

**Próximo passo (MO-2):** implementar APENAS MO-2-A/B/C com TDD, preservando tudo listado em "Garantias preservadas", e entregar relatório + testes + evidências (MO-3).

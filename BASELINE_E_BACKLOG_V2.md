# BASELINE E BACKLOG ÚNICO — DZ23 TradingAgents (retomada V2)

Branch de integração: `integracao-v2` (base `codex-v0.5.1` + camada Claude).
Data da inspeção: 12/09/2026. Suíte reproduzida nesta árvore:
**1139 passed / 2 skipped / 71 subtests**.

## 1. Estado real encontrado (não presumido)

- `main` @ 4cdd1fc = núcleo Gate A r12 + serviço com painel + broker CCXT.
  **Não continha** backtest, portfolio/sizing, memory, runtime, manifest,
  observability, execution nem worker durável.
- `codex-v0.5.1` @ 48bf8c8 = trabalho do Codex com esses módulos, Gate B/PIT
  cabeado nas fontes, `tradingagents/service/` (store/jobs/worker/api) —
  mas sem painel e com executor Alpaca (superado por decisão do dono).
- O zip enviado pelo Prado é byte-a-byte a árvore `main`; nada novo a recuperar.

**DECISÃO ASSUMIDA (integração):** base = `codex-v0.5.1` (núcleo mais
completo), sobre ela portada a camada Claude — `service/app.py`, painel,
`brokers/ccxt_bridge.py`, testes próprios e docs. Justificativa: preserva
integralmente o trabalho correto do Codex, evita motores duplicados e mantém
a decisão de corretora (CCXT) do dono. O `redact.py` ficou na versão do Codex
(import no topo, resolve o E402 anotado no Gate A) **com a correção de
folhas-string reaplicada** — vazamento provado corrigido e idempotência
verificada.

## 2. Matriz TA — verificada nesta árvore

| ID | Item | Status | Evidência |
|----|------|--------|-----------|
| TA-001 | Baseline consolidado | VERIFIED | árvores comparadas; integração em `integracao-v2`; 1031 testes |
| TA-002 | Instalação/empacotamento | VERIFIED | wheel construído e instalado em venv VAZIO com o extra `[desk]`; serviço e painel viajam dentro do pacote (provado: subiu e rodou a mesa sem copiar arquivo nenhum). Dois achados corrigidos: fastapi não era declarado e o Dockerfile subia a CLI em vez do painel |
| TA-003 | Segurança web | VERIFIED | auth Bearer, trava anti-força-bruta (5/IP ⇒ 15 min), headers, config 0600 mascarada — 16 testes |
| TA-004 | RunContext imutável | IMPLEMENTED_NOT_VERIFIED | `tradingagents/runtime.py` + 16 testes Gate B; falta ligar ao caminho do serviço |
| TA-005 | PIT / available_at | PARTIAL | `dataflows/fundamentals_pit.py` + agora `desk/market.py` com cutoff no fechamento da vela e descarte da vela em formação (13 testes); auditoria externa pendente |
| TA-006 | Banco/worker durável | **VERIFIED** | painel migrado para o store único (Postgres via DATABASE_URL, senão SQLite do mesmo contrato); claim/lease/heartbeat do Codex; provado ao vivo: idempotência e 409 em payload divergente |
| TA-007 | Decisão/intenção | IMPLEMENTED_NOT_VERIFIED | `execution/models.py`, `engine.py` |
| TA-008 | Sizing/reservas | IMPLEMENTED_NOT_VERIFIED | `portfolio/sizing.py` — 25 testes |
| TA-009 | Ordens/reconciliação | VERIFIED | máquina de 11 estados em `desk/execution.py`; timeout ⇒ UNKNOWN_RECONCILIATION_REQUIRED sem retry cego nem liberação; fill duplicado/tardio ignorado; cancelamento pedido ≠ confirmado |
| TA-010 | Ledger | VERIFIED | `desk/ledger.py` append-only em partidas dobradas; correção por compensação; atribuição por trader; saldos conferem manualmente |
| TA-011 | Backtest | IMPLEMENTED_NOT_VERIFIED | `backtest/runner.py` — 18 testes; auditoria própria pendente |
| TA-012 | Avaliação temporal | VERIFIED | `desk/evaluation.py`: walk-forward com embargo entre treino e teste, janelas de teste que nunca se sobrepõem, período curto recusado em vez de forjar dobra; agregação exige a mesma estratégia e recusa dobras contaminadas |
| TA-013 | CCXT | VERIFIED (funcional) | 9 exchanges, testnet/live com frase, gate incontornável — 32 testes + prova viva |
| TA-014 | Jornada simulada | PARTIAL | análise ponta a ponta provada; PAPER/SHADOW com 13 testes, sem jornada integrada única |
| TA-015 | Observabilidade/custo IA | IMPLEMENTED_NOT_VERIFIED | `observability.py` — 9 testes |
| TA-016 | Frontend | PARTIAL | painel com Configurações, Operação e **Mesa de Agentes** (traders, risco, ordens, contabilidade, guardião, ciclos, ligar/parar mesa autônoma); faltam telas de pesquisa/backtest e administração |
| TA-017 | Recuperação/release | VERIFIED | `desk/backup.py` com MANIFEST SHA-256 por arquivo; restauração valida TUDO antes de gravar (pacote adulterado ⇒ nada escrito), destino ocupado exige sobrescrita explícita, config volta 0600; provado ao vivo: serviço novo subiu sobre o restaurado e enxergou a chave; `RUNBOOK.md` com procedimentos e incidentes |

## 3. Matriz MA (mesa multiagente) — meta ampliada

| ID | Item | Status |
|----|------|--------|
| MA-01 | Mandatos/registry/permissões (DeskMandate, AgentSpec) | VERIFIED | `desk/models.py`: mandato com revogação/expiração/versão, estágios com transições autorizadas, ferramentas pelo backend |
| MA-02 | Snapshots/propostas | VERIFIED | snapshot imutável com cutoff e qualidade; proposta com validade, invalidação e razão — proposta não autoriza |
| MA-03 | Orquestrador por eventos | VERIFIED | `desk/scheduler.py`: laço próprio roda ciclos sozinho (provado ao vivo: 4 ciclos sem comando), estado durável em disco com escrita atômica, recuperação reconcilia ANTES de retomar, snapshot vencido nunca vira ordem atrasada, ciclo defeituoso não mata o laço |
| MA-04 | Três traders distintos | VERIFIED | tendência, reversão à média e rompimento — lógica determinística distinta, provado que leem o MESMO snapshot de formas diferentes |
| MA-05 | Arbitragem + reserva transacional | VERIFIED | allocator veta direção oposta e disputa por instrumento; RiskEngine com lock atômico: capital nunca prometido duas vezes |
| MA-06 | Execução + guardião | VERIFIED | `ExecutionCoordinator` é o único caminho à corretora (revalida posse/mandato/expiração); `PositionGuardian` adota posições de trader pausado; kill switch distingue pausar, drenar e cancelar |
| MA-07 | Reconciliação/atribuição | VERIFIED | reconciliação consulta a corretora e só então decide; resultado atribuído por trader sem dupla contagem; custo de IA lançado como custo real |
| MA-08 | Painel Mesa de Agentes | VERIFIED | aba com traders/estágios, risco, ordens, contabilidade, guardião e histórico de ciclos; botões chamam APIs reais (`/v1/desk/status|cycle|pause`), dados de fixture rotulados |
| MA-09 | Experimentos de equipe | VERIFIED | `Comparison` recusa comparar universos, capitais ou períodos diferentes; veredito nunca sai como 'campeão' seco — traz ressalvas (amostra pequena, custos não descontados, candidato único); custo muda o ranking; concordância entre agentes do mesmo modelo conta como UMA evidência |
| MA-10 | Cenários de aceite | PARTIAL | 106 testes cobrindo 01–16; faltam 15 (avaliação prospectiva separada), 17 (separação de modos ponta a ponta com corretora real), 18 (conectividade externa comprovada — bloqueada por rede neste ambiente) |

## 4. Próxima fatia (dependência real, não preferência)

**Feed de mercado real — IMPLEMENTADO, validação BLOQUEADA POR AMBIENTE**:
`desk/market.py` lê velas públicas via CCXT com cutoff no fechamento da vela
(a vela em formação é descartada), cache compartilhado, e degradação honesta
quando a fonte falha. 13 testes com dublês provam o comportamento. A
validação CONTRA A EXCHANGE REAL não foi possível daqui: a saída de rede
deste ambiente bloqueia api.binance.com, api.kraken.com, okx, bitget e
coinbase (NetworkError em todas as 5). No servidor do Prado isso deve
funcionar — e o painel agora mostra exatamente qual é o caso
(`/v1/desk/feed`, botão 📡 Testar dados de mercado, com a próxima ação).

**Executor por corretora real — IMPLEMENTADO, validação pendente de
credencial**: `desk/brokers.py` adapta a ponte CCXT ao contrato do executor
(mesmo livro de ordens, sem duplicar motor). Padrão continua PAPER: só
`TRADINGAGENTS_DESK_BROKER=ccxt` liga a corretora, e o gate de capital
segue incontornável.

**Restam apenas itens que dependem do dono ou de auditoria externa:**
- Cenário 17/18: modos separados ponta a ponta e conectividade real —
  exigem credencial de testnet e um servidor com saída de rede para as
  exchanges (bloqueada neste ambiente).
- Revisão independente da mesa (Codex), no mesmo funil do Gate A.
- Ligar o executor da mesa à CCXT em testnet com chaves reais (código
  pronto e testado com dublês).

**Achado corrigido nesta rodada (teste vivo, não unitário):** depois do fill
a reserva é consumida e a mesa esquecia a posição — o ciclo seguinte
recomprava o mesmo ativo. A exposição agora soma reservas + posições do
livro-razão; regressão coberta por teste.

## 5. Vereditos separados (nada declarado além do provado)

- IMPLEMENTAÇÃO: integração das duas árvores concluída e testada.
- VALIDAÇÃO LOCAL: 1031 testes; serviço, painel e broker provados ao vivo.
- IA/DADOS EXTERNOS: não comprovados (chaves de teste; falha de rede é o
  comportamento esperado observado).
- TESTNET: não comprovado (exige credencial real do dono).
- REVISÃO INDEPENDENTE: pendente — auditoria externa da v1.1.0 está com o
  Codex; esta integração exigirá nova rodada.
- PRODUÇÃO/LIVE: fora do escopo desta missão; capacidades preservadas sem
  acionamento.

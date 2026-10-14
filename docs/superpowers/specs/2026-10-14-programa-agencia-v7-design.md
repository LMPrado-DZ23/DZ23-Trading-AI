# Programa Agência V7 — Copiloto Manual, Exit Manager Global, Alertas, Multimercado e Evolução

> **Status:** spec de programa (missões do dono de 2026-10-13/14). Base auditada: `34a91b4`
> (`feat/integracao-mestre-20261012`; item 22 fechado em `f07ea47`). Este documento mapeia as
> **seis missões** recebidas em um programa único com fases E1–E6, e é o artefato de retomada
> após auto-pause da sessão.

**Branch:** `feat/integracao-mestre-20261012` (PR #7). Não tocar em `audit/claude-financial-security`.
O workflow Windows só builda a partir desta branch — é ela que permite homologar no bundle.
Quando o bloco do Copiloto estiver completo, os commits podem ser publicados como PR isolado
(cherry-pick) — decisão registrada no checkpoint E3.

**Invariantes (valem para TODAS as fases):**
- LIVE `BLOCKED_BY_POLICY` — nenhuma rota nova pode enviar ordem real; `live_authorized=False`.
- Copiloto Manual é **read-only estrutural** perante corretoras: o grafo de objetos do modo não
  contém broker com escrita; nenhuma rota/worker/LLM/botão/alerta chama `submit`/`create_order`.
- LLM não calcula dinheiro: P&L/stops/fees/timestamps/alertas críticos são determinísticos.
- Não alterar `config.real`, HARD caps, gestor de risco, auditoria de velas, trava de instância,
  checagem de conta demo, regras da escola/veredito/estresse. Preservar `%APPDATA%\DZ23-Trading-AI\`.
- Segredos nunca em texto simples em arquivos públicos; tokens de notificação protegidos.
- Toda fase: TDD + ruff + suítes vizinhas + checkpoint em `docs/mission/` + commit+push.

---

## 1. Estado auditado (resumo do que já existe × lacuna)

| Área | Existe | Lacuna-chave |
|---|---|---|
| 10 traders / IDs estáveis | `traders.py:664-690` | horizonte único "1h" (`traders.py:33`); sem tabela por horizonte |
| Política de saída (alvo/stop/tempo) | `policy.py:18-79` (`ExitPolicy.avaliar` → alvo/stop/tempo/mantendo) | sem trailing/parcial/break-even; sem versão; tese em prosa |
| Gestão de saída autônoma | `orchestrator.py:123-135` (avalia), `close_position` (283-320) | **saídas NUNCA são executadas** (`scheduler.py:132-147`, `app.py:2247-2251` → `encerradas=[]`) |
| Metadados por posição | `Position` (`orchestrator.py:23-33`) | sem stop/alvo/horizonte/versão de política; `book` não persiste nem reidrata (temporal morre no restart) |
| Sizing por risco + correlação | `sizing.py:68-120`, `risk.py:99-105` | budget por trader/ativo/mercado/corretora ausente; perda diária não bloqueia |
| Custos ida-e-volta | `costs.py:44-121` | ok (base para "só realizar com líquido") |
| Escola por (estrategia,versão,ativo,horizonte) | `salas.py:160-180,497-521` | certificação **por política de saída** inexistente; ciclo nunca re-avalia |
| Maestro multi-sala | `maestro.py:205-267` | sem coordenação anti-saída-duplicada global |
| Salas por corretora|conta|mercado|modo | `salas.py:120-146` | mercado é vocabulário cripto (`spot/futuros/opcoes_binarias`, L44); sem classe de ativo |
| Alertas | `alerts.py` (5 códigos + Telegram/webhook), `notifications.py` (Discord) | sem Windows nativo, inbox, fila durável/retry/dedup/rate limit, sem UI de canais |
| Modos | `ModoDaMesa` (`modo.py`), `Mode` (`models.py:23-33`) | **Copiloto Manual inexistente** (0 ocorrências) |
| Mercados | ccxt (binance default); `EXCHANGE_ALIASES` fechado (`ccxt_bridge.py:29-39`) | sem B3/US/IBKR/Alpaca; sem calendário/sessão/lote |
| Evolução | laboratório de hipóteses (BT-2), validação walk-forward (item 8) | sem Champion/Challenger por trader, sem promoção/rollback automáticos, sem classificação de causa de perda |

---

## 2. Workstreams

### E1 — Smart Position & Exit Manager Global (missões 2+3+global; task #56)
**Objetivo:** motor único de gestão de posições com decisão determinística HOLD/REDUCE/CLOSE/NO_ACTION,
executando saídas dentro do ciclo PAPER (com autorização do risco) e explicando cada decisão.

- **E1.1 registry de políticas versionadas** — novo `tradingagents/desk/saida.py`:
  `PoliticaDeSaida(versao, stop_tecnico, stop_financeiro, alvo_ref, trailing, saida_parcial,
  stop_temporal, invalidacao_tese)` + `politica_por(def_estrategia, horizonte, regime, mercado)`
  — versões imutáveis, lookup explícito; `ExitPolicy` atual vira a política "v1 clássica".
- **E1.2 política viva por posição** — estender `Position` (`orchestrator.py:23-33`) com
  `stop, alvo, horizonte, policy_versao, risk_amount, opened_at(vé já)`; persistir/reidratar no
  OMS doc (`snapshot_estado`/`restaurar_estado` `execution.py:1406-1541`) para o motor renascer
  após crash (fecha a lacuna de `book` não persistido).
- **E1.3 decisão contínua** — `avaliar_posicao(posicao, snapshot, custos, tempo) -> DecisaoDeSaida`
  com `acao ∈ {HOLD,REDUCE,CLOSE,NO_ACTION}`, `motivo_pt`, `reason_code`, `validade`, `fonte`,
  `freshness` — nunca vender só por lucro bruto positivo; só encerra se líquido (após
  `costs.ida_e_volta`) justificar; trailing só com mecanismo validado (flag por política).
- **E1.4 autonomia no ciclo** — `scheduler.run_once` consome `report.exits` e submete via
  `coord.close_position` respeitando `_guarda_do_laco` e `RiskEngine` (fecha `encerradas=[]`).
  Guardas: 1 saída por posição por ciclo; nunca vender acima da quantidade; idempotência por
  `client_order_id` derivado da posição+política (anti-duplicidade em reconexão).
- **E1.5 coordenação global** — Maestro: um único ator fecha cada instrumento por sala;
  saídas nunca conflitam com entradas do mesmo ciclo (saída vence, como hoje no supervisor).
- **E1.6 Escola** — política de saída entra no contexto de certificação (`versao` da política no
  `Desfecho`/`Sala`), com gate próprio: mecanismos novos (trailing/parcial) só ativam com
  evidência estatística (walk-forward, líquido, drawdown, expectativa) ≥ política fixa.
- **E1.7 painel** — `/v1/desk/status` ganha `posicoes[]` com entrada/stop/alvo/lucro líquido
  estimado/trailing/horizonte/tempo restante/proposta/justificativa; seção na página Mesa.
- **Testes (TDD):** gap (preço pula o stop), fill parcial, restart no meio (posição reidratada),
  reconexão (sem ordem duplicada), oversell impossível, stop nunca ampliado por agente
  (`_buy` não pode mover stop), temporal com `opened_at` restaurado, comparação estatística
  vs alvo fixo (sintética, documentada como tal).

### E2 — Centro Inteligente de Alertas (missão dos alertas; task #57)
- **E2.1 núcleo durável** — novo `tradingagents/desk/notificacao_viva.py` (nome a confirmar):
  `FilaDeAlertas` persistida no Armazem (`alertas`), `Alerta` com preço/hora/validade/fonte/
  link; `dedupe_key`; estados `novo/entregue/expirado/falhou`; retry com backoff exponencial
  (teto), rate limit por canal, timeout, auditoria de tentativas. **Alerta vencido nunca
  apresenta como oportunidade atual** (campo `validade` + marcação explícita na entrega).
- **E2.2 canais** — Telegram (principal; já em `alerts.py`), **Windows nativo** (toast via
  PowerShell/WinRT — sem dependência nova se possível), **inbox do painel** (rota + UI),
  Web Push/PWA (estrutura para Oracle; feature-flag), Discord (`notifications.py` já existe —
  ligar ao núcleo), WhatsApp oficial (opcional, só com credencial; senão documentar),
  e-mail só para relatórios (SMTP config).
- **E2.3 configurações** — seção "Configurações > Notificações": cadastro+teste de entrega por
  canal SEM terminal; matriz por trader/estratégia/corretora/ativo/prioridade; segredos com
  máscara (padrão SEGREDOS_DECLARADOS `app.py:209-216`); `POST /v1/notificacoes/testar`.
- **E2.4 segurança** — botões de Telegram só abrem detalhes/registram leitura/intenção
  (autenticadas); NENHUM botão envia ordem — teste garante que o handler de callbacks não tem
  rota de execução; modo manual nunca executa nada.
- **E2.5 tipos de alerta** — oportunidade, confirmação de entrada, mudança de tese, lucro
  parcial, condição de saída, stop, perda de conexão, incidente de risco (com os 5 códigos
  existentes de `alerts.py` como subconjunto).
- **Testes:** entrega, falha do provedor, atraso, reinício (fila sobrevive), desconexão,
  expiração (não parece atual), duplicidade (dedupe), rate limit, segredo mascarado.

### E3 — Copiloto Manual backend (missão anexada; task #58)
- **E3.1 modo** — `ModoDaMesa` ganha `COPILOTO`/`MANUAL` **ou** flag própria `copiloto_manual`
  (decisão no design da fase): sem broker de escrita no grafo; validação na rota de ciclo.
- **E3.2 modelo** — `manual_positions`, `manual_position_events`, `manual_monitoring_policies`,
  `manual_alerts`, `notification_deliveries`, `manual_trade_journal` (adaptar nomes/entidades ao
  Armazem/`storage.py`; Decimal; timestamps UTC; idempotência por `idempotency_key`; concorrência
  otimista por `version`). Eventos separados p/ entrada parcial/aumento/redução/encerramento;
  nunca usar cotação atual como substituta do preço executado; declarado × API-confirmado × estimado.
- **E3.3 monitor** — `ManualPositionMonitor` (carrega ativas, feed autorizado do mesmo
  `market.py`, exchange+receive timestamp, `feed_stale`/relógio, P&L líquido com custos
  conhecidos, distância stop/alvos/invalidação, registra snapshots, dedupe de alertas em
  reconexão, retoma com reconciliação pós-crash; intervalos configuráveis com teto seguro).
- **E3.4 API** — `/v1/manual/positions` (POST/GET), `/{id}` GET, `/{id}/events` POST,
  `/{id}/monitoring-policy` PUT, `/{id}/pause|resume` POST, `/{id}/alerts` GET,
  `/v1/manual/alerts/{id}/ack` POST, `/v1/manual/monitor/health` GET. Auth obrigatória;
  **nenhum endpoint manual executa ordem** (teste de segurança estrutural).
- **E3.5 diário** — journal com revisão por IA (usa provedores já configurados; IA não inventa
  preço/fill; falha de IA não apaga nada) + estados (MONITORING/PAUSED/FEED_STALE/
  CONNECTION_LOST/RECONCILIATION_REQUIRED/MONITORING_STOPPED) separados do estado financeiro.
- **Testes:** unit (validação, Decimal, P&L, multimoeda, parciais, stop/alvo, trailing,
  expiração de tese, dedupe), integração (persistência, feed, Telegram/Windows, reinício,
  import read-only), segurança (IDOR, replay, malformados, separação de modos, impossibilidade
  de ordem), adversariais (queda com posição aberta, cotação velha, preço inválido, encerrada
  na corretora vs local, divergência de quantidade, fills duplicados), E2E Playwright (10 passos
  do spec).

### E4 — Copiloto Manual UI + E2E (task #59)
Seção "Copiloto Manual → Minhas Operações" em `service/static/index.html` (padrão do painel:
páginas array L114, section, loader; tokens no `:root`); colunas do spec; ações do spec;
estados loading/empty/error/success/offline/feed-stale; botão "Acompanhar esta operação" na
página Análises e na pagina de operações manuais; E2E completo do aceite (10 passos).

### E5 — Evolução Autônoma de Estratégias (missão da evolução; task #60)
- Memória auditável por trader (reusa `learning.py`/`Laboratorio`): decisões, P&L líquido,
  custos, regimes; **classificação de causa de perda** (risco normal / erro de execução /
  dados inadequados / regra descalibrada / mudança de regime) — determinística por regras.
- Champion/Challenger: parâmetros de cada experimento **congelados** (assinatura), comparação
  walk-forward + fora da amostra com correção de múltiplas hipóteses (Bonferroni já em BT-2) e
  benchmarks; relatório "o que mudou, por quê, evidência".
- Promoção automática **somente** dentro de experimentos e PAPER (a cadeia da Escola já é o
  gate); rollback/suspensão por deterioração (revogação já existe — ligar ao vigia).
- Proibido: alterar código de execução/permissões/limites automaticamente. LIVE: só com
  aprovação explícita do dono. Nunca confundir taxa de acerto com rentabilidade (métricas:
  expectativa líquida, drawdown, estabilidade, custos).

### E6 — Fundação Multimercado (task #61)
- Registro de classes de ativo (cripto spot/futuros; B3 ações/FIIs/ETFs; US ações/ETFs) com
  metadados (moeda de cotação, lote, tick, sessão/calendário, taxas) — **camada de política**,
  sem prometer execução não homologada; salas ganham a classe como dimensão nova preservando as
  chaves atuais (compatibilidade por migração explícita).
- Risco adaptado: horário de sessão/feriado (bloqueio fail-closed fora do pregão), liquidação,
  moedas, lotes — tudo como política com testes; cripto 24/7 não muda.
- Avaliação escrita (doc de decisão) de **Interactive Brokers** e **Alpaca** + pesquisa das
  APIs oficiais da **B3**; nenhum adaptador de execução é ativado sem homologação explícita do dono.

---

## 3. Ordem de execução e checkpoints

1. **E1** (núcleo) → checkpoint `E1` (testes + evidência + docs). É a base do Copiloto (monitor)
   e dos alertas (eventos de saída) e da Escola (políticas).
2. **E2** (alertas) → checkpoint `E2` (canais homologados com entrega real de teste; sem
   declarar "funcionando" só porque o webhook aceitou).
3. **E3+E4** (copiloto backend+UI) → checkpoint `E3/E4` + bundle Windows re-homologado quando
   a UI entrar (harness novo por canal/página).
4. **E5** (evolução) — reusa Escola/Laboratório; paralelizável após E1.
5. **E6** (multimercado) — fundação + avaliação de corretoras; últimos adaptadores só com
   homologação.

Cada checkpoint: teste focado + ruff + suíte das áreas tocadas + evidência em
`docs/mission/evidence/` + entrada em `TRADING-CHECKPOINTS.md` + atualização de
`TRADING-MISSION-STATE.md` + commit+push. Homologação em bundle para tudo que tem UI ou ciclo
autônomo. Relatório honesto de ressalvas (nada de "100% por CI verde").

## 4. Critérios de aceite globais (por missão, verificáveis)

- **Exit Manager:** motor administra uma posição do início ao encerramento decidindo
  HOLD/REDUCE/CLOSE/NO_ACTION com justificativa auditável, sem duplicar ordens, sem vender a
  descoberto, respeitando mercado (spot/ações/opções) e sem sair do modo autorizado.
- **Copiloto:** os 10 passos do aceite (analisar → plano condicional → executar manualmente →
  registrar → acompanhar → alertas de saída → P&L → diário → reinício persistente) com **zero
  envio de ordem** pelo DZ23 no modo manual (teste estrutural).
- **Alertas:** cada canal homologado com evidência real de entrega + teste de falha/atraso/
  reinício/expiração/duplicidade; botões jamais enviam ordens.
- **Evolução:** nenhuma hipótese apresentada como superior sem prova estatística (walk-forward,
  fora da amostra, múltiplas hipóteses); promoção só em PAPER; rollback funcional.
- **Multimercado:** classes registradas com testes de política; salas/Escola não quebram;
  avaliação IBKR/Alpaca/B3 publicada; nada de execução real.

## 5. Riscos e mitigação

- **Escopo grande + sessão com auto-pause:** este doc + checkpoints por fase garantem retomada
  limpa; cada fase termina com estado verde e commitado, nunca no meio de uma refatoração.
- **Flakiness de E2E (precedente BT-10):** arbitragem determinística; testes de UI com
  comparação em caixa baixa (precedente C021); APPDATA descartável em homologação de bundle.
- **Regressão financeira:** toda mudança no caminho de dinheiro exige teste de livro
  balanceado + regressão no harness de crash (item 22) quando tocar OMS/ledger.

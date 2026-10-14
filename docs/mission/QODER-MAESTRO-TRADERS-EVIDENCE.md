# MISSÃO QODER 01 — Maestro Central e 10 Traders Especializados — EVIDÊNCIA

- **Branch de entrega:** `feat/qoder-maestro-traders` (criada de `0c3fc26`, publicada em `origin`)
- **UTC:** 2026-10-13
- **Status:** critérios de aceite fechados com prova (testes + PAPER ponta a ponta); traders aprovados × experimentais distinguidos por construção.
- **LIVE:** `BLOCKED_BY_POLICY` (inalterado — nenhuma operação com dinheiro real em nenhum ponto desta missão).

## 1. O que já existia vs. o que foi corrigido (auditoria)

Os 10 traders + `MarketRegimeSupervisor` + `default_desk` já existiam (commit `2562bd6`, integrado no C011). A auditoria encontrou violações do mandato e lacunas **fechadas** nesta missão:

| # | Achado da auditoria | Correção |
|---|---------------------|----------|
| 1 | `_desk_boot` **forçava todos os 10 traders para `PAPER_ENABLED`** no boot (promoção por existir no código — proibido: "não habilitar traders sem resultados estatísticos") | Boot por tiers: 3 homologados operam; 7 experimentais nascem `SHADOW`; override persistido do dono é respeitado |
| 2 | Não existia **gate de evidência** para habilitar um trader | `MemoriaDeDesempenho.pode_promover()` — só operações FECHADAS, líquidas de custos |
| 3 | Não havia rotas para o dono **habilitar/pausar/retomar** por trader | `POST /v1/desk/traders/{id}/promover|pausar|retomar` com semântica de estágios |
| 4 | Painel não mostrava **modelo IA, operações, motivo do NO_TRADE, estado em PT-BR** | Mesa de Agentes com 7 colunas + ações; `ultimo_no_trade` derivado do ciclo real |
| 5 | Rota de ciclo **descartava vetos/abstenções** (painel nunca veria o motivo) | `vetos`/`abstencoes` no retorno do ciclo; tetos 12/24 no scheduler (antes 6 escondia os experimentais do fim da fila) |
| 6 | Testes não fixavam **contrato** (ids, regime, timeframe, needs) por trader | `tests/test_qoder_maestro_traders.py` — contrato completo dos 10 |

## 2. Contrato dos 10 traders (fonte: `tradingagents/desk/traders.py`)

| ID (estável) | Estratégia | Tier | `needs` | Regimes favoráveis | Timeframe |
|---|---|---|---|---|---|
| `trader-trend` | `trend_following` | **homologado** | 30 | ALTA | 1h |
| `trader-reversion` | `mean_reversion` | **homologado** | 20 | LATERAL, BAIXA, VOLATIL | 1h |
| `trader-breakout` | `breakout` | **homologado** | 25 | ALTA, VOLATIL | 1h |
| `trader-scalping` | `scalping` | experimental | 10 | LATERAL, VOLATIL | 1h |
| `trader-swing` | `swing` | experimental | 30 | ALTA, LATERAL | 1h |
| `trader-momentum` | `momentum` | experimental | 20 | ALTA | 1h |
| `trader-volatility` | `volatility` | experimental | 40 | VOLATIL | 1h |
| `trader-arbitrage` | `arbitrage` | experimental | 5 | ALTA, BAIXA, LATERAL, VOLATIL, INDEFINIDO | 1h |
| `trader-statistical` | `statistical` | experimental | 20 | LATERAL, BAIXA | 1h |
| `trader-adaptive` | `adaptive_regime` | experimental | 30 | ALTA, BAIXA, LATERAL, VOLATIL, INDEFINIDO | 1h |

**Aprovados × experimentais:** os 3 que operavam antes da missão carregam `homologado = True` (selo no código, com o comentário do mandato) e sobem a `PAPER_ENABLED` no boot. Os 7 novos carregam `homologado = False`, nascem `SHADOW` e só sobem por evidência — nunca por estarem no código.

## 3. Maestro Central + Supervisor de regime (como seleciona)

- `MarketRegimeSupervisor` lê o regime POR instrumento (ALTA/BAIXA/LATERAL/VOLATIL/INDEFINIDO a partir das velas do snapshot) e monta o cardápio: cada trader propõe dentro do seu universo; o Supervisor **filtra por regime** e registra abstenção explícita por trader (`"… · Filtro do Supervisor: inativo no regime X"`).
- **Orçamento dividido** entre elegíveis (`capital / len(elegíveis)`) — um trader sozinho no regime recebe o capital da mesa (prova: 1000/6=166,67 na ALTA; 1000,00 para o scalping na LATERAL).
- **Resolução de conflito:** compra duplicada no mesmo instrumento → vence a maior confiança (momentum 0,70 > trend 0,60); empate → ordem estável da lista; reduzir sempre vence comprar (proteção).
- Nenhum caminho contorna o pipeline: trader → Supervisor → `RiskEngine` (veto absoluto) → `ExecutionCoordinator` (executor autorizado único). A IA generativa **não emite ordens** — no painel ela aparece como "contexto (veta ou reduz; nunca autoriza)" (governança por teste estrutural AST, pré-existente).

## 4. Gate de evidência (promoção só por resultado realizado)

`MemoriaDeDesempenho.pode_promover(estrategia)` em `tradingagents/desk/learning.py` — determinístico, sobre operações **FECHADAS** (desfecho realizado, líquido de custos):

1. amostra madura: ≥ `AMOSTRA_MINIMA` (12) operações fechadas;
2. `retorno_medio > 0`;
3. `taxa_acerto ≥ 45%`;
4. não constar como `deve_rebaixar`.

Falhou qualquer um → `409` na rota de promoção com o motivo em português. Promessa de backtest não promove; só desfecho real.

## 5. Rotas e persistência (o dono manda; o reboot respeita)

| Rota | Semântica |
|---|---|
| `POST /v1/desk/traders/{id}/promover` | Só com evidência (`pode_promover`); 409 com motivo quando não elegível; idempotente se já habilitado |
| `POST /v1/desk/traders/{id}/pausar` | Para de propor; posições abertas seguem sob o guardião; estágio anterior memorizado |
| `POST /v1/desk/traders/{id}/retomar` | Volta AO estágio que tinha (sem pular etapas; pausado sem histórico → `shadow`) |
| `GET /v1/desk/status` | Mesa de Agentes: `stage`, `stage_label` PT-BR, `tier`, `modelo_ia`, `evidencia` (operações/maduro/taxa/retorno/elegível/motivo), `ultimo_no_trade` |
| `POST /v1/desk/cycle` | Retorna `ordens`, `encerradas`, `descartados`, `vetos`, `abstencoes`, `erros` |

Estágios persistem no `ArmazemDeEstado` (doc `agentes` por tenant: `{agent_id: {stage, antes}}`) — promoção/pausa do dono **sobrevivem ao reinício**; sem registro salvo, vale o boot por tier.

## 6. Painel "Mesa de Agentes" (leigo decide)

Colunas: **Agente · Estratégia · Estado · Modelo IA · Operações · Último motivo de não operar · Ações**. Estado em PT-BR (`pesquisa/validado/experimental (paper)/aprovado (paper)/pausado/bloqueado`). Botões: `Pausar` em todos (fora pausado), `Retomar` em pausado, `Habilitar` **desabilitado com o motivo visível** até a evidência existir — e habilitado quando elegível. Marcadores verificados por teste de contrato do frontend.

## 7. Prova (tudo verde, sem declaração sem teste)

- **`tests/test_qoder_maestro_traders.py` — 16 testes** em 6 classes:
  - `TestContratoDosDez` — ids estáveis exatos, contrato completo (universo, timeframe, needs, regimes, versão), 3 preservados homologados;
  - `TestSupervisorDeRegime` — elegível propõe / inelegível recebe filtro explícito; conflito compra duplicada (momentum vence; trend/adaptive vetados com "exposição duplicada"); orçamento dividido (166,67 × 6; 1000,00 sozinho);
  - `TestGateDeEvidencia` — sem amostra não promove; 14 operações positivas promovem; retorno negativo reprova; acerto < 45% reprova;
  - `TestBootETiersDaMesa` — boot: 3× `aprovado (paper)` + 7× `experimental (paper)`; promover sem evidência → 409 e estado intacto; promover com evidência → habilita e **sobrevive ao reinício** (`_desk_reset`); pausar → próximo ciclo mostra "não propõe" no `ultimo_no_trade` → retomar volta ao estágio anterior; trader inexistente → 404;
  - `TestCicloPaperE2E` — ciclo completo com reinício: abstenções visíveis no status, ledger balanceado, `mode` paper, ciclos/ordens preservados, sem pendências de reconciliação;
  - `TestPainelMesa` — marcadores do painel (colunas novas, `deskTrader(`, `Habilitar`, `stage_label`, `ultimo_no_trade`).
- **Lote focado de regressão:** `161 passed in 42.16s` (`test_qoder_ten_traders`, `test_desk_multiagente`, `test_desk_scheduler`, `test_service_leigo`, `test_desk_inteligencia`, `test_desk_custos`).
- **Suíte completa do repositório:** `2196 passed, 5 skipped, 19 warnings, 71 subtests passed in 177.69s`, exit 0 — exatamente `2180 (C011) + 16 (M01)`; os 5 skips são dependências opcionais ausentes (Playwright no venv, langchain_aws, chave DeepSeek, permissões POSIX, lightgbm) — nenhum teste de trading pulado.
- **ruff:** `All checks passed!` nos 5 arquivos alterados.

## 8. Regras obrigatórias do mandato — onde cada uma mora

| Regra | Implementação |
|---|---|
| Nenhum trader envia ordem direto à corretora | Só `ExecutionCoordinator` (executor autorizado) envia; traders devolvem propostas; supervisor filtra; risco veta — teste estrutural |
| Não aumentar frequência artificialmente | Nada nesta missão toca cadência; mercados/ciclos inalterados; experimentais entram em SHADOW (provas, não ordens) |
| Não habilitar sem evidência estatística | `pode_promover` + boot por tier + 409 com motivo |
| IA generativa só por APIs externas | Camada de contexto inalterada (provider configurável no painel); taxa de chamadas com teto; IA nunca autoriza ordem |
| Nenhum modelo contorna regra financeira | Governança por teste estrutural (AST): IA não emite ordem/veto final |
| LIVE bloqueado | `BLOCKED_BY_POLICY` durante toda a missão; nenhuma credencial real usada |

## 9. Aberto (honestidade de estado)

- Os 7 experimentais acumulam evidência em PAPER (propostas em SHADOW são registradas na memória); a promoção é decisão do dono quando o gate abrir — nenhum promovido artificialmente para "ficar bonito".
- Investigação das 3 operações Binance reais com logs (item G6 aberto, herdado) segue pendente.
- Doc de governança da mesa (manual do dono) pode evoluir a partir deste documento.

*Última atualização: 2026-10-13 — fechamento da MISSÃO QODER 01 na branch `feat/qoder-maestro-traders`.*

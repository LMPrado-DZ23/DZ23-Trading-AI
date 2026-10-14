# E5 — Evolução Autônoma de Estratégias (task #60) — evidência reprodutível

Spec: `docs/superpowers/specs/2026-10-14-programa-agencia-v7-design.md` (E5, linhas 134–145).
Commit-base do checkpoint: `9c3a4e9` + este batch (commit abaixo).

## 1. O que foi entregue

### 1.1 Núcleo novo — `tradingagents/desk/evolucao.py`

- **Memória auditável por trader** (`RegistroDoDiario`): decisão, instrumento, regime,
  versão, retorno **líquido** (descontado o custo de ida-e-volta de `costs.py`), custo e
  classificação de causa; persistida no Armazem (chave `evolucao`) e em arquivo atômico.
- **Classificação determinística da causa de perda** (`classificar_causa`), por regras e
  em ordem de prioridade fixa: `erro_execucao` (desvio de execução ≥ 0,3%) →
  `dados_inadequados` → `mudanca_de_regime` → `regra_descalibrada` (memória madura com
  retorno médio negativo) → `risco_normal`. Ganho não é perda (sem classificação).
- **Champion/Challenger com parâmetros congelados**: `Experimento` frozen com `assinatura()`
  SHA-256 (id, trader, título, família + assinaturas das duas hipóteses); registrar é
  idempotente por assinatura; executar hipótese não pré-registrada é impossível; segunda
  execução só com `forcar=True` (anti p-hacking, espelho do BT-2).
- **Walk-forward fora da amostra**: janelas comuns aos dois lados (mesma régua), executor só
  vê `serie[:i+1]` (sem look-ahead); custo real descontado em cada operação; correção de
  múltiplas hipóteses por **Bonferroni** (`alpha / familia_n`); benchmark presente no veredito.
- **Métricas de rentabilidade, nunca taxa de acerto**: `MetricasDoLado` = operações,
  expectativa líquida, drawdown, estabilidade, custos (teste pina a AUSÊNCIA de taxa de acerto).
- **Promoção automática SOMENTE em PAPER**: `promover(modo≠"paper")` levanta ValueError
  ("LIVE"); exige registrado + executado + aprovado; idempotente; evento auditável com
  evidência; `ciclo()` auto-promove todos os aprovados.
- **Rollback/suspensão por deterioração ligados ao vigia**: `vigiar()` consome evidência
  nova (marcador); com versão anterior → rollback; sem → suspensão; `bloqueios()` é o
  contrato do vigia (`{agent_id: motivo}`); `reabilitar(motivo)` exige motivo e consome a
  evidência antiga (reincidência só com perda NOVA).
- **Proibido por construção**: o módulo não toca código de execução, permissões nem limites;
  nenhuma referência a `create_order`/`cancel_order`/`submit`; promoção LIVE recusada.

### 1.2 Fiação (observa, nunca governa)

- `tradingagents/desk/orchestrator.py`: `DeskOrchestrator(..., evolucao=None)`; em
  `close_position` o desfecho é montado UMA vez (mesma fonte do aprendizado) e, se
  `evolucao` existir, o diário é alimentado com custo de ida-e-volta; falha da evolução vira
  aviso auditável (`avisos_de_evolucao`, limitado a 10) e **não derruba a saída**.
- `service/app.py`: `EvolucaoAutonoma` construída no `_desk_boot` (armazem + tenant +
  memória) e passada ao orquestrador; em `_vigia_pilotos`, os bloqueios por deterioração
  são MESCLADOS aos do piloto (`setdefault` — motivo do piloto vence), dentro do try
  fail-closed já existente do vigia.

### 1.3 Contrato pinado por teste (34 testes em `tests/test_evolucao_autonoma.py`)

Classificação de perda (7), diário auditável (3), experimento champion/challenger (10,
incl. no-look-ahead: rajada no último candle não vira trade de treino), promoção/rollback/
suspensão/reabilitação/relatório (11) e **integração com o orquestrador (3)**: fechamento
alimenta o diário com retorno líquido 0,097 = 10% − 0,3% (BTC/USDT binance);
falha da evolução vira aviso sem derrubar a saída (lista limitada a 10); deterioração
suspende E bloqueia entrada no ciclo (`Maestro/piloto: ...deterioração...`) e `reabilitar`
devolve o trader ao jogo — tudo pelo caminho real do vigia do `run_cycle`.

## 2. Comandos e resultados (reproduzíveis)

```text
uv run python -m pytest tests/test_evolucao_autonoma.py -q
→ 34 passed in 0.20s

uv run python -m pytest tests/test_desk_piloto.py tests/test_desk_saidas.py \
    tests/test_desk_laboratorio.py tests/test_desk_maestro.py \
    tests/test_desk_escola.py tests/test_desk_evaluation.py -q
→ 93 passed in 0.55s

uv run ruff check .
→ All checks passed!

uv run python -m pytest tests/ -q --ignore=tests/e2e -p no:cacheprovider
→ 1 failed, 2589 passed, 4 skipped, 71 subtests passed in 255.69s (exit 0 da coleta/execução)
```

## 3. Ressalvas declaradas (honestidade)

1. **A única falha da suíte completa local é AMBIENTAL, não do E5:**
   `tests/windows/test_desktop_launcher.py::test_main_backend_real_morre_e_avisa_com_log`
   falha porque o **app INSTALADO do dono está rodando na porta 8000** nesta estação
   (PID 13800, `%LOCALAPPDATA%\DZ23-Trading-AI\DZ23-Trading-AI.exe`, desde 06:20) — a sonda
   de saúde do teste encontra o servidor REAL e conclui "backend saudável". Provado por
   `curl http://127.0.0.1:8000/healthz` → `{"status":"ok","app":"tradingagents-agency/1",...}`
   e pelo `netstat` (LISTENING PID 13800). A falha reproduz ISOLADA (não é ordem de suite) e
   o teste independe dos arquivos do E5. O app do dono **não foi tocado** (trava de
   instalação vigente). Em runner virgem do CI a porta está livre — a suíte windows
   permanece verde no CI (ciclos anteriores). Nenhum arquivo do E5 aparece nesse teste.
2. **Homologação no bundle Windows instalado deste E5 fica pendente do artefato do CI
   deste push** — não declarada. E5 não adiciona UI; a fiação nova (diário + vigia de
   deterioração) só produz efeito em execução real da mesa, que já é coberta E2E no bundle
   pelos ciclos anteriores.
3. **E5 não tem página própria no painel** (não exigida pela spec): a evidência da evolução
   é o relatório por API (`relatorio()`), consumível por painel numa iteração futura.
4. **LIVE `BLOCKED_BY_POLICY` o tempo todo** — nenhuma operação real; promoção LIVE é
   recusada por construção (ValueError) e nenhum caminho do E5 altera execução/permissões/
   limites automáticos.

## 4. Arquivos tocados neste checkpoint

- `tradingagents/desk/evolucao.py` (novo, ~560 linhas)
- `tests/test_evolucao_autonoma.py` (novo, 34 testes)
- `tradingagents/desk/orchestrator.py` (param `evolucao` + fiação em `close_position`)
- `service/app.py` (construção no boot + mescla no `_vigia_pilotos` + kwarg no orquestrador)

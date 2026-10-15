# V9 — Auditoria independente final (convergência Qoder × Claude)

**Data:** 2026-10-15 · **Executado por:** Qoder (consolidação do candidato — §2 da missão) · **Auditoria independente:** Claude (PR #13 — cobertura do HEAD final pendente; §3/§4) · **Auditoria financeira histórica:** PR #9 (governança resolvida nesta rodada).

**LIVE permanece BLOQUEADO.** Nenhuma ordem real executada, autorizada ou planejada nesta rodada. Nenhum merge, Release, tag ou deploy. Nenhuma chave API anteriormente exposta é usada.

> **Princípio final (missão V9):** conclusão documentada **não** substitui homologação independente do código final. Este documento consolida o candidato; a declaração `INDEPENDENT_AUDIT_PASSED` só cabe com os sete critérios da §9 cumpridos (tabela na seção "Encerramento").

## 1. Commit auditado

| Alvo | Branch / PR | HEAD | Observação |
| :-- | :-- | :-- | :-- |
| **Candidato (Qoder)** | `audit/v9-rc-20261015` / PR #12 | inicial `1be0e60` (CI verde, run `38789838731`); código desta rodada `31ac251` (V9C-01) + este documento (só doc/evidência) | O hash exato do HEAD final está na descrição do PR #12 (um commit não pode citar o próprio hash). |
| **Auditoria (Claude)** | `docs/auditoria-claude-v9` / PR #13 | `6467e6c` | Baseline auditado pelo Claude: `main` @ `2b7754a` — **não** é o candidato (problema central da missão §1). Reauditoria do HEAD do PR #12 pendente (externa; §4). |
| **Auditoria financeira histórica** | `audit/claude-financial-security` / PR #9 | `2f43c36` | Achados classificados um a um (governança) e PR encerrado **sem merge** — ver "Encerramento". |
| **Integração (V9 + documentação Claude)** | `integration/v9-docs-auditoria` | `83030f5` → `Y` (após cherry-picks desta rodada, run `B`) | Prova o CI dos documentos do PR #13 sobre o HEAD V9, sem tocar o branch do Claude. Run de referência: `38817442500` verde (8 jobs). |

Documentos canônicos: [V9-BASELINE.md](V9-BASELINE.md) (achados classificados antes das correções + histórico de fechamento) e [V9-MATRIZ-EVIDENCIAS.md](V9-MATRIZ-EVIDENCIAS.md) (rastreabilidade F0–F8 e entregáveis A–L). Evidências: `docs/mission/evidence/v9-baseline/` (congelado) e `docs/mission/evidence/v9-convergencia/` (desta rodada de convergência).

## 2. Matriz de achados — consolidação das 11 áreas (§2 da missão)

Estado no HEAD do candidato nas 11 áreas exigidas; toda linha aponta evidência executada (a matriz única tem o detalhe por achado, ciclo RED→GREEN→regressão e digests).

| Área | Achados | Estado | Evidência (executada) |
| :-- | :-- | :-- | :-- |
| 1. Ledger e reconciliação | FIN-03, FIN-04 | **GREEN** | Suíte FIN `v9-fin-6.log` 286 passaram; conferência de caixa do reconciliador RED 17 → GREEN 17 + regressão 330 (`v9-fin04-caixa-*`); reauditoria no HEAD `85af9ca`: `v9-sec-fin-reauditoria.log` **69 passaram** (autorização + posse de dois processos + regressões financeiras) |
| 2. Saldos, caixa, taxas e reservas | FIN-04, FIN-09, FIN-12, V9-RESERVA-01 | **GREEN** | Taxas multi-moeda por componente com moeda própria; taxa de entrada no freio (`v9-fin09-*`); moeda funcional recusada + quitação FIFO do passivo da taxa (`v9-fin12-*`); reserva vencida sem vínculo expira no sweep com auditoria (`68eabda`, 7 testes novos) |
| 3. Fills parciais e totais | FIN-03 | **GREEN** | Concordância acumulada de execuções; `fills_unresolved` sobrevive a reinício (23 testes FIN adversariais; `v9-fin-6.log`) |
| 4. Proteções de exposição e perda | FIN-07, FIN-09 | **GREEN** | Exposição **projetada** (`gross_exposure + qty·entrada`, só no lado que abre risco); freio de perda com taxa de entrada; kill-switch e tetos com testes próprios |
| 5. Smart Exit Manager | FIN-11 | **GREEN** | Afrouxar proteção é recusado por construção (stop mais largo, alvo mais distante, prazo maior, trailing removido → `ValueError`); auditoria adversarial do Claude: **VERIFICADO** (4 recusas + aperto OK) |
| 6. Ordens incertas após timeout | FIN-05, FIN-06 | **GREEN** | Scheduler marca FILLED imediatamente; `submit_intent`/ACK/reserva; resposta perdida re-levanta sem `submit_ok` e o gate de capital barra estado vazio (`TestTimeoutAmbiguoNaoLiberaCapital` — FIN-3 do Claude **CONFIRMADO coberto**) |
| 7. Idempotência | FIN-06 | **GREEN** | `clientOrderId` determinístico (difere por lado); tentativa jornalizada **antes** da rede; reexecução de idempotência/crash-restart no HEAD |
| 8. Posse exclusiva de execução | FIN-08, G2 | **GREEN** | Posse entre dois processos (backend arquivo local) + caminho Postgres exercitado no CI (job `postgres` do run `38817442500`); dono único fail-closed e invariável |
| 9. Recuperação após crash | FIN-08 | **GREEN** | WAL síncrono antes do envio: crash após o aceite externo não perde a identidade da ordem; restauração reencontra a ordem e o portão de reconciliação bloqueia risco novo |
| 10. Autorização de cada endpoint financeiro | SEC-01..SEC-08, F7/F8 | **GREEN** | Suíte de autorização V9 (21 testes) + suíte adversarial; 146 rotas inventariadas, 121 com `Depends(...)`; rotas sensíveis + `/readyz` + `/metrics` com teste HTTP direto (RED 3 → GREEN 14, `f8-rotas-*`); SaaS morto responde 404 pelo `bloqueio_single_owner`; webhooks com HMAC fail-closed |
| 11. Bloqueio de todos os caminhos LIVE | REAL-01, SEC-08, FASE 2 | **GREEN (bloqueado)** | `live_authorized=False` barra em 3 pontos + `MESA_LIVE_LIBERADA=False` + tetos de capital independentes + rota legada `/v1/broker/order` → 410 + MT5 fail-closed; auditoria adversarial do Claude (FASE 2): nenhum botão/env/config/HTTP libera LIVE. **CONFIRMADO** |

**Falha local registrada na matriz (3387 + 1) — reproduzida e explicada.** A suíte completa desta rodada (`v9c-fullsuite.log`) registra **3397 passaram, 41 puladas, 71 subtests, 1 falha**: `tests/windows/test_notificacoes_identidade_win.py::test_identidade_registrada_no_windows`. Causa provada: o executor hermético bloqueia escrita no Registro do Windows **por desenho** (fail-closed da auditoria); a contraprova está em `evidence/v9-convergencia/` — sem o executor o mesmo teste passa (5 passaram, exit 0) e com o executor falha (1 falhou/4 passaram, exit 1). Nenhum teste foi enfraquecido: os asserts permanecem intactos e pinos novos foram **acrescentados**.

**Correções desta rodada (resumo executivo).** (a) **V9C-01** (`31ac251`): o veredito de validação culpava "os custos" mesmo quando a reprovação era por amostra pequena — agora cita as ressalvas reais (achado cosmético levantado pelo Claude; convergência dos dois auditores). (b) **V9-SEC-03 2ª metade** (`4c0675c`): o pacote de backup nasce `0600` e nunca é alargado. (c) **V9-TEST-01/02** (`4c0675c`): pinos executáveis do executor hermético de auditoria. Detalhe com números e evidências na §3.

## 3. Correções e regressões (ciclos RED→GREEN→regressão)

| Correção | RED (evidência) | GREEN (evidência) | Regressão / suíte |
| :-- | :-- | :-- | :-- |
| **V9C-01** — veredito cita as ressalvas reais (`tradingagents/desk/validation.py`) | `v9c-validador-red.log`: **3 falharam / 5 passaram** (asserts exigem "amostra pequena" e "resultado líquido não positivo", proíbem "comeram") | `v9c-validador-green.log`: **8 passaram** | `v9c-validador-regressao.log`: **78 passaram** (validação + vizinhos) |
| **V9-SEC-03 2ª metade** — pacote de backup `0600` por nascença (`tradingagents/desk/backup.py`; `os.open(..., 0o600)` + `fchmod`; regra na docstring) | NTFS: `v9-sec03-red.log` **1 falhou determinística / 7 passaram / 3 puladas**; Linux real (docker): `v9-sec03-docker-red.log` **3 falhas** (`+644 vs 0600`) | `v9-sec03-green.log` **8 passaram / 3 puladas**; docker `v9-sec03-docker-green.log` **11 passaram** (suíte inteira de backup em Linux) | `v9-regressao-vizinha.log` **103 passaram / 4 puladas** (backup + caminho único + fills/reconciliação + credenciais do dono + paper) |
| **V9-TEST-01/02** — executor de auditoria com pinos executáveis (`tests/test_v9_executor_hermetico.py`) | `v9-test01-red.log`: o controle sem executor **reproduz o vazamento do canário** (1 falha determinística — a reprodução original permanece viva) | `v9-test01-green.log`: **3 passaram** (gancho precede `import`; filho sem canário; fail-closed; Registro bloqueado; invocação só com NOMES de variáveis) | Rodado com a suíte completa (§acima) |
| **Reauditoria no HEAD** (`85af9ca`) | — | `v9-sec-fin-reauditoria.log`: **69 passaram** | `v9-regressao-vizinha.log` 103/4 + suíte completa |

**Suíte completa do fechamento** (`v9c-fullsuite.log`, invocação em `v9c-fullsuite-invocation.json`): **3397 passaram, 41 puladas, 71 subtests, 1 falha** — a única falha é a ambiental com contraprova (`v9c-winreg-plain.log` / `v9c-winreg-sandbox.log`). A contagem subiu de 3387 (fechamento anterior) para 3397 porque a rodada acrescentou pinos: +7 (V9-RESERVA-01, `68eabda`) e +3 (V9C-01, `31ac251`). `ruff check .` (0.16.9): **All checks passed!** (`v9-ruff-final.log`).

**Nuance de registro:** as evidências `v9c-*` declaram `head=1be0e60` porque as corridas precedem o commit da correção (a árvore testada é idêntica ao commit `31ac251`; o commit contém exatamente o diff testado). O mesmo padrão vale para as corridas do fechamento anterior.

## 4. Comparação entre Qoder e Claude

**Baseline do Claude:** `main` @ `2b7754a` — suíte local dele **3124 passaram / 7 puladas / 0 falhas**, Ruff limpo, CI do HEAD verde, sem segredos commitados, sem padrões perigosos. O parecer dele (apta a PAPER/TESTNET/COPILOTO como RC auditável; LIVE não autorizado) **não** cobre o candidato V9 — por isso esta rodada (§1 da missão).

**Achados do Claude × estado no candidato:**

| Achado (Claude) | Classificação dele na `main` | Estado no candidato V9 |
| :-- | :-- | :-- |
| **SEC-1** (chaves em texto claro) | CONFIRMADO corrigido (DPAPI) | Coerente; o V9 ainda endurece o pacote de backup para `0600` (SEC-03 2ª metade) |
| **FIN-1** (docstring dizia LIVE "removido") | CONFIRMADO corrigido | Não reproduzível no HEAD V9; docstring honesta ("existe e é FECHADO por construção") |
| **FIN-2** (gates de autorização) | CONFIRMADO coberto | Não reproduzível como defeito; 3 gates + invariantes pinados |
| **FIN-3** (resposta perdida → dedup/estado) | CONFIRMADO coberto | Não reproduzível como defeito; pinos FIN-05/06 + `TestTimeoutAmbiguoNaoLiberaCapital` |
| **ARCH-1** (ledger multiprocess) | RISCO — limitação documentada | Mantido como limitação declarada (imposição forte só com `UNIQUE` em Postgres) — §8 |
| **AUTHZ-1** (inventário rota-a-rota) | NÃO REPRODUZIDO como defeito | Inventário produzido (146 rotas; 121 com `Depends`) na F7 |
| **Achado cosmético** (resumo do veredito culpa custos) | Apontado na FASE 3 dele | **Corrigido nesta rodada — V9C-01** (`31ac251`), ciclo RED→GREEN com evidência |

**O que falta (e é externo a esta estação):** o Claude **reauditar o HEAD exato do candidato** (missão §3 — FIN-03 a FIN-12, reservas, segurança, autenticação/autorização e bloqueio LIVE) e **atualizar o PR #13 com o novo SHA**. As instruções operacionais (rebase sobre o candidato/integração + o run vermelho do PR #13 está resolvido pelo V9-CI-02, §5) estão no comentário de convergência postado no PR #13 nesta rodada. **Enquanto isso não acontecer, nenhuma conclusão anterior do Claude vale como aprovação da V9.**

## 5. Resultados de CI

| Run (ID) | Alvo / evento | SHA | Resultado |
| :-- | :-- | :-- | :-- |
| `38660769209` | `main`, push (baseline do Claude) | `2b7754a` | **Falha** nos 4 jobs `tests (py3.10–3.13)`: `test_recolar_o_segredo_recupera_o_cofre` (1 falhou / 3119 passaram por versão) — defeito pré-V9, corrigido pelo V9-CI-02 (§RCA abaixo) |
| `38789838731` | PR #12 candidato, `pull_request` | `1be0e60` | **Verde** (matriz completa) |
| `38814627826` | PR #13 (Claude), `pull_request` | `6467e6c` | **Falha** nos mesmos 4 jobs, **mesmo teste** — o branch do PR #13 está sobre a base pré-correção; os demais jobs (ruff, postgres, deps, SBOM, ARM64) verdes |
| `38817442500` | `integration/v9-docs-auditoria`, `workflow_dispatch` | `83030f5` | **Verde, 8/8 jobs** (ruff estrito, smoke público, clean-install, secrets/licenças/SBOM, ARM64, postgres, dep audit, tests py3.12) — prova que a documentação do PR #13 roda verde sobre o HEAD V9 |
| `38781679788` | Windows Release, primeiro build pós-`c443c28` | `4c0675c` | Aceite-bundle falhou em `test_fm6_03c` — divergência do harness do CI (boot sem pinar `ALERTA_WINDOWS`), **sem defeito de produto**; corrigido por `c443c28` |
| `38783887786` | Windows Release (re-execução) | `c443c28` | **Verde — aceite-bundle `20 passed`** (artefato com capturas TD5/FM6, pytest e launcher.log) |
| `38781684667` | Matriz do Instalador Windows | `4c0675c` | **Verde** (8 cenários, incl. upgrade sem perda) |
| `38781483003` + `38776664256` | CI da branch V9 (dispatch) | `4c0675c` / `85af9ca` | **Verdes** |

**RCA do vermelho do PR #13 (missão §4) — defeito, não ambiente.** Teste: `tests/test_auditoria_sec1_segredos_em_repouso.py::TestMigracaoDoConfigLegado::test_recolar_o_segredo_recupera_o_cofre`; linha 186 do traceback: `assert "ALERTA_TELEGRAM_TOKEN" not in d["segredos_em_repouso"]["ilegiveis"]`. Causa-raiz: em `service/app.py`, `_segredos_do_disco` só removia o diagnóstico obsoleto ao **decifrar** um valor cifrado; em POSIX/Linux o valor recém-colado fica em claro (protegido por permissões) e o diagnóstico antigo permanecia — falso "ilegível". Reproduzido em **Linux real** (contêiner Debian com a árvore do baseline) e corrigido no candidato (**V9-CI-02**: limpa o diagnóstico no ramo ausente/em-claro, preservando o registro quando a leitura falha de verdade; regressão portátil parametrizada para substituição e exclusão). **Consequência para o PR #13:** rebase sobre o HEAD do candidato (ou cherry-pick) resolve — o mesmo teste passa no run `38817442500`. O run do HEAD final desta rodada (X) e o run `B` da integração são registrados na descrição do PR #12 e no comentário do PR #13 (fora do commit, para o HEAD verificado não mudar após a verificação). **Nenhum CI é disparado enquanto houver run em voo no mesmo ref.**

## 6. Evidências Windows

- **Runner hermético de auditoria** (`tools/v9_pytest.py`): ambiente filho por allowlist, diretórios isolados, `PYTHON_DOTENV_DISABLED=1` antes de qualquer import, bloqueio de escrita no Registro e de rede não-loopback. Pins executáveis em `tests/test_v9_executor_hermetico.py` (V9-TEST-01/02).
- **Contraprova da falha local** (agora em `evidence/v9-convergencia/`): `v9c-winreg-plain.log` — sem o executor, `tests/windows/test_notificacoes_identidade_win.py` **5 passaram** (exit 0); `v9c-winreg-sandbox.log` — com o executor, **1 falhou / 4 passaram** (exit 1, bloqueio de `winreg` por desenho). A falha única da suíte completa é, portanto, ambiental **comprovada** — não enfraquecemos o teste nem o runner.
- **Suíte completa local:** 3397 passaram / 41 puladas / 71 subtests / 1 falha ambiental (acima).
- **E2E na fonte (app real + navegador real):** `v9-e2e-final.log` **31 passaram / 0 falhas** (CONECTAR→MODO→PLAY, painel, sala, dez traders, copiloto, alertas, PWA).
- **Aceite no executável instalado** (ambiente CI Windows virgem; instalação do proprietário intocada): run `38783887786` @ `c443c28` — **bundle `20 passed`**.
- **Matriz do Instalador** run `38781684667` @ `4c0675c`: 8 cenários completos (instalação, upgrade sem perda, reparo, cancelar, downgrade, processo ativo). Registro consolidado em `evidence/v9-baseline/win-release-runs-2026-10-15.json`.
- **UI-01** (falha silenciosa no painel da Escola): corrigida com toast visível + retenção/retentativa (testada no E2E e no bundle).

## 7. Estado de integrações externas

Classificação vinculante em `evidence/v9-baseline/f5-classificacao-conectores.md`; **mock não é apresentado como integração real**:

| Integração | Nível de prova | Bloqueio/pendência |
| :-- | :-- | :-- |
| **Binance dados públicos (leitura)** | **HOMOLOGADO no serviço real** (velas 1h BTC/USDT qualidade `ok` + livro L2 20 níveis; sem credencial, sem ordens) | — |
| **Yahoo Finance** | **HOMOLOGADO** via dataflow do produto (`get_YFin_data_online("AAPL")`) | — |
| **Provedores de IA** | **HOMOLOGADO pelo caminho do produto** (`testar_provedor`, GET /models, zero tokens): DeepSeek/Groq/Mistral/OpenRouter/Ollama 200; nunca grava valor, só status | — |
| **CCXT testnet (leitura)** | Exercitada; **ordens apenas com mock** | Ordens testnet reais = credencial do dono (G4/G5) |
| **Saldo/posição autenticados (REAL read-only)** | Checklist fail-closed (permissão somente-leitura + identidade da conta exigidas); consulta autenticada | **DEPENDÊNCIA EXTERNA** — chave do dono, somente leitura (G4/G5) |
| **Telegram / Windows Notifications / Web Push** | Implementados e testados **com mock** (transporte injetado; webhook com header secreto) | Entrega real: token/dispositivo/infra do dono |
| **FRED / Alpha Vantage / Polymarket / IQ Option** | Adaptadores com bloqueio preciso | Credencial/infra externas |

## 8. Riscos residuais

1. **Vantagem financeira não comprovada** — §9 (nenhuma estratégia certificada).
2. **ARCH-1 (ledger multiprocess):** dedup in-process + caminho Postgres; imposição forte multiprocess só com `UNIQUE` em Postgres — **limitação arquitetural declarada** (não-defeito).
3. **DPAPI:** protege contra outros usuários da máquina, não contra o próprio usuário; portabilidade entre máquinas exige re-colar segredos (documentado).
4. **Homologação de ponta a ponta com serviços reais** (IA/corretora autenticada, dispositivos físicos, VPS) — dependências externas descritas nas §7/§10.
5. **Falha única local** (winreg) — ambiental comprovada com contraprova; permanece registrada, nunca ocultada.
6. **Histórico do gitleaks** com fixtures falsos — triado e pinado (`.gitleaksignore`; nenhuma credencial real encontrada).
7. **Limites de P&L por desenho** (F1.2) e observações secundárias de slippage **sem defeito demonstrável** (F1.1/J) — revisitar apenas com evidência nova.
8. **G6 (3 operações Binance nos logs históricos), sleep/wake, offline/reconexão, crash abrupto na estação real** — pendências de sessão com o dono.

## 9. Estado de estratégias (§6 da missão)

**Nenhuma estratégia da Escola Central é classificada como capaz de ganhos consistentes.** Provas de software verdes **não** certificam vantagem — exigência por evidência distinta:

| Evidência exigida | Estado |
| :-- | :-- |
| Backtest **sem vazamento** (lookahead fail-closed; decisão na vela i só vê o passado) | Implementado e pinado (QUANT-04) |
| Backtest que **não aprova por positivo** (reprova amostra < 20; aviso "não prevê o futuro"; Ressalvas obrigatórias) | Implementado (veredito honesto; V9C-01 tornou a **mensagem** correta) |
| **Walk-forward** com treino/embargo explícitos | Mecânica implementada (QUANT-04); rodada completa em dados reais: **pendente** |
| **Out-of-sample** por estratégia | **Pendente** |
| **PAPER com preços reais ao longo do tempo** | Mecanismo existe (feed Binance homologado); série longa: **pendente** |
| **Custos e slippage** explícitos (taxa+spread+slippage ida-e-volta) | Implementado; divergência de modelo de taxas medida com fonte primária e pinada (QUANT-07 / FQ-2) |
| **Drawdown** | Métricas existem na sala/dashboard; limite formal por estratégia: pendente com o dono |
| **Expectativa líquida** | Só o veredito honesto do próprio sistema; sem promessa |
| **Benchmark e robustez entre regimes** | **Pendentes** |

Comparação independente Freqtrade: **rodada 1 executada** (motor de saída equivalente: 66×66 trades, 0 divergências; P&L ≤ 1e-6 USDT); comparação de **estratégia completa** (portar a entrada) permanece pendente. **Estratégias sem amostra suficiente permanecem não certificadas** — nada aqui autoriza capital.

## 10. Pendências para homologação mobile/Oracle

- **PWA (F6.1):** manifest/service worker/cache seguro/backups executados (`f6-pwa.log`, 45 passaram); HTTPS em degradação honesta; reconexão testada.
- **Aparelho físico (F6.2):** Android/iPhone **não fornecidos** — instalação e push em segundo plano **não homologados** (emulador não homologa hardware; regra da missão).
- **Oracle (F6.4):** build **ARM64 local OK** (`f6-arm64-build.log`, imagem `arch=arm64`); VPS Oracle **não provisionada** — deploy/HTTPS/DNS reais aguardam infraestrutura autorizada do dono (`DEPLOY.md` + `ops/Caddyfile.example` prontos).

## 11. Critério objetivo de aprovação PAPER

Aprovação para **operar PAPER** (conta simulada) exige **todos**:

1. **CI do HEAD final verde** na matriz completa (3.10–3.13 + ruff + postgres + deps/SBOM + ARM64) e na cadeia Windows (build + aceite-bundle).
2. **Suíte local no runner hermético sem falhas não-ambientais**; qualquer falha ambiental com contraprova registrada (padrão desta rodada).
3. **Operação PAPER com preços reais** (feed público Binance homologado) e **amostra ≥ 20 operações por estratégia** — o critério de conclusividade do próprio veredito — com **ressalvas vazias** (amostra suficiente; custos ≤ metade do resultado bruto).
4. **Retorno líquido positivo** no período com custos completos (taxa+spread+slippage) e **drawdown dentro do limite declarado pelo dono**; registros no diário de operações.
5. **Zero P0/P1 aberto** no escopo homologado; **nenhuma chave com permissão de trading** em uso (REAL permanece somente-leitura).
6. **Evidências por rodada** (logs/invocações herméticas), com **nenhum mock apresentado como integração real**.

Aprovar PAPER **não** autoriza LIVE, não certifica ganhos consistentes e não substitui a homologação independente.

## 12. Por que LIVE continua bloqueado

LIVE segue **impossível por construção** — múltiplas camadas independentes, todas fail-closed:

1. **Política:** `MESA_LIVE_LIBERADA=False` (padrão imutável da mesa).
2. **Código:** `live_authorized=True` exigido em **3 pontos** (mandato `models.py`, corretora `brokers.py`, execução `execution.py`) — env/config **não** contorna.
3. **Frase do dono:** `EU AUTORIZO OPERAR COM DINHEIRO REAL` exigida explicitamente.
4. **Tetos independentes:** `LIVE_MAX_ORDER_VALUE` e `LIVE_MAX_DAILY_LOSS` válidos e finitos.
5. **Rotas legadas:** `/v1/broker/order` → **410** (removida).
6. **MT5:** gates fail-closed (V9-SEC-08).
7. **Capital:** o R$ 1.000 registrado é **possível piloto futuro** — não é autorização; as 4 condições do registro seguem pendentes (`REGISTRO-CAPITAL-PILOTO.md`).
8. **Chaves expostas anteriormente: não usar** (rotação recomendada; nada foi ecoado/persistido).
9. **Auditoria independente ainda não concluída** (Claude sobre o HEAD final) — pelo princípio final, LIVE não se discute antes disso.

## Encerramento (§9 da missão) — estado dos 7 critérios

| Critério para `INDEPENDENT_AUDIT_PASSED` | Estado |
| :-- | :-- |
| 1. Auditoria do Claude cobre o **HEAD final** do Qoder | **PENDENTE (externa)** — instruções postadas no PR #13; requer rebase + reauditoria do Claude |
| 2. CI do candidato **e** da auditoria verdes | Candidato: em verificação no run do HEAD final (X, descrição do PR #12); referência verde em `38817442500` (integração) e `38789838731` (HEAD inicial). Auditoria: PR #13 depende do rebase (externo) |
| 3. Falhas locais relevantes explicadas ou corrigidas | **CUMPRIDO** — winreg ambiental com contraprova; V9-CI-02 corrigido com reprodução em Linux real |
| 4. Sem defeitos P0/P1 sem tratamento no escopo | **CUMPRIDO** — nenhum P0/P1 aberto; ARCH-1 residual declarado como limitação |
| 5. Evidências financeiras reproduzíveis | **CUMPRIDO** — ciclos RED→GREEN→regressão com invocações herméticas; reauditoria 69 passaram |
| 6. Dependências externas pendentes descritas | **CUMPRIDO** — §7/§10 + PRs |
| 7. LIVE permanece bloqueado | **CUMPRIDO** — §12 |

**Decisão: `INDEPENDENT_AUDIT_PASSED` NÃO é declarado nesta rodada.** 5/7 critérios cumpridos; restam a cobertura do Claude sobre o HEAD final (externa, item 1) e os runs finais de CI (item 2 — candidato em execução pós-publicação; auditoria aguarda o rebase do PR #13). **Conclusão documentada não substitui homologação independente do código final.**

**Governança do PR #9:** achados classificados um a um (FIN-1 **CORRIGIDO**; FIN-2 **CORRIGIDO**; SEC-1 **SUBSTITUÍDO** — a solução histórica Fernet/SecretStore deu lugar ao DPAPI do produto; ARCH-1 **AINDA ABERTO** como limitação documentada; AUTHZ-1 **CORRIGIDO**; blockers F3–F6/K1–K6 **CORRIGIDOS/cobertos** com provas equivalentes no candidato) — matriz completa no comentário de governança do PR #9. **Encerrado sem merge** (merge reintroduziria caminho de segredo conflitante e documentação desatualizada); reabertura é trivial se o proprietário discordar.

# DZ23 Trading AI — Auditoria Independente (Claude) — Missão V9

- **Auditor:** Claude (independente). **Executor:** Qoder (concluiu sua parte).
- **Baseline auditado:** `main` @ `2b7754a94cc1e4233ad95ac6fa377be4078e5055`.
- **Data:** 2026-10-15 (UTC). **Ambiente:** worktree + venv isoladas (não altera a main).
- **LIVE:** permanece BLOQUEADO. Nenhuma ordem real executada nesta auditoria.

## Veredito do auditor

A base técnica da `main` está **sólida e coerente** para uso pessoal em
**PAPER / TESTNET / COPILOTO MANUAL**, como **Release Candidate auditável**.
Os defeitos P0 que eu havia levantado foram **corrigidos na main pelo Qoder** e
**re-verificados por mim no HEAD atual**. O que falta para "100% pronto para
operar" **não são defeitos** — são homologações que dependem de recursos seus
(credenciais, certificado, autorização de release, aparelhos reais). Não
declaro prontidão financeira nem LIVE.

## Evidência de base (reexecutada no HEAD atual)

- **Suíte completa:** `3124 passed, 7 skipped, 0 failed` (352 s). Skips são
  ambientais: Playwright/langchain_aws/lightgbm não instalados localmente
  (rodam no CI), perms POSIX no Windows, caminho Postgres sem DATABASE_URL.
- **Ruff (repo inteiro):** limpo.
- **CI do HEAD:** verde (build, clean-install, postgres, ARM64, public smoke,
  dependency audit, secret scanning).
- **Sem segredos commitados** (`.env`/`.db`/`.key`/chaves `sk-`/`AKIA`/`ghp_`).
- **Sem padrões perigosos** (`eval`/`os.system`/`shell=True`/`verify=False`/
  `pickle`/`yaml.load` inseguro).

## Matriz de achados (re-verificados na `main`)

| ID | Achado original | Situação na `main` | Classificação | Evidência |
| :-- | :-- | :-- | :-- | :-- |
| **SEC-1** | Chaves de API em texto claro no `config.json` | **CORRIGIDO** pelo Qoder: `service/api/segredos_em_repouso.py` cifra com **DPAPI do usuário** (ctypes, sem dep nova), verificação de ida-e-volta ANTES de gravar, nunca apaga valor ilegível; POSIX mantém 0600 com status honesto; limitação documentada (protege contra outros usuários, não contra o próprio) | **CONFIRMADO (corrigido)** | `tests/test_auditoria_sec1_segredos_em_repouso.py` 15/15; leitura do módulo |
| **FIN-1** | Docstring do CCXTBridge dizia que LIVE foi "removido" (falso) | **CORRIGIDO**: docstring agora diz "existe e é FECHADO por construção, não removido" + camada extra de código | **CONFIRMADO (corrigido)** | leitura `ccxt_bridge.py` 1-20 |
| **FIN-2** | Gates de autorização financeira provados separadamente | **ROBUSTO**: `live_authorized=False` barra em 3 pontos (mandato `models.py`, corretora `brokers.py:337`, execução `execution.py:1331`) + `MESA_LIVE_LIBERADA=False` + gate de capital independente | **CONFIRMADO (coberto)** | checagem inline + grep; `test_auditoria_blockers.py`, `test_kill_switch*` |
| **FIN-3** | Resposta de rede perdida → dedup e estado desconhecido | **VERIFICADO inline na main**: `clientOrderId` determinístico (difere por lado), tentativa jornalizada ANTES da rede, resposta perdida re-levanta sem `submit_ok`, gate de capital barra estado vazio | **CONFIRMADO (coberto)** | checagem adversarial inline + `TestTimeoutAmbiguoNaoLiberaCapital` |
| **ARCH-1** | Ledger multiprocess | Rejeição de componentes duplicados in-process (`ledger.py`) + caminho Postgres; multiprocess puro segue limitação arquitetural declarada | **RISCO (limitação documentada)** | `docs/finalization/BLOCKERS.md`; `ledger.py:444-449` |
| **AUTHZ-1** | Inventário de autorização rota-a-rota | **ADEQUADO**: 146 rotas; sensíveis com `Depends(auth)`; superfície SaaS respondendo **404** pelo middleware `bloqueio_single_owner` (PR #8 integrado); webhooks (Telegram/Woovi) autenticados por segredo com `hmac.compare_digest`, fail-closed; demais sem auth são estáticos/health/PWA/login-do-dono e `/publico` opt-in | **NÃO REPRODUZIDO como defeito** | enumeração de rotas + leitura dos webhooks e do middleware |

> Observação sobre o **PR #9**: ele contém a correção HISTÓRICA do SEC-1 (minha,
> com `SecretStore`/Fernet) e as provas adversariais FIN-2/FIN-3. Como a `main`
> já corrige SEC-1 por outro caminho (DPAPI), **o PR #9 NÃO deve ser mesclado**
> (seguindo a diretriz da V9) — seu valor agora é histórico/comparativo. As
> provas adversariais equivalentes já existem na main.

## Segurança (resumo)

- Dono único **fail-closed** e **invariável** (flag não reabre) — integrado e provado.
- Segredos cifrados em repouso (DPAPI) com migração sem perda de credencial.
- LIVE bloqueado em profundidade (política + 3 gates de código + frase do dono).
- Governança quant **estrutural**: conselheiro que exponha método de comando é
  rejeitado na construção ("conselho, nunca comando").
- Webhooks autenticados; nenhum segredo em log/PR.

## O que falta para "100% pronto para operar" (NÃO são defeitos)

| Item | Natureza | Depende de |
| :-- | :-- | :-- |
| **G4/G5 — homologação com credenciais reais** (IA + corretora em leitura/testnet) | DEPENDÊNCIA EXTERNA | você fornecer chave de IA de teste e habilitar testnet/demo |
| **G3 — assinatura de código** (evita aviso SmartScreen) | DEPENDÊNCIA EXTERNA | certificado de code signing |
| **G3 — sleep/wake, offline/reconexão, crash abrupto** | RISCO (não exercitado) | sessão de teste na estação real (executor) |
| **G8 — Release publicada** | BLOQUEADO POR POLÍTICA | sua autorização explícita de tag/release |
| **FASE 3 — comparação Freqtrade** e **FASE 6 — PWA em aparelho real / Oracle ARM64** | PENDENTE | ambiente isolado + aparelhos/infra |
| **G6 — investigar as 3 operações Binance com logs reais** | PENDENTE | logs reais (executor) |

## Riscos residuais

1. **Vantagem financeira NÃO comprovada.** Backtests/paper verdes não provam
   lucro. A diretriz "vantagem financeira comprovável" exige a comparação
   independente (Freqtrade) e walk-forward out-of-sample ainda pendentes.
2. **Homologação de ponta a ponta com serviços reais** (IA/corretora) ainda não
   feita — o núcleo de valor só está provado com mocks/contratos.
3. **DPAPI** protege contra outros usuários da máquina, não contra o próprio
   usuário; portabilidade entre máquinas exige re-colar segredos (documentado).
4. **ARCH-1** multiprocess puro: imposição forte só com `UNIQUE` em Postgres.

## Parecer sobre READY_FOR_INDEPENDENT_AUDIT

Do ponto de vista do **auditor Claude**, a `main` está **apta a ser submetida à
auditoria independente** para uso PAPER/TESTNET/COPILOTO: sem defeitos críticos
conhecidos sem tratamento, suíte verde, segurança sólida. **Não** equivale a
LIVE autorizado, estratégia lucrativa, capital aprovado nem release publicada.
A aprovação final depende da segunda revisão (ChatGPT + Claude) e das
homologações externas acima.

## FASE 2 — Autorização LIVE e conta real (prova adversarial, main)

Verificado por mim com a API real (sem credenciais reais, corretora simulada):

| Caminho testado | Resultado | Recusa |
| :-- | :-- | :-- |
| Mandato `mode=LIVE` sem `live_authorized` → `assert_active()` | recusa antes de operar | `MandateError` |
| `build_desk_broker(cfg live, live_authorized=False)` | recusa na construção | `BrokerSecurityError` |
| cfg com a **frase exata + chaves** mas `live_authorized=False` | **env/config NÃO contorna** | `BrokerSecurityError` |
| Padrão (sem `TRADINGAGENTS_DESK_BROKER=ccxt`) | cai em PAPER | `PaperBroker` |
| HTTP `/v1/broker/order` | rota legada removida | `410` |

**Conclusão FASE 2:** a conta real fica **preparada tecnicamente e bloqueada**;
nenhum botão, variável de ambiente, configuração salva ou caminho HTTP libera
LIVE sem autorização explícita de CÓDIGO (`live_authorized=True`) + a frase do
dono + tetos na ponte. `MESA_LIVE_LIBERADA=False`. CONFIRMADO.

## Homologação com credenciais (G4/G5) — caminho seguro do proprietário

A prova com serviço real **deve ser feita por você**, sem me entregar as chaves:
1. **IA:** no painel do app → Configurações → IA, cole a chave de um provedor
   (ex.: Groq/DeepSeek/OpenRouter) e clique **Testar** — o app grava só o
   status (configurado→conectado→autenticado→operacional), nunca o valor.
2. **Corretora (PAPER/TESTNET):** gere uma chave no **Binance Spot Testnet**
   (testnet.binance.vision) e cole no painel → Corretoras → Testar (somente
   leitura). A sua chave de conta real só deve ser colada por você, no painel,
   se quiser a leitura da conta real — nunca no chat.
3. Depois disso eu **leio o status sanitizado** (sem segredo) como evidência de
   G4/G5 — sem nunca manipular o valor da credencial.

## Auditoria adversarial dos módulos novos (antes só BASELINE)

| Módulo | Invariante provada por mim | Resultado |
| :-- | :-- | :-- |
| **Smart Exit Manager** (`desk/saida.py`) | afrouxar proteção é recusado por construção (stop mais largo, alvo mais distante, prazo maior, trailing removido → `ValueError`); só aperto é aceito e auditado | **VERIFICADO** (4 recusas + aperto OK) |
| **Dedup de alertas** (`desk/notificacao_viva.py`) | reincidência na janela incrementa ocorrências (não cria alerta novo); chave distinta cria novo; cap de 400 | **VERIFICADO** |
| **Maestro / salas / multimercado / saídas** | suítes próprias | **BASELINE** (123 testes verdes) |

## FASE 3 — Escola, vantagem quantitativa e comparação Freqtrade

### Integridade do backtester DZ23 (verificado por mim)
- **Sem lookahead:** o laço usa `janela = closes[:i]` — a decisão na vela i só vê o passado.
- **Custos aplicados:** cada operação paga `custo_de_operar` (taxa+spread+slippage) ida-e-volta.
- **NÃO aprova por backtest positivo:** num cenário sintético de alta, mesmo com
  **100% de acerto e +24,45% líquido**, o veredito foi **REPROVADA / `conclusivo:false`**
  com a ressalva "amostra pequena: 6 operações" e o aviso "teste sobre o passado;
  não prevê o futuro". Isto satisfaz diretamente a diretriz da missão.
- **Achado menor (cosmético):** o texto do resumo diz "os custos (1,80%) comeram o
  resultado" para um líquido de **+24,45%** — a atribuição do motivo está errada
  (o motivo real é amostra pequena). Mensagem enganosa ao dono; corrigir o template.

### Comparação independente com Freqtrade (ambiente isolado)
- **Freqtrade 2026.9** instalado e operacional em venv isolada.
- **Bloqueio ambiental registrado:** sob o scratchpad profundo o caminho do
  package-data do Freqtrade chegava a **264 chars > MAX_PATH (260)** do Windows,
  causando `FileNotFoundError` no `importlib.resources`. **Solução:** instalar em
  caminho curto (`C:\Users\DZ23\ft`) — registry carrega e o backtest roda.
- **Experimento controlado** (mesmo OHLCV com barras planas O=H=L=C para ambos
  avaliarem no fechamento; entradas idênticas a cada 12 velas; alvo 4% / stop -2%;
  taxa 0,30% ida-e-volta; 1 posição por vez):

  | Engine | Trades | Win% | Média/trade | Total |
  | :-- | :-- | :-- | :-- | :-- |
  | DZ23 (`ExitPolicy` real) | 7 | 100% | ~4,03% | +28,21% (soma) |
  | Freqtrade (minimal_roi 4% / stoploss -2%) | 6 | 100% | 4,35% | +26,16% (composto) |

- **Conclusão:** os dois motores de saída **concordam** (alvo ~4%/trade, 100% de
  acerto nessa alta). Divergências e **causas**: (1) 7 vs 6 trades → timing de
  entrada (DZ23 preenche no fechamento da vela de sinal; Freqtrade na abertura da
  vela seguinte, com 1 posição aberta); (2) total por soma de retornos (DZ23) vs
  capitalização no saldo (Freqtrade). Nenhuma divergência aponta erro na
  matemática do DZ23.
- **Limitação declarada:** uma comparação de ESTRATÉGIA completa exigiria portar a
  entrada (indicador) do trader DZ23 para o Freqtrade; aqui isolei e comparei o
  MOTOR DE SAÍDA, que é a parte diretamente equivalente.

### Vantagem financeira — parecer honesto
A "vantagem financeira comprovável" exigida pela diretriz final **NÃO está
demonstrada** — e o próprio sistema é honesto quanto a isso (reprova amostra
pequena, avisa que não prevê o futuro). Comprovar exigiria out-of-sample e
paper-forward com dados reais ao longo do tempo. Backtest positivo não é prova.

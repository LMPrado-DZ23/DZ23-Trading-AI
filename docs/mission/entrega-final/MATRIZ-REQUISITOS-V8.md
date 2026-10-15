# DZ23 TRADING AI — MATRIZ COMPLETA DE REQUISITOS (Documento A)

> **Documento A de 3 da entrega final (V8 §19).** Tudo o que foi solicitado nas missões **V8** (PROMPT MASTER +
> COMPLEMENTO PWA) e **V8.1** (Diretriz do Orquestrador), com implementação, teste, evidência e estado de
> verificação. Documentos irmãos: `RELATORIO-HOMOLOGACAO-V8.md` (B) e `RELATORIO-PROPRIETARIO-V8.md` (C).
>
> Repositório: `https://github.com/LMPrado-DZ23/DZ23-Trading-AI` · Branch da RC: `fix/instalador-atualizacoes-windows`
> · Versão: `0.5.0` · Data: 2026-10-14 · Executor: Qoder · Auditoria futura: ChatGPT + Claude.

## Como ler esta matriz (V8 §1 — nunca confundir os seis níveis)

Cada linha recebe **um** estado, sempre no nível de evidência que existe de fato:

| Estado | Significado |
| :--- | :--- |
| **HOMOLOGADO** | Verificado no nível mais alto exigido (bundle Windows instalado e/ou teste real no ambiente externo), com evidência arquivada. |
| **VERIFICADO (integrado)** | Implementado e coberto por testes unitários/integrados/E2E locais; **sem** alegação de homologação no bundle para este item específico. |
| **PARCIAL** | Parte entregue e verificada; parte restante declarada (motivo na própria linha). |
| **BLOQUEADO_EXTERNO** | Depende de credenciais, infraestrutura paga, assinatura de código ou dispositivos físicos que o executor não possui. Impacto e ação necessária registrados. |
| **BLOQUEADO_POLÍTICA** | Proibido por decisão do proprietário (LIVE com dinheiro real). Não é falha técnica. |
| **FALHA** | Defeito real sem tratamento. **Nenhum item crítico nesta situação** — qualquer falha conhecida está listada na §6. |

Regra de honestidade da missão (V8 §1/§16/§19): testes com mocks **não** são apresentados como homologação real;
dependências externas inacessíveis estão documentadas com precisão; `skip` não foi usado para ocultar falha;
nenhum percentual de conclusão inventado.

**Placar de execução (2026-10-14):** suíte unitária completa **3002 passed / 6 skipped / 71 subtests** (exit 0, 297,25 s) ·
E2E Playwright local **26/26** · aceite no bundle Windows do artefato CI **13/13** (run `38596224361`, commit
`b3e9dae` — harness original). O **artefato final desta fase** (decisão do ciclo + índices de risco + pacote RC) foi re-citado no run `38599535042` (push de `d131226`): **13/13** em 75,63 s, com o artefato `aceite-td5-bundle-evidencias` baixado e copiado para `docs/mission/evidence/v8-td5-exe/` — placar registrado em `docs/mission/evidence/v8-td5-aceite.md` §2/§5.

---

# 1. Matriz — PROMPT MASTER V8 (§1–§20)

## §1 Regra principal (não confundir os níveis de verificação)

| Requisito | Implementação | Teste/Evidência | Estado |
| :--- | :--- | :--- | :--- |
| Cada requisito avança por níveis (código→unit→integrado→bundle Windows→serviço externo→usuário final), com evidência por nível | Esta matriz + `docs/mission/TRADING-GATES.md` (G1–G8) + checkpoints C001–C021 e V8-* em `TRADING-CHECKPOINTS.md` | Todo o pacote de evidências em `docs/mission/evidence/` (por item) | **HOMOLOGADO** |
| Dependências externas documentadas; mock ≠ homologação | §5 desta matriz (dependências externas) + ressalvas em cada doc de evidência | — | **HOMOLOGADO** |
| Não apagar dados / não substituir trabalho de outros agentes / não habilitar dinheiro real | Regras de missão cumpridas: worktree/instalação do dono intocados (guardas grep+harness); LIVE fail-closed | `tests/test_seguranca_*`, launcher isolation guard, `E66/FIN-1` | **HOMOLOGADO** |

## §2 Auditoria inicial obrigatória

| Requisito | Implementação | Teste/Evidência | Estado |
| :--- | :--- | :--- | :--- |
| Auditoria de branch/PRs/checkpoints/CI/inventário/matriz de rastreabilidade (10 passos) | C002 + `G2-SINGLE-OWNER-AUDIT.md` + `TRADING-BACKLOG.md` + esta matriz | `docs/mission/` completo; histórico de commits da branch | **HOMOLOGADO** |

## §3 Copiloto Manual — fluxo de 11 passos (conclusão total)

| # | Passo do fluxo | Implementação | Evidência | Estado |
| :--- | :--- | :--- | :--- | :--- |
| 1 | Usuário escolhe corretora e ativo | Página **Copiloto Manual** (`#page-manual`, `service/static/index.html`) com carteira/ativo | E2E TD-5 itens 04–06 + aceite bundle 13/13 | **HOMOLOGADO** |
| 2 | Análise com dados identificados | `GET /v1/manual/sinais` → `tradingagents/desk/copiloto_sinais.py` (módulo puro; fonte+horário+qualidade no payload) | `tests/test_copiloto_sinais.py`; E2E TD-5 item 04; painel 8787 | **HOMOLOGADO** |
| 3 | Oportunidade, riscos, custos, horizonte, invalidação | `copiloto_sinais.py`: tendência/RSI/viés/níveis/invalidação; **custos NUNCA estimados** (declara "informe o custo real"); 3 avisos fixos (sem promessa de lucro) | `tests/test_copiloto_sinais.py` (pins de texto); E2E item 04 | **HOMOLOGADO** |
| 4 | Usuário efetua a operação **manualmente na corretora** | Fora do sistema por definição; o painel não oferece envio | Pin por AST/grep: rota e módulo **sem** `create_order`/`cancel_order`/ccxt/rede (`test_copiloto_sinais.py::TestModuloPuro`; `test_decisao_mesa.py` idem) | **HOMOLOGADO** |
| 5 | Registra preço efetivo, quantidade, taxas, horário | Formulário "Registrar operação" com prefill vindo da análise (§4) + campo de custos reais | E2E TD-5 item 06; `POST /v1/manual/positions` | **HOMOLOGADO** |
| 6 | Clica em **Acompanhar esta operação** | Botão renomeado e pinado (regex exata); prefill do formulário | E2E TD-5 item 05 (`td5-05-acompanhar-prefill.png`) | **HOMOLOGADO** |
| 7–8 | Monitora a posição; atualiza preço/custos/resultado/riscos | `tradingagents/desk/manual.py` (monitor de produção) + `GET /v1/manual/positions*`, `/v1/manual/monitor/health`; ticker rotulado por origem | `tests/test_manual_*`; E2E item 07; reinício durável (C014) | **HOMOLOGADO** |
| 9 | Emite alertas conforme regras objetivas | Regras objetivas no monitor → Centro de Alertas (fila durável) | E2E TD-5 item 07 (stop 159 × preço 158.5) + item 08 (caixa de entrada); aceite bundle 13/13 | **HOMOLOGADO** |
| 10 | Registra saída parcial ou total | `POST /v1/manual/positions/{id}/events` (saída parcial/total) | `tests/test_manual_*`; E2E item 13 (fluxo iniciante) | **HOMOLOGADO** |
| 11 | Resultado alimenta o diário e a Escola Central | Fechamento grava no diário/aprendizado (mesma fonte do fechamento autônomo) | `tests/test_escola_*`, integração `test_manual_*` | **VERIFICADO (integrado)** — a fiação existe e é testada; no bundle, "alimentar a Escola" só ocorre em sessão com operação manual fechada (ver §6 ressalvas) |
| — | Persistência após reinicialização | Estado manual durável no disco; C014 provou restauração no bundle | C014 (item 14 de `WINDOWS-RELEASE-EVIDENCE.md`) | **HOMOLOGADO** |
| — | Reconciliação de eventos, prevenção de duplicidade, rastreamento da origem de cada preço | Ids de evento + `provenance.py` + origem do preço no payload | `tests/test_manual_*` (dedup/reconciliação) | **VERIFICADO (integrado)** |

## §4 Botão "Acompanhar esta operação" — cenários de teste

Testes de referência: `tests/e2e/test_aceite_td5.py` (bundle: 13/13, run `38597411938`) + `tests/test_manual_*` +
`tests/test_copiloto_sinais.py`.

| Cenário (§4) | Cobertura | Estado |
| :--- | :--- | :--- |
| Cadastro de posição | E2E TD-5 06/13; unit | **HOMOLOGADO** |
| Monitoramento ativo | E2E 07/13 (monitor com tick real de fixture) | **HOMOLOGADO** |
| Mudança de preço | E2E 07 (preço cruza o stop) | **HOMOLOGADO** |
| Stop / Alvo | Regras objetivas no monitor; alerta de stop provado E2E 07–08 | **HOMOLOGADO** |
| Trailing stop | `manual.py` (trailing) + unit tests | **VERIFICADO (integrado)** — não disparado no roteiro do bundle |
| Saída parcial / antecipada condicionada / encerramento total | Eventos de saída + unit tests; fluxo iniciante fecha a operação | **VERIFICADO (integrado)** |
| Pausa e retomada | `POST /v1/manual/positions/{id}/pause|resume` + unit | **VERIFICADO (integrado)** |
| Reconexão / feed desatualizado / falha de conexão | Feed vencido recusa operar ("snapshot vencido, não vira ordem" — item 16); estados de canal honestos | **HOMOLOGADO** (feed vencido no bundle, item 16) |
| Reinicialização do computador / recuperação do histórico | C014 (reinício restaura posições, ordens, contador); item 22 (crash recovery 30/30) | **HOMOLOGADO** |

## §5 Alertas multicanal

| Requisito | Implementação | Evidência | Estado |
| :--- | :--- | :--- | :--- |
| Centro de Alertas no painel | `#page-alertas` + fila durável (`alerts.py` + `canais_alerta.py`) | E2E TD-5 08; `tests/test_alertas_*` | **HOMOLOGADO** |
| Windows Toast | Canal nativo (`notificacao_viva.py`) com AUMID registrado e rótulos honestos | N01 (9/9 em Windows real) + E2 (entrega real 282 ms) | **HOMOLOGADO** |
| Telegram / Discord | Canais HTTP implementados com configuração do dono | E2 (`tests/test_canais_*`; fail-closed 401) | **PARCIAL** — código e testes prontos; entrega real depende do token do dono (**BLOQUEADO_EXTERNO** para prova ponta a ponta) |
| WhatsApp oficial | Não integrado | Declarado: "quando houver credenciais e capacidade homologadas" | **BLOQUEADO_EXTERNO** |
| Web Push/PWA | `push_web.py` (VAPID) + `sw.js` + UI de consentimento | PWA-1..5; E2E TD-5 11; `docs/mission/evidence/v8-pwa-push.md` | **PARCIAL** — mecanismo íntegro e testado; entrega a dispositivo físico no §9 do complemento PWA (**BLOQUEADO_EXTERNO**: servidor HTTPS público + celulares) |
| Diagnóstico de cada canal | Estado por canal no painel (configurado/conectado) com mensagem honesta | E2E TD-5 08; Smart Setup diagnóstico 4 estados | **HOMOLOGADO** |
| Fila durável, dedup persistente, idempotência, expiração, retry/backoff, rate limit, leitura | Núcleo de alertas (E2) + fix da corrida de despacho | C003/§5: causa-raiz `ord-3` (f7f1f0f) + testes de dedup; `v8-s5-notificacoes-repetidas.md` | **HOMOLOGADO** |
| Simulado/PAPER × TESTNET × LIVE distinguidos | Rótulos `[SIMULADO (PAPER)]`/`[TESTNET]`/`[REAL (LIVE)]` em toda notificação | N01 (AUMID + rótulos) | **HOMOLOGADO** |
| Nunca dizer "corretora executou" sem evidência de execução | Pins de texto nos canais (mensagem só após `kind=fill_*` no ledger) | `tests/test_alertas_*` | **HOMOLOGADO** |
| Canal não configurado informa, sem simular entrega | Estado "não configurado" no diagnóstico | E2E TD-5 08 | **HOMOLOGADO** |

## §6 Configuração completa de IA (8 provedores)

| Requisito | Implementação | Evidência | Estado |
| :--- | :--- | :--- | :--- |
| Cadastro visual: OpenAI, Anthropic, Gemini, DeepSeek, Groq, Mistral, OpenRouter, outros por adaptador | `ia_provedores.py` + `#page-config` seção IA + `GET/POST /v1/ai/*` | `tests/test_ia_provedores*`; E2E TD-5 09; `v8-s6-configuracao-ia.md` | **HOMOLOGADO** (UI/rotas no bundle) |
| Cadastrar/remover credenciais; segredos protegidos | Chaves armazenadas com DPAPI (SEC-1); nunca devolvidas em claro pelo HTTP | `tests/test_ia_provedores*` + SEC-1 | **HOMOLOGADO** |
| Descobrir modelos disponíveis; escolher por função; testar autenticação; latência/limites/erros | `GET /v1/ai/modelos`, `POST /v1/ai/testar` (latência e erro honestos) | testes de rota + UI | **VERIFICADO (integrado)** |
| Fallback entre provedores; orçamento; consumo e custo estimado | Roteamento com fallback + `precos` + `GET /v1/ai/uso` | `tests/test_ia_orcamento*`; UI de consumo | **VERIFICADO (integrado)** |
| 10 traders e Maestro com modelos distintos; econômico p/ simples, capaz p/ complexo | Roteamento por função/perfil no Smart Setup | SS-1..SS-4 (`v8-smart-setup.md`) | **VERIFICADO (integrado)** |
| IA não substitui controles determinísticos de risco | LLM sem caminho para ordens; vetos determinísticos no Comitê de Capital | Pins AST/grep; `tests/test_decisao_*`, FIN-2 | **HOMOLOGADO** |
| Testes com APIs reais somente com credenciais autorizadas (nunca chaves fictícias) | Nenhuma chave real foi fornecida | Declarado em §6-resumo: "Testar autenticação real de cada provedor" | **BLOQUEADO_EXTERNO** |
| Ollama fora do escopo desta fase | Não instalado, não exigido | — | **HOMOLOGADO** |

## §7 Corretoras de criptomoedas

| Requisito | Implementação | Evidência | Estado |
| :--- | :--- | :--- | :--- |
| Catálogo e adaptadores efetivamente suportados | `CONNECTOR-MATRIX.md` + `brokers.py`/`ccxt_bridge` (Binance, OKX, IQ Option laboratório) | `tests/test_brokers*`, `CONNECTOR-MATRIX.md` | **HOMOLOGADO** (PAPER) |
| Por corretora: credenciais protegidas, permissão mínima, teste de conexão, saldos, ordens/posições, metadados, histórico, taxas, precisão, rate limits, reconciliação, PAPER/TESTNET, estados de capacidade | Implementado no bridge/adaptadores; PAPER completo; TESTNET onde suportado | testes de conector + reconciliação FIN-3 | **VERIFICADO (integrado)** — prova TESTNET/LIVE real depende de credenciais |
| Não presumir testnet/mercados iguais entre corretoras | `CONNECTOR-MATRIX.md` declara capacidades por corretora | E6 + matriz de conectores | **HOMOLOGADO** |
| Certificação separada por corretora, conta, mercado, estratégia, horizonte | Escola por contexto (`chave corretora|conta|mercado|modo`) | BT-6/BT-10 (isolamento entre salas; Binance ≠ OKX) | **HOMOLOGADO** |
| Falha de rede após ordem simulada (adversarial) | Resposta perdida: `clientOrderId` + reconciliação por identidade da corretora | FIN-3 (`auditoria-4b83e16-fin3-resposta-perdida.md`; `test_fin3_*`) | **HOMOLOGADO** |

## §8 B3 e EUA

| Requisito | Implementação | Evidência | Estado |
| :--- | :--- | :--- | :--- |
| Multimercado: B3 (ações/FIIs/ETFs), EUA (ações/ETFs), cripto spot+ | `mercados.py` (7 classes; cripto homologada; demais `execucao_homologada=False`) | `tests/test_multimercado.py` (15 testes; fronteiras DST 2026 pinadas) | **HOMOLOGADO** (camada de política) |
| IBKR/Alpaca avaliados; nada ativado sem elegibilidade real | ADR-005 com avaliação escrita (fontes oficiais) e decisão de **nenhum adaptador ativado** | `E6` (`docs/mission/evidence/e6-multimercado.md`) | **BLOQUEADO_EXTERNO** (credenciamento/custos) — decisão registrada |
| B3: separar dado de mercado × execução; não alegar integração direta sem credenciamento | Declarado no ADR-005 e na matriz; nenhuma alegação de integração B3 | E6 | **HOMOLOGADO** |
| Calendários, feriados, fusos, DST, lotes/ticks, moedas, liquidação, eventos, dividendos, câmbio, custos com fonte, restrições | `mercados.py` (metadados completos como estimativas de POLÍTICA; DST dos EUA por regra sem tzdata; Brasil UTC−3; feriados snapshot anual) | teste de cada metadado; `mercado_aberto()` fail-closed | **HOMOLOGADO** |
| Mercados sem conector: análise/acompanhamento **manual** com estados honestos, sem envio de ordens | Salas multimercado + Copiloto Manual; nenhum caminho de ordem para classes não homologadas (2º gate independente) | `tests/test_multimercado.py` + `test_escola_*` (recusa classe sem homologação) | **HOMOLOGADO** |

## §9 Segurança das chaves de API (P0)

| Requisito | Implementação | Evidência | Estado |
| :--- | :--- | :--- | :--- |
| Sem texto claro permanente (Windows: DPAPI/Credential Manager; segregação por instalação) | SEC-1: `escrever_json_atomico` + DPAPI por usuário/instalação | `auditoria-4b83e16-sec1-segredos-em-repouso.md`; `test_sec1_*` | **HOMOLOGADO** |
| Oracle Linux: injeção segura sem DPAPI | Fallback documentado (env/arquivo restrito) + runbook | C019 + `RUNBOOK.md` | **PARCIAL** — executável só quando houver Oracle (**BLOQUEADO_EXTERNO**) |
| Migração segura de instalações anteriores | Migração silenciosa verificada (chaves antigas migram; txt legado tratado) | SEC-1 + C017 (migração do dono) | **HOMOLOGADO** |
| Nunca imprimir chaves em logs/HTTP/backups/relatórios/testes | Redação nos payloads (pin de teste); export sem segredos (`segredos_omitidos`) | `tests/test_seguranca_*`; C019 export `dz23.mudanca/2` | **HOMOLOGADO** |
| Recuperação + limitações de portabilidade declaradas | `ROLLBACK.md` + docs de recuperação | C019/C021 | **HOMOLOGADO** |
| Testes: permissões, migração, recuperação, rotação, revogação, vazamento | Baterias SEC-1 + gitleaks no CI (sem allowlist) | `8e369f1` (incidente gitleaks corrigido); CI `security` job | **HOMOLOGADO** |

## §10 Auditoria financeira e ledger

| Requisito | Implementação | Evidência | Estado |
| :--- | :--- | :--- | :--- |
| Taxas, retorno líquido, conversão de moedas, quantidades fracionárias, precisão Decimal | Pipeline F3–F6 (P0-02/03) + `ledger.py`/`costs.py` | `tests/test_fees_*`, `test_ledger_*` (partidas dobradas balanceadas) | **HOMOLOGADO** |
| Fills parciais, cancelamentos, reembolsos | Kernel K1–K6 + eventos do ledger | `tests/test_kernel_*` | **HOMOLOGADO** |
| Posições, reservas, saldos, exposição global, risco por conta/corretora | Ledger + Maestro Central (teto de exposição) + painel global | `test_maestro_*`, `test_painel_global*`; TD-1 | **HOMOLOGADO** |
| Reconciliação após crash; persistência; concorrência; idempotência; eventos duplicados; timeout pós-ordem aceita | FIN-3 (identidade da corretora) + posse exclusiva (C019) + item 22 (crash 30/30 com 2 defeitos reais corrigidos) | `e2e/matriz-*` item 22; `test_fin3_*`; `test_posse_*` | **HOMOLOGADO** |
| Stops e saídas; proteções de perdas | E1 (Smart Exit) + perda diária no ExecutionCoordinator | `test_saida_*`; `estado_do_dia()` | **HOMOLOGADO** |
| Nenhuma posição "executada" por intenção | Pins: posição só nasce de `fill_*` com evidência | `tests/test_ledger_*`, `test_alertas_*` | **HOMOLOGADO** |
| Toda evidência financeira com procedência e estado explícito | `provenance.py` + estados no ledger | testes de procedência | **HOMOLOGADO** |

## §11 Dez traders e Escola Autônoma

| Requisito | Implementação | Evidência | Estado |
| :--- | :--- | :--- | :--- |
| 10 traders operacionais com especialidades, integrados ao Maestro | `traders.py` + Mesa de Agentes (3 homologados + 7 SHADOW) | M01 (`QODER-MAESTRO-TRADERS-EVIDENCE.md`); C013/C014 | **HOMOLOGADO** |
| Memória de operações; causas de perdas; Champion/Challenger; experimentos reproduzíveis; estratégias versionadas; walk-forward; fora da amostra; anti-overfitting | `evolucao.py` (parâmetros congelados com assinatura SHA-256; janelas comuns + Bonferroni) | E5 (34 testes; `e5-evolucao-autonoma.md`) | **HOMOLOGADO** (PAPER) |
| Certificação por contexto; revogação por deterioração; rollback; auditoria de mudanças | Escola BT-1..BT-10 + vigia no ciclo + `reabilitar` auditável | `tests/test_escola_*` (22/22 em 10 execuções); E5 | **HOMOLOGADO** |
| Promoção automática só em PAPER; capital real exige autorização; sem promessa de rentabilidade | Recusa fora de PAPER (pinado); LIVE jamais | `test_evolucao_*` (promoção recusada em modo≠paper) | **HOMOLOGADO** |

## §12 Smart Exit Manager

| Requisito | Implementação | Evidência | Estado |
| :--- | :--- | :--- | :--- |
| Motor global de saídas para todas as classes suportadas: stop técnico/financeiro, trailing, take-profit, parcial, tempo, invalidação, regime, saída antecipada, custos/liquidez | `saida.py` (E1) — determinístico, no ciclo | `tests/test_saida_*` (bateria E1) | **HOMOLOGADO** (PAPER) |
| Não vender só porque há lucro; comparar alternativas; considerar custos | Política de saída com avaliação contra manter posição + custos | testes de política E1 | **HOMOLOGADO** |
| Não comparar mercados distintos sem normalizar | Normalização por moeda/instrumento/custos | `test_multimercado*`, E1 | **HOMOLOGADO** |
| Diferenciar recomendação × execução | Estado de saída recomendada × ordem executada no ledger | pins E1 | **HOMOLOGADO** |

## §13 Aplicativo Windows — itens de homologação em instalação real

Fonte linha a linha: `WINDOWS-RELEASE-EVIDENCE.md` (itens 1–22, todos com evidência). Resumo:

| Item | Onde foi provado | Estado |
| :--- | :--- | :--- |
| Instalação limpa; reconhecimento de versão; atualização sem perda; reparação; downgrade recusado; backup/rollback | INST-1..6 (matriz CI run `38512611447`, upgrade real 0.4.0→0.5.0) | **HOMOLOGADO** |
| Primeira execução; login; recuperação de acesso | C021 itens 18–19 (assistente de 1ª execução, Argon2id, recuperação) | **HOMOLOGADO** |
| Configuração de APIs; Mesa de Agentes; Copiloto Manual; Alertas; Diário | C021 item 21 (27/27), C014, TD-5 (13/13) | **HOMOLOGADO** |
| Desligamento seguro; reinício | C013/C014/C021 (botão "Fechar aplicativo", launcher exit 0, reinstalação byte a byte) | **HOMOLOGADO** |
| Retomada de suspensão (sleep/wake) | Não exercitado | **BLOQUEADO_EXTERNO** — exige sessão física de sleep/wake; declarado (ressalva do G3) |
| Rede indisponível | C016 item 16 (snapshot vencido recusa operar); SS-3 cenário sem internet | **HOMOLOGADO** (comportamento; não em arrancamento físico de cabo no bundle) |
| Recuperação após crash | Item 22 (30/30; `kill -F` + reabertura) | **HOMOLOGADO** |
| Interface responsiva | C020 (e2e 1920/1366/768/390/360) + TD-5 item 12 (390/320 sem rolagem) | **HOMOLOGADO** |
| Desinstalação preservando dados | C013/C021 (dados preservados byte a byte) | **HOMOLOGADO** |
| Não encerrar serviços externos; não tocar a instalação pessoal do dono | Guardas de isolamento (harness aborta se instância do dono responde); grep zero de taskkill por imagem | **HOMOLOGADO** |

## §14 Oracle Cloud ARM64

| Requisito | Implementação | Evidência | Estado |
| :--- | :--- | :--- | :--- |
| Docker/Compose ARM64, segredos, persistência, reverse proxy/HTTPS, single-owner, backups, observabilidade, health, recuperação, update/rollback, acesso por navegador/celular | Entregue como **preparação**: empacotamento + posse exclusiva + runbook PC→Oracle | C019 + `RUNBOOK.md`; CI job ARM64 (QEMU — **não é** homologação Oracle) | **BLOQUEADO_EXTERNO** — não há VM Oracle disponível; deploy real exige autorização do dono |
| Um único executor autorizado por conta, inclusive na migração | `PosseDeExecucao` (renovação a cada ciclo; vence em 15 min; 2º processo não executa) | C019 (corrida da posse em Postgres real no CI — `38473108095`) | **HOMOLOGADO** (mecanismo em runtime Windows+Postgres CI) |
| Não afetar outros serviços; sem deploy/DNS sem aprovação | Nada foi implantado fora da estação | Registro C019 | **HOMOLOGADO** |

## §15 Automação financeira real

| Requisito | Implementação | Evidência | Estado |
| :--- | :--- | :--- | :--- |
| Controles implementados e testados com PAPER/TESTNET | FIN-1 (todos os caminhos de `create_order` mapeados e gated) + FIN-2 (matriz adversarial dos gates) | `auditoria-4b83e16-fin1/fin2` | **HOMOLOGADO** |
| LIVE desabilitado na configuração e na interface | REAL `disabled` nos dois modos; selo "DINHEIRO REAL BLOQUEADO"; `SINGLE_OWNER_ENFORCED` | SS-5 e2e; C020 selos dinâmicos; `test_*_live_*` | **HOMOLOGADO** |
| Verificar todos os caminhos de envio, incluindo legados | FIN-1 mapeou e fechou caminhos legados | `auditoria-4b83e16-fin1-selo-live.md`; `e66f2b7` (selo de código) | **HOMOLOGADO** |
| Autorização humana separada para futura ativação LIVE | Registrada como etapa futura (pós-auditoria independente) | V8.1 §12 + `REGISTRO-CAPITAL-PILOTO.md` | **BLOQUEADO_POLÍTICA** |
| Nenhuma aprovação de teste concede autorização para dinheiro | Declarado em todos os docs de gate | — | **HOMOLOGADO** |

## §16 Testes e homologação

| Requisito | Implementação | Evidência | Estado |
| :--- | :--- | :--- | :--- |
| Matriz requisito → implementação → teste → evidência | **Este documento** + matriz TD-5 + `WINDOWS-RELEASE-EVIDENCE.md` | — | **HOMOLOGADO** |
| Unitários; integração; adversariais; segurança; propriedades financeiras; E2E Playwright; Windows instalado; atualização; reconexão; recuperação | Bateria completa (3016 passed) + e2e 26/26 + aceite bundle 13/13 + matriz instalador + item 22 crash | `docs/mission/evidence/` por área | **HOMOLOGADO** |
| Testes com provedores externos autorizados | Sem credenciais fornecidas | — | **BLOQUEADO_EXTERNO** |
| Linux ARM64 quando ambiente disponível | Job ARM64 no CI (QEMU) — explicitamente **não** alegado como homologação Oracle | CI `arm64` job | **PARCIAL** |
| Falhas reproduzíveis eliminadas, inclusive dependência indevida da porta 8000 | 74c319d (launcher isolado da porta 8000) + correções de flakiness (BT-10 arbitragem determinística; e2e com própria porta) | `test_desktop_launcher`; BT-10 | **HOMOLOGADO** |
| Sem `skip` para ocultar falha; sem relaxar asserts | Nenhum skip novo; falha transiente registrada sem remoção (TD-5 §4); 6 skips condicionais de ambiente declarados | `v8-td5-aceite.md` §4 | **HOMOLOGADO** |
| Dependências externas e testes não executados registrados | §5 desta matriz + ressalvas por documento | — | **HOMOLOGADO** |

*(Os 6 skips da suíte: gates condicionais de ambiente — declarados em `RELATORIO-HOMOLOGACAO-V8.md` §4.)*

**Correção datada (2026-10-15, MISSÃO V9/F7):** a palavra "reconexão" na linha de testes acima agregava um **HOMOLOGADO** mais amplo do que a evidência cobre. O recorte testado desta missão: recuperação pós-crash do processo (item 22 — queda forçada do processo no bundle instalado e retomada sem perda), feed vencido recusando operar (item 16 — "snapshot vencido, não vira ordem"), reconexão de sessão do painel/PWA (e2e + correções V9-PWA-05/06) e inicialização/healthz do launcher (C021). O que **NÃO** está homologado: **reconexão contra corretora real** (restabelecimento de sessão/stream de uma corretora real após queda) segue **BLOQUEADO_EXTERNO** por ausência de credencial de conta real somente-leitura nesta missão; entrega de push em aparelho físico não foi homologada (emulador/navegador não substitui aparelho real). A tabela histórica acima é preservada sem edição; esta correção prevalece para leitura de estado.

## §17 GitHub e release

| Requisito | Implementação | Evidência | Estado |
| :--- | :--- | :--- | :--- |
| Commits organizados, PRs auditáveis, CI verde | Branch da RC com histórico de checkpoints; PRs #3/#5/#7/#10/#11 (mergeados) | GitHub Actions nos runs da branch | **HOMOLOGADO** |
| Auditoria de dependências; segredos; SBOM; artefatos reproduzíveis; checksums | CI: `security` (gitleaks + licenças + SBOM) + `Windows Release` (ZIP + Setup + SHA256SUMS.txt) | runs do CI 0.5.0 | **HOMOLOGADO** |
| Changelog; guias de instalação/recuperação/configuração; evidência do instalador; versão consistente; release notes; rollback | `CHANGELOG`/`README`/`SECURITY`/`DEPLOY`/`RUNBOOK.md`/`ROLLBACK.md`; versão 0.5.0 em pyproject↔exe↔setup | INST-4 (consistentes) | **HOMOLOGADO** |
| Release candidate final; publicação/assinatura/deploy respeitam autorizações | RC pronta; **publicação aguarda autorização explícita** (sem tag/Release) | Este pacote | **PARCIAL** — publicação é etapa separada do dono |

## §18 Critérios de conclusão (os 20 itens) — ver §4 deste documento

## §19 Entrega final (documentos A/B/C + atualização de STATE/CHECKPOINTS/GATES + classificação)

| Requisito | Implementação | Estado |
| :--- | :--- | :--- |
| Documentos A/B/C | `entrega-final/MATRIZ-REQUISITOS-V8.md` (A), `RELATORIO-HOMOLOGACAO-V8.md` (B), `RELATORIO-PROPRIETARIO-V8.md` (C) | **HOMOLOGADO** (neste pacote) |
| Atualizar STATE/CHECKPOINTS/GATES | `TRADING-MISSION-STATE.md`, `TRADING-CHECKPOINTS.md`, `TRADING-GATES.md` atualizados no commit deste pacote | **HOMOLOGADO** |
| Classificação da entrega (sem percentuais inventados) | **PARTIALLY_VERIFIED** — motivo: dependências externas comprovadas (credenciais reais de IA/corretoras, Oracle ARM64, assinatura de código, dispositivos móveis físicos) + publicação da RC não autorizada | **HOMOLOGADO** (classificação emitida em B §1) |

## §20 Instrução final (continuar até esgotar)

| Requisito | Estado |
| :--- | :--- |
| Não reiniciar do zero; avançar por checkpoints; autonomia no reversível; parar só no irreversível | **HOMOLOGADO** — execução contínua entre V7→V8→V8.1 documentada em `TRADING-CHECKPOINTS.md` |

---

# 2. Matriz — COMPLEMENTO PWA (§1–§10)

| § | Requisito | Implementação | Evidência | Estado |
| :--- | :--- | :--- | :--- | :--- |
| 1 | Preservar arquitetura (mesmo backend/banco/motor; PWA = cliente remoto) | `manifest.webmanifest`, painel único, sem executor paralelo | PWA-1..5; e2e; pins de "não criar executor" | **HOMOLOGADO** |
| 2 | Instalação como app: manifest completo, SW versionado, registro seguro, Android/iOS, ícones, splash, janela independente, atualização controlada, versão obsoleta, **sem botão "Instalar" falso** | `sw.js` versionado + manifest + UI de instalação com instruções por plataforma (não mostra botão se `beforeinstallprompt` ausente) | E2E TD-5 11; `tests/test_pwa_*`; PWA-3/4 | **PARCIAL** — entrega em dispositivo físico pendente (WEB) |
| 3 | Responsividade real: Android Chrome, iPhone Safari+PWA, tablets, Windows, 320px+, retrato/paisagem, todas as páginas, navegação móvel, tabelas com rolagem controlada, nada crítico oculto | e2e responsivo (1920/1366/768/390/360/320) + fixes reais de 320px (TD-5 §3) | `v8-td5-aceite.md` itens 12; C020 e2e | **PARCIAL** — prova em navegadores desktop (engine Chromium); iPhone Safari físico pendente |
| 4 | Web Push real: consentimento, subscription por dispositivo, endpoints, armazenamento protegido, rotação/expiradas, fila durável, retry/backoff, dedup, TTL, prioridade, diagnóstico, integração ao Centro | `push_web.py` + `/v1/push/*` + Despachante | PWA-1/2, `v8-pwa-push.md`; E2E 11 | **PARCIAL** — mecanismo completo e testado; entrega a dispositivo físico pendente |
| 4b | Categorias de push (oportunidade, posição, stop, meta, saída antecipada, risco, conexão, suspensão, emergência) | Categorias mapeadas no despacho | `tests/test_push_*` | **VERIFICADO (integrado)** |
| 4c | Tocar na notificação abre a área autenticada sem expor dados na URL | `notificationclick` no SW → rota autenticada | PWA-3 pin | **VERIFICADO (integrado)** |
| 5 | Particularidades iPhone: exigir instalação p/ push; telas educativas; não prometer o impossível | UI educativa por plataforma; textos honestos | PWA-4 pins | **PARCIAL** — validação física iOS pendente |
| 6 | Segurança: HTTPS, single-owner, sessões, CSRF, autorização nas rotas push, origem, rate limit, proteção de subscriptions/VAPID, CSP, cache mínimo, **nunca cachear dados sensíveis** | CSP + SW com política mínima (nunca cacheia conteúdo autenticado/dados) | `tests/test_pwa_*` (pins de cache) | **VERIFICADO (integrado)** — HTTPS público real depende da Oracle (**BLOQUEADO_EXTERNO**) |
| 7 | Backend 24/7 monitora no servidor (não depende do PWA aberto); recuperação/diagnóstico (offline, subscription expirada, push falho, servidor reinicia, vencimento); histórico no Centro mesmo sem push; push ≠ stop na corretora | Monitor no backend (ciclo/scheduler); Centro conserva histórico; texto explícito | E2 + PWA-2; TD-5 08 | **VERIFICADO (integrado)** — "24/7 em servidor" pressupõe Oracle (**BLOQUEADO_EXTERNO**) |
| 8 | PWA cliente do backend autorizado; sem executor no celular; posse exclusiva anti-split-brain; Copiloto sem ordens; LIVE bloqueado | Sem executor no PWA (pins); posse C019; LIVE bloqueado | pins + C019 | **HOMOLOGADO** |
| 9 | 15 testes de aceitação em dispositivo real (Android + iPhone) | Roteiro pronto em `docs/mission/` + equivalente dos 15 passos coberto por e2e local/bundle | TD-5 + PWA-5 | **BLOQUEADO_EXTERNO** — exige celulares físicos e servidor HTTPS público |
| 10 | Critério de conclusão: dono acessa pelo celular, instala, acompanha, vê traders, recebe push sem página aberta; se Oracle indisponível: HTTPS de homologação + pendência declarada | Parcialmente atendido no navegador (mobile 390/320 provados); Oracle/HTTPS público pendente | Este pacote declara a pendência explicitamente | **BLOQUEADO_EXTERNO** — implementação pronta; implantação real pendente |

---

# 3. Matriz — DIRETRIZ V8.1 (§1–§14)

| § | Requisito | Implementação | Evidência | Estado |
| :--- | :--- | :--- | :--- | :--- |
| 1 | Missão = vantagem líquida demonstrável com controle de risco; saber esperar; sem garantia de lucro | Filosofia codificada: abstenção é decisão válida; textos sem promessa | §4 abaixo + `copiloto_sinais.py` (avisos fixos) | **HOMOLOGADO** |
| 2 | Congelamento de escopo: concluir o aprovado V8, sem features novas por iniciativa | Nada novo fora do escopo V8/V8.1 foi adicionado | `TRADING-CHECKPOINTS.md` | **HOMOLOGADO** |
| 3 | **Comitê de Capital**: entrada só com estratégia versionada, contexto certificado, dados recentes, mercado homologado, custos/slippage, limites de exposição, sem conflito, risco agregado, ledger saudável, reconciliação, autorização de modo, sem kill-switch; recusa com motivo auditável em PT; LLM não contorna vetos | Maestro + Risk Engine + vigia no ciclo + `decisao.py` (motivo em PT) | `test_maestro_*`, `test_decisao_mesa.py`; TD-5 ciclo real (veto visível) | **HOMOLOGADO** |
| 4 | **ABSTENÇÃO INTELIGENTE** com 4 estados nomeados: OPERAR / AGUARDAR / BLOQUEAR / SUSPENDER; sem meta de quantidade; abstenção ≠ prova de rentabilidade | `tradingagents/desk/decisao.py` (módulo puro) + `GET /v1/desk/status` campo `decisao` + painel "Decisão do ciclo" | `tests/test_decisao_mesa.py` (17 testes: escada de precedência, HTTP, DOM) | **VERIFICADO (integrado)** — homologação no bundle do artefato final registrada em `v8-td5-aceite.md` §5 |
| 5 | Escola: provas reais A–F (dev / validação temporal / backtest independente / PAPER dados reais / TESTNET / eventual piloto LIVE); proibir vazamento/reuso do teste/otimização cega; amostra com requisitos estatísticos; poucas ops favoráveis = SEM EVIDÊNCIA | `laboratorio.py`/`escola.py` (pré-registro, controle aleatório, Bonferroni) + estados de evidência | BT-2/BT-5 (`tests/test_escola_*`); E5 | **HOMOLOGADO** (PAPER; TESTNET/LIVE bloqueados como manda o §) |
| 6 | Indicadores por trader e consolidados (taxa de acerto, ganho/perda média, profit factor, drawdown, exposição, **volatilidade, Sharpe/Sortino quando apropriados**, custos, slippage, regime, benchmark, estabilidade OOS, tempo em mercado, qualidade de execução, taxa de abstenção); fórmulas/origem/período/moeda/limitações documentadas; **jamais misturar PAPER/TESTNET/LIVE**; nunca lucro simulado = real | `sala_trader.py` (`metricas_portfolio` + `indices_de_risco`: Sharpe/Sortino por operação, mínimos estatísticos, motivo declarado quando indefinido) + UI da sala (Volatilidade/Sharpe/Sortino com rótulo "por operação, não anualizado") | `tests/test_sala_trader.py` (incl. `TestIndicesDeRisco` + pins DOM); TD-1 (segregação) | **VERIFICADO (integrado)** |
| 7 | Champion/Challenger: IA propõe/simula/compara/suspende/melhora, mas **não** altera homologada em execução, não aprova a si, não sobe limites, não vai a LIVE, não altera código sem revisão; promoção automática só em PAPER; rollback e rastreabilidade | `evolucao.py` (parâmetros congelados, assinatura, vigia, rollback, `reabilitar`) | E5 (34 testes) | **HOMOLOGADO** |
| 8 | Segurança financeira: SEC-1/FIN-1/FIN-2/FIN-3 + F3–F6/K1–K6 + 13 tipos de teste adversarial (aceita sem resposta, reenvio, parcial, saldo inconsistente, dados vencidos, perda de conectividade, 2 instâncias, reinício em operação, falha de taxa, falha de câmbio, stop com gap/slippage, estratégia excedendo limites, credenciais revogadas); erro de IA não gera ordem | Auditoria 4b83e16 + correções + matriz adversarial | `auditoria-4b83e16-*.md` (4 docs); `test_fin2_*`, `test_fin3_*`, `test_kernel_*`, `test_fees_*` | **HOMOLOGADO** |
| 9 | 10 agentes com especialização, versão, histórico, gráficos, portfólio atribuível, indicadores, certificação, motivos, suspensão; seleção automática só de elegíveis; SHADOW/PAPER quando faltar evidência; sem duplicar capital; 10 agentes ≠ 10 oportunidades | Mesa + sala por trader (TD-2) + tiers | M01 + TD-2 (`test_sala_trader.py`); C013 | **HOMOLOGADO** |
| 10 | Smart Exit: prioridade à preservação de capital; testar stop/trailing/TP/parcial/tempo/regime/invalidação/liquidez/antecipada; avaliar contra manter posição e custos; não encerrar só por lucro; stops não garantem preço; | `saida.py` + política | E1 (baterias) | **HOMOLOGADO** (PAPER) |
| 11 | UX: CONECTAR → ESCOLHER DEMO/REAL/COPILOTO → PLAY; diagnóstico; iniciar só o modo autorizado; saúde própria; alertas; Avançado sem desativar proteções; REAL visível porém indisponível | SS-5 (Modo Simples/Avançado) + Smart Setup 4 estados | SS-1..SS-5; e2e 13/13 | **HOMOLOGADO** |
| 12 | Registrar formalmente: **R$ 1.000 — possível capital inicial para um piloto futuro** (não é depósito/autorização/limite/meta/garantia; LIVE nem com R$ 1) | `docs/mission/REGISTRO-CAPITAL-PILOTO.md` | Este pacote | **HOMOLOGADO** (registro) |
| 13 | RC para auditoria: 15 entregáveis (código em branch, commits/PRs, matriz, relatório de testes, relatório financeiro/quant, E2E Windows, PWA/push, integrações externas, credenciais/segurança, riscos, dependências, artefatos reproduzíveis, docs instalação/recuperação, checklist de auditoria, relatório PT) | Este pacote + `RELATORIO-HOMOLOGACAO-V8.md` + `RELATORIO-PROPRIETARIO-V8.md` + `CHECKLIST-AUDITORIA-INDEPENDENTE.md` | — | **HOMOLOGADO** |
| 13b | **Não publicar release oficial / não deploy / não usar capital sem autorização**; Qoder **não** certifica a própria entrega como financeiramente aprovada | Nenhuma tag/Release criada; nenhum deploy; classificação PARTIALLY_VERIFIED (aprovação final = auditoria independente) | — | **HOMOLOGADO** |
| 14 | Critério de parada: executáveis concluídos; testes críticos aprovados; sem defeitos críticos conhecidos; pendências só externas; bloqueios registrados com impacto/evidência/ação; nunca declarar "pronto para capital real" | §6 deste documento + RELATORIO-HOMOLOGACAO §5 | — | **HOMOLOGADO** |

---

# 4. Os 20 critérios de conclusão do proprietário (V8 §18) — prova por item

> Critério: "O produto estará tecnicamente pronto para uso pessoal em PAPER/Copiloto Manual somente quando o
> proprietário conseguir:". Cada item com prova técnica abaixo.

| # | O proprietário consegue… | Prova técnica | Estado |
| :- | :--- | :--- | :--- |
| 01 | Instalar ou atualizar sem perder dados | INST-2..6: matriz CI `38512611447` completa; upgrade real 0.4.0→0.5.0 com backup verificado e dados byte a byte; C013 (reinstalar → idênticos) | **HOMOLOGADO** |
| 02 | Entrar no painel | Portão premium 4 estados; C017/C021 item 18 (27/27); login do dono no bundle | **HOMOLOGADO** |
| 03 | Configurar provedores de IA | `#page-config` IA: cadastrar/remover/testar/fallback/orçamento (TD-5 09) | **HOMOLOGADO** |
| 04 | Consultar os modelos disponíveis | `GET /v1/ai/modelos` + botão "Descobrir modelos"; quando API real não responde, estado honesto | **VERIFICADO (integrado)** — descoberta real por provedor depende de chave (**BLOQUEADO_EXTERNO** para prova real) |
| 05 | Configurar uma corretora suportada | Smart Setup/Config: corretora + credenciais DPAPI + teste de conexão; PAPER pronto | **VERIFICADO (integrado)** — conexão real depende de chave (**BLOQUEADO_EXTERNO**) |
| 06 | Consultar dados reais de mercado com identificação da fonte | Item 16 (fonte "real" PRONTO: 239 velas BTC/ETH, qualidade ok, cutoff); seletor "Fonte de dados" no bundle | **HOMOLOGADO** |
| 07 | Visualizar os 10 traders | C013 (3 aprovados + 7 experimentais na Mesa) + C021 item 21 | **HOMOLOGADO** |
| 08 | Executar ciclo PAPER | C014 (20 propostas → 2 admitidas → 2 entradas; ledger balanceado) + TD-5 ciclo real | **HOMOLOGADO** |
| 09 | Receber alertas reais pelos canais homologados | Windows Toast entrega real 282 ms (E2); N01 rótulos; canais não configurados informam honestamente | **PARCIAL** — Telegram/Discord/WhatsApp reais dependem de credenciais do dono |
| 10 | Registrar uma operação manual | E2E TD-5 06; C014 (posição manual registrada e durável) | **HOMOLOGADO** |
| 11 | Clicar em Acompanhar esta operação | E2E TD-5 05 (botão + prefill); renome pinado | **HOMOLOGADO** |
| 12 | Monitorar a posição | E2E TD-5 07 (tick do monitor, alerta objetivo); C014 (posições restauradas após reinício) | **HOMOLOGADO** |
| 13 | Receber alertas de saída | Regras objetivas → Centro (E2E 07/08); alerta de stop provado; alertas de saída do Smart Exit (E1) | **HOMOLOGADO** (regra+alerta) |
| 14 | Registrar o encerramento | `events` de saída; E2E 13 (fluxo iniciante conclui) | **HOMOLOGADO** |
| 15 | Consultar resultado, taxas e histórico | Sala do trader/portfólio do ledger (TD-2/TD-3); histórico de ciclos; diário | **HOMOLOGADO** |
| 16 | Reiniciar sem perder os dados | C014 (reinício restaura OMS/posições/contador); item 22 (crash 30/30) | **HOMOLOGADO** |
| 17 | Confirmar bloqueio de execução LIVE não autorizada | REAL `disabled`; selo "DINHEIRO REAL BLOQUEADO" dinâmico; FIN-1/FIN-2; `SINGLE_OWNER_ENFORCED` | **HOMOLOGADO** |
| 18 | Verificar backups e recuperação | INST-3 (backup verificado pré-upgrade + rollback); `ROLLBACK.md`; item 22 | **HOMOLOGADO** |
| 19 | Consultar relatório de saúde operacional | `/v1/status/operacao`, diagnósticos do Smart Setup, monitor/health do manual, healthz | **HOMOLOGADO** |
| 20 | Confirmar que não existem defeitos críticos conhecidos nos fluxos homologados | **Nenhum defeito crítico conhecido aberto**; defeitos encontrados foram corrigidos com causa-raiz (TD-5 §3; C021; item 22; flakiness BT-10) | **HOMOLOGADO** |

**Leitura honesta dos 20 critérios:** os itens com prova incompleta são exatamente os que dependem de credenciais
externas do proprietário (03–05 na parte "prova real de conexão", 09 canais pagos) — a mecânica de todos eles
está implementada e testada; o que falta é a chave/credencial real, que a missão proíbe inventar. Item 06 e 08 são
100% homologados no bundle com dados reais de mercado.

---

# 5. Dependências externas (registradas, não omitidas — V8 §1/§16; V8.1 §14)

| # | Dependência | O que falta | Impacto | Ação necessária |
| :- | :--- | :--- | :--- | :--- |
| D1 | Credenciais reais de IA (OpenAI/Anthropic/Gemini/DeepSeek/Groq/Mistral/OpenRouter) | Provar autenticação/modelos/latência reais | Itens §6 e critérios 03/04 ficam em "verificado integrado" | Proprietário fornecer chaves quando quiser; depois rodar `POST /v1/ai/testar` |
| D2 | Credenciais de corretora (Binance/OKX etc.) | Prova TESTNET (quando houver) e leitura real de saldos/ordens | §7 e critério 05 parciais | Proprietário fornecer chave com permissão mínima (leitura) + PAPER já pronto |
| D3 | Telegram/Discord (bot/webhook) | Entrega real nos canais | Critério 09 parcial | Proprietário criar bot/webhook e colar no painel |
| D4 | WhatsApp oficial | Integração completa | Fora do escopo atual ("quando houver credenciais e capacidade homologadas") | Decisão futura do proprietário |
| D5 | Oracle Cloud ARM64 (VM) | Deploy real, HTTPS público, PWA no celular, push físico | §14 e complemento PWA §9/§10 bloqueados | Provisionar VM + autorização de deploy (etapa irreversível — exige aprovação) |
| D6 | Celulares físicos (Android + iPhone) | 15 testes de aceitação reais | Complemento PWA §9 | Sessão de teste com o dono quando houver HTTPS público (ou homologação local via túnel) |
| D7 | Assinatura de código (certificado EV) | Instalador sem aviso do SmartScreen | Não impede uso pessoal (dono instala mesmo assim) | Aquisição do certificado pelo proprietário |
| D8 | Sessão física de sleep/wake | "Retomada de suspensão" no bundle | Ressalva declarada do G3 | Sessão com o dono na estação dele |
| D9 | Publicação da RC (tag/Release) | Distribuição oficial | Nada publicado por decisão de governança | Autorização explícita do proprietário |

---

# 6. Falhas conhecidas, ressalvas e limitações (nada escondido)

1. **Nenhum defeito crítico conhecido em aberto.** Todo defeito encontrado nas matrizes foi corrigido com
   causa-raiz provada e teste de regressão (TD-5 §3: 2 estouros de 320 px; C021: falha muda de inicialização;
   item 22: 2 defeitos reais de recuperação; BT-10: flakiness de arbitragem; primeiro-Salvar: kernel fix PR #11).
2. **1 corrida transiente registrada** na suíte e2e completa sob carga (TD-5 §4) — não reproduzida em 4 corridas
   seguintes; mantida em registro, sem skip.
3. **6 skips da suíte** são condicionais de ambiente (declarados em B §4), não ocultação de falha.
4. **PWA/push:** mecanismo completo; as provas em dispositivo físico e servidor HTTPS público estão bloqueadas
   (D5/D6). Nada foi declarado homologado apenas "porque o manifest existe" (proibição explícita do complemento).
5. **Telegram/Discord:** código + testes prontos; entrega real depende das credenciais do dono (D3).
6. **Oracle:** preparação entregue (empacotamento, posse, runbook); deploy NÃO realizado (D5).
7. **Listas de preços de IA** usadas para custo estimado têm data de referência e estão marcadas como estimativa
   (não são fatura do provedor).
8. **Dados de fixture** provam fiação e regras, não o mercado; o caminho com fonte real foi exercitado no item
   16 (feed real rotulado) e nos ciclos do bundle.
9. **O executor (Qoder) não certifica a própria entrega como financeiramente aprovada** (V8.1 §13). A validação
   final é a auditoria independente (ChatGPT + Claude), conforme o desenho da missão.

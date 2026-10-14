# DZ23 TRADING AI — RELATÓRIO DE HOMOLOGAÇÃO (Documento B)

> **Documento B de 3 da entrega final (V8 §19).** Testes, versões, artefatos, falhas, riscos e evidências.
> Irmãos: `MATRIZ-REQUISITOS-V8.md` (A) e `RELATORIO-PROPRIETARIO-V8.md` (C — português simples).
> Branch da RC: `fix/instalador-atualizacoes-windows` · Versão `0.5.0` · 2026-10-14.

## 1. Classificação da entrega (V8 §19)

**PARTIALLY_VERIFIED.**

Justificativa (nenhum percentual inventado):

- **O escopo de uso pessoal PAPER/Copiloto Manual está implementado e homologado no bundle Windows** em seus
  fluxos principais (itens 13–22 de `WINDOWS-RELEASE-EVIDENCE.md`; aceite TD-5 13/13 no bundle do artefato CI;
  ver §3).
- **A entrega não é declarada VERIFIED_COMPLETE** porque permanecem pendências de validação que dependem
  exclusivamente de terceiros/credenciais/infraestrutura (§7 — lista completa):
  credenciais reais de IA e de corretora, Telegram/Discord/WhatsApp, Oracle ARM64 + HTTPS público, celulares
  físicos (PWA/push), assinatura de código, sessão de sleep/wake e a publicação da RC (que aguarda autorização
  explícita do proprietário).
- **Não é FAILED**: nenhum defeito crítico conhecido permanece aberto (§5 registra tudo o que foi encontrado e
  corrigido com causa-raiz).
- **Não é BLOCKED_BY_EXTERNAL_DEPENDENCY como classificação única**, pois a maior parte do produto foi
  efetivamente verificada; o bloqueio externo atinge itens específicos, identificados um a um na Matriz A §5.
- **A certificação financeira final NÃO é feita pelo executor** (V8.1 §13): ela pertence à auditoria independente
  (ChatGPT + Claude) que sucederá esta entrega. Nada aqui constitui autorização para operar com capital real.

## 2. Versão e artefatos

| Item | Valor |
| :--- | :--- |
| Versão do produto | `0.5.0` (fonte única `pyproject.toml`; consistente com exe, setup.iss e Release — INST-4) |
| Runtime de build | GitHub Actions `windows-latest`, workflow `Windows Release`; ZIP `dz23-windows-x64` + Setup Inno + `SHA256SUMS.txt` |
| Artefato de referência da homologação G3 (fase anterior) | run `38450504893` (item 12 de `WINDOWS-RELEASE-EVIDENCE.md`) |
| Artefato do aceite TD-5 (matriz de dashboard/UX) | run `38596224361`, commit `b3e9dae` — 13/13 no bundle (`v8-td5-aceite.md` §5) |
| Artefato final desta fase (decisão do ciclo + índices de risco) | run do commit final de 2026-10-14 — registrado em `docs/mission/evidence/v8-td5-aceite.md` §5 |
| Matriz do instalador (8 cenários + C1–C8 etc.) | run `38512611447` — `TOTAL: 0 falha(s)` (INST-5/6) |
| Upgrade real 0.4.0→0.5.0 | mesmo run — backup verificado + dados byte a byte + healthz 0.5.0 (INST-6) |
| Publicação (tag/Release) | **Não realizada** — aguarda autorização explícita do proprietário (sem tag criada) |

Integridade: todo artefato usado em homologação teve `sha256sum -c SHA256SUMS.txt` OK antes do uso; os
checksums vivem dentro do próprio artefato do CI (reprodutível a partir do commit).

## 3. Placar de testes por nível (evidência rastreável)

| Nível | Bateria | Placar | Evidência |
| :--- | :--- | :--- | :--- |
| Unit + integrado (local) | suíte completa `uv run pytest -q` | **3015 passed / 6 skipped / 71 subtests** (exit 0, 296,34 s) — **+24 testes desde o TD-4** (23 da fase final: decisão do ciclo + índices de risco; 1 do §17/#106: guias do produto real) **+13 da auditoria rodada-8** (10 de timeouts LLM + 3 de escapamento do painel) | `v8-td5-aceite.md` §2; `aud-r8-timeouts-llm-e-escaping.md` |
| E2E navegador real (local) | `uv run --with playwright pytest tests/e2e -q` | **26/26** (13 de painel + 13 de aceite TD-5), 4 corridas seguidas + 2 da auditoria rodada-8 (41,06 s e 41,78 s) | `v8-td5-aceite.md` §2; `aud-r8-timeouts-llm-e-escaping.md` |
| E2E bundle Windows (CI, runner virgem) | job `aceite-bundle` sobre o ZIP do artefato | **13/13** em 37,79 s (harness original), **13/13** em 75,63 s (**artefato final da fase**) e **13/13** em 37,14 s (**re-homologação pós-endurecimento do instrumento**) — 12 dashboards + fluxo do iniciante em 10 passos | runs `38596224361`, `38599535042` e `38607540029`; `docs/mission/evidence/v8-td5-exe/` (20 arquivos) |
| Instalador Windows (matriz CI) | cenários C1–C8 + extensões (≈143 verificações) | `TOTAL: 0 falha(s)` | run `38512611447`; `e2e/matriz-38512611447/` |
| Portão premium/identidade no bundle | itens 17–19, 21 | **24/24**, **27/27**, **27/27** | C018/C021 (`WINDOWS-RELEASE-EVIDENCE.md`) |
| Matriz de inicialização no bundle | 6 cenários (incl. intruso na porta, WM_CLOSE) | **24/24** | C021 |
| Recuperação de crash | item 22 (`kill -F` + reabertura) | **30/30** (2 defeitos reais encontrados e corrigidos) | item 22, `f07ea47` |
| Escola de traders (E2E) | `tests/test_service_escola.py` | **22/22** em 10 execuções seguidas | BT-10 |
| PAPER E2E no bundle instalado | ciclo real + reinício durável | C014 (2 entradas, ledger balanceado, posições restauradas) | `WINDOWS-RELEASE-EVIDENCE.md` item 14 |
| Notificação Windows real | entrega no sistema operacional | 282 ms | E2 (`30b8683`) |
| Segurança (CI) | gitleaks + licenças + SBOM | verdes, sem allowlist de segredos | jobs do CI `security` |
| PWA/push | unit + estáticos + e2e | verdes | `docs/mission/evidence/v8-pwa-push.md` |

## 4. Skips declarados (V8 §16 — nada oculto)

Exatamente **6 skips** na suíte unitária local, todos **condicionais de ambiente**, com o caminho coberto em
outro ambiente (CI) ou por bateria equivalente — nenhum esconde falha:

| Skip | Motivo | Onde é coberto de verdade |
| :--- | :--- | :--- |
| `tests/e2e/test_panel_e2e.py:14` (módulo inteiro) | `playwright` ausente na venv base (extra opcional) | a mesma bateria roda **26/26** com `uv run --with playwright pytest tests/e2e -q`; job `aceite-bundle` no CI |
| `tests/e2e/test_aceite_td5.py:34` (módulo inteiro) | idem (o `importorskip` de módulo conta 1 skip, não 13) | idem — **13/13** no bundle do artefato |
| `tests/test_modo_da_mesa.py:161` | sem `DATABASE_URL` | job `postgres` do CI (corrida da posse em banco real — run `38473108095`) |
| `tests/test_desk_backup.py:58` | POSIX permissions (600) não existem em NTFS | execução em Linux/CI |
| `tests/test_bedrock_provider.py:74` | `langchain_aws` ausente (extra opcional; Bedrock não é um dos 8 provedores principais) | instalável via extra; declarado |
| `tests/test_quant_intelligence.py:74` | `lightgbm` ausente (extra opcional de quant) | instalável via extra; declarado |

Além disso, o registro honesto de **1 corrida transiente** da suíte e2e completa (2 timeouts de aquecimento sob
carga; não reproduzida em 4 corridas seguidas; **não foi skipada nem removida**) — `v8-td5-aceite.md` §4.

## 5. Falhas encontradas e corrigidas (registro com causa-raiz)

| Onde | Falha | Causa-raiz | Correção |
| :--- | :--- | :--- | :--- |
| TD-5 §3 | Botão "Fechar aplicativo" estourava a topbar em ≤760 px; valores longos estouravam a lista do monitor a 320 px | Regra móvel escondia só `.identity-copy`; `JSON.stringify` sem ponto de quebra | Fixes reais no `index.html` + teste 12 auto-diagnóstico |
| C021 | Duplo clique do dono morria mudo no exe `--windowed` | `sys.stdout.isatty()` com stdout `None` no logging do uvicorn, antes de qualquer bind | Launcher com pré-checagem de portas + `log_config=None` + `launcher.log`; matriz 24/24 |
| Item 22 | 2 defeitos reais de recuperação de crash | Investigados com reprodução (`kill -F`) | Corrigidos no artefato `f07ea47`; 30/30 |
| BT-10 | Flakiness do E2E da escola | Arbitragem sorteava pela hash do relógio | Decisão pelo estado de mercado; regressão pinada; 22/22 × 10 |
| PR #11 | "Primeiro Salvar" do painel reconstruía a mesa | Kernel de save | Corrigido no kernel fix; provado no item 16 |
| `f7f1f0f` | Notificação duplicada (`ord-3`) | Corrida no despacho de alertas | Fila serializada; evidência `v8-s5-notificacoes-repetidas.md` |
| `8e369f1` | gitleaks acusou fixture sintética (entropia 3,5069 > 3,5) | Fixture com alta entropia | Corrigido **sem allowlist** |
| CI aceite-bundle (1º run) | `coletados=0` mudo no runner | venv do runner sem `pytest` (só base + playwright) + stderr oculto | Job sincroniza `--extra desk --extra dev` + diagnóstico de coleta (`b3e9dae`) |
| E5 (local) | 1 falha ambiental: `test_desktop_launcher` encontrou o app instalado DO DONO na porta 8000 | Estação do dono não é ambiente isolado | Provado por curl/netstat; CI em runner virgem verde; launcher isolado da porta (74c319d) |
| CI aceite-bundle (selo `b5c80d5`, run `38604619895`) | 1ª tentativa **12/13**: o passo 8 do fluxo do iniciante não via o toast "Execução registrada." | Resposta tardia de `manualHealth()` ("Monitor verificado.", do passo 7) sobrescrevia o toast seguinte no `#toast` único (last-write-wins); o evento foi registrado no servidor (reducao 0.25 @ 161.10) | Dreno determinístico após "Verificar agora" nos itens 07/13 (`f41e150` — test-only, sem afrouxar assert); rerun da própria run **13/13 em 32,21 s**; re-homologado no CI `38607540029` (**13/13 em 37,14 s**) |
| Rodada-8 (auditoria pós-selo, tasks #108–#111) | Timeouts HTTP do caminho LLM **desabilitados por requisição**: uma conexão travada com um provedor pendura a chamada sem limite (o default do `httpx.Client` endurecido era peso morto) | Upstream: langchain passa `timeout=None` EXPLICITAMENTE ao SDK (openai 3.19.2 trata None como valor "dado" via `is_given`); o SDK carimba o None em cada requisição e anula o default do client — mesmo padrão em anthropic (`default_request_timeout`) e google (`self.timeout` → ms por requisição) | `LLM_HTTP_TIMEOUT` finito (10/300/30/10 s) como fonte única em `transport.py` + `setdefault` nos 4 clientes (anthropic/google com float 300 s — pydantic rejeita `httpx.Timeout`); passthrough do usuário prevalece; RED→GREEN (evidência `aud-r8-timeouts-llm-e-escaping.md`; bedrock não provável — SDK ausente, declarado) |
| Rodada-8 (auditoria pós-selo, tasks #108–#111) | 2 inserções cruas de dados do servidor em `innerHTML` no painel (`operacaoReal`, `renderEvents`) | Renderização fora da convenção `escapeHtml` do próprio arquivo | Escapamento aplicado nos 2 pontos + 3 pinos estáticos (`tests/test_painel_render_escaping.py`) |

## 6. Riscos residuais (V8.1 §13.10)

| # | Risco | Severidade | Mitigação atual |
| :- | :--- | :--- | :--- |
| R1 | Fluxos que exigem credenciais reais (IA, corretoras, canais) não foram provados ponta a ponta | Média | Estados honestos no painel; diagnóstico de cada canal; roteiro de teste pronto para o dia em que as chaves forem inseridas |
| R2 | PWA/push físico e Oracle ARM64 não homologados (sem infraestrutura) | Média | Mecanismo íntegro e testado; pendência declarada; nenhuma alegação falsa |
| R3 | Estabilidade de e2e sob máquina carregada (1 corrida transiente) | Baixa | 4 corridas seguidas verdes; teste com auto-diagnóstico |
| R4 | Números de fixture ≠ mercado real | Baixa | Caminho real homologado no item 16 (feed rotulado + corte na vela fechada) |
| R5 | Sleep/wake não exercitado no bundle | Baixa | Ressalva declarada do G3; recuperação de crash e reinício cobertos |
| R6 | Instalador sem assinatura de código (SmartScreen) | Baixa (uso pessoal) | Documentado; aquisição do certificado é decisão do proprietário |
| R7 | Listas de preços de IA são estimativas datadas | Baixa | Marcadas como estimativa com data de referência; jamais apresentadas como fatura |
| R8 | Estratégias com amostra pequena podem parecer promissoras | Média | Regra codificada: "SEM EVIDÊNCIA SUFICIENTE" abaixo dos mínimos; Sharpe/Sortino nulos com motivo; Bonferroni |

## 7. Integrações externas — status (V8.1 §13.8)

| Integração | Estado | Bloqueio |
| :--- | :--- | :--- |
| Binance (PAPER) | Homologada (ciclos reais PAPER no bundle; reconciliação FIN-3) | — |
| OKX / IQ Option (laboratório) | Sala isolada / laboratório; certificação separada por contexto | Testes reais de execução exigem credencial |
| Binance/OKX TESTNET | Suportado quando a corretora oferecer | Credenciais do proprietário |
| Telegram / Discord | Implementado e testado; entrega real pendente | Bot/webhook do proprietário |
| WhatsApp oficial | Não integrado | Sem credenciais/capacidade homologada |
| Web Push (VAPID) | Implementado e testado (unit + e2e) | Dispositivo físico + HTTPS público |
| Provedores de IA (8) | Catálogo, rotas, fallback, orçamento, custo estimado | Chaves reais do proprietário |
| Oracle Cloud ARM64 | Preparação entregue (empacotamento, posse, runbook, job ARM64) | VM + autorização de deploy |
| Assinatura de código | Não contratada | Certificado do proprietário |

## 8. Diagnóstico de credenciais e segurança (V8.1 §13.9)

- **Em repouso (Windows):** segredos cifrados com **DPAPI** por usuário; migração verificada de instalações
  anteriores; testes de vazamento/rotação/revogação (`auditoria-4b83e16-sec1-segredos-em-repouso.md`).
- **Portão do dono:** credencial em **Argon2id** (migração silenciosa do `owner_password.txt` provada no bundle —
  C017/C018 item 17); `SINGLE_OWNER_ENFORCED` é trava de código; sem MULTI-TENANT.
- **Repositório:** gitleaks no CI **sem allowlist**; SBOM + gate de licenças; nenhum segredo real em teste
  (fixtures sintéticas); export de mudança **sem segredos** (`segredos_omitidos`).
- **LIVE:** fail-closed em profundidade — FIN-1 (mapeamento de todos os caminhos), FIN-2 (matriz adversarial dos
  gates), selo de código obrigatório, REAL `disabled` na interface, `DINHEIRO REAL BLOQUEADO` no painel.
- **Nunca** foram lidas/impressas cópias de `.env`, tokens ou chaves; nenhuma chave fictícia foi usada para
  declarar homologação.

## 9. Artefatos reproduzíveis (V8.1 §13.12)

| Artefato | Como reproduzir |
| :--- | :--- |
| ZIP Windows + Setup + SHA256SUMS | push na branch `fix/instalador-atualizacoes-windows` → job `build` do workflow `Windows Release` |
| Aceite no bundle (13/13) | mesmo run → job `aceite-bundle` (baixa o ZIP, valida checksums, roda Playwright contra o bundle em runner virgem) |
| Matriz do instalador | workflow de matriz em runner Windows virgem (`38512611447`; evidência curada em `e2e/matriz-38512611447/`) |
| Evidências dos harnesses | artefatos de CI (`aceite-td5-bundle-evidencias`, matriz) + cópias curadas em `docs/mission/evidence/` |
| Suíte completa local | `uv run pytest -q`; e2e: `uv run --with playwright pytest tests/e2e -q` |

## 10. Documentação de instalação e recuperação (V8.1 §13.13)

`README.md` (instalação/uso) · `DEPLOY.md` · `RUNBOOK.md` (PC→Oracle) · `ROLLBACK.md` (backup/rollback) ·
`WINDOWS-LOCAL-FIRST.md` · `docs/mission/evidence/inst6-upgrade-real-0.4.0-0.5.0.md` (upgrade real) ·
`RELATORIO-PROPRIETARIO-V8.md` (guia em português simples).

## 11. Nota de governança (V8.1 §13, cláusula final)

Este relatório **não** certifica o produto como financeiramente aprovado, e **não** autoriza operar com capital
real. A aprovação de um agente, de um teste ou de uma estratégia **nunca** substitui a autorização explícita do
proprietário. A etapa seguinte é a auditoria independente (ChatGPT + Claude), conforme desenho da missão.

# HANDOFF — TradingAgents (Claude ⇄ Codex ⇄ Prado)

> Arquivo de coordenação da pasta compartilhada. Cada rodada APENDA uma seção.
> Regras: Claude escreve em `claude-execucao\` e entrega em `entrada\`;
> Codex audita em `codex-auditoria\` (imutável) e salva relatórios em
> `aprovados\`; ninguém edita a cópia do outro; promoção só após veredito.

## Estado do projeto (2026-09-06)

- **Base auditada:** TradingAgents-main.zip — SHA-256
  `1364592cc4bb21b8a424161ebe0e9047195321b2ef89138a03d59c0ccf634e5e`
- **Roadmap fechado:** Fase 0 → P4+P7 → P2 → P1 → P3 → P6 → P5 → brokers PAPER/SHADOW
- **Gate A (segurança):** r6 reprovado (7 achados) → r7 reprovado (4 achados)
  → r8 reprovado (2 bypasses no redator; 3/4 fechados) → **r9 FOCAL entregue, EM AUDITORIA**
  - r7 REPROVADO (4 achados) → r8 entregue:
  - `fase0-r8-integrated.zip` — `54fc073a37dff7b6c6abe1e7255c9732cfcefaf1e7da174afd8a9d5c031c13fa`
  - `fase0-r8-evidence.zip` — `6808fdaaf81d8da3fcf92cf6b8f99f73109458924c69afdb9335200077c415aa`
  - Suíte r8: 770 passed / 2 skipped / 71 subtests / 0 xfail
- **Gate B (PIT/Runtime) — PENDENTE, bloqueia P1/P2:** fundamentals por data
  de publicação; Polymarket histórico ou fail-closed em backtest;
  `available_at` nas fontes; RunContext imutável; remoção de config global;
  `workflow_schema_version` no checkpoint; manifesto reproduzível.
- **P4/P7:** design aprovado para evolução isolada
  (`design-envelope-telemetria-p4p7.md`); implementação após aprovação do
  Gate A, sem promover baseline reprovado.
- **Decisões fechadas (não reabrir sem evidência nova):** separação de
  credencial (chave oficial nunca em origem custom; relays via
  `openai_compatible`); sizing determinístico fora do LLM; backtest v1
  honesto-mínimo com 6 condições; fill em raw next-open; timestamp lógico
  `decision_at = session_close(D, tz)`; P5 = Postgres + worker Python
  (`FOR UPDATE SKIP LOCKED`), ADR na fase; `available_at` desconhecido =
  exclusão no histórico.
- **Follow-ups declarados:** proxy corporativo como config explícita com
  proveniência; transporte dos SDKs nativos (anthropic/google); evidência
  tipada na origem para Polymarket/fundamentals; envelope-no-estado no P3.

## Protocolo por rodada

1. Claude implementa em `claude-execucao\`, roda a suíte integral e gera
   `entrada\gateX-rN-integrated.zip` + `gateX-rN-evidence.zip` (+ SHA-256 aqui).
2. Codex copia para `codex-auditoria\`, audita, salva relatório em
   `aprovados\` e registra veredito aqui (APROVADO / REPROVADO + achados).
3. REPROVADO → Claude corrige na rodada seguinte, mesmo fluxo.
   APROVADO → Prado promove o ZIP para `aprovados\` e o baseline avança.
4. Ações destrutivas, custo financeiro ou credenciais: só com confirmação
   explícita do Prado, registrada aqui.

## Log de rodadas

### 2026-09-06 — Gate A r7 (Claude → Codex)
- Entrega: 7/7 achados da auditoria r6 corrigidos; patch autocontido.

### 2026-09-06 — Gate A r7 — VEREDITO: REPROVADO (Codex)
- Hashes conferidos; suíte REPRODUZIDA independentemente (761/2/71).
- 4 achados: Azure sem transporte endurecido; gate aceita estado anônimo;
  envelopes persistindo no histórico do debate; redação sem JSON/Basic.
- Relatórios em aprovados/ (pacote codex-gate-a-r7-audit.zip,
  SHA-256 C4CE4CD0677AF85B76172F8951F46806D40C5A84FB2B3B5A88A726C7B21EEFC1).

### 2026-09-06 — Gate A r8 — VEREDITO: REPROVADO (Codex, scan a8d45554)
- 33/33 manifesto ok; suíte reproduzida (770/2/71). 3/4 achados FECHADOS
  (Azure, identidade do gate, raw/wrapped history). Redator PARCIAL:
  2 bypasses baixa severidade (Basic curto 'YTpi'; aspa escapada em JSON
  vaza sufixo). R9 deve ser ESTRITAMENTE FOCAL no redator.
- Pendências preservadas p/ Gate B: checkpoints r7 contaminados não migrados;
  mark sem digest imutável de RunContext; consumidor broker obrigatório de
  assert_secure_for_capital inexistente (não há broker ainda).

### 2026-09-06 — Orquestrador v1 — VEREDITO: REPROVADO (Codex, revisão estática)
- Bloqueadores: aprovação forjável; read-only × relatório; TOCTOU; baseline;
  zip slip; guarda financeira léxica. NÃO EXECUTAR o v1.

### 2026-09-06 — Orquestrador v2 — VEREDITO: REPROVADO (1 alto, 4 médios, 1 baixo)
- F1 alto: mesma identidade de SO ⇒ nonce/canal forjáveis por processo
  residual. Gate de 8 itens definido para um v3 aceitável.

### 2026-09-06 — Orquestrador v3 — VEREDITO: REPROVADO (2 críticos, 3 médios)
- P0: pytest de commit não-confiável rodava como coordenador no host;
  .git montado permitia escape por hooks. + injection no manifesto, contêiner
  sem quotas, gate humano pós-efeitos; 7 defeitos de QA (inclusive {{ROOT}}
  literal e ANTHROPIC_API_KEY nunca passada). Exigida REVISÃO ARQUITETURAL.

### 2026-09-06 — Orquestrador v4 — REPROVADO p/ congelamento; DIREÇÃO APROVADA
- 10 achados ARQ-01..10 (3 P0): tar como parser privilegiado; commit nascendo
  na autoridade; JUnit auto-atestado; sanitização carregada do candidato;
  HANDOFF como autoridade de aprovação; CI existente ≠ isolamento por padrão
  (socket Docker!); broker/egress conceituais; promoção não-transacional;
  política desprotegida; digest/SBOM ≠ proveniência. Exigida v4.1 documento.

### 2026-09-06 — Orquestrador v4.1 — REPROVADO p/ congelamento; direção mantida
- 10 achados V41-01..10 (3 P0): /out é filesystem hostil (captura imutável +
  openat/no-follow exigidos); pytest do candidato pode neutralizar acceptance
  (harness blindado exigido); veredito do LLM ≠ autorização (renomear para
  AUDITOR_RECOMMENDED; promoção = gates determinísticos + aprovação humana
  assinada); + aprovações em 2 registros com log ancorado, git por plumbing,
  worker inteiro descartável, contrato de aplicação do broker, 8 estados,
  anti-rollback, verify-attestation. 9 itens aprovados sem reabertura.

### 2026-09-06 — Orquestrador v4.2 (Claude) — EMENDAS E1..E10 entregues p/ veredito
- Documento curto: v4.1 + 10 emendas normativas, uma por bloqueador.

### CORREÇÃO DE ESTADO — Gate A r9: REPROVADA (não "em auditoria")
- Informada pela auditoria v4.1: r9 reprovada com UM achado focal no redator.
- BLOQUEIO: o relatório da auditoria r9 NÃO chegou ao Claude — a r10 não
  será fabricada por adivinhação. Ação do Prado: anexar no chat do Claude o
  relatório r9 (AUDITORIA/CODEX_SECURITY do r9) para a r10 focal.

### 2026-09-06 — Orquestrador v4.1 (registro anterior)
- Manifesto+blobs por hash (sem tar); quarentena com config git explícita e
  autoridade recebendo só digest aprovado; supervisor atesta (JUnit=anexo);
  acceptance tests imutáveis do repo de política; orq-core vendoriza a
  segurança (nada carregado do candidato); aprovação humana = assinatura
  minisign com TTL/nonce/append-only (HANDOFF=recibo); topologia sem socket
  do host e sem coabitar produção (decisão A/B ADIADA até a fronteira
  existir); máquina de estados PREPARED→AUDITED→APPROVED→PROMOTED com
  journal fsync; broker com chave virtual/orçamento/egress por rota;
  orq-policy protegido; imagens assinadas (cosign) com builder allowlist.

### 2026-09-06 — Orquestrador v4 — (registro anterior)
- Documento sem código: 3 identidades reais; travessias só como DADOS
  (snapshot tar, git de autoridade do coordenador — mata os 2 críticos);
  verificador em contêiner sem rede/segredos; evidência autocontida com
  nonce+veredito; feedback read-only; gates/egress/credencial-broker ANTES
  da execução; promoção transacional; imagens por digest. Recomendação:
  substrato de CI existente (Gitea+act_runner) em vez de script artesanal —
  tie-break do Prado. Implementação só após veredito da arquitetura.

### AVISO LOGÍSTICO — Gate A r9 segue NÃO AUDITADO
- codex51.zip não continha os dois ZIPs r9. Enviar codex6.zip com EXATAMENTE:
  fase0-r9-integrated.zip (9cece007…8386) + fase0-r9-evidence.zip (c1aabb0d…7872).

### (obsoleto) Orquestrador v3 (Claude) — entregue p/ re-revisão estática
- Python; implementador em contêiner efêmero (fronteira de identidade real);
  staging 0700 fora da raiz; nonce pós-contêiner; disciplina estrita do
  auditor (exit 0, 1 veredito, última linha); porcelain --untracked-files=all
  + ancestralidade; coordenador roda a suíte e sela manifesto; capital-facing
  = exit 75 + humano; flock/uuid/cleanup; sem sed.

### (obsoleto) Orquestrador v2 (Claude) — entregue p/ re-revisão
- Nonce pós-fase + veredito por stdout; orquestrador único escritor;
  git archive/worktree (sem extração de zip do implementador); baseline por
  commits; controle determinístico no diff (allowlist+denylist).

### 2026-09-06 — Gate A r9 — REPROVADA (finding sensitive-data.redaction-json-bypass)
- JSON truncado preservava o segredo (regex exigia aspa de fechamento) e
  chave escapada api\u005fkey escapava do match literal. Relatório repassado.

### 2026-09-06 — Gate A r10 — VEREDITO: REPROVADO (scan 04b32298)
- Fechou os 2 PoCs da r9; suíte reproduzida (802/2/71); 33 arquivos
  byte-idênticos conferidos. 2 achados P3/Low novos: F1 valores NÃO-STRING
  sob chave sensível em JSON truncado vazam (scanner exigia aspa do valor);
  F2 RecursionError com JSON profundo (~2KB) — cap de conteúdo roda DEPOIS
  do redator e não limita o parser; escapa por UntrustedEvidence.

### 2026-09-06 — Orquestrador v4.2 — VEREDITO: REPROVADA p/ congelamento (8/10 aprovados)
- 2 bloqueantes: P0-E2 (harness importava candidato no MESMO processo —
  import é execução; acceptance autoritativo deve ser black-box em
  processo/guest separado); P1-E6 (identidade "não exportável" dentro do
  guest = oráculo de assinatura; supervisor/assinador fora, sem alternativa).
- Pacote da auditoria: codex-auditoria-files123-v4.2.zip
  (5e447ad58c12da8d23bdd594d3cfc8eefa3c2befb1764fbddc581f087636d59d).

### 2026-09-06 — Orquestrador v4.3 — VEREDITO: APROVADA — ARQUITETURA CONGELADA
- E2' (acceptance black-box por vetores) e E6' (assinador fora do guest,
  alternativa revogada) aceitos; 8 controles da v4.2 + 2 emendas = baseline
  arquitetural SELADO (hash 9619e8c7dabffac449e8ee4be1c8decbb92c80a5702c8f926cfd079a50a4422c).
- Próximo da trilha (pós-Gate A): fronteira de execução (§7) → decisão A/B
  do Prado → implementação componente a componente (normalizador primeiro),
  cada uma com auditoria própria. Nenhum código antes disso.

### (registro) — Orquestrador v4.3 (Claude) — entrega
- Só E2' e E6'; os 8 controles aprovados intocados. E2': acceptance por
  VETORES do repo de política, candidato roda em executor descartável sem
  rede lendo stdin/devolvendo JSON, harness afere as propriedades ele mesmo,
  desvio de protocolo = falha fechada; pytest do candidato = anexo.
  E6': cláusula alternativa revogada; nenhum material/capacidade/canal de
  assinatura em guest que execute candidato; evidência sai como dados,
  supervisor assina após captura, telemetria interna = anexo.

### 2026-09-06 — Gate A r11 — VEREDITO: REPROVADO (scan 7672b597)
- F1/F2 da r10 FECHADOS por reprodução (36/36 arquivos; suíte 834/2/71;
  profundidades 63–1500 sem exceção). 1 achado novo P3: F1-r11 — contador
  de profundidade aceitava fechamento TROCADO (']' fecha '{'), encerrando a
  redação cedo e preservando sufixo do segredo; contradiz o fail-closed
  declarado.

### 2026-09-06 — ★ GATE A ENCERRADO — r12 APROVADA (scan 440ca000) ★
- Arquivos verificados em C:\TradingAgents-Handoff\entrada\ (tamanhos e
  hashes exatos); manifesto 37/37; suíte independente 849/2/71; fuzz
  determinístico seed 120043: 500 válidos + 500 mismatches, 0 vazamentos;
  0 findings reportáveis. Pilha tipada confirmada como correção do F1-r11.
- Não-bloqueantes anotados: ruff E402 em redact.py:57 (cosmético, NÃO tocar
  fora de marco); lock/manifesto multiplataforma reproduzível → Gate B.
- LIBERA: Gate B e evolução isolada de P4/P7. NÃO libera P1/P2/PAPER/
  SHADOW/corretora/capital (bloqueados pelos marcos do Gate B + auditorias).

### 2026-09-06 — PROTOCOLO C ADOTADO (decisão do Prado)
- Blocos maiores via Claude Code local em C:\TradingAgents-Handoff\
  claude-execucao; entrega por marco em entrada\ com manifesto, hashes e
  suíte reproduzível.
- Codex audita marcos de: Gate B/PIT; sizing determinístico; backtest/PnL;
  concorrência e checkpoint; integração PAPER/SHADOW/broker.
- P4/P7 e mudanças internas de baixo risco: revisão agrupada.
- Nenhum marco promovido só com testes do implementador. PAPER/SHADOW/
  capital fail-closed até aprovação independente.
- Papel do chat Claude: arquitetura, debates e PROMPTS DE MISSÃO.

### 2026-09-06 — Gate A r12 (registro da entrega)
- Diff r11→r12: SOMENTE redact.py + teste novo. Pilha TIPADA de fechamentos
  esperados; mismatch/pilha-vazia nunca termina o valor — consumo até EOF.
  As 3 evidências literais + duas ordens de mismatch × chave literal/escapada
  + mismatch profundo + controles válidos mistos + idempotência + regressão
  r8–r11. Suíte: 849 passed / 2 skipped / 71 subtests / 0 xfail.
- fase0-r12-integrated.zip — cfaa360084bfd2dbc2e609837ef1653a860c99a860f21ae96e851b9baa4473e3
- fase0-r12-evidence.zip — 99151851f993e14b6e6fffd4fcb8630cd6884262711ed7919261abc0435818bf

### (registro) — Gate A r11 — entrega anterior
- Diff r10→r11: SOMENTE redact.py + teste novo (34 byte-idênticos, amostras
  no RUN-INFO). F1: _consume_value tipo-completo (string/objeto/lista/
  número/bool/null/ambíguo, fail closed, truncado→EOF), chave decodificada.
  F2: orçamento de profundidade+tamanho ANTES do parse (O(n) early-exit),
  walk iterativo por pilha, RecursionError capturado; acima do limite cai
  no scanner bounded — nunca exceção, nunca passthrough.
- Matriz: todos os pontos de truncamento × 7 valores × chave literal/escapada,
  via UntrustedEvidence; limite-4 processa, limite+1/200/1000/1500 sem
  exceção; profundo+truncado+sensível; regressões r8–r10 preservadas.
- Suíte: 834 passed / 2 skipped / 71 subtests / 0 xfail.
- fase0-r11-integrated.zip — d6effcd5cc077b51b2cef22b5b2666d871a46113e4342b2bc8b19eb556136af3
- fase0-r11-evidence.zip — c235caeccf94138405444b07a359d39d660a10c91caf3b250df6c327ecc0ac16

### (registro) — Gate A r10 — entrega anterior
- Diff r9→r10: SOMENTE redact.py + teste novo. Passe estruturado (parse
  recursivo com normalização semântica) para JSON íntegro; scanner textual
  com máquina de escape para fragmento/truncado (segredo consumido até aspa
  não-escapada ou fim da entrada — nunca devolvido por falha de parse).
  Matriz completa: todos os pontos de truncamento, chave escapada, ausência
  de segredo, nested, regressão Basic/Bearer/param/env, idempotência.
- Suíte: 802 passed / 2 skipped / 71 subtests / 0 xfail.
- fase0-r10-integrated.zip — 36fbd5d22dd28e5fd256319a76c160578afca747a2f6d4c6aab4b4cf752112af
- fase0-r10-evidence.zip — fda720f09d37b176076eb172c4a42195a91f720986355373e70670043ae05d61
- Diff r8→r9: SOMENTE redact.py + teste novo; demais correções congeladas.
- A1 Basic separado (token não-vazio, PoC YTpi coberto; fronteiras 4/8/longo);
  A2 JSON escape-aware (sem vazamento de sufixo, saída JSON válida,
  aninhado/\uXXXX/inválido cobertos).
- Suíte: 783 passed / 2 skipped / 71 subtests / 0 xfail.
- fase0-r9-integrated.zip — 9cece007a5ee056cdc8755fccf5840e988a7c07710060b409dc88fc725cb8386
- fase0-r9-evidence.zip — c1aabb0ddf6b2b7654aa7dcada81d0301b75f5747620302c83d25c02ca607872

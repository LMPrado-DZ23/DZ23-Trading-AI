# DZ23 TRADING AI — LISTA DE COMMITS E PRs (V8.1 §13.2)

> Gerado por `git log --oneline main..HEAD` em 2026-10-14 (branch `fix/instalador-atualizacoes-windows`).
> Base: `main` (`80e99fb`). **202 commits** de entrega da missão V7+V8+V8.1 — geração em `abe18cc` (a lista não inclui o commit da própria regeração).

## Pull Requests

| PR | Conteúdo | Estado |
| :--- | :--- | :--- |
| #3 / #5 | correções iniciais auditadas (P0) | mergeados |
| #7 | integração mestre (V7→V8) | aberto na branch de integração |
| #10 | C017 — login do dono único premium | mergeado |
| #11 | kernel fix — "primeiro Salvar" efetivo | mergeado |
| branch `feat/qoder-maestro-traders` | entrega M01 (Maestro + 10 traders) | publicada; mergeada por fast-forward (`d6491ab`) |

## Fases (ver `TRADING-CHECKPOINTS.md` para o detalhe de cada uma)

C002–C004 (auditoria/integração) · P0 (CI/fees/kernel/single-owner/LIVE) · G3–G8 (gates e instalador) ·
INST-1..6 (instalador/upgrade real) · SEC-1/FIN-1..3 (segurança e gates financeiros) · PWA-1..5 · M01 ·
BT-1..10 (Escola de Traders) · E1–E6 (exit, alertas, copiloto, evolução, multimercado) ·
V8-§6a–e (provedores de IA) · SS-1..5 (Smart Setup + Modo Simples) · TD-1..5 (dashboards/UX/aceite) ·
V8.1 (decisão do ciclo + índices de risco) · RC §19 (pacote de entrega final).

## Commits (mais recente primeiro)

```
abe18cc RC §17 (task #106): guias de instalação do produto real — fim dos guias SaaS no repositório
d131226 RC §19 (task #104): pacote de entrega final — A matriz de requisitos, B relatório de homologação, C relatório ao proprietário + checklist de auditoria independente e registro do capital-piloto (V8.1 §12)
16a8807 V8.1 §4+§6 (tasks #102/#103): decisão do ciclo OPERAR/AGUARDAR/BLOQUEAR/SUSPENDER + volatilidade/Sharpe/Sortino honestos na sala; aceite TD-5 com evidências (e2e local + bundle)
b3e9dae TD-5/#100: aceite-bundle — venv do runner com extras (pytest) e diagnóstico de coleta (matriz TD-5 no bundle; missão V8 §4/§16)
63a4622 TD-5/#100: aceite E2E no bundle do artefato CI em ambiente isolado (missão V8 §4/§13)
ac2a1bc TD-5: matriz de aceite E2E (13 testes) + fixes responsivos 320px (missão V8; task #94/#99)
f02be5f TD-4: Dashboard do Copiloto Manual — sinais determinísticos + botão "Acompanhar esta operação" (missão V8; task #93)
b4a6f61 TD-3: Gráficos profissionais (Lightweight Charts vendorizado + avaliação TradingView) e IA lendo gráficos (missão V8; task #92)
d40c788 TD-2: Salas individuais dos 10 traders — gráfico de mercado + portfólio do ledger (missão V8; task #91)
996b7f4 TD-1: Painel Global do Maestro — segregação PAPER/TESTNET/LIVE + moedas como invariante (missão V8; task #90)
c93f41d SS-5: Modo Simples (padrão) + Controle Avançado + assistente CONECTAR/MODO/PLAY (missão V8; task #89)
79db0fd docs(mission): V8 §6 + Smart Setup — checkpoint, gates e estado (tasks #79-#88)
c38473e SS-3: matriz de cenários R14 do Smart Setup em instância isolada (missão V8)
74d669f SS-2: painel do Smart Setup — assistente de primeira execução, diagnóstico em 4 estados e correção do gráfico de fundo (missão V8)
e592c13 SS-1: Smart Setup backend — pré-config idempotente, perfis e roteamento (missão V8)
3756db0 V8 §6d: UI do painel — provedores de IA, chaves, fallback, orçamento e consumo
13276e2 V8 §6c: consumo, custo estimado e orçamento de IA (precos + /v1/ai/uso)
41bb168 V8 §6b: rotas /v1/ai/* (catálogo, testar, modelos) + remoção de credenciais
8d5f522 V8 §6a: conserta consulta de IA do service (IA de contexto e diário)
7a83168 docs(mission): V8-PWA — checkpoint, gate G7 e estado da missão (PWA + push)
bc0ba68 feat(pwa): Web Push real (VAPID) + Service Worker versionado + instalacao no painel (complemento V8)
413a974 docs(mission): V8 §5 — causa-raiz da notificacao duplicada (corrida de despacho) + checkpoint V8-S16 (porta 8000)
f7f1f0f fix(alertas): despacho serializado por fila — fim da notificacao duplicada (§5)
74c319d test(launcher): isolar probe de saude do cenario backend-morto (porta 8000)
cb54353 docs(mission): auditoria 4b83e16 — evidencias por achado (SEC-1/FIN-1/FIN-2/FIN-3) + gates/checkpoints/estado
aade521 fix(fin3): reconciliacao usa a identidade DA CORRETORA, nao o id interno (auditoria ChatGPT 4b83e16)
3ee3fe0 test(fin2): matriz adversarial dos gates financeiros por achado (auditoria ChatGPT 4b83e16)
a0997df fix(sec1): segredos em repouso cifrados com DPAPI + migracao verificada (auditoria ChatGPT 4b83e16)
e66f2b7 fix(fin1): mesa recusa corretora LIVE sem selo de codigo (auditoria ChatGPT 4b83e16)
4b83e16 docs(mission): auditoria final de fechamento — CI do push E6 verde (runs 38546817504/38546817421) + TRADING-GATES atualizado
87a14da feat(multimercado): E6 — fundacao multimercado (classes de ativo + sessoes fail-closed + salas com classe) + checkpoint
a55ec73 feat(evolucao): E5 — evolucao autonoma de estrategias (champion/challenger + promocao so PAPER + rollback/suspensao no vigia) + checkpoint
9c3a4e9 docs(mission): INST-5 e INST-6 FECHADOS — matriz VERDE (run 38512611447, TOTAL 0 falhas) + evidencias do upgrade real 0.4.0->0.5.0
486a60b docs(mission): state no 5d5983c — E4 commitado (6ed001b) + matriz re-executando com fix do C8
5d5983c fix(matriz): C8 — espera a auto-remocao do unins000.exe (2a fase do Inno) antes do check
6ed001b feat(painel): E4 — pagina "Minhas Operacoes" (copiloto manual) + correcao estrutural do HTML + E2E
98e9857 docs(mission): checkpoints E3, N01 e INST-1..5 (+ state no fab7815); INST-5 em fechamento (matriz CI)
fab7815 fix(launcher): --quit com encerramento gracioso real (INST-5)
8e2127a fix(notificacoes): identidade Windows (AUMID) + rotulos de modo honestos (PAPER/TESTNET/LIVE)
1ec84b4 feat(copiloto): API HTTP do Copiloto Manual (E3) — rotas read-only com guardas
a444d2c fix(instalador): SHA256 e IO via .NET puro nos scripts de backup/restauracao
ba28e2e feat(copiloto): dominio do Copiloto Manual (E3) — posicoes, eventos, politica e monitor read-only
5529ae6 fix(instalador): MOTIVO de falha do backup persistido ao lado dos backups + matriz a prova de travamento
9c0f7bc feat(instalador): atualizador Windows com versao detectada, backup verificado e fechamento seguro
30b8683 feat(alertas): centro inteligente de alertas (M5) — fila durável, canais priorizados, matriz e painel
06e4c4b feat(saida): motor global de saída (M3/M4) — gestão versionada por posição, execução autônoma no ciclo e livro durável
f3986f2 docs(programa): spec consolidado E1-E6 das missões (copiloto manual, exit manager global, alertas, multimercado, evolução)
34a91b4 docs(mission): C021-6 item 22 — recuperação de crash 30/30 no bundle de f07ea47
f07ea47 fix(desk): risco lê o livro vivo desde o 1º boot e reinício preserva estado financeiro
f24f27d docs(mission): C021-5 item 21 — escola/mesa autenticada 27/27 no bundle de f26f1ed
6c86aa6 docs(mission): C021 fechado no bundle de f26f1ed — matriz 24/24 e item 18 27/27
f26f1ed feat(escola): BT-10 mesa↔escola E2E, vigia contextual e arbitragem determinística
8bf78ad feat(escola): BT-9 rotas /v1/escola, painel Escola Central e storage seguro p/ Windows
d40ba13 feat(conhecimento,maestro): BT-7 prior rotulado entre salas + BT-8 Maestro Central
42429c4 feat(salas): BT-6 Escola Central + salas independentes por corretora
bf50022 feat(piloto): BT-3 cadeia FORMADO->ELEGIVEL->AGUARDANDO->AUTORIZADO com gates e vigia
04e6c6f feat(laboratorio): BT-2 hipóteses pré-registradas com assinatura, execução única, controle aleatório e Bonferroni
134c05b feat(escola): BT-1 núcleo da Escola de Traders (estados, IC vs equilíbrio, prova independente, revogação)
d8053fd docs(mission): checkpoint C021 — causa-raiz provada e correção verificada em binário --windowed
ac3bad5 fix(launcher): C021 — causa-raiz da falha de inicialização sem console + portas e logs persistentes
a4065ea docs(mission): checkpoint C020 — identidade visual definitiva + login premium
962021c feat(identidade): C020 — identidade visual definitiva + login premium
732f9f5 docs(mission): checkpoint C019 — portabilidade (modo da mesa, posse exclusiva, export sem segredos, CI Postgres/ARM64)
8e369f1 fix(ci): fixtures de teste sem valores com cara de segredo (gitleaks)
977adce feat(portabilidade): C019 — modo da mesa + posse exclusiva, export sem segredos, CI postgres/ARM64
2cc1d5c docs(mission): C018 — itens 16–17 homologados no bundle da integração
d8892dc fix(kernel): primeiro Salvar do painel não derruba a mesa
2347270 docs(mission): C017 — portão do dono único + auditoria open source (suíte 2226, e2e 9/9)
5405916 chore(open-source): auditoria de licenças, docs públicas e CI de supply-chain
9fe7537 feat(auth): portão do dono único — Argon2id, assistente de primeira execução e UX premium
5fea049 feat(panel): C016 — seletor "Fonte de dados" (fixture × mercado real) na Configuração
b64c374 docs(mission): C015 — G8 com evidência do item 15 e suíte atualizada (2193)
56bccd0 docs(mission): C015 — painel sem o SaaS morto homologado no bundle (item 15)
ba3307d feat(panel): C015 — remover DOM morto do SaaS do painel do dono
2ca1f95 docs(mission): C014 — operação PAPER ponta a ponta no bundle (item 14)
6c3940e docs(mission): C013 — homologação do bundle da entrega M01 (item 13)
d6491ab feat(desk): MISSÃO QODER 01 — 10 traders por evidência (tiers, gate, painel) + homologação final do bundle (C012)
0c3fc26 merge: integra PR #8 — fail-closed de dono único é política de código (C011)
477433f merge: integra base feat/integracao-mestre e resolve conflito de política SaaS
690fd04 fix(desktop): porta 8000 ocupada por outro programa deixa de ser fracasso silencioso (C010)
f8254ab feat(desktop+ux): saída do usuário no app windowed (botão Fechar aplicativo + /v1/app/quit) e homologação completa do artefato C008
142e030 fix(runtime+security): homologacao do bundle real corrige launcher, trava 429 do dono e cache do floor
0e6fdeb test(e2e): suite Playwright com servidor real, fix ruff I001 e SHA256SUMS com LF
0eb40a2 fix(windows+ux): dono entra no painel (login e2e), floor SAMEORIGIN, launcher cria senha na 1a execucao, SHA256SUMS padrao, install sem UAC
e3e6239 ci(windows): build installer artifact on branch push, release only on tags
2562bd6 feat(desk): ten specialized traders, quant intelligence layer and owner panel
281296e fix(stores): cap Postgres connects at 5s so dead endpoints degrade
8ce9f23 fix(ci): troca smoke multi-tenant por smoke de dono único
09409ae fix(security): G2 single-owner — desativa superfície SaaS de forma fail-closed
5032a9d docs(mission): require ten specialized traders with gated supervisor and evidence
70b153d feat(windows): integrate Inno Setup compiler and enhance PyInstaller bundling in release pipeline
10a3897 fix(tests): add recursive route introspection helper for IncludedRouter instances
5ebe457 fix(windows): resolve Windows file locking, POSIX permissions, and FastAPI route inspection failures
7cfff6b fix(lint): organize imports and remove unused imports for ruff
bd710ae feat(security): add security incident center router
9ee6296 feat(api): add automated diagnostics router
2e269b2 feat(quant): add monte carlo simulation engine
971f6ea feat(desk): add telegram and discord notification engine
69e5004 ci(github): add windows release build workflow
90c069b feat(windows): add Inno Setup installer script
25b463b feat(windows): enhance installer with desktop shortcuts and pyinstaller builder script
f713afd fix(windows): correct launcher healthcheck endpoint and update TRADING-GATES evidence references
1fb8e68 feat(windows): add single-owner desktop launcher, powershell installer and matrix docs
54dffe5 docs(product): external AI APIs only for Windows-first trading; defer Ollama
e0278ef docs(product): require local Windows-first single-owner trading app, not server deploy
340ff95 fix(service): fallback to local data dir when /data is not writable
34fb76b fix: organize imports and resolve B008 default argument lint error
9ec6aa9 fix: specify utf-8 encoding in test_frontend_contract.py for Windows compatibility
d37e81e Merge branch 'correcao-auditoria' into feat/integracao-mestre-20261012
0b99d47 feat(integracao): merge PR #5 (catalogo pessoal CCXT, discovery e docs de missao)
9db4eb3 fix(py310): adiciona shim de StrEnum e fallback para tomli no Python 3.10
2229e89 fix(security): corrige as 4 vulnerabilidades que quebravam o dependency audit no CI
a580f93 ci: bissecção empírica da falha de runners (A checkout, B concurrency, C matrix)
b24e830 ci: workflow de diagnóstico para provar causa raiz dos runners
2625a32 docs: add mission checkpoint C000 for G0 audit baseline and tracking files
62ad0ce docs(mission): checkpoint C001 and CI blockers
d1575a4 docs(mission): log prioritized blockers and external CI evidence
4a7bcf7 feat(personal): authenticated local-only CCXT capability endpoint
3680a2d feat(personal): add non-executing CCXT capability inspection
4fb920f feat(personal): add non-executing CCXT capability inspection
b78063c test/docs: secure exchange catalogue endpoint and checkpoint C000
c11e347 test/docs: secure exchange catalogue endpoint and checkpoint C000
c8f5e0d feat(personal): expose authenticated read-only exchange inventory endpoint
ee266f8 docs: define expandable exchange coverage and validation gates
ff3386b feat(personal): dynamically discover installed CCXT exchanges without trade permissions
e377e1f feat(personal): dynamically discover installed CCXT exchanges without trade permissions
65fdb8b feat(personal): add fail-closed market quote integrity gate and tests
72f6fbe feat(personal): add fail-closed market quote integrity gate and tests
90e0527 feat(personal): add docs/personal-trading/ESCOPO-PESSOAL.md
357186e feat(personal): add tests/test_personal_exchange_catalog.py
37add4d feat(personal): add service/personal_exchange_catalog.py
126e5e9 feat(personal): add .env.personal.example
c02f456 docs: define personal multi-exchange trading lab and safe live gates
247038c fix(smoke): corrige o KillSwitch do TenantRuntime (causa do public smoke 500)
3715a19 fix(ci/compat): restaurar compatibilidade real com Python 3.10
e51ce53 docs(finalizacao): corrige gate 1 (CI so roda via PR, nao em push da branch)
d6472bd docs(finalizacao): patch F1/F2 completo (B1-B10); fecha B5/B6
8b265ce docs(risk): fronteira de concorrencia multiprocesso explicita (B6)
cad7a5c fix(ledger): identidade do componente de taxa como dado duravel (B5)
5ffa8c8 docs(finalizacao): fecha B4; proximos B5, B6, F3/F4
a4e9490 fix(risk): valorar_taxa congela quantidade e batch de componentes (B4)
8b65804 docs(finalizacao): fecha B2/B3; registra proximos B4-B6
7f5b714 fix(fees): valoracao operacional exige lastro e soma exata (B2/B3)
6384c11 docs(finalizacao): registra B1/B7/B8/B9; reabre F1/F2; gates e evidencia
b199ee1 fix(risk): identidade canonica na adocao de recovery (B1/B9)
5563dcd fix(fees): fingerprint canonico normalizado + policy version (B7)
7884687 fix(ledger): restore revalida dimensoes (B8)
40dbbf3 feat(fees): fingerprint da valoracao; retry com conteudo diferente e conflito
f28dc55 feat(fees): lastro por COMPONENTE, nao por saldo acumulado
e678871 docs(finalization): baseline reproduzida e mapa de blockers
8925e0d fix(fees): passagem unica pelos componentes; moeda canonica; ambiguidade estruturada
ef2a234 fix(fees): remove incidente espurio; todas as moedas nativas chegam ao livro
aed6334 fix(fees): todo componente produz efeito; valorar nao quita a origem
363b8ed feat(ledger): partida dobrada na moeda funcional; subledger nativo por moeda
853ea10 style: suppress explicito no teste de concorrencia (ruff SIM105)
5cd2c1e feat(fees): valoracao exige lastro nativo e e exactly-once
fee75cd feat(fees): moeda sobrevive do broker ao livro; FEE_CURRENCY_UNKNOWN
b488eb3 fix(risk): guard decide por INTENCAO; ACK chega a RESOLVED; preflight de consistencia
8b0f912 fix(risk): ativo descoberto nao se resolve com texto; submit consulta guard real
3829e39 fix(fees): deteccao de ativo descoberto por moeda, idempotente e sem efeito colateral
c69fc46 fix(fees): valorar e APLICAR sao atos distintos; ativo descoberto vira incidente
4632dae feat(fees): taxa tem MOEDA — FEE_VALUATION_UNKNOWN em vez de soma errada
c48bd21 fix(ui): olho de senha duplicado e campos descobertos
4ef11ce fix(status): parser estrito de flag e escopo publico declarado
485c030 fix(ui): semantica de produto — estado operacional vem do backend
1b00320 fix(risk): recuperacao usa incident_id E settlement_id; estado nao retrocede
a9ffce6 feat(ui): site publico, ver senha e entrar com Google
3068d8d fix(risk): liquidacao de incidente serializada, idempotente e recuperavel
b51df7d fix(risk,ledger): compensacao pertence a UM incidente; tempo do livro e duravel
6e133ea fix(risk): resolver deficit exige COMPENSACAO registrada, nao descricao
b16c2c2 fix(risk): deficit com fonte unica; resolucao exige capacidade e estado corrigido
7236df8 fix(risk): incidente financeiro e entidade propria e bloqueia ate ser resolvido
c1efbaa fix(ledger): capital travado por RESERVA, nao num bolo comum
4b46348 fix(capital): liberacao de reserva canonica; marca do produto na interface
ad8c6da fix(capital): uma referencia economica apos restore; folga de execucao financiada
57fbaa3 fix(risk): custo REAL vs cobertura — deficit de reserva vira incidente
17bcd50 fix(risk): uma reserva financia UMA ordem; quantidade consumida avanca
1f82126 fix(capital): dinheiro investido nao ressuscita; reserva preserva a base original
2259192 fix(capabilities,identity): PRESENTE nao e prova; normalizacao de identidade unica
3e1bfa8 feat(reconciliacao): descoberta de ordens orfas + estado real de capacidade
22e3d1b feat(reconciliacao): matriz de capacidades — limite estrutural separado de divergencia
bf2fd2b fix(reconciliacao): relatorio duravel, identificado e com validade explicita
7748872 fix(reconciliacao): efeitos economicos vem dos FILLS; acumulado e so invariante
5c0f4a9 fix(risk): consumo incremental de reserva — livro e risco contam o mesmo dinheiro
944ca7d fix(oms): autorizacao de ENTRADA simetrica a da saida no caminho unico
f4b6c1e refactor(oms): caminho unico de execucao — ExecutionIntent + submit_intent
8ff1590 fix(ledger,oms): posicao por LOTE de agente — fim da posse agregada
312b803 fix(oms): entrada ambigua tranca novo risco; cancelamento e posse corrigidos
f9870ac fix(oms): posicao zero nao autoriza venda; saida verifica o mandato
dc99233 fix(oms): travas de seguranca na saida, custo persistido e reconciliador legado domado
d0164e1 fix(oms): ACK nao e execucao, e a saida passa a ser ordem do OMS
340fd1b style: remove if aninhado desnecessario no restore do indice de idempotencia
c70ced7 fix(oms): idempotencia pela chave certa, uma unica funcao de fill, ausencia so com prova
2b05095 fix(broker,oms): ausencia precisa de CONFIRMACAO, reserva proporcional e versao persistida
2d76095 fix(reconciliacao): venda reconciliada fecha posicao, consulta ausente e UNKNOWN, adaptador real
85e4716 fix(reconciliacao): exchange-first — todas as ordens vivas, posicoes e fills
a358385 fix(oms,ledger,risk): reidratação financeira real, reconciliação com a corretora e equação de capital verificável
d72f83e fix(policy): backtest e produção passam a usar a MESMA política de saída
07e60ba fix(oms,risk,tenancy): estado financeiro durável, parada persistente e credencial isolada
65e4a0b test: teste de classificação de erro passa a exercitar o caminho real
c71d7b3 style: ruff limpo após as correções
424ab6e fix(execution): preserva timeout ambíguo, liga freio diário ao livro e dá procedência real às ordens
```

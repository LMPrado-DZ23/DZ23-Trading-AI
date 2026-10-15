# F7.2 — Rotas: recontagem no HEAD atual e classificação

Artefato de evidência da Fase 7 (cobertura e documentação). Recontagem dos
números do relatório funcional anterior (145 rotas / 52 sem interface direta /
16 sem teste direto) contra o HEAD atual, classificação de todas as rotas
apontadas pelos scanners e verificação da superfície SaaS (G2).

- HEAD base: `2b7754a94cc1e4233ad95ac6fa377be4078e5055` (+ correções V9 ainda
  não commitadas — ver `V9-BASELINE.md`).
- Ferramenta: `tools/v9_route_inventory.py` — conta rotas declaradas por
  decorador em `service/app.py` e `service/api/*.py` e classifica cada rota por
  **referência textual** da própria rota em (a) HTML estático do painel e
  (b) arquivos de teste.

> Aviso do próprio scanner (docstring): "Uma referência textual NÃO é prova de
> cobertura comportamental executada: apenas localiza se algum consumidor/teste
> menciona o caminho." O mesmo vale para o marcador `Depends(...)`: indica que
> a rota declara uma dependência (tipicamente de autorização), não que ela
> nega acesso de fato. Onde isso importa, este documento verifica o código.

## 1. Artefatos e reprodutibilidade

| Inventário | Arquivo | sha256 (16 hex) | Estado medido |
|---|---|---|---|
| PRÉ-correção | `f7-rotas.json` | `a06cbda23b572c96` | HEAD `2b7754a`, com routers `diagnostics`/`security_center` montados |
| PÓS-correção | `f7-rotas-pos-sec06.json` | `54fd401763299128` | `2b7754a` + remoção V9-SEC-06 (routers eliminados) |

Comando (reprodutível): `python tools/v9_route_inventory.py --json docs/mission/evidence/v9-baseline/f7-rotas-pos-sec06.json`

## 2. Recontagem

| Métrica | PRÉ | PÓS | Δ |
|---|---|---|---|
| rotas total | 148 | 146 | −2 (remoções §3) |
| com referência de UI | 79 | 79 | 0 |
| sem referência de UI | 69 | 67 | −2 (as duas removidas não tinham UI) |
| com referência de teste | 132 | 131 | −1 (ver nota) |
| sem referência de teste | 16 | 15 | −1 (ver nota) |
| sem UI e sem teste | 13 | 13 | 0 |
| com marcador `Depends` | 121 | 121 | 0 |
| sem marcador `Depends` | 27 | 25 | −2 (as duas removidas) |

Nota sobre a aritmética de testes: as duas rotas removidas tinham referência de
teste (`tem_teste: true` no inventário PRÉ), o que removeria 2 de
`com_referencia_teste` (132→130); `/readyz` deixou a lista de "sem teste"
(16→15) porque os arquivos de teste atualizados agora **mencionam textualmente**
`/healthz` e `/readyz` em comentários. Resultado: 132 − 2 + 1 = 131. Essa +1 é
referência textual, não cobertura comportamental — ver §6 (riscos residuais).

Os números do relatório anterior (145/52/16) foram produzidos em HEAD anterior
com critério próprio; a recontagem acima é a medição ligada a este HEAD, com
ferramenta e hashes registrados. A diferença de total (145→148→146) decorre de
o scanner atual contar todas as rotas declaradas por decorador no app completo
(incluindo estáticas/PWA) — critério explicitado na docstring da ferramenta.

## 3. Rotas eliminadas (2) — análise de dependências

Missão, Fase 7: "Elimine rotas obsoletas somente com análise de dependências."
Achado de origem: V9-SEC-06 ("respostas autenticadas derivadas de estado real;
sem alegação fictícia de banco saudável" — `V9-BASELINE.md:86`).

| Rota | Handler | Módulo | UI | Teste (PRÉ) | Destino |
|---|---|---|---|---|---|
| `POST /v1/diagnostics/run` | `run_diagnostics` | `service/api/diagnostics.py:8` | não | sim (assert 200 + import em teste Windows) | removida; arquivo deletado; import+`include_router` removidos de `service/app.py` |
| `GET /v1/security/incident-center` | `get_security_status` | `service/api/security_center.py:6` | não | sim (assert 200) | removida; arquivo deletado; import+`include_router` removidos de `service/app.py` |

Análise de dependências executada antes da remoção:

1. `grep -rn "security_center\|diagnostics" service/ tests/ ui/` — nenhum
   importador restante após as edições; nenhuma referência em UI (ambas com
   `tem_ui: false` no inventário PRÉ).
2. Ambos os stubs devolviam **resposta fixa e sem autenticação** — "banco
   saudável" e "status de segurança" fabricados, exatamente o objeto do
   V9-SEC-06.
3. As superfícies reais equivalentes já existem e permanecem:
   - `/healthz` (público: status, versão, armazenamento, **banco real**, avisos);
   - `/readyz` (público: kill-switch + saúde do PAPER + sequência de eventos);
   - `GET /v1/execution/kill-switch` (autenticado).
4. Alternativa "reimplementar com resposta autenticada derivada de estado
   real" foi descartada: duplicaria superfícies existentes (a missão proíbe
   recriar/crescer escopo) e ampliaria área autenticada sem consumidor.

Verificação RED→GREEN (evidências em `docs/mission/evidence/v9-baseline/`):

- RED — `v9-sec06-red.log` / `v9-sec06-red-invocation.json` (sha log `8dd66290`):
  com os routers ainda montados e os testes atualizados para exigir 404:
  `2 failed, 2 passed in 0.78s` (falhas = `assert 200 == 404`).
- GREEN (auditado) — `v9-sec06-green.log` / `v9-sec06-green-invocation.json`:
  runner com audit hook (proíbe escrita em `winreg`, rede só loopback):
  `4 passed in 5.22s`, exit 0. Corroboração em execução simples: `4 passed in
  0.43s` + `ruff: All checks passed!`.
- Travas anti-remontagem deixadas no lugar:
  - `tests/unit/test_diagnostics.py` → exige 404 em `POST /v1/diagnostics/run`;
  - `tests/unit/test_security_center.py` → exige 404 em
    `GET /v1/security/incident-center`;
  - `tests/windows/test_installer_and_packaging.py:171+` → passou a afirmar o
    bloqueio na **fonte real** (`service.app.MESA_LIVE_LIBERADA is False`) em
    vez do stub removido. Nenhum assert foi enfraquecido (os asserts ficaram
    estritamente mais fortes: 404 obrigatório onde antes se aceitava 200).

## 4. As 25 rotas sem marcador `Depends` — classificação

Nenhuma rota desta lista fica sem controle por omissão do marcador. A
classificação abaixo foi verificada no código (não apenas no inventário):

### Grupo A — 10 estáticas/infra/PWA (públicas por desenho)

`GET /` (site.html público), `GET /app`, `GET /app/` (index.html),
`GET /favicon.ico`, `GET /healthz` (status/banco reais), `GET /readyz`
(kill-switch + PAPER), `GET /manifest.webmanifest`, `GET /sw.js`,
`GET /static/marca/{arquivo}` (allowlist de 10 nomes), `GET /static/vendor/lightweight-charts/{arquivo}`
(allowlist de 3 arquivos). Não carregam dado pessoal; os dados operacionais
ficam atrás da API autenticada.

### Grupo B — 8 rotas SaaS sombreadas pelo G2 (existem no código, respondem 404 sempre)

`POST /v1/auth/bootstrap`, `POST /v1/auth/login`,
`GET /v1/auth/oauth/providers`, `GET /v1/auth/oauth/{provider}/start`,
`GET /v1/auth/oauth/{provider}/callback`, `POST /v1/auth/signup`,
`GET /v1/auth/signup-status`, `POST /v1/webhooks/woovi`.
O middleware G2 responde 404 incondicional antes de autenticação e antes do
roteamento — ver §5.

### Grupo C — 3 rotas de autenticação do dono (sem `Depends` por desenho)

`GET /v1/auth/status` (público e honesto: informa se o dono já configurou;
sem dado sensível — coberto por `test_auth_status_is_public_and_honest`),
`POST /v1/auth/setup` (primeira configuração do dono), `POST /v1/auth/recover`
(recuperação com código do dono). São a própria superfície de autenticação do
produto pessoal; o fluxo usa `ACCESS_PASSWORD`/código de recuperação, não
sessões multi-tenant.

### Grupo D — 3 públicas de conteúdo com dados protegidos

`GET /floor` (shell visual do Trading Floor; dados só pela API autenticada),
`GET /publico/{tenant_id}` (404 salvo `TRANSPARENCIA_PUBLICA=true`; nunca
saldo/chave/ordem), `GET /v1/status/operacao` (capacidade/rollout do processo;
sem dados de inquilino).

### Grupo E — 1 webhook fail-closed

`POST /v1/telegram/webhook` — autenticação própria por HMAC
(`hmac.compare_digest` contra `ALERTA_TELEGRAM_WEBHOOK_SECRET`); 401 sem
segredo válido. Por isso não usa `Depends(auth)`.

Total: 10 + 8 + 3 + 3 + 1 = 25 ✓ (bate com `sem_marcador_depends` do
inventário PÓS).

## 5. Superfície SaaS — verificação G2

- `SAAS_BLOCKED_PREFIXES` — `service/app.py:267-277` — 9 prefixos:
  `/v1/auth/signup`, `/v1/auth/signup-status`, `/v1/auth/login`,
  `/v1/auth/bootstrap`, `/v1/auth/oauth`, `/v1/admin/tenants`,
  `/v1/admin/users`, `/v1/billing`, `/v1/webhooks/woovi`.
- `rota_saas_bloqueada(path)` — `service/app.py:280` — casamento por SEGMENTO
  (igualdade exata ou prefixo + `/`), para não atingir vizinhos legítimos
  (ex.: `/v1/admin/database` — inspeção do próprio banco pelo dono — não é
  afetado por `/v1/admin/tenants`).
- Middleware `bloqueio_single_owner` — `service/app.py:286-298` — responde 404
  JSON **antes da autenticação e antes do roteamento**, para QUALQUER método e
  QUALQUER credencial. Incondicional: não lê nenhuma flag de ambiente.
- `SINGLE_OWNER_ENFORCED = True` — `service/app.py:112` — constante de código
  (não flag).
- Prova adversarial: `tests/test_single_owner_g2.py` carrega o app no pior
  cenário (flags SaaS ligadas de propósito: `MULTI_TENANT_ENABLED`,
  `SIGNUP_ENABLED`, chaves Woovi/tenant) e exige 404 em toda a superfície:
  teste parametrizado sem auth e com auth do dono, corpo de signup válido →
  404, webhook Woovi assinado e válido → 404 e **nada é ativado**, vizinhos
  legítimos (`/v1/config`, `/v1/admin/database`) continuam acessíveis,
  `/healthz` continua alcançável.

Conclusão: não existe superfície SaaS **ativa** no produto. As 8 rotas do
Grupo B existem apenas como código sombreado que nunca responde (404 sempre).

## 6. As 15 rotas sem referência direta de teste — avaliação e ações

Todas têm `Depends` (autenticadas); o que falta é referência textual direta
em teste. Cobertura indireta avaliada por grep do nome do handler em
`tests/`:

| # | Rota | Handler | Cobertura indireta (avaliação) |
|---|---|---|---|
| 1 | `GET /metrics` | `metrics` | hits textuais em testes de métricas/PAPER (podem ser homônimos — não confirmado como cobertura da rota); **F8: teste HTTP direto de auth adicionado — §8** |
| 2 | `POST /v1/agency/agents/{agent_id}/status` | `agency_agent_status` | nenhum hit |
| 3 | `POST /v1/agency/meetings` | `agency_meeting` | nenhum hit |
| 4 | `POST /v1/agency/meetings/{meeting_id}/votes` | `agency_vote` | nenhum hit |
| 5 | `GET /v1/agency/memory` | `agency_memory` | nenhum hit |
| 6 | `GET /v1/agency/stream` | `agency_stream` | nenhum hit |
| 7 | `POST /v1/agency/tasks/{task_id}/approve` | `agency_approve_task` | nenhum hit |
| 8 | `POST /v1/alertas/verificar` | `disparar_alertas` | nenhum hit |
| 9 | `GET /v1/backups` | `list_backups` | nenhum hit |
| 10 | `POST /v1/broker/order` | `broker_order` | nenhum hit pelo nome do handler; módulo subjacente tem suíte de contrato (`tests/test_broker_contrato.py`); **F8: teste HTTP direto — 410 permanente + 401 sem auth — §8** |
| 11 | `POST /v1/execution/paper` | `paper_execution` | nenhum hit pelo nome do handler; **F8: HTTP direto — 423 fail-closed, 200 só com MATCHED provado, kill-switch, 422/401 — §8** |
| 12 | `POST /v1/execution/reconciliation` | `execution_reconciliation` | nenhum hit pelo nome do handler; **F8: HTTP direto — UNKNOWN honesto sem remoto + 401 — §8** |
| 13 | `GET /v1/execution/shadow` | `shadow_summary` | nenhum hit; **F8: HTTP direto — resumo reflete trades, orders_sent=0 — §8** |
| 14 | `POST /v1/execution/shadow` | `shadow_execution` | nenhum hit; **F8: HTTP direto — registra intenção com orders_sent=0, 422/401 — §8** |
| 15 | `POST /v1/jobs/{jid}/cancel` | `cancel` | hits textuais em outros testes (homônimos prováveis) |

Leitura honesta: referência textual ≠ cobertura; ausência de referência ≠
ausência de uso (fluxos E2E podem exercitar rotas sem citá-las). O grep por
nome de handler é grosseiro (homônimos). O que fica comprovado nesta fase é:
(a) nenhuma das 15 está exposta sem autenticação; (b) não há evidência
textual de teste direto. A decisão de adicionar testes HTTP de regressão para
as financeiramente sensíveis (#10–#14) fica registrada como item de Fase 8
("Adicione testes quando houver risco relevante") — sem enfraquecer nada
existente. **Executado na F8: os testes HTTP de regressão existem para
#10–#14, #1 e `/readyz` — ver §8.**

## 7. Riscos residuais / itens de acompanhamento

1. `/readyz` — **RESOLVIDO na F8**: teste comportamental completo em §8
   (`test_readyz_reports_real_state_and_never_dies`: contrato 200 + estado
   real + transição para `blocked` com kill-switch engatado).
2. 15 rotas sem referência direta de teste (§6) — **as financeiras #10–#14
   (+ #1 `/metrics`) ganharam testes HTTP diretos na F8 (§8)**; as demais
   (#2–#9, #15: agency/alertas/jobs) seguem sem teste direto, todas
   autenticadas e não-financeiras — item aberto, sem risco financeiro.
3. `/metrics` — **teste HTTP de auth adicionado na F8 (§8)**; o consumidor
   externo (scrape/observabilidade) permanece não confirmado — a rota exige
   credencial, então o item segue como observação, não como risco.

## 8. F8 — testes HTTP de regressão das rotas financeiras (§6/§7)

Arquivo novo: `tests/test_v9_f8_rotas_financeiras_http.py` (14 testes, todos
herméticos — runner `tools/v9_pytest.py`, sem rede externa, sem winreg).

Cobertura por rota:

| Rota | Testes | Contrato provado |
|---|---|---|
| `POST /v1/broker/order` | 2 | 410 permanente ("removido" + aponta `/v1/execution/paper`); 401 sem auth |
| `POST /v1/execution/reconciliation` | 2 | sem remoto: 200 com `UNKNOWN`/`safe=false`/mismatch `RuntimeError` (honesto, fail-closed); 401 sem auth |
| `POST /v1/execution/paper` | 5 | 423 enquanto reconciliação insegura **e nada muda no adapter**; com MATCHED espelhado: 200 → risk APPROVE → ordem FILLED + posição no snapshot oficial; 422 sem campos; 401 sem auth; kill-switch engatado → 423 (com reconciliação já MATCHED, o 423 é atribuível ao kill-switch) |
| `GET /v1/execution/shadow` | 3 | resumo inicial zerado; após registro: `trades=1`, `orders_sent=0`; auth |
| `POST /v1/execution/shadow` | (idem) | registra intenção (`status OPEN`, `orders_sent=0`), 422 side inválido, 401 |
| `GET /readyz` | 1 | 200 com estado real (`status ready`, `kill_switch false`, `paper.environment PAPER`, `paper.account_id paper-account`, `event_sequence` int); após engage: `blocked` + `kill_switch true` |
| `GET /metrics` | 1 | 401 sem auth; 200 com snapshot real |

Prova de bloqueio real (não decorativo): o teste do MATCHED espelhado usa
`ReconciliationMonitor(snapshot, snapshot)` — o remoto É o próprio adapter,
logo reconciliar contra ele é genuinamente MATCHED (orders/fills/positions/
saldos iguais item a item). O portão do router lê o monitor do módulo em
tempo de chamada, então o monkeypatch vale para rota e guard simultaneamente.
A execução só é aceita quando existe um MATCHED verdadeiro; sem ele, o
adapter fica comprovadamente intocado (`orders == []`, `positions == []`).

Evidência RED→GREEN (documentos em `docs/mission/evidence/v9-baseline/`):

- RED — `f8-rotas-red.{log,xml,json}`: primeira execução `3 failed, 10 passed
  in 31.92s`. As 3 falhas eram **expectativas de teste erradas, não defeitos
  do produto** (o produto estava certo nas 3): (a) reconciliação sem remoto
  reporta honestamente UNKNOWN/unsafe (nunca MATCHED); (b) execução em papel
  permanece 423 mesmo depois de rodar a reconciliação insegura; (c)
  `paper.environment` é `"PAPER"` (case do enum). Expectativas corrigidas —
  nenhum assert enfraquecido, todos ficaram mais estritos.
- RED (2ª iteração) — `assert 'REJECT' == 'APPROVE'` no teste do MATCHED
  espelhado: causa-raiz encontrada no motor de risco (`risk_pct_equity`
  declarado `0.02` > teto `max_risk_pct_trade=0.005` → `TRADE_RISK_LIMIT`).
  Correção no **dado do teste** (`0.002`), não no produto — o motor estava
  correto ao rejeitar.
- GREEN — `f8-rotas.{log,xml,json}`: `14 passed`.

Efeito colateral encontrado e corrigido (hermeticidade, Fase 8): a suíte
completa hermética (`full-3`) revelou 5 falhas em
`tests/test_structured_agents.py::TestSentimentAnalystAgent` — os testes
chamavam a rede real (`api.stocktwits.com` → bloqueado pelo audit hook com
`RuntimeError("audit permits loopback network only")`, não capturado pelos
`except` do fetcher). Diagnóstico: falha de higiene de teste (dependência de
API de terceiros), não defeito de produção — os testes cobrem render/fallback
do LLM, não a rede. Correção: fixture autouse que isola os 3 prefetchers
(`fetch_stocktwits_messages`, `fetch_reddit_posts`, `get_news`) no namespace
do módulo, sem enfraquecer nenhum assert.

- RED hermético direcionado — `f8-sentiment-red.log`: `5 failed in 7.58s`
  (`RuntimeError: audit permits loopback network only`).
- GREEN hermético — `f8-sentiment.log`: `5 passed in 6.28s` (sem rede).

Nota de risco residual (produto): os fetchers degradam para todos os erros de
rede reais (`OSError`/`HTTPException`/`JSONDecodeError` → placeholder). Um
`RuntimeError` de fora (ex.: hooks de monitoramento) escaparia — só se
materializou no ambiente hermético de teste; em produção, risco avaliado como
irrelevante e registrado aqui para auditores.

Confirmação em suíte completa: `full-4` (rodada hermética pós-correção via
`tools/v9_pytest.py`) — **3181 passed, 0 failed, 5 skipped, 71 subtests passed
in 195.9s, exit 0** (`full-4.xml`: `tests=3257` com subtests, `failures=0`),
contra `full-3` pré-fix (`5 failed, 3162 passed, exit 1`). Os mesmos 5 testes
que falhavam passam offline; nenhum assert foi alterado.

---

Evidências correlatas: `V9-BASELINE.md` (achados V9-SEC-06 e colisão
Playwright), `v9-sec06-*.{log,json}`, `f7-rotas.json`,
`f7-rotas-pos-sec06.json`, `tools/v9_route_inventory.py`,
`tests/test_v9_f8_rotas_financeiras_http.py`,
`f8-rotas-red.*`, `f8-rotas.*`, `f8-sentiment-red.*`, `f8-sentiment.*`.


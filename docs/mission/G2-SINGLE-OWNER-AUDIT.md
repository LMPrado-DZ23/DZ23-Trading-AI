# G2 — Auditoria e Correção de DONO ÚNICO (Single-Owner)

- **UTC:** 2026-10-13
- **Branch:** `fix/g2-single-owner-20261013`
- **Base verificada (HEAD):** `70b153d744037a9ca17db1bbd797abed0ec739bc`
- **Execução:** worktree git isolada + venv própria (editable install da worktree),
  para não colidir com o agente Qoder, que opera em paralelo na branch
  `feat/integracao-mestre-20261012` (observado avançar durante este trabalho).
- **Status do gate:** **NÃO** declarado PASS. A fronteira de segurança HTTP está
  **comprovada por teste adversarial executado**; itens cosméticos/de produto
  seguem em aberto (ver §7). Integração controlada pelo Qoder.

---

## 1. Resumo executivo

A auditoria confirmou o achado anterior: **o aplicativo ainda era um SaaS
operacional**, não um app pessoal. O backend expunha e executava cadastro
self-service (cria inquilino + trial), cobrança PIX via Woovi, login social
OAuth, administração de inquilinos e um portão de assinatura (HTTP 402) sobre
a mesa de operações. O documento `TRADING-GATES.md` declarava `G2 = PASS`;
o comportamento real contradizia a declaração.

Esta correção fecha a superfície SaaS de forma **fail-closed e incondicional**
(independe de flags, método HTTP ou autenticação), preservando todo o código e
os dados legados, e **prova** o fechamento com uma suíte adversarial.

Nenhum dado foi apagado. Nenhuma operação real foi habilitada.

## 2. Inventário da superfície SaaS (antes)

Rotas registradas e acionáveis em `service/app.py` no HEAD base:

| Rota | Método | O que fazia |
| :--- | :--- | :--- |
| `/v1/auth/signup-status` | GET | vitrine pública do cadastro (dias de trial, planos) |
| `/v1/auth/signup` | POST | cria inquilino + admin + trial + sessão |
| `/v1/auth/login` | POST | login multi-tenant (emite token de inquilino) |
| `/v1/auth/bootstrap` | POST | bootstrap do primeiro inquilino |
| `/v1/auth/oauth/providers` | GET | lista provedores de login social |
| `/v1/auth/oauth/{p}/start` | GET | inicia fluxo OAuth |
| `/v1/auth/oauth/{p}/callback` | GET | cria conta via login social + trial |
| `/v1/admin/tenants` | POST | cria inquilinos |
| `/v1/admin/users` | GET/POST | lista/cria usuários de inquilino |
| `/v1/billing/planos` | GET | catálogo de planos pagos |
| `/v1/billing/status` | GET | status da assinatura |
| `/v1/billing/checkout` | POST | gera cobrança PIX |
| `/v1/webhooks/woovi` | POST | ativa plano ao receber pagamento assinado |

Efeitos colaterais acoplados:
- `require_plan` impunha **HTTP 402** na mesa quando a assinatura expirava.
- `auth` trocava todo o modelo de autenticação do processo para multi-tenant
  quando `MULTI_TENANT_ENABLED=true` — ou seja, uma flag de ambiente redefinia
  a autenticação de TODAS as rotas.

## 3. Correção implementada (`service/app.py`)

Três mudanças cirúrgicas, sem remover código legado:

1. **Dono único forçado na origem.** Constante de código `SINGLE_OWNER_ENFORCED =
   True` que, **antes** de qualquer construção de store, força
   `MULTI_TENANT_ENABLED = False` e `MULTI_TENANT_REQUESTED = False`. Assim a
   autenticação do processo inteiro usa sempre a senha do dono
   (`ACCESS_PASSWORD`) e nunca o fluxo de inquilinos — mesmo que o ambiente
   peça multi-tenant.

2. **Middleware de bloqueio incondicional** (`bloqueio_single_owner`). Roda
   ANTES da autenticação e do roteamento, para QUALQUER método, e responde
   **404** a toda a superfície SaaS (`SAAS_BLOCKED_PREFIXES`). É fail-closed por
   construção: não lê nenhuma variável de ambiente, logo configuração incorreta
   não o reabre. Casamento por segmento de caminho, para não atingir vizinhos
   legítimos como `/v1/admin/database`.

3. **Portão de plano neutralizado.** `require_plan` retorna cedo no modo dono
   único: o dono tem direito pleno, não há 402. A assinatura continua
   registrada apenas para preservar dados antigos.

Toda a lógica de billing/Woovi/tenancy/OAuth permanece no repositório como
biblioteca preservada (nada apagado); apenas deixou de ser alcançável.

## 4. Prova adversarial (`tests/test_single_owner_g2.py`)

A suíte carrega o app no **pior cenário**: `MULTI_TENANT_ENABLED=true`,
`SIGNUP_ENABLED=true`, `WOOVI_WEBHOOK_PUBLIC_KEY` presente, `WOOVI_TOKEN`,
`TENANT_*` e credenciais OAuth todas configuradas. Mesmo assim prova:

- Toda rota SaaS responde **404** para **GET/POST/PUT/DELETE/PATCH**, com e sem
  o Bearer do dono.
- `MULTI_TENANT_ENABLED` é **forçado a False** no import; `TENANT_STORE is None`.
- Um **webhook Woovi assinado e válido** (que ativaria um plano se a rota
  existisse) responde 404 e **não altera nenhuma assinatura**.
- Vizinhos legítimos seguem vivos: `/healthz`, `/v1/auth/status` (reporta
  `multi_tenant=false`, `live_trading=false`), `/v1/admin/database` (não é
  404 por bloqueio), e a mesa (`/v1/desk/status`) responde 200 **sem 402**
  mesmo com assinatura expirada.

> A primeira execução da suíte **reprovou** e revelou uma brecha real na lógica
> de prefixo (`/v1/auth/signup-status` não casava com `/v1/auth/signup`), que
> foi corrigida. Isto é evidência de que o teste prova comportamento, não
> slogan.

Testes existentes foram **reconciliados** (não apagados): a lógica de módulo
(cripto do webhook Woovi, matemática de assinatura, `SubscriptionStore`,
helpers OAuth/PKCE/anti-CSRF, `TenantRuntime`) continua testada e passando; os
testes que exercitavam os **endpoints** SaaS foram convertidos para provar o
bloqueio.

## 5. Comandos de verificação (reproduzíveis)

```bash
# na worktree isolada, com a venv própria:
python -m pytest tests/test_single_owner_g2.py -q
python -m pytest tests/test_signup.py tests/test_billing_woovi.py \
                 tests/test_oauth_social.py tests/test_kill_switch_runtime.py \
                 tests/test_site_publico.py -q
python -m pytest -q        # suíte completa
ruff check .
```

## 6. Auditoria do caminho `mode=live` do CCXTBridge

Arquivo: `tradingagents/brokers/ccxt_bridge.py`.

**Achado 1 — docstring contradiz o código (defeito).** O cabeçalho do módulo
afirma: *"MODO LIVE (dinheiro real): removido nesta branch segura; nenhuma
configuração pode criar um cliente com endpoints reais."* **Isto é falso.** O
construtor possui um ramo `mode == "live"` totalmente funcional que cria um
cliente CCXT **real** (sem `set_sandbox_mode`).

**Achado 2 — a execução real É possível, porém fortemente travada.** Para
enviar uma ordem com dinheiro real é preciso satisfazer TODAS estas condições,
todas vindas de ambiente/estado (nenhuma acionável por rede anônima):

1. `TRADINGAGENTS_BROKER_MODE=live`;
2. `LIVE_TRADING_CONFIRM` **exatamente** igual à frase
   `"EU AUTORIZO OPERAR COM DINHEIRO REAL"`;
3. `EXCHANGE_API_KEY` + `EXCHANGE_API_SECRET` reais;
4. `LIVE_MAX_ORDER_VALUE > 0` e `LIVE_MAX_DAILY_LOSS > 0`;
5. o `final_state` passar por `assert_secure_for_capital` (marca de transporte
   do Gate B) — incontornável e aplicado a TODOS os modos;
6. kill-switch não acionado, preço de referência disponível (ordem sem teto é
   recusada) e perda diária abaixo do limite.

Ou seja: o caminho é **deliberado e multi-travado**, não acidental. Porém a
política da missão (G9) é PAPER/TESTNET apenas, com LIVE `BLOCKED_BY_POLICY`
até **nova decisão humana explícita fora desta missão**. Hoje a ativação
depende apenas de configuração de ambiente — uma trava mais fraca do que
"nova solicitação humana".

**Ação tomada:** conforme instrução, **nada foi habilitado nem alterado** neste
gate. Recomendações para decisão humana (NÃO implementadas aqui):
- Corrigir o docstring para refletir a realidade (ou, se a decisão for manter
  PAPER/TESTNET puro, adicionar uma trava fail-closed de código que recuse
  `mode=live` da mesma forma incondicional usada no G2).
- Registrar a decisão em `docs/mission/TRADING-DECISIONS.md`.

(Observação: `tradingagents/mt5/adapter.py` também lê `LIVE_TRADING_CONFIRM`;
mesma recomendação se aplica, fora do escopo desta correção.)

## 7. Escopo entregue e remanescentes conhecidos (honestidade de estado)

**Entregue e provado nesta branch:**
- Fronteira de segurança HTTP do G2 (superfície SaaS inalcançável), com prova
  adversarial executada e verde.
- Autenticação do processo forçada a dono único.
- Portão de assinatura (402) removido da mesa.
- **UI SaaS (DOM) removida — C015 (item 15, run `38456790614`):** os
  formulários de cadastro/bootstrap, a tela de login de inquilino, a página
  de planos/pagamento e o login social saíram do **DOM** (não apenas
  `hide`). O painel do dono tem **exatamente 6 páginas** (Visão geral,
  Configuração, Análises, Mesa de Agentes, Trading Floor, Risco); provado no
  bundle instalado em navegador real com **zero 5xx** e a ausência pinada
  por contrato de teste (`test_the_saas_dom_is_gone_not_just_hidden` e
  correlatos). Evidência: `WINDOWS-RELEASE-EVIDENCE.md` item 15.

**Remanescente (NÃO feito aqui — recomendado como follow-up focado):**
- **Guia de onboarding** (`/v1/primeiros-passos`): **re-verificado na C015 e
  já está limpo** — é um checklist do usuário final (IA + chave, dados de
  mercado, primeira análise, corretora de treino, ligar a mesa, backup), sem
  menção a Woovi/checkout/callbacks OAuth. A observação anterior desta
  auditoria estava desatualizada; nada a fazer.
- **Armazenamento de segredos via DPAPI/Windows Credential Manager** e
  **threat model** documentado: previstos no G2 amplo, ainda pendentes.

Por isso o G2 **não** é declarado PASS; declara-se comprovada a sua parte
crítica e de segurança, com os itens acima explicitamente em aberto.

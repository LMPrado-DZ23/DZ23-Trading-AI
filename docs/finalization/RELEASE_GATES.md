# RELEASE_GATES — DZ23 Trading AI

Frase oficial: **release candidate para PAPER/SHADOW/TESTNET e preparação
REAL; LIVE continua bloqueado.**

Este documento não afirma estado de produção. Toda evidência de deployment
deve citar ambiente, SHA e horário e ser refeita no host de destino.

## Gate de código desta branch

| Gate | Prova exigida | Estado no HEAD da branch |
|---|---|---|
| suíte Python | matriz CI 3.10–3.13 e suíte local | 3.691 passaram na última suíte local integral; 161 testes focados do fluxo Escola → Sala REAL passaram; matriz 3.10–3.13 verde no run `39016140316` do PR #14 |
| lint | `ruff check .` | verde no run `39016140316` do PR #14 |
| instalação limpa | job CI dedicado | verde no run `39016140316` do PR #14 |
| dependências/segredos/licenças/SBOM | jobs CI dedicados | verdes no run `39016140316` do PR #14 |
| PostgreSQL e ARM64 | jobs CI dedicados | verdes no run `39016140316` do PR #14; ARM64 valida dependências, não homologa host Oracle |
| pacote Windows | build, bundle acceptance/E2E e matriz de instalador | verdes nos runs `39016202897` e `39016204670`; publicação deliberadamente omitida |
| runtime da automação | Actions oficiais sem Node 20 depreciado | `checkout@v7`, `setup-python@v7`, `setup-uv@v10.3.0`, artifacts v7/v8 e gitleaks v3 validados nos mesmos runs |
| assinatura Windows | PFX somente no cofre, assinatura/verificação e limpeza efêmera | caminho implementado; build de branch provado como `UNSIGNED_TEST_ARTIFACT`; certificado confiável continua externo e tag `v*` falha fechada sem ele |
| LIVE fechado | testes + `/v1/operational-readiness` | `MESA_LIVE_LIBERADA=False` |
| rollback local | backup local + restore isolado + prova recente | aprovado em 2026-10-17: 7 itens, 3 bancos SQLite e 4 documentos JSON validados; `envio_de_ordens=false`; PostgreSQL externo continua condicional a `DATABASE_URL` |

Os testes adversariais desse total cobrem a sincronização persistida entre a
fila da Sala REAL e os estados autorizado/revogado/expirado/suspenso, a recusa
de limites financeiros `NaN`/infinitos, o bloqueio fail-closed de telemetria
de risco inválida, não finita ou negativa e a contenção transitória do replace
atômico no Windows. A identidade do encaminhamento também isola estratégia e
versão, com migração idempotente das chaves persistidas pelo formato anterior.
As nove corretoras CCXT do catálogo abrem salas PAPER spot isoladas na Escola.
A IQ Option permanece laboratório PAPER, mas uma estratégia formada pode
entrar na fila bloqueada da Sala REAL graças à integração MCP oficial; isso
não cria executor, não habilita LIVE e não dispensa a prova de conta somente
leitura. Seu boot de produção abre uma sala `opcoes_binarias` somente quando o
payout observado foi declarado explicitamente no painel e validado no
intervalo `(0, 1]`; ausência e valores inválidos falham fechado, sem sala e
com motivo visível.
Nenhuma dessas provas abre o selo LIVE.

Em 2026-10-17, a sonda pública foi repetida no HEAD
`e9a357a211b1f11c9d9ac03fc979546b03521749`: as nove corretoras CCXT
responderam com ticker e velas, sem credenciais e sem ordens. Essa prova
confirma somente conectividade de mercado público. Ela não certifica conta,
saldo, permissões, testnet, reconciliação nem execução.

## Preflight local isolado

Em 2026-10-17, o preflight `--target preparacao` foi executado contra uma
instância local descartável, sem credenciais de corretora e sem chamadas de
ordem. O serviço respondeu saudável, mas o preflight recusou prontidão com
`preparacao_pendente`, `selo_de_codigo_live=false`,
`envio_real_habilitado=false` e 14 pendências classificadas. O código de saída
2 é a prova fail-closed esperada; não é falha do release check. No mesmo estado,
`scripts/release_check.py` aprovou arquivos, defaults seguros, isolamento de
serviços e dependência PostgreSQL. Essa execução não homologa produção nem
substitui as provas externas abaixo.

## Gate do ambiente de destino

Todos os itens abaixo precisam de evidência nova no SHA implantado:

1. TLS/reverse proxy, DNS e acesso administrativo controlado;
2. volume persistente e reinício sem perda;
3. backup local criado, inspecionado e restaurado em diretório isolado;
4. backup lógico e restore do PostgreSQL, quando `DATABASE_URL` estiver ativo;
5. `/healthz`, `/readyz`, `/metrics` autenticado e logs sem segredos;
6. coleta de métricas e alertas com janela prolongada;
7. kill switch e reconciliação comprovados;
8. SHA implantado igual ao SHA aprovado;
9. preflight `--target preparacao` concluído;
10. nenhum 5xx novo e nenhuma ordem real enviada.

No host local atual, o item 3 foi concluído por restauração em diretório
temporário. Os itens 5 e 6 ainda não podem ser certificados como ambiente de
destino: o serviço observado está em HTTP local e não há Telegram, Discord,
WhatsApp ou WebPush configurado para comprovar entrega externa. A prova não
deve ser sintetizada; o verificador exige origem HTTPS, bearer, canal externo
e janela contínua de pelo menos uma hora.

## Gate externo por corretora/conta

- credencial somente leitura criada e identidade da conta comprovada;
- fill TESTNET reconciliado, quando a corretora oferece sandbox compatível;
- PAPER prolongado por agente/estratégia/conta;
- limites do piloto e tetos globais compatíveis;
- IQ Option com os sete handshakes passivos comprovados (7/7 em 2026-10-16),
  contrato exato pinado e prova `list_balances(NORMAL)` preparada; a primeira
  leitura autenticada da conta ainda é uma prova externa pendente,
  sem ferramenta mutável ou ordem chamada; o painel oficial confirma que é
  possível criar token com `Read access` e todos os `Trading access`
  desligados, mas o token DZ23 atual anuncia ferramentas mutáveis e não
  satisfaz esse gate;
- divergências e provas expiradas ausentes.

## Ações que continuam exigindo o proprietário no momento da ação

Merge definitivo, deploy de produção, DNS, migração destrutiva, rotação de
credencial crítica, ativação LIVE e qualquer ordem/dinheiro real. Preparar o
software não concede essa autorização. A frase de confirmação não é aceita em
`/v1/config`, não é persistida nem restaurada por backup; ela é validada apenas
no pedido final vinculado ao agente e à conta. O painel só apresenta essa ação
final quando `/v1/live/status` informa `ativacao_final.disponivel=true`; em
qualquer outro estado, a lista de agentes ativáveis e o botão ficam ausentes.
Mesmo nessa etapa, o proprietário precisa digitar a conta e a frase exatas e
confirmar novamente. A ação prepara a mesa com reconciliação obrigatória e não
envia ordem.

## Condição de liberação futura

Mesmo com todos os gates anteriores verdes, o estado esperado é
`aguardando_liberacao_de_codigo`. Abrir o selo é uma alteração de código
separada, revisada e confirmada pelo proprietário no momento da ação. Esta
branch não faz essa alteração. O modo da corretora também permanece em
PAPER/TESTNET durante a preparação e só muda para LIVE junto dessa etapa final.

# RELEASE_GATES — DZ23 Trading AI

Frase oficial: **release candidate para PAPER/SHADOW/TESTNET e preparação
REAL; LIVE continua bloqueado.**

Este documento não afirma estado de produção. Toda evidência de deployment
deve citar ambiente, SHA e horário e ser refeita no host de destino.

## Gate de código desta branch

| Gate | Prova exigida | Estado no HEAD da branch |
|---|---|---|
| suíte Python | matriz CI 3.10–3.13 e suíte local | 3.660 passaram localmente no código atual; a matriz deve estar verde no HEAD publicado do PR #14 |
| lint | `ruff check .` | verde localmente; o job deve estar verde no HEAD publicado do PR #14 |
| instalação limpa | job CI dedicado | exige job verde no HEAD publicado do PR #14 |
| dependências/segredos/licenças/SBOM | jobs CI dedicados | exigem jobs verdes no HEAD publicado do PR #14 |
| PostgreSQL e ARM64 | jobs CI dedicados | exigem jobs verdes no HEAD publicado do PR #14; ARM64 valida dependências, não homologa host Oracle |
| LIVE fechado | testes + `/v1/operational-readiness` | `MESA_LIVE_LIBERADA=False` |
| rollback | backup local + restore isolado + prova recente | implementado; ensaio no host ainda externo |

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
no pedido final vinculado ao agente e à conta.

## Condição de liberação futura

Mesmo com todos os gates anteriores verdes, o estado esperado é
`aguardando_liberacao_de_codigo`. Abrir o selo é uma alteração de código
separada, revisada e confirmada pelo proprietário no momento da ação. Esta
branch não faz essa alteração. O modo da corretora também permanece em
PAPER/TESTNET durante a preparação e só muda para LIVE junto dessa etapa final.

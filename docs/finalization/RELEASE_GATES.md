# RELEASE_GATES — DZ23 Trading AI

Frase oficial: **release candidate para PAPER/SHADOW/TESTNET e preparação
REAL; LIVE continua bloqueado.**

Este documento não afirma estado de produção. Toda evidência de deployment
deve citar ambiente, SHA e horário e ser refeita no host de destino.

## Gate de código desta branch

| Gate | Prova exigida | Estado no HEAD da branch |
|---|---|---|
| suíte Python | matriz CI 3.10–3.13 e suíte local | 3.591 passaram localmente; a matriz precisa estar verde no HEAD publicado do PR #14 |
| lint | `ruff check .` | verde localmente; o job do PR precisa corresponder ao HEAD |
| instalação limpa | job CI dedicado | exige job verde no HEAD publicado do PR #14 |
| dependências/segredos/licenças/SBOM | jobs CI dedicados | exigem jobs verdes no HEAD publicado do PR #14 |
| PostgreSQL e ARM64 | jobs CI dedicados | exigem jobs verdes no HEAD publicado do PR #14 |
| LIVE fechado | testes + `/v1/operational-readiness` | `MESA_LIVE_LIBERADA=False` |
| rollback | backup local + restore isolado + prova recente | implementado; ensaio no host ainda externo |

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
  sem ferramenta mutável ou ordem chamada; o token atual anuncia ferramentas
  mutáveis e não satisfaz o gate de escopo read-only no provedor;
- divergências e provas expiradas ausentes.

## Ações que continuam exigindo o proprietário no momento da ação

Merge definitivo, deploy de produção, DNS, migração destrutiva, rotação de
credencial crítica, ativação LIVE e qualquer ordem/dinheiro real. Preparar o
software não concede essa autorização.

## Condição de liberação futura

Mesmo com todos os gates anteriores verdes, o estado esperado é
`aguardando_liberacao_de_codigo`. Abrir o selo é uma alteração de código
separada, revisada e confirmada pelo proprietário no momento da ação. Esta
branch não faz essa alteração. O modo da corretora também permanece em
PAPER/TESTNET durante a preparação e só muda para LIVE junto dessa etapa final.

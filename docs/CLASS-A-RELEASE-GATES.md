# Gates de Release Classe A

Este documento separa claramente o que foi verificado no repositório do que depende do host, credenciais, terminal MetaTrader 5 ou revisão humana.

| Gate | Estado | Evidência ou ação necessária |
| --- | --- | --- |
| Suíte automatizada | PASS | 930 testes aprovados, 2 ignorados, 71 subtestes aprovados no ambiente sanitizado |
| Lint e compilação | PASS | `ruff` focalizado e `compileall` executados |
| API smoke | PASS | `scripts/smoke_agency_api.py` |
| Multi-tenancy smoke | PASS | `scripts/smoke_multitenant.py` |
| Job ledger local | PASS | SQLite com migração de schema legado e isolamento por tenant |
| Job ledger Enterprise | IMPLEMENTADO / EXTERNO | PostgreSQL selecionado por `DATABASE_URL`; requer container PostgreSQL real |
| PostgreSQL persistente | EXTERNO | Reiniciar container, verificar dados e restaurar backup |
| Redis persistente | EXTERNO | Validar AOF, reinício e comportamento de fila/cache |
| Qdrant persistente | EXTERNO | Inserir, reiniciar e recuperar coleção vetorial |
| Docker Compose | ESTRUTURALMENTE VALIDADO | Executar `scripts/validate_docker_runtime.sh` no host autorizado |
| TLS e domínio | EXTERNO | Configurar proxy reverso, certificado, HSTS e DNS |
| Backups | IMPLEMENTADO / EXTERNO | Scripts existem; testar restauração em host separado |
| Teste de carga | SCRIPT DISPONÍVEL / PENDENTE | Executar `scripts/load_test.py` com limites de CPU, memória e conexões definidos |
| Segurança multi-tenant | PARCIAL | Testes de isolamento e RBAC locais; falta teste dinâmico autenticado no deployment |
| MT5 Demo | PENDENTE EXTERNO | Terminal, conta Demo, `order_check`, `order_send`, fill e reconciliação |
| Painel administrativo | PARCIAL | Endpoints Admin existem; UI completa de gestão ainda requer validação de produto |
| Live Trading | BLOQUEADO / PREPARAÇÃO IMPLEMENTADA | Chaves TESTNET, leitura REAL e execução REAL são separadas; provas expiram e são vinculadas à mesma conta. Não faz parte deste release; `MESA_LIVE_LIBERADA=False` e nenhuma ordem real é autorizada |
| Revisão regulatória | PENDENTE HUMANO | Deve ser feita conforme jurisdição, produto e público-alvo |

## Critério de release

O sistema só pode ser chamado de **produção Classe A** quando os gates externos forem executados e anexados a um relatório com data, commit, host, configuração sanitizada, logs e resultado. Testes mockados não substituem teste de integração com PostgreSQL, Redis, Qdrant ou MT5 Demo.

## Ordem operacional recomendada

Primeiro validar Compose e persistência. Depois validar TLS, backups e restauração. Em seguida executar carga e testes de isolamento. Só então conectar o terminal MT5 Demo. O modo Live continua fora do escopo e bloqueado.

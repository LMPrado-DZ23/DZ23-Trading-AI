# DZ23 Trading AI — laboratório pessoal (2026-10-12)

## Escopo aprovado
Produto de **uso exclusivamente pessoal**, por um único proprietário. Manter as salas IQ Option, Binance, OKX e Mercado Bitcoin com ledgers, métricas, riscos e adaptadores isolados. Nenhuma migração destrutiva ou remoção do código SaaS antes de auditoria e backup.

## Estado observado
- Repo privado `LMPrado-DZ23/DZ23-Trading-AI`, branch-base `main`.
- Código em Python/FastAPI, `ccxt`, múltiplos traders, risco determinístico e reconciliação.
- PR #3 (`correcao-auditoria`) está aberto em draft com correções que **não devem ser substituídas**.
- Em `main`, o arquivo `.env.example` sugere `MULTI_TENANT_ENABLED=true` e `SIGNUP_ENABLED=true`: **não utilizar esses defaults em instalação pessoal**.
- `docs/finalization/BLOCKERS.md` documenta F3–F6, K1–K6, A1–A2 e multiprocess como blockers, especialmente contabilidade, autorização e preenchimento de ordens.
- Não há prova nesta auditoria de que a IQ Option ofereça dados de preço/execução equivalentes a mercado spot. Registrar origem dos preços e não comparar P/L de produtos diferentes como se fossem iguais.

## Objetivo
Construir um único painel pessoal PT-BR com:
1. Cofres separados por corretora e modo (PAPER, SHADOW, TESTNET, LIVE).
2. Feed de mercado com origem/timestamp, lacunas, liquidez, spreads, divergência e relógio.
3. Research loop: dados históricos íntegros -> estratégias -> backtest com taxas, spread e slippage -> walk-forward -> paper -> shadow -> testnet -> auditoria -> aprovação manual de LIVE.
4. Agentes de tendência, reversão, rompimento, regime, auditoria e risco; LLMs **nunca** possuem credencial para envio de ordens.
5. Executor determinístico: client-order-ID/idempotência, reconciliação antes de retry, fill ledger durável, circuit breaker, kill switch, limite de perda diária e de exposição global.
6. Uma sala IQ Option separada e marcada `EXPERIMENTAL`, em sandbox/demo, com integridade de dados verificada e sem propagação automática de aprovações para outras corretoras.
7. Dashboard acessível e responsivo, posições abertas, P/L realizado e não realizado, taxas, ordens bloqueadas com motivo, estados vazios, incidentes e parada de emergência.

## Segurança / implantação pessoal
- Sem cadastro público, billing, gestão de clientes, convites, API pública de signup nem OAuth de múltiplos usuários. Componentes legados removidos **somente após testes de não regressão**.
- Perfil de configuração pessoal seguro a ser implementado: `SIGNUP_ENABLED=false`, `LIVE_TRADING=false`, `REAL_MONEY_EXECUTION=false`, segredos no host, 2FA no acesso, lista restrita de origens e rede, chaves de API **sem permissão de saque**, mínimo privilégio.
- Isolamento de modos/corretoras inclusive em cache, filas, armazenamento e resultados.
- Runtime LIVE não pode ser ativado por simples troca de variável: exigir release gates com evidências + confirmação humana explícita; não gerar nem armazenar credenciais reais no repositório.
- Não liberar Futures nem alavancagem na primeira fase; priorizar Spot e validação estatística real.
- R$ 5.000 representa apenas limite orçamentário projetado; não é capital depositado nem autorização de negociação.

## Bloqueadores obrigatórios antes de LIVE
- PR #3 auditado, CI completo verde, problemas F3–F6/K1–K6/A1/A2 e concorrência multiprocesso reavaliados e fechados com evidência, não com bypass.
- Modelo de custos alinhado aos fills reais e às moedas das taxas; correção dos problemas da contabilidade.
- Testes determinísticos de duplicação, execução parcial, timeout incerto, crash/restart, feed stale, taxas não cotadas, manipulação de timestamps e kill switch.
- Operação paper e testnet por amostra representativa, abrangendo regimes de mercado e descontando custos; documentar win rate, expectancy, drawdown, slippage e intervalos de incerteza.
- Revisão humana de performance e risco antes de qualquer liberação de LIVE.

## Execução por fases
1. Auditar árvore de código, branches, CI, runtime atual, credenciais (sem imprimir), conectores e cron; congelar baseline.
2. Fechar blockers financeiros e de segurança em branch própria; integrar/conciliar PR #3 por revisão e testes, não sobrescrever.
3. Implementar perfil single-user e retirar superfícies SaaS da UI sem destruir dados.
4. Integrar Binance/OKX/MB com contratos de corretora e simulador fiel; manter IQ Option em isolamento experimental.
5. Construir laboratório quantitativo e observabilidade de agentes; investigar causa das três operações Binance (sem aumentar frequência cegamente).
6. Implementar painel pessoal e documentação de recuperação e deploy.
7. Homologação integral (lint, typecheck, unit, integração, e2e, API, segurança, migrações, smoke); gate LIVE continua fechado.

## Política de mudanças desta branch
Branch preparada para evolução segura. Este documento **não comprova implementação nem testes**. Não realizar deploy, merge, exclusões, mudanças de credenciais ou LIVE com base apenas nele.

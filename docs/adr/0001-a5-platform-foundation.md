# ADR 0001 — Fundação da plataforma A5

- **Status:** accepted
- **Date:** 2026-09-08
- **Decision owner:** Prado

## Context

O serviço v1.1.0 usa senha, configuração, SQLite e threads globais. A auditoria
confirmou que essa arquitetura não isola usuários, não preserva jobs em restart
e não vincula ordens à decisão/mandato. O núcleo analítico endurecido permanece
útil e deve ser preservado.

## Decisions

### Monólito modular e worker separado

A API e o worker compartilham pacotes de domínio, mas rodam em processos e
containers separados. Contextos de domínio não acessam internals uns dos outros.
Microserviços ficam adiados até existir uma necessidade operacional medida.

### PostgreSQL autoritativo e RLS

PostgreSQL registra identidade, tenancy, jobs, carteira, ledger e execução. Toda
entidade de cliente contém `tenant_id`; RLS é defesa obrigatória além dos filtros
da aplicação. SQLite não é ambiente de produção.

### Fila durável no PostgreSQL

Workers adquirem jobs com `FOR UPDATE SKIP LOCKED`, lease e heartbeat. Isso evita
um segundo broker de mensagens como dependência autoritativa. Redis é somente
efêmero e nunca decide saldo, ordem ou estado de job.

### Ledger append-only

Movimentos financeiros não são sobrescritos. Projeções de saldo e posição podem
ser reconstruídas e são reconciliadas com a venue. Reservas e outbox pertencem à
mesma transação que autoriza a tentativa de execução.

### LIVE progressivo

Não existe frase global de autorização. Os modos são BACKTEST, SHADOW, PAPER,
LIVE_SUPERVISED e LIVE_AUTOMATED. LIVE exige eligibility, MFA/step-up, mandato,
policy e intenção vinculados por digest. Automação pode ser revogada e nunca
recupera perdas aumentando risco.

### Frontend React estático

React, TypeScript e Vite fornecem jornadas acessíveis e testáveis. O build é
estático e servido junto à API, evitando um runtime Node adicional em produção.

## Consequences

- A camada `service/app.py` será substituída gradualmente, não expandida.
- Migrações, RLS e worker são pré-requisitos antes de carteira/execução.
- LIVE permanece desabilitado até o marco M10.
- O custo operacional inicial é PostgreSQL + Redis + dois processos Python.
- Testes precisam provar isolamento tanto na aplicação quanto diretamente no
  banco, além de concorrência, restart e reconciliação.

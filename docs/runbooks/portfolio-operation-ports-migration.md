# Runbook: portfolio operation ports migration

Migration `20260909_0022` is a forward-only security migration. It replaces the
capital-operation ports, tightens table privileges, binds active reservations to
an immutable valuation snapshot, and expires legacy active reservations that do
not have that binding. Its `downgrade()` intentionally refuses to run.

## Pre-deploy gate

Do not apply this migration to staging or production until all of the following
are recorded for the target database:

1. the exact application commit and Alembic head;
2. a provider-native point-in-time recovery checkpoint taken immediately before
   the maintenance window;
3. an encrypted logical backup whose restore has succeeded in an isolated
   database running the same PostgreSQL major version;
4. the count and identifiers of active cash reservations that will be expired;
5. a tested forward-fix package and the operator responsible for the window.

Quiesce API and worker writers before the backup checkpoint. Apply the migration
with an ephemeral administrative connection, restart workers before the API, and
verify migration head, readiness, worker nonce maintenance, portfolio reads,
fresh snapshot capture, one PAPER reservation and its release. Do not enable
LIVE execution as part of this migration.

## Failure response

Never attempt a schema downgrade. Before accepting new writes, choose one of:

- apply a reviewed forward-fix migration when stored data remains valid; or
- restore the pre-deploy checkpoint into a new database, verify checksums and
  Alembic head, then atomically redirect API and worker connections.

If writes were accepted after the migration, recovery requires reconciliation
of the immutable ledger and reservation journal before traffic resumes. A code
rollback alone is not a database rollback and must remain blocked while schema
head `20260909_0022` is active.

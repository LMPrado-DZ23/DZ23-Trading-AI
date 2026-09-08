# TradingAgents job service

The service exposes authenticated, durable jobs for one-date analysis and
point-in-time backtests. It does not expose broker execution or arbitrary
runtime configuration.

## Production contract

- Install the `api` extra and run `tradingagents-api` and
  `tradingagents-worker` as separate processes.
- Set `TRADINGAGENTS_DATABASE_URL` to PostgreSQL. Production fails closed when
  it is absent. SQLite is accepted only with `TRADINGAGENTS_ENV=development`.
- Set a randomly generated `TRADINGAGENTS_API_TOKEN` of at least 32 characters.
- Terminate TLS and enforce request-size and rate limits at the trusted reverse
  proxy. The application binds to `127.0.0.1` by default and disables API docs.
- Keep provider credentials only in worker environment variables. Clients
  cannot submit credentials, provider URLs, prompts, or arbitrary config.
- Give each API and worker instance the same job-schema version. Deploy schema
  changes before application changes and do not roll an older binary over a
  newer queue.

The queue uses idempotency keys, PostgreSQL row locks with `SKIP LOCKED`, lease
tokens, heartbeats, retry limits and stale-worker rejection. Cancellation is
cooperative: a running model call is not interrupted, but its result cannot be
committed after cancellation.

## Development

Set `TRADINGAGENTS_ENV=development` and optionally
`TRADINGAGENTS_SQLITE_PATH`. SQLite is a single-host development backend, not a
multi-host production queue.

Submit `POST /v1/jobs` with `Authorization: Bearer ...` and a unique
`Idempotency-Key` header. Query `GET /v1/jobs/{job_id}` or cancel with
`POST /v1/jobs/{job_id}/cancel`. Only `/healthz` is unauthenticated.

No job endpoint can place a market order. PAPER/SHADOW execution is a separate,
fail-closed boundary.

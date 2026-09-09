# M5.2b2 simulation authority implementation

Status: candidate, pending independent review

## Scope

This slice joins the approved M5.2a execution-price authority to the approved
M5.2b1 pure ledger. It remains an offline backtest component and contains no
broker, PAPER, LIVE, network or LLM path.

## Authority boundary

- `FrozenSimulationAuthorityRepository` publishes two independent namespaces:
  immutable price-free order requests and immutable ledger checkpoints.
- Every record is canonical JSON, published no-replace, read as a bounded
  regular file and authenticated with HMAC-SHA256 under a server key that is
  never written beside the record.
- An order request binds tenant, backtest ID/spec digest, ledger, policy,
  batch/session, quantities, dataset manifests, decision time and eligible
  views. It contains no `raw_open` or fill price.
- A checkpoint binds tenant, backtest ID/spec digest, ledger policy, complete
  ledger state and the previous checkpoint digest.
- The execution entrypoint accepts neither a caller-created batch nor a ledger
  state/hash. It loads both authorities, deeply revalidates `BacktestSpec`,
  checks every order against its tradeable dataset reference, resolves every
  next-open through `resolve_execution_price_authority()` and only then calls
  the pure reducer.
- The resolver loads a candidate from frozen bytes and calls the non-virtual
  `verify_execution_price_authority()` boundary internally. No production
  consumer may call the lower-level price APIs directly.

## Replay and concurrency boundary

An applied result emits a deterministic next-checkpoint candidate. After the
trusted coordinator publishes that checkpoint, retrying the same request
returns `replayed`, appends no event and creates no additional checkpoint.

This file repository closes authenticity and offline replay, not concurrent
head selection. It is not connected to the public API or worker. M5.5 must move
run/spec/order/fill/equity state to PostgreSQL with tenant RLS, coordinator
lease, fencing and an atomic head transition before multiple workers or public
backtest execution are enabled.

## Required independent attacks

- self-consistent alternate raw-open dataset against the frozen manifest;
- request/checkpoint content, signature and reference mutation;
- wrong HMAC key, tenant, run, spec, ledger, policy, dataset, instrument,
  manifest, decision time and eligible view;
- subclass and instance-shadow attempts on both repositories;
- invalid final order after valid preceding orders, proving the ledger is not
  called early;
- replay from the successor checkpoint;
- namespace collision and concurrent no-replace publication;
- POSIX symlink/reparse/non-regular/TOCTOU and maximum-size records;
- structural scan for direct price/reducer consumers and capital imports.

## Explicit non-claims

This candidate does not approve corporate actions, FX conversion, a complete
BacktestRunner, metrics, persistence, API/UI, brokers, PAPER or LIVE execution.

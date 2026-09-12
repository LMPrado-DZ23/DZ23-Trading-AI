# M5.2b2 simulation authority implementation

Status: approved after correction and three independent re-reviews

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
- The spec binds the complete ledger-policy digest and explicitly matches
  overlapping economic fields. Instrument currencies are checked against the
  frozen dataset.
- Liquidity is capped from decision-session D raw volume only; D+1 full-day
  volume is neither exposed nor consumed. One net order per instrument prevents
  repeated full-dataset amplification.
- Returned custody references are reconstructed from authenticated records;
  caller objects are never returned after validation.
- Execution evidence v2 is created only after the ledger reducer returns and
  binds the submitted quantity to the fill's requested quantity, executed
  quantity and canonical fill digest. Recomputing the outer unkeyed result
  digest cannot hide a mismatched fill.

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
- policy/spec mismatch for cash, currency, costs, tax and instrument rules;
- D-volume cap versus a deliberately contradictory D+1 volume;
- mutation of caller references during price resolution;
- Windows failure between staging writes and final rename, followed by retry.
- Windows staging-directory creation failure after claiming an identifier,
  proving cleanup and immediate retry.
- execution-evidence quantity or fill-digest mutation with a recomputed outer
  result digest.

## Explicit non-claims

This candidate does not approve corporate actions, FX conversion, a complete
BacktestRunner, metrics, persistence, API/UI, brokers, PAPER or LIVE execution.
The serialized result is not a standalone custody attestation: M5.5 must bind
it to the expected tenant/run under an authenticated PostgreSQL transition.

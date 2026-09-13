# M5.2c2 core primitives — independent review checkpoint

- Implementation commit: `2824c42`
- Classification: internal core/primitives only
- Runtime status: disabled; outer economic session authority pending
- Production status: not approved

## Delivered primitives

- typed, complete PRE_OPEN -> fills -> POST_CLOSE economic transition;
- deterministic double-entry events for fills, dividends and delisting;
- explicit non-monetary events for split, entitlement, delisting block and
  zero-holding payment/settlement;
- revision-aware corporate-action lineage;
- checkpoint-bound corporate-cash basis and frozen PIT FX conversion;
- append-only local/dev checkpoint-head CAS with sibling-fork prevention and
  exact orphan retry.

The reducer and cash conversion helpers remain private. They validate and
rederive their inputs but do not create authority from self-digests.

## Reproduction

- Focused final: **61 passed**.
- Related simulation/economics regression: **158 passed**.
- Full Windows suite on the final pre-commit snapshot:
  **1618 passed, 24 skipped, 71 subtests** in 463.92 seconds.
- Ruff and compileall: green.

The 24 skips are external-service or POSIX-only gates already recorded by the
mission; none was silently converted into a pass.

## Independent verdicts

Three independent reviews approved this exact design only as an intermediate
core checkpoint:

1. Architecture: approved after the verified-basis wrapper became immutable
   by application contract and was deeply revalidated at consumption.
2. QA/contracts: approved after `checkpoint_generation` became strict and
   positive, with zero, bool, string and float rejected.
3. Security: approved under the explicit trusted-process threat model; direct
   private imports and hostile `object.__setattr__` demonstrate why these
   primitives are not an authorization boundary or Python sandbox.

Consultative first passes used DZ23 subagents and Ollama
`deepseek-v4-flash:cloud`; every conclusion above was checked against source
and local tests.

## Mandatory gate before runtime

No caller may use these primitives for runtime, quantitative promotion or
capital-facing results until an integrated outer session authority:

1. loads an authenticated economic checkpoint from trusted custody;
2. observes and validates the exact checkpoint head/CAS generation;
3. enumerates every tradeable instrument from an exact `BacktestSpecV3`;
4. rederives every PRE_OPEN and POST_CLOSE view from frozen datasets;
5. rederives every fill from authenticated execution evidence;
6. derives dividend/delisting quantities from ledger state, never request data;
7. validates every corporate-cash conversion against its frozen authority;
8. commits exactly one full session, including economically empty sessions;
9. publishes an authenticated checkpoint and advances the head atomically;
10. rejects stale, sibling, foreign-scope and self-anchored requests.

The local filesystem head store remains dev/test-only. Production requires a
transactional PostgreSQL adapter and process isolation from untrusted Python or
plugins.

## Multi-portfolio boundary

This checkpoint still has the M5 tenant/backtest/ledger scope. The later
multi-portfolio version must add portfolio, external account, connection and
venue bindings before multiple wallets or brokers can share the runtime. It
must serialize each portfolio ledger and each external broker account while
allowing independent analysis teams and independent portfolios to work in
parallel.

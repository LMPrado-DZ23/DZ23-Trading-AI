# M5.2a independent review

Date: 2026-09-09
Final approved commit: `d35eae7b8ea58ec2838d0bd77e08d2f94b1eda6d`

## Verdict

**APPROVED** by three independent review tracks after three correction loops.
No M5.2 accounting reducer, broker path or capital execution was approved by
this checkpoint.

## Reproduced controls

- Linux/WSL Python 3.12: `111 passed`; the POSIX `renameat2` race executed and
  passed rather than being skipped.
- Windows Python 3.14 full suite on the exact final commit:
  `1455 passed, 24 skipped, 71 subtests passed`.
- Ruff, compileall and `git diff --check`: green.
- A stale `model_copy()` poison, child swap and digest mutation are rejected by
  deep contract revalidation.
- A self-consistent forge with recalculated hashes is rejected by
  `verify_execution_price_authority()` after rederivation from frozen bytes.
- Wrong tenant, instrument, dataset, manifest, decision or eligible-view
  bindings fail closed.
- Repository subclass dispatch and instance method shadow cannot bypass the
  non-virtual authority boundary.
- The structure-only helper is private, absent from the package API and
  explicitly documented as non-authoritative.
- The production consumer guard covers `tradingagents/`, `service/`, `cli/`
  and `main.py`, including normal aliases, literal `getattr`, direct load and
  private-helper references.
- Mutating only D+1 high, low, close or volume keeps the economic next-open
  projection identical; provenance still records the full parent bar.

## Findings closed

1. Existing Pydantic instances initially bypassed deep revalidation.
2. Public self-digests were initially mistaken for origin authority.
3. Virtual dispatch and instance shadow could initially skip repository guards.
4. Slots initially broke one existing POSIX race test.
5. The first structural guard missed service/CLI roots and common aliases.
6. A public structure-only helper could have been mistaken for authority.

All six were reproduced, corrected and independently retested.

## Evidence

- Final JUnit: `audit/m5-2a-final-junit.xml`
- Bytes: `212946`
- SHA-256:
  `ec89445946150624dae8c31500ca5a4bfb520ad17583fe713822c055e7563353`

## Binding condition for M5.2b

The future reducer must not accept a caller-supplied price projection, bundle,
raw open or fill price. It must obtain authenticated run/order bindings and
call the repository authority internally. Its gate must repeat the
self-consistent forge end to end and prove no economic event is emitted.

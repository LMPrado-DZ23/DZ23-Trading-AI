# M5.2a execution-price boundary

Date: 2026-09-09
Parent commit: `c0fd3646db84955ac17a2c82233f99b2e8936a85`

## Scope

This checkpoint establishes the narrow point-in-time boundary that the M5.2
accounting reducer will consume. It does not yet implement orders, fills,
postings, corporate-action settlement or portfolio state.

- `ExecutionPriceProjection` contains only the raw next open and the minimum
  economic identity/timestamps required to price a fill.
- `ExecutionPriceProvenance` separately binds the projection to the frozen
  dataset, manifest, eligible decision view and full parent bar/source digests.
- `ExecutionPriceBundle` validates both children and their binding.
- `FrozenDatasetRepository.load_execution_price()` produces evidence after
  revalidating tenant, instrument, manifest and eligible-view bindings from
  frozen bytes. A produced bundle is not authority by itself.
- `verify_execution_price_authority()` is the non-virtual consumer boundary: it
  rejects repository subclasses, deeply rebuilds exact contract types, reopens
  the frozen dataset through the base implementation and compares the complete
  candidate with the rederived value.
- Changing D+1 high, low, close or volume while retaining the same raw open
  leaves the economic projection byte-identical and changes only full
  provenance. Changing the raw open changes the economic projection.

The separation prevents future D+1 fields from influencing fill economics
while retaining complete chain-of-custody evidence. The existing
`ExecutionOpen` contract remains backward compatible and unchanged.

## Retained boundaries

- No broker, order, target, side, quantity, ledger, database, service, LLM,
  network or wall-clock dependency is introduced.
- A later simulation reducer must accept authenticated run/order bindings and
  call `verify_execution_price_authority()` internally. It must never accept a
  projection or bundle as authority. A structural test currently rejects any
  direct production consumer outside the contracts/repository boundary.
- Dataset limitations remain active and quantitative claims remain disabled.

## Verification

- Focused contracts + frozen repository suite: `107 passed`.
- Full suite with disposable PostgreSQL 16 and Redis 7:
  `1473 passed, 2 skipped, 71 subtests passed`.
- Skips: DeepSeek live credential absent; Windows-only taskkill failure path.
- Ruff: green.
- Compileall: green.
- JUnit: `audit/m5-2a-full-junit.xml`
- JUnit bytes: `205427`
- JUnit SHA-256:
  `39c3e555cfbf4ae162fa8a77ec61f62c44787b3d63ba54557294d5ac348b9ef7`

## Independent rejection and correction

All three independent reviews rejected commit `c2e5110` on authority-boundary
attacks. The original self-digests detected accidental mutation but a caller
could recalculate them, or could pass a stale `model_copy()` instance. Virtual
dispatch also allowed a repository subclass or an instance method shadow to
skip the original guard.

The correction is fail closed:

- all three execution-price contracts revalidate existing instances;
- the authoritative boundary performs exact-type dump-and-rebuild validation;
- the complete candidate is compared with a value rederived from frozen bytes;
- `expected_instrument_id` is required alongside tenant/manifest/view/session;
- the public boundary calls the exact base implementation non-virtually;
- `FrozenDatasetRepository` has slots, preventing instance method shadowing;
- adversarial tests cover stale nested models, child swapping, subclasses,
  virtual override and a fully self-consistent forged bundle with recalculated
  hashes.

Correction verification on Windows/Python 3.14:

- Backtest contracts + repository + candlestick suite: `164 passed, 5 skipped`;
  the five skips are POSIX-only filesystem controls.
- Full local suite: `1455 passed, 24 skipped, 71 subtests passed`; 18 integration
  cases lacked PostgreSQL/Redis URLs, one DeepSeek live test lacked credentials,
  and five filesystem cases are POSIX-only. This is 1,479 collected tests, four
  more than the pre-correction full run.
- Optional Bedrock extra installed; its transport-policy test passed.
- Ruff, compileall and `git diff --check`: green.
- Correction JUnit: `audit/m5-2a-correction-junit.xml`
- JUnit bytes: `212941`
- JUnit SHA-256:
  `545a8ecd09a8a895706b5ffb175e069a30b126cb9d9d53e623fb5ba9806d474e`

The first correction review then found that adding slots invalidated a POSIX
race test which had monkeypatched one repository instance. The production
slots remain necessary to prevent instance method shadowing; the test now
patches the static method on the class and retains the same race behavior. The
structural guard was also widened from `tradingagents/` to
all production roots (`tradingagents/`, `service/`, `cli/`, `main.py`) and now
detects aliased imports, string lookups and direct `load_execution_price` or
private verification references. Focused Windows authority tests and Ruff are
green. The corrected Linux contracts/repository suite is `111 passed` under
WSL; independent Linux review remains required before M5.2a can be approved.

The test-audit review then identified that the structure-only deep-revalidation
helper was still exported and had an authority-sounding docstring. A plausible
future reducer could have imported that helper, accepted a self-consistent
forgery and remained invisible to the guard. The helper is now private, absent
from the package API, explicitly documented as non-authoritative, and both its
old public name and private name are protected by the structural scan. M5.2b
must additionally exercise its actual reducer end to end with the same
self-consistent forgery before its accounting gate can pass.

Final exact-commit verification after the private-helper correction:

- Full Windows suite: `1455 passed, 24 skipped, 71 subtests passed`.
- Final JUnit: `audit/m5-2a-final-junit.xml`
- JUnit bytes: `212946`
- JUnit SHA-256:
  `ec89445946150624dae8c31500ca5a4bfb520ad17583fe713822c055e7563353`
- Three independent review tracks: approved.

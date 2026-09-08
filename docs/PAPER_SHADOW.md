# PAPER and SHADOW execution

TradingAgents has no live-capital adapter. The only accepted execution modes
are `shadow` and `paper`; any other value fails before an intent is created.

`prepare_execution` requires mutually consistent evidence from the graph:

- the persistent secure-transport mark bound to ticker/date;
- a successful telemetry summary produced in the requested mode;
- the closed, versioned deterministic sizing plan;
- matching ticker, date, decision timestamp and a fresh execution window;
- a finite quantity whose sign agrees with BUY/SELL/HOLD.

REVIEW and every blocked or zero-quantity sizing result become HOLD. The client
order ID is derived from the run and sizing evidence, and an SQLite execution
journal prevents repeated dispatch after a recorded response. Identity uses the
stable telemetry `run_key`, not the per-attempt `run_id`: rerunning the same
logical decision cannot create a second order, and a changed sizing result for
that identity fails as a conflict.

Adapters:

- `ShadowBroker`: records observation only.
- `LocalPaperBroker`: deterministic offline simulation, clearly labelled; it
  does not prove behavior at an external venue.
- `AlpacaPaperBroker`: uses only Alpaca's fixed official paper URL, disables
  environment proxies and redirects, bounds response size and uses a stable
  `client_order_id`. No caller-supplied base URL and no live Alpaca URL exist.

External PAPER validation still requires the user's own paper credentials and
network authorization. It is not performed by the test suite. Enabling real
capital requires a separate implementation, threat model, independent audit
and explicit human approval; changing a configuration value cannot enable it.

`TradingExecutionOrchestrator` is the complete bridge: it runs the graph in the
declared mode with immutable portfolio/market inputs, receives deterministic
sizing, applies the execution gate, dispatches through the selected adapter and
returns the signal, intent and durable receipt. There is no fallback around a
failed gate.

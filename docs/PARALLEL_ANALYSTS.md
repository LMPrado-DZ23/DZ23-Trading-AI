# P3 — Parallel analyst fan-out

The selected market, sentiment, news and fundamentals analysts now start from
the outer graph's `START` node in parallel. The bull/bear debate begins through
a barrier edge only after every selected analyst has completed.

Each analyst owns a private nested graph containing its LLM/tool loop. It starts
from a copy of the common input state and returns exactly one unique report
field to the outer graph. Temporary AI/tool messages never enter another
analyst's branch and never merge into the persistent outer transcript.

This provides four explicit guarantees:

- state merge is deterministic because branches write disjoint report keys;
- missing reports fail closed instead of silently weakening the debate;
- a failed branch prevents the downstream fan-in;
- LangGraph checkpoint pending writes preserve completed branches, so resume
  retries only failed work.

The workflow schema is `tradingagents/workflow/3`; older checkpoints are
incompatible by design. Telemetry callbacks are shared but thread-safe and
attribute LLM calls to their LangGraph node.

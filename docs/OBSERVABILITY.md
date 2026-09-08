# P4/P7 — Cost tracking and observability

TradingAgents emits one metadata-only telemetry bundle for every graph run. It
records call counts, token usage, duration, failures and configured cost
estimates without recording prompts, messages, tool inputs, tool outputs or
model responses. LLM totals are also grouped by the LangGraph node when that
metadata is provided by the runtime.

## Output

Each invocation writes two atomic files below:

`<results_dir>/<ticker>/telemetry/<trade-date>/`

- `<run_id>.events.jsonl`: ordered lifecycle events.
- `<run_id>.summary.json`: status, context, totals and artifact identity.

`run_id` is unique per invocation. `run_key` is stable for the same canonical
`RunContext`, which permits grouping retries and checkpoint resumes without
overwriting evidence.

## Cost configuration

Vendor prices are never hard-coded. Supply the rates that apply to the account:

```python
config = {
    "model_pricing": {
        "provider-model-name": {
            "input_per_million": 2.0,
            "output_per_million": 8.0,
        }
    }
}
```

If any completed LLM call has no matching price, `estimated_cost_usd` is
`null`; `priced_cost_usd` still reports the known subtotal and
`unpriced_llm_calls` reports the gap. Invalid or negative pricing fails at graph
construction.

Set `telemetry_enabled` to `false` only for local compatibility. PAPER and
SHADOW execution gates may require telemetry independently of this setting.

## Security contract

The telemetry callback deliberately ignores all content-bearing callback
arguments. Error strings and metadata labels pass through the shared secret
redactor and are length-bounded. The CLI's legacy message/tool log also redacts
credential-shaped values before writing. Strategy reports remain separate
user-requested artifacts and are not copied into telemetry.

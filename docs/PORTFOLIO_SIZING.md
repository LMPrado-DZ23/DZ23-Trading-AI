# P2 — Deterministic portfolio state and sizing

The LLM decides direction and conviction through the existing five-tier rating.
It does not choose portfolio weights, capital amounts or order quantities.
`tradingagents.portfolio.size_position` is the sole sizing policy.

## Inputs

- Immutable `RunContext` with the point-in-time decision cutoff.
- `PortfolioSnapshot` whose equity reconciles to cash plus marked long positions.
- `MarketRiskSnapshot` with a positive decision price, annualized volatility and
  timezone-aware availability timestamp.
- `PortfolioConstraints`, normally loaded from `portfolio_constraints` in the
  runtime config.

Snapshots newer than the decision cutoff, stale volatility, ticker mismatch,
different `as_of` timestamps, inconsistent position marks, invalid ratings and
malformed numeric values fail closed before any plan exists.

## Policy

- Buy and Overweight map to fixed conviction multipliers over volatility
  targeting.
- Hold preserves the current position, subject to hard risk caps.
- Underweight may reduce but never increase exposure.
- Sell targets zero. REVIEW never trades, even when the existing portfolio is
  outside a cap; emergency handling of an already-breached portfolio is a
  separate risk-control workflow and cannot be inferred from malformed LLM output.
- Position, gross-exposure and net-exposure caps are applied deterministically.
- For a valid rating, at or beyond the drawdown limit, the target is zero.
- Quantities round down to the configured step so rounding cannot breach a cap.
- Minimum-notional filtering applies only to risk-increasing purchases; it never
  suppresses a risk-reducing sale.

The output is a versioned `tradingagents/sizing/1` plan. When both portfolio and
market snapshots are supplied to `TradingAgentsGraph.propagate`, the plan is
stored in `final_state["portfolio_plan"]` and in the run log. Supplying only one
snapshot is rejected before the LLM workflow starts.

This layer produces an intent, not a broker order. PAPER/SHADOW adapters must
revalidate freshness, limits, transport security and idempotency at execution.

# P1 — Point-in-time backtesting

`tradingagents.backtest.BacktestRunner` evaluates a sequence of decisions with
an explicit execution clock:

1. The strategy receives an immutable `RunContext` at the session close, a
   reconciled portfolio snapshot and volatility calculated only from closes
   available through that session.
2. The five-tier signal is converted by the P2 deterministic sizing policy.
3. The fixed order quantity is filled at the next session's **raw Open**, with
   adverse slippage and commission. No next-session value is present in the
   decision request.
4. Splits adjust existing shares and pending quantities before the open;
   dividends are credited only to shares held before that session's fill.
5. Equity is marked on raw closes. The passive benchmark is independently
   adjusted for its splits and dividends.

## Inputs and fail-closed rules

- Asset and benchmark `PriceSeries` must contain unique, ascending sessions.
- Every asset session must exist in the benchmark series.
- Raw `Open` and `Close` are mandatory. `load_yfinance_price_series` explicitly
  uses `auto_adjust=False` and requests corporate actions.
- The warm-up window must contain the configured minimum number of returns.
- Unknown strategy signals abort the run; `REVIEW` creates no order.
- A gap that makes a purchase unaffordable produces a partial fill rounded down
  to the configured quantity step. Cash never becomes negative and short sales
  are not permitted.

## Results

`BacktestResult` contains the trade ledger, equity curve and:

- total and annualized return;
- annualized volatility and Sharpe ratio;
- maximum drawdown;
- realized sell hit rate;
- turnover;
- benchmark return and accumulated alpha.

`BacktestResult.write(path)` atomically writes `backtest.json` and
`backtest.md`. A missing hit rate or Sharpe is emitted as `null`/`N/A`, never
invented as zero.

`GraphDecisionProvider` connects a `TradingAgentsGraph` while requesting only
its rating. It forces `run_mode="backtest"`; sizing and execution remain outside
the LLM graph.

The engine is an evaluation simulator, not evidence for real-capital promotion.
PAPER/SHADOW execution requires its own adapter and acceptance gates.

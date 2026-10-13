import numpy as np


def run_monte_carlo_simulation(returns: np.ndarray, num_simulations: int = 1000, horizon: int = 30, initial_capital: float = 10000.0) -> dict:
    if len(returns) == 0:
        return {"var_95": 0.0, "var_99": 0.0, "probability_of_ruin": 0.0}

    simulated_final_capitals = []
    ruin_count = 0

    for _ in range(num_simulations):
        sampled_returns = np.random.choice(returns, size=horizon, replace=True)
        equity_curve = initial_capital * np.cumprod(1 + sampled_returns)
        if np.any(equity_curve < (initial_capital * 0.5)):
            ruin_count += 1
        simulated_final_capitals.append(equity_curve[-1])

    final_returns = (np.array(simulated_final_capitals) - initial_capital) / initial_capital
    var_95 = float(np.percentile(final_returns, 5))
    var_99 = float(np.percentile(final_returns, 1))

    return {
        "var_95": round(var_95, 4),
        "var_99": round(var_99, 4),
        "probability_of_ruin": round(ruin_count / num_simulations, 4),
    }

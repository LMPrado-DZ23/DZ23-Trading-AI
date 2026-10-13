import numpy as np

from tradingagents.research.monte_carlo import run_monte_carlo_simulation


def test_monte_carlo_metrics():
    returns = np.array([0.01, -0.005, 0.02, -0.01, 0.015, -0.008])
    res = run_monte_carlo_simulation(returns, num_simulations=100, horizon=30, initial_capital=10000)
    assert "var_95" in res
    assert "var_99" in res
    assert "probability_of_ruin" in res
    assert res["var_95"] <= 0 or isinstance(res["var_95"], float)

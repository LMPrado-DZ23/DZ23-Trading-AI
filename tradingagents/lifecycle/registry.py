from __future__ import annotations

from tradingagents.domain.contracts import Strategy
from tradingagents.domain.enums import StrategyStatus

_ALLOWED: dict[StrategyStatus, set[StrategyStatus]] = {
    StrategyStatus.RESEARCH: {StrategyStatus.BACKTEST, StrategyStatus.DISABLED},
    StrategyStatus.BACKTEST: {StrategyStatus.WALK_FORWARD, StrategyStatus.DISABLED},
    StrategyStatus.WALK_FORWARD: {StrategyStatus.PAPER, StrategyStatus.DISABLED},
    StrategyStatus.PAPER: {StrategyStatus.SHADOW, StrategyStatus.PROBATION, StrategyStatus.DISABLED},
    StrategyStatus.SHADOW: {StrategyStatus.CHALLENGER, StrategyStatus.PROBATION, StrategyStatus.DISABLED},
    StrategyStatus.CHALLENGER: {StrategyStatus.CHAMPION, StrategyStatus.PROBATION, StrategyStatus.DISABLED},
    StrategyStatus.CHAMPION: {StrategyStatus.PROBATION, StrategyStatus.DISABLED},
    StrategyStatus.PROBATION: {StrategyStatus.CHALLENGER, StrategyStatus.DISABLED},
    StrategyStatus.DISABLED: set(),
}


class StrategyRegistry:
    def __init__(self):
        self._strategies: dict[str, Strategy] = {}

    def register(self, strategy: Strategy) -> Strategy:
        if strategy.strategy_id in self._strategies:
            raise ValueError(f"estratégia já registrada: {strategy.strategy_id}")
        self._strategies[strategy.strategy_id] = strategy
        return strategy

    def get(self, strategy_id: str) -> Strategy:
        return self._strategies[strategy_id]

    def transition(self, strategy_id: str, target: StrategyStatus) -> Strategy:
        current = self.get(strategy_id)
        if target not in _ALLOWED[current.status]:
            raise ValueError(f"transição inválida: {current.status} -> {target}")
        updated = Strategy(current.strategy_id, current.name, current.version, current.desk_id, target, current.artifact_hash, current.metadata)
        self._strategies[strategy_id] = updated
        return updated

    def list(self) -> list[Strategy]:
        return list(self._strategies.values())

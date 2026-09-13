from __future__ import annotations

from enum import StrEnum


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class DecisionStatus(StrEnum):
    PROPOSED = "PROPOSED"
    RISK_APPROVED = "RISK_APPROVED"
    RISK_REDUCED = "RISK_REDUCED"
    RISK_REJECTED = "RISK_REJECTED"
    EXECUTION_PENDING = "EXECUTION_PENDING"
    EXECUTED = "EXECUTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class RiskStatus(StrEnum):
    APPROVE = "APPROVE"
    REDUCE = "REDUCE"
    WAIT = "WAIT"
    REJECT = "REJECT"


class OrderStatus(StrEnum):
    CREATED = "CREATED"
    RISK_PENDING = "RISK_PENDING"
    RISK_APPROVED = "RISK_APPROVED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


class StrategyStatus(StrEnum):
    RESEARCH = "RESEARCH"
    BACKTEST = "BACKTEST"
    WALK_FORWARD = "WALK_FORWARD"
    PAPER = "PAPER"
    SHADOW = "SHADOW"
    CHALLENGER = "CHALLENGER"
    CHAMPION = "CHAMPION"
    PROBATION = "PROBATION"
    DISABLED = "DISABLED"


class Environment(StrEnum):
    DEMO = "DEMO"
    PAPER = "PAPER"
    SHADOW = "SHADOW"
    LIVE = "LIVE"


class BrokerModeError(RuntimeError):
    pass

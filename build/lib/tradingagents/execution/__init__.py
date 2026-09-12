"""Fail-closed SHADOW and PAPER execution boundary."""

from .brokers import AlpacaPaperBroker, LocalPaperBroker, ShadowBroker
from .engine import ExecutionEngine
from .gates import ExecutionRejected, prepare_execution
from .journal import ExecutionJournal
from .models import EXECUTION_SCHEMA_VERSION, ExecutionIntent, ExecutionReceipt
from .orchestrator import ExecutionOutcome, TradingExecutionOrchestrator

__all__ = [
    "AlpacaPaperBroker",
    "EXECUTION_SCHEMA_VERSION",
    "ExecutionEngine",
    "ExecutionIntent",
    "ExecutionJournal",
    "ExecutionOutcome",
    "ExecutionReceipt",
    "ExecutionRejected",
    "LocalPaperBroker",
    "ShadowBroker",
    "TradingExecutionOrchestrator",
    "prepare_execution",
]

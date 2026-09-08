"""Idempotent dispatch of a prevalidated non-live intent."""

from __future__ import annotations

from .brokers import BrokerAdapter
from .journal import ExecutionJournal
from .models import ExecutionIntent, ExecutionReceipt


class ExecutionEngine:
    def __init__(self, broker: BrokerAdapter, journal: ExecutionJournal) -> None:
        self.broker = broker
        self.journal = journal
        self.journal.initialize()

    def execute(self, intent: ExecutionIntent) -> ExecutionReceipt:
        existing = self.journal.get(intent.client_order_id)
        if existing is not None:
            if existing.request_sha256 != intent.request_sha256:
                raise RuntimeError("client order id collision")
            return existing
        receipt = self.broker.submit(intent)
        return self.journal.record(intent, receipt)

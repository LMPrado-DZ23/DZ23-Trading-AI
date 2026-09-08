"""Durable idempotency journal for broker receipts."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import EXECUTION_SCHEMA_VERSION, ExecutionIntent, ExecutionReceipt


class ExecutionJournal:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS execution_receipts (
                client_order_id TEXT PRIMARY KEY,
                request_sha256 TEXT NOT NULL,
                receipt_json TEXT NOT NULL
            )""")

    def get(self, client_order_id: str) -> ExecutionReceipt | None:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT receipt_json FROM execution_receipts WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
        if row is None:
            return None
        value = json.loads(row[0])
        if value.pop("schema", None) != EXECUTION_SCHEMA_VERSION:
            raise RuntimeError("execution receipt schema is incompatible")
        return ExecutionReceipt(**value)

    def record(self, intent: ExecutionIntent, receipt: ExecutionReceipt) -> ExecutionReceipt:
        if receipt.client_order_id != intent.client_order_id:
            raise ValueError("receipt identity does not match intent")
        if receipt.request_sha256 != intent.request_sha256:
            raise ValueError("receipt digest does not match intent")
        encoded = json.dumps(receipt.to_dict(), sort_keys=True, separators=(",", ":"))
        with sqlite3.connect(self.path, timeout=30) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT request_sha256, receipt_json FROM execution_receipts WHERE client_order_id = ?",
                (intent.client_order_id,),
            ).fetchone()
            if row:
                if row[0] != intent.request_sha256:
                    raise RuntimeError("client order id was reused with different content")
                existing = json.loads(row[1])
                existing.pop("schema", None)
                return ExecutionReceipt(**existing)
            connection.execute(
                "INSERT INTO execution_receipts VALUES (?, ?, ?)",
                (intent.client_order_id, intent.request_sha256, encoded),
            )
        return receipt

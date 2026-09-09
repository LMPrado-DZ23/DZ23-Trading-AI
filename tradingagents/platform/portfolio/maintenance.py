"""Least-privilege PostgreSQL maintenance for portfolio security state."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker


class PortfolioMaintenance:
    """Run bounded maintenance through worker-only database capabilities."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        nonce_batch_size: int = 1000,
    ) -> None:
        if not 1 <= nonce_batch_size <= 10_000:
            raise ValueError("nonce_batch_size must be between 1 and 10000")
        self._sessions = sessions
        self._nonce_batch_size = nonce_batch_size

    def run_once(self) -> int:
        with self._sessions() as db, db.begin():
            if db.get_bind().dialect.name != "postgresql":
                raise RuntimeError("portfolio maintenance requires PostgreSQL")
            deleted = db.scalar(
                text(
                    "SELECT tradingagents_private."
                    "portfolio_prune_capability_nonces_v1(:batch_size)"
                ),
                {"batch_size": self._nonce_batch_size},
            )
            if not isinstance(deleted, int) or deleted < 0:
                raise RuntimeError("portfolio nonce maintenance returned an invalid result")
            return deleted

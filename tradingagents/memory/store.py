"""Append-only, content-free statistical memory with point-in-time queries."""

from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from tradingagents.agents.schemas import PortfolioRating
from tradingagents.dataflows.utils import safe_ticker_component
from tradingagents.portfolio.models import aware_datetime, decimal_value

from .regime import MarketRegime

REGIME_MEMORY_SCHEMA_VERSION = "tradingagents/regime-memory/1"


@dataclass(frozen=True, slots=True)
class RegimeObservation:
    ticker: str
    rating: PortfolioRating
    regime: MarketRegime
    decision_at: datetime
    resolution_at: datetime
    raw_return: Decimal
    alpha_return: Decimal
    holding_sessions: int
    schema: str = REGIME_MEMORY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        ticker = safe_ticker_component(str(self.ticker).strip())
        object.__setattr__(self, "ticker", ticker)
        object.__setattr__(self, "rating", PortfolioRating(self.rating))
        object.__setattr__(self, "regime", MarketRegime(self.regime))
        object.__setattr__(self, "decision_at", aware_datetime(self.decision_at, "decision_at"))
        object.__setattr__(
            self, "resolution_at", aware_datetime(self.resolution_at, "resolution_at")
        )
        if self.resolution_at <= self.decision_at:
            raise ValueError("resolution_at must be after decision_at")
        object.__setattr__(self, "raw_return", decimal_value(self.raw_return, "raw_return"))
        object.__setattr__(self, "alpha_return", decimal_value(self.alpha_return, "alpha_return"))
        if self.holding_sessions <= 0:
            raise ValueError("holding_sessions must be positive")
        if self.schema != REGIME_MEMORY_SCHEMA_VERSION:
            raise ValueError("regime-memory schema is incompatible")

    @property
    def observation_id(self) -> str:
        identity = "|".join((
            self.ticker,
            self.rating.value,
            self.regime.value,
            self.decision_at.isoformat(),
            self.resolution_at.isoformat(),
            str(self.holding_sessions),
            str(self.raw_return),
            str(self.alpha_return),
        ))
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "observation_id": self.observation_id,
            "ticker": self.ticker,
            "rating": self.rating.value,
            "regime": self.regime.value,
            "decision_at": self.decision_at.isoformat(),
            "resolution_at": self.resolution_at.isoformat(),
            "raw_return": str(self.raw_return),
            "alpha_return": str(self.alpha_return),
            "holding_sessions": self.holding_sessions,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RegimeObservation:
        expected = {
            "schema", "observation_id", "ticker", "rating", "regime",
            "decision_at", "resolution_at", "raw_return", "alpha_return",
            "holding_sessions",
        }
        if set(value) != expected:
            raise ValueError("regime observation has missing or unknown fields")
        observation = cls(
            schema=value["schema"],
            ticker=value["ticker"],
            rating=PortfolioRating(value["rating"]),
            regime=MarketRegime(value["regime"]),
            decision_at=datetime.fromisoformat(value["decision_at"]),
            resolution_at=datetime.fromisoformat(value["resolution_at"]),
            raw_return=value["raw_return"],
            alpha_return=value["alpha_return"],
            holding_sessions=int(value["holding_sessions"]),
        )
        if value["observation_id"] != observation.observation_id:
            raise ValueError("regime observation identity mismatch")
        return observation


@dataclass(frozen=True, slots=True)
class RegimeStatistics:
    sample_size: int
    mean_alpha: float
    alpha_hit_rate: float
    sample_stddev: float | None
    confidence_95_low: float | None
    confidence_95_high: float | None


class RegimeMemoryStore:
    def __init__(
        self,
        path: str | Path | None = None,
        *,
        min_samples: int = 5,
        max_records: int = 100_000,
        max_file_bytes: int = 50_000_000,
    ) -> None:
        if min_samples < 2:
            raise ValueError("min_samples must be at least 2")
        if max_records <= 0 or max_file_bytes <= 0:
            raise ValueError("memory resource limits must be positive")
        self.path = Path(path) if path else None
        self.min_samples = min_samples
        self.max_records = max_records
        self.max_file_bytes = max_file_bytes
        self._lock = threading.RLock()
        self._memory: list[RegimeObservation] = []

    def load(self) -> tuple[RegimeObservation, ...]:
        with self._lock:
            if self.path is None:
                return tuple(self._memory)
            if not self.path.exists():
                return ()
            if self.path.stat().st_size > self.max_file_bytes:
                raise ValueError("regime-memory file exceeds the configured byte limit")
            observations = []
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    if len(observations) >= self.max_records:
                        raise ValueError("regime-memory file exceeds the configured record limit")
                    observations.append(RegimeObservation.from_dict(json.loads(line)))
            return tuple(observations)

    def append(self, observation: RegimeObservation) -> bool:
        with self._lock:
            existing = self.load()
            if any(item.observation_id == observation.observation_id for item in existing):
                return False
            if any(
                item.ticker == observation.ticker
                and item.rating is observation.rating
                and item.regime is observation.regime
                and item.decision_at == observation.decision_at
                and item.resolution_at == observation.resolution_at
                and item.holding_sessions == observation.holding_sessions
                for item in existing
            ):
                raise ValueError("conflicting regime observation for the same evaluation")
            updated = (*existing, observation)
            if len(updated) > self.max_records:
                raise ValueError("regime-memory record limit reached")
            if self.path is None:
                self._memory = list(updated)
                return True
            self.path.parent.mkdir(parents=True, exist_ok=True)
            content = "".join(
                json.dumps(item.to_dict(), sort_keys=True, separators=(",", ":")) + "\n"
                for item in updated
            )
            temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
            try:
                temporary.write_text(content, encoding="utf-8")
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
            return True

    def query(
        self,
        ticker: str,
        regime: MarketRegime,
        *,
        as_of: datetime,
        rating: PortfolioRating | None = None,
    ) -> tuple[RegimeObservation, ...]:
        cutoff = aware_datetime(as_of, "as_of")
        ticker = safe_ticker_component(str(ticker).strip())
        return tuple(
            item for item in self.load()
            if item.ticker == ticker
            and item.regime is regime
            and item.resolution_at <= cutoff
            and (rating is None or item.rating is rating)
        )

    def statistics(
        self,
        ticker: str,
        regime: MarketRegime,
        *,
        as_of: datetime,
        rating: PortfolioRating | None = None,
    ) -> RegimeStatistics | None:
        observations = self.query(ticker, regime, as_of=as_of, rating=rating)
        if len(observations) < self.min_samples:
            return None
        values = [float(item.alpha_return) for item in observations]
        mean = statistics.mean(values)
        deviation = statistics.stdev(values) if len(values) >= 2 else None
        margin = 1.96 * deviation / math.sqrt(len(values)) if deviation is not None else None
        return RegimeStatistics(
            sample_size=len(values),
            mean_alpha=mean,
            alpha_hit_rate=sum(value > 0 for value in values) / len(values),
            sample_stddev=deviation,
            confidence_95_low=mean - margin if margin is not None else None,
            confidence_95_high=mean + margin if margin is not None else None,
        )

    def context(self, ticker: str, regime: MarketRegime, *, as_of: datetime) -> str:
        rows = []
        for rating in PortfolioRating:
            result = self.statistics(ticker, regime, as_of=as_of, rating=rating)
            if result is None:
                continue
            rows.append(
                f"{rating.value}: n={result.sample_size}, mean alpha={result.mean_alpha:+.2%}, "
                f"alpha hit rate={result.alpha_hit_rate:.1%}, "
                f"95% CI=[{result.confidence_95_low:+.2%}, {result.confidence_95_high:+.2%}]"
            )
        if not rows:
            return ""
        heading = (
            f"Statistical memory for {ticker} in {regime.value} regime, "
            f"resolved by {as_of.isoformat()}:"
        )
        return "\n".join((heading, *rows))

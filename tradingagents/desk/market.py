"""Feed de mercado real para a mesa (fecha a lacuna do snapshot_provider).

Só leitura pública — sem credencial de escrita, como manda a separação de
poderes: quem lê mercado não envia ordem.

Regras codificadas:
- Toda vela carrega o instante de FECHAMENTO; a vela em formação é
  descartada (ela ainda vai mudar; usá-la é olhar o futuro).
- `cutoff` do snapshot é o fechamento da última vela usada, nunca o relógio
  de parede — decisão nasce de dado disponível, não de "agora".
- Dado insuficiente, fonte fora do ar ou vela velha ⇒ snapshot com qualidade
  degradada. Os traders já recusam propor sobre isso (cenário 06).
- Cache por (exchange, instrumento, timeframe) com validade curta, para a
  mesa inteira reusar a mesma leitura em vez de multiplicar chamadas.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .models import MarketSnapshot

TIMEFRAME_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600,
                     "4h": 14400, "1d": 86400}


class MarketDataError(RuntimeError):
    """Falha de leitura de mercado — nunca vira preço inventado."""


class PublicMarketFeed:
    """Velas públicas via CCXT (sem chaves). Um objeto por exchange."""

    def __init__(self, exchange_id: str = "binance", *, client=None,
                 cache_seconds: int = 30):
        self.exchange_id = exchange_id
        self._client = client
        self._cache: dict[tuple, tuple[float, list]] = {}
        self._lock = threading.Lock()
        self.cache_seconds = cache_seconds

    @property
    def client(self):
        if self._client is None:
            import ccxt

            from tradingagents.brokers.ccxt_bridge import EXCHANGE_ALIASES
            ccxt_id = EXCHANGE_ALIASES.get(self.exchange_id, self.exchange_id)
            if not hasattr(ccxt, ccxt_id):
                raise MarketDataError(f"exchange {self.exchange_id} desconhecida")
            self._client = getattr(ccxt, ccxt_id)({"enableRateLimit": True})
        return self._client

    def ohlcv(self, instrument: str, timeframe: str = "1h", limit: int = 240) -> list:
        key = (self.exchange_id, instrument, timeframe, limit)
        now = time.time()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < self.cache_seconds:
                return hit[1]
        try:
            rows = self.client.fetch_ohlcv(instrument, timeframe, limit=limit)
        except Exception as exc:
            raise MarketDataError(f"{type(exc).__name__}: {exc}"[:200]) from exc
        with self._lock:
            self._cache[key] = (now, rows)
        return rows

    def snapshot(self, instrument: str, timeframe: str = "1h", limit: int = 240,
                 *, now: datetime | None = None) -> MarketSnapshot:
        now = now or datetime.now(timezone.utc)
        step = TIMEFRAME_SECONDS.get(timeframe, 3600)
        try:
            rows = self.ohlcv(instrument, timeframe, limit)
        except MarketDataError as exc:
            return MarketSnapshot(instrument, timeframe, now, (),
                                  source=f"{self.exchange_id}:indisponivel",
                                  quality=f"degraded: {exc}"[:80])
        closed = []
        for ts_ms, _open, _high, _low, close, _vol in rows:
            close_at = datetime.fromtimestamp(ts_ms / 1000, timezone.utc) + timedelta(seconds=step)
            if close_at <= now:                      # descarta a vela em formação
                closed.append((close_at, Decimal(str(close))))
        if not closed:
            return MarketSnapshot(instrument, timeframe, now, (),
                                  source=self.exchange_id,
                                  quality="degraded: nenhuma vela fechada")
        cutoff = closed[-1][0]
        atraso = now - cutoff
        quality = "ok" if atraso <= timedelta(seconds=step * 3) else \
            f"degraded: dado atrasado {int(atraso.total_seconds() // 60)} min"
        return MarketSnapshot(instrument, timeframe, cutoff,
                              tuple(c for _, c in closed),
                              source=f"{self.exchange_id}:{timeframe}", quality=quality)

    def last_price(self, instrument: str, timeframe: str = "1h") -> Decimal:
        snap = self.snapshot(instrument, timeframe, limit=3)
        if not snap.closes:
            raise MarketDataError(f"sem preço para {instrument}")
        return snap.last


class MarketFeedProviders:
    """Adapta o feed aos contratos que o agendador espera."""

    def __init__(self, feed: PublicMarketFeed, instruments, timeframe: str = "1h",
                 limit: int = 240):
        self.feed = feed
        self.instruments = tuple(instruments)
        self.timeframe = timeframe
        self.limit = limit

    def snapshots(self) -> list[MarketSnapshot]:
        return [self.feed.snapshot(i, self.timeframe, self.limit)
                for i in self.instruments]

    def prices(self) -> dict[str, Decimal]:
        """Preço de EXECUÇÃO. Instrumento sem preço fica de fora — o
        orquestrador veta a proposta em vez de chutar um número."""
        out = {}
        for i in self.instruments:
            try:
                out[i] = self.feed.last_price(i, self.timeframe)
            except MarketDataError:
                continue
        return out


__all__ = ["PublicMarketFeed", "MarketFeedProviders", "MarketDataError"]

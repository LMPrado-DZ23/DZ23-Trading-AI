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
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .microestrutura import LeituraDeLivro, analisar_livro
from .models import MarketSnapshot

TIMEFRAME_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600,
                     "4h": 14400, "1d": 86400}


class MarketDataError(RuntimeError):
    """Falha de leitura de mercado — nunca vira preço inventado."""


class PublicMarketFeed:
    """Velas públicas via CCXT (sem chaves). Um objeto por exchange."""

    def __init__(self, exchange_id: str = "binance", *, client=None,
                 cache_seconds: int = 30, book_cache_seconds: int = 10):
        self.exchange_id = exchange_id
        self._client = client
        self._cache: dict[tuple, tuple[float, list]] = {}
        self._cache_livro: dict[tuple, tuple[float, datetime, dict]] = {}
        self._lock = threading.Lock()
        self.cache_seconds = cache_seconds
        self.book_cache_seconds = book_cache_seconds

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

    def historical_snapshot(self, instrument: str, timeframe: str = "1h", *,
                            limit: int = 5000, page_size: int = 1000,
                            now: datetime | None = None) -> MarketSnapshot:
        """Carrega histórico público paginado para validação quantitativa.

        ``snapshot`` atende a mesa online com uma leitura curta e cacheada.
        Esta variante é deliberadamente offline/read-only: pagina por ``since``,
        deduplica timestamps e exige velas fechadas. Corretora que ignora
        ``since`` não causa loop nem ganha velas inventadas.
        """
        if limit <= 0 or page_size <= 0:
            raise ValueError("limit e page_size precisam ser positivos")
        if timeframe not in TIMEFRAME_SECONDS:
            raise ValueError(f"timeframe não suportado: {timeframe}")
        now = now or datetime.now(timezone.utc)
        step_ms = TIMEFRAME_SECONDS[timeframe] * 1000
        now_ms = int(now.timestamp() * 1000)
        # Duas barras de folga: uma pode estar aberta e limites de API podem
        # cortar exatamente na fronteira solicitada.
        since = now_ms - (limit + 2) * step_ms
        por_timestamp: dict[int, list] = {}
        max_paginas = ((limit + 2 + page_size - 1) // page_size) + 3
        try:
            for _ in range(max_paginas):
                pagina = self.client.fetch_ohlcv(
                    instrument, timeframe, since, min(page_size, limit + 2))
                if not pagina:
                    break
                maior = since - step_ms
                for row in pagina:
                    if not isinstance(row, (list, tuple)) or len(row) < 6:
                        continue
                    try:
                        ts = int(row[0])
                        close = Decimal(str(row[4]))
                        if not close.is_finite():
                            continue
                    except (TypeError, ValueError, ArithmeticError):
                        continue
                    normalizada = list(row[:6])
                    normalizada[4] = close
                    por_timestamp[ts] = normalizada
                    maior = max(maior, ts)
                proximo = maior + step_ms
                if proximo <= since:       # API ignorou since / página repetida
                    break
                since = proximo
                if since > now_ms:
                    break
        except Exception as exc:
            return MarketSnapshot(
                instrument, timeframe, now, (),
                source=f"{self.exchange_id}:historico-indisponivel",
                quality=f"degraded: {type(exc).__name__}: falha de rede"[:80])

        fechadas = []
        for ts, row in sorted(por_timestamp.items()):
            close_at = datetime.fromtimestamp(
                ts / 1000, timezone.utc) + timedelta(milliseconds=step_ms)
            if close_at <= now:
                fechadas.append((close_at, row[4]))
        fechadas = fechadas[-limit:]
        if not fechadas:
            return MarketSnapshot(
                instrument, timeframe, now, (),
                source=f"{self.exchange_id}:{timeframe}:historico",
                quality="degraded: nenhuma vela fechada")
        cutoff = fechadas[-1][0]
        atraso = now - cutoff
        if len(fechadas) != limit:
            quality = f"degraded: apenas {len(fechadas)}/{limit} velas fechadas"
        elif atraso > timedelta(milliseconds=step_ms * 3):
            quality = (
                "degraded: dado histórico atrasado "
                f"{int(atraso.total_seconds() // 60)} min")
        else:
            quality = "ok"
        return MarketSnapshot(
            instrument, timeframe, cutoff,
            tuple(close for _, close in fechadas),
            source=f"{self.exchange_id}:{timeframe}:historico",
            quality=quality)

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

    def leitura_de_livro(self, instrument: str, *, limit: int = 50,
                         niveis: int = 10, max_idade_s: int = 15,
                         agora: datetime | None = None) -> LeituraDeLivro:
        """Livro L2 PÚBLICO e SEM escrita — mesma regra do resto do feed.

        Corretora sem `fetchOrderBook` nativo (flag True no ccxt;
        'emulated' não vale) ⇒ `nao_suportado` sem tocar a rede. Falha de
        rede: sem leitura anterior ⇒ `indisponivel`; com leitura anterior
        ⇒ `vencida` (números ficam, motivo aponta a falha). Cache curto
        por (exchange, instrumento, limit)."""
        agora = agora or datetime.now(timezone.utc)
        try:
            cliente = self.client
        except MarketDataError as exc:
            return LeituraDeLivro(instrumento=instrument, exchange=self.exchange_id,
                                  estado="indisponivel",
                                  motivo=f"fonte indisponível: {exc}",
                                  obtido_em=agora.isoformat())
        has = getattr(cliente, "has", None)
        if isinstance(has, dict) and has.get("fetchOrderBook") is not True:
            return LeituraDeLivro(
                instrumento=instrument, exchange=self.exchange_id,
                estado="nao_suportado",
                motivo=(f"corretora sem fetchOrderBook nativo (capability="
                        f"{has.get('fetchOrderBook')!r}) — sem leitura de "
                        "livro"), obtido_em=agora.isoformat())
        if not callable(getattr(cliente, "fetch_order_book", None)):
            return LeituraDeLivro(
                instrumento=instrument, exchange=self.exchange_id,
                estado="nao_suportado",
                motivo="cliente sem fetch_order_book — sem leitura de livro",
                obtido_em=agora.isoformat())
        key = (self.exchange_id, instrument, limit)
        with self._lock:
            hit = self._cache_livro.get(key)
        if hit and time.monotonic() - hit[0] < self.book_cache_seconds:
            return analisar_livro(self.exchange_id, instrument, hit[2],
                                  agora=agora, max_idade_s=max_idade_s,
                                  niveis=niveis, obtido_em=hit[1])
        try:
            bruto = cliente.fetch_order_book(instrument, limit)
        except Exception as exc:
            if hit is not None:
                ultima = analisar_livro(self.exchange_id, instrument, hit[2],
                                        agora=agora, max_idade_s=max_idade_s,
                                        niveis=niveis, obtido_em=hit[1])
                if ultima.estado in ("disponivel", "vencida"):
                    return replace(ultima, estado="vencida", motivo=(
                        f"fonte indisponível ({type(exc).__name__}) — última "
                        f"leitura há {ultima.idade_s} s"))
            return LeituraDeLivro(
                instrumento=instrument, exchange=self.exchange_id,
                estado="indisponivel",
                motivo=f"{type(exc).__name__}: falha de rede na leitura",
                obtido_em=agora.isoformat())
        with self._lock:
            self._cache_livro[key] = (time.monotonic(), agora, bruto)
        return analisar_livro(self.exchange_id, instrument, bruto,
                              agora=agora, max_idade_s=max_idade_s,
                              niveis=niveis)


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

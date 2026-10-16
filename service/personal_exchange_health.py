"""Prova pública, somente leitura, das corretoras pessoais DZ23.

O catálogo local mostra que um adaptador existe. Esta sonda comprova uma
coisa diferente: que o endpoint público respondeu agora com cotação e velas
válidas para um mercado conhecido. Ela nunca recebe nem injeta credenciais e
não possui caminho de criação, cancelamento, saque ou transferência.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable
from decimal import Decimal, InvalidOperation

from service.personal_exchange_catalog import get_personal_exchange

PROBE_SYMBOLS = {
    "binance": "BTC/USDT",
    "bybit": "BTC/USDT",
    "okx": "BTC/USDT",
    "coinbase": "BTC/USD",
    "gateio": "BTC/USDT",
    "kraken": "BTC/USD",
    "kucoin": "BTC/USDT",
    "mercadobitcoin": "BTC/BRL",
    "bitget": "BTC/USDT",
}


def _preco_positivo(valor) -> bool:
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return numero.is_finite() and numero > 0


def probe_public_exchange(
        slug: str, *, loader: Callable[[], object] | None = None,
        monotonic: Callable[[], float] = time.monotonic) -> dict:
    """Consulta somente ticker e OHLCV públicos de uma corretora aprovada."""
    spec = get_personal_exchange(slug)
    if spec.ccxt_id is None or slug not in PROBE_SYMBOLS:
        return {
            "id": spec.slug, "status": "NOT_ELIGIBLE",
            "ok": False, "read_only": True, "credentials_used": False,
            "detail": "adaptador experimental sem prova pública operacional",
        }
    if loader is None:
        import ccxt
        module = ccxt
    else:
        module = loader()
    if not hasattr(module, spec.ccxt_id):
        return {
            "id": spec.slug, "ccxt_id": spec.ccxt_id,
            "status": "ADAPTER_NOT_INSTALLED", "ok": False,
            "read_only": True, "credentials_used": False,
            "detail": "adaptador não existe no CCXT instalado",
        }

    symbol = PROBE_SYMBOLS[slug]
    inicio = monotonic()
    exchange = getattr(module, spec.ccxt_id)({
        "enableRateLimit": True,
        "timeout": 10_000,
    })
    try:
        capacidades = getattr(exchange, "has", {}) or {}
        if capacidades.get("fetchTicker") is not True:
            raise RuntimeError("fetchTicker público nativo ausente")
        if capacidades.get("fetchOHLCV") is not True:
            raise RuntimeError("fetchOHLCV público nativo ausente")
        ticker = exchange.fetch_ticker(symbol)
        preco = (ticker or {}).get("last") or (ticker or {}).get("close")
        if not _preco_positivo(preco):
            raise ValueError("ticker sem preço positivo e finito")
        velas = exchange.fetch_ohlcv(symbol, "1h", limit=3)
        validas = [row for row in (velas or ())
                   if isinstance(row, (list, tuple)) and len(row) >= 6
                   and _preco_positivo(row[4])]
        if not validas:
            raise ValueError("OHLCV público sem vela válida")
        latencia = max(0, math.ceil((monotonic() - inicio) * 1000))
        return {
            "id": spec.slug, "name": spec.name, "ccxt_id": spec.ccxt_id,
            "status": "PUBLIC_READ_OK", "ok": True,
            "symbol": symbol, "timeframe": "1h",
            "candles": len(validas), "latency_ms": latencia,
            "read_only": True, "credentials_used": False,
            "methods": ["fetch_ticker", "fetch_ohlcv"],
        }
    except Exception as exc:
        return {
            "id": spec.slug, "name": spec.name, "ccxt_id": spec.ccxt_id,
            "status": "PUBLIC_READ_UNAVAILABLE", "ok": False,
            "symbol": symbol, "read_only": True,
            "credentials_used": False,
            "error_type": type(exc).__name__,
            "detail": str(exc)[:200],
        }
    finally:
        fechar = getattr(exchange, "close", None)
        if callable(fechar):
            fechar()


__all__ = ["PROBE_SYMBOLS", "probe_public_exchange"]

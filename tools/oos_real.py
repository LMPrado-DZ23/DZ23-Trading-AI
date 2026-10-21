"""Validação walk-forward em dados reais (Binance público, sem chaves).

Baixa OHLCV fechado, roda os traders homologados com a MESMA ExitPolicy da
produção e imprime o veredito com custos descontados. Não otimiza parâmetro:
o objetivo é medir, não fazer o backtest ficar bonito.

Uso:
    uv run python tools/oos_real.py --days 365 \
        --symbols BTC/USDT,ETH/USDT,SOL/USDT --exchange binance \
        --out docs/mission/oos-real.json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import ccxt

from tradingagents.desk.traders import default_desk
from tradingagents.desk.validation import validar


def baixar(exchange, symbol: str, dias: int, timeframe: str = "1h") -> tuple:
    """Fecha candles completos dos últimos `dias`; descarta a vela em formação."""
    step_ms = 3600_000
    agora = datetime.now(timezone.utc)
    since = int((agora - timedelta(days=dias)).timestamp() * 1000)
    fim = int(agora.timestamp() * 1000)
    rows: list = []
    while since < fim:
        lote = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        if not lote:
            break
        rows.extend(lote)
        since = lote[-1][0] + step_ms
        if len(lote) < 1000:
            break
    unicos = {int(r[0]): r for r in rows}
    fechadas = [r for ts, r in sorted(unicos.items()) if ts + step_ms <= fim]
    return tuple(Decimal(str(r[4])) for r in fechadas), len(fechadas)


def rodar(exchange_id: str, simbolos: tuple[str, ...], dias: int) -> dict:
    exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
    saida = {
        "exchange": exchange_id,
        "timeframe": "1h",
        "dias": dias,
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ativos": {},
    }
    for simbolo in simbolos:
        try:
            closes, n = baixar(exchange, simbolo, dias)
        except Exception as exc:  # rede/exchange: declarar, não inventar
            saida["ativos"][simbolo] = {
                "erro": f"{type(exc).__name__}: {exc}"[:200]}
            continue
        traders = []
        for trader in default_desk((simbolo,)):
            if not getattr(trader, "homologado", False):
                continue
            resultado = validar(trader, closes, simbolo, exchange=exchange_id)
            traders.append({
                "trader": trader.spec.agent_id,
                "estrategia": trader.strategy,
                "velas": resultado.velas_avaliadas,
                "operacoes": resultado.total,
                "abstencoes": resultado.abstencoes,
                "retorno_bruto_total": str(resultado.retorno_bruto_total),
                "custo_total": str(resultado.custo_total),
                "retorno_liquido_total": str(resultado.retorno_liquido_total),
                "retorno_medio": str(resultado.retorno_medio),
                "taxa_acerto": round(resultado.taxa_acerto, 4),
                "pior_sequencia_perda": str(resultado.maior_perda_seguida),
                "veredito": resultado.veredito(),
            })
        saida["ativos"][simbolo] = {"velas_fechadas": n, "traders": traders}
        print(f"{simbolo}: {n} velas, {len(traders)} traders", flush=True)
    return saida


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--symbols", default="BTC/USDT,ETH/USDT,SOL/USDT")
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    simbolos = tuple(s.strip().upper() for s in args.symbols.split(",") if s.strip())
    saida = rodar(args.exchange, simbolos, args.days)
    texto = json.dumps(saida, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(texto + "\n")
    print(texto)


if __name__ == "__main__":
    main()

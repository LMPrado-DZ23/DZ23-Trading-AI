#!/usr/bin/env python3
"""Valida as estratégias com DADOS REAIS de mercado, descontando custos.

Rode no servidor (onde há acesso às exchanges):

    python scripts/validar_estrategias.py --exchange binance \
        --instrumentos BTC/USDT,ETH/USDT --velas 1000

O que ele responde: depois de taxa, spread e slippage, cada estratégia
ganhou ou perdeu dinheiro neste período? Sem enfeite e sem otimização.
"""
from __future__ import annotations

import argparse
import sys
from decimal import Decimal

sys.path.insert(0, ".")

from tradingagents.desk.market import PublicMarketFeed  # noqa: E402
from tradingagents.desk.traders import default_desk  # noqa: E402
from tradingagents.desk.validation import validar  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--exchange", default="binance")
    p.add_argument("--instrumentos", default="BTC/USDT,ETH/USDT")
    p.add_argument("--timeframe", default="1h")
    p.add_argument("--velas", type=int, default=1000)
    p.add_argument("--capital", default="1000")
    args = p.parse_args()

    feed = PublicMarketFeed(args.exchange)
    instrumentos = [i.strip().upper() for i in args.instrumentos.split(",") if i.strip()]
    capital = Decimal(args.capital)
    houve_aprovada = False

    for instrumento in instrumentos:
        print(f"\n{'=' * 72}\n{instrumento} · {args.exchange} · {args.timeframe} "
              f"· {args.velas} velas\n{'=' * 72}")
        snap = feed.snapshot(instrumento, args.timeframe, args.velas)
        if not snap.closes:
            print(f"  SEM DADOS: {snap.quality}")
            print("  (servidor sem acesso à exchange? tente outra ou verifique a rede)")
            continue
        print(f"  {len(snap.closes)} velas até {snap.cutoff:%Y-%m-%d %H:%M} "
              f"· qualidade: {snap.quality}")
        for trader in default_desk(tuple(instrumentos), exchange=args.exchange,
                                   capital=capital):
            r = validar(trader, snap.closes, instrumento,
                        exchange=args.exchange, capital=capital)
            v = r.veredito()
            marca = "APROVADA " if v.get("aprovada") else "reprovada"
            houve_aprovada = houve_aprovada or v.get("aprovada", False)
            print(f"\n  [{marca}] {trader.strategy}")
            print(f"    {v['resumo']}")
            if r.total:
                print(f"    acerto {v['acerto']:.0%} · bruto {v['retorno_bruto']} "
                      f"· custo {v['custo_pago']} · líquido {v['retorno_liquido']}")
                print(f"    média por operação {v['retorno_medio_por_operacao']} "
                      f"· pior sequência {v['pior_sequencia']}")
            for r_ in v.get("ressalvas", []):
                print(f"    ⚠ {r_}")

    print(f"\n{'=' * 72}")
    print("Nenhuma estratégia aprovada não é fracasso do sistema: é informação "
          "honesta.\nRodar com dinheiro uma estratégia reprovada aqui é que "
          "seria o erro." if not houve_aprovada else
          "Estratégia aprovada no passado ≠ lucro futuro. Continue em paper "
          "antes de arriscar capital.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Valida as estratégias com DADOS REAIS de mercado, descontando custos.

Rode no servidor (onde há acesso às exchanges):

    python scripts/validar_estrategias.py --exchange binance \
        --instrumentos BTC/USDT,ETH/USDT --velas 5000 \
        --treino 2000 --teste 500 --embargo 48

O que ele responde: depois de taxa, spread e slippage, cada estratégia
ganhou ou perdeu dinheiro somente nas janelas fora da amostra? Sem enfeite,
sem otimização e sem usar credenciais ou enviar ordens.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, ".")

from tradingagents.desk.market import PublicMarketFeed  # noqa: E402
from tradingagents.desk.traders import default_desk  # noqa: E402
from tradingagents.desk.validation import (  # noqa: E402
    consolidar_walk_forward,
    validar_em_walk_forward,
    veredito_walk_forward,
)
from tradingagents.research.backtest import revisao_do_codigo  # noqa: E402


def _hash_serie(closes) -> str:
    serializado = "\n".join(str(valor) for valor in closes).encode()
    return hashlib.sha256(serializado).hexdigest()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--exchange", default="binance")
    p.add_argument("--instrumentos", default="BTC/USDT,ETH/USDT")
    p.add_argument("--timeframe", default="1h")
    p.add_argument("--velas", type=int, default=5000)
    p.add_argument("--treino", type=int, default=2000)
    p.add_argument("--teste", type=int, default=500)
    p.add_argument("--embargo", type=int, default=48)
    p.add_argument("--capital", default="1000")
    p.add_argument("--saida-json", type=Path,
                   help="salva evidência reproduzível, sem segredos")
    args = p.parse_args()

    if min(args.velas, args.treino, args.teste) <= 0 or args.embargo < 0:
        p.error("velas/treino/teste devem ser positivos e embargo >= 0")
    if args.treino + args.embargo + args.teste > args.velas:
        p.error("a série não comporta treino + embargo + teste")

    feed = PublicMarketFeed(args.exchange)
    instrumentos = [i.strip().upper() for i in args.instrumentos.split(",") if i.strip()]
    capital = Decimal(args.capital)
    houve_aprovada = False
    evidencia = {
        "schema": "dz23-validacao-oos-v1",
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "revisao_codigo": revisao_do_codigo(),
        "exchange": args.exchange,
        "timeframe": args.timeframe,
        "configuracao": {
            "velas": args.velas,
            "treino": args.treino,
            "teste": args.teste,
            "embargo": args.embargo,
            "capital": str(capital),
        },
        "instrumentos": [],
        "aviso": "dados públicos históricos; não autoriza LIVE nem prevê lucro futuro",
    }

    for instrumento in instrumentos:
        print(f"\n{'=' * 72}\n{instrumento} · {args.exchange} · {args.timeframe} "
              f"· {args.velas} velas\n{'=' * 72}")
        snap = feed.historical_snapshot(
            instrumento, args.timeframe, limit=args.velas)
        item = {
            "instrumento": instrumento,
            "fonte": snap.source,
            "qualidade": snap.quality,
            "cutoff": snap.cutoff.isoformat(),
            "velas_obtidas": len(snap.closes),
            "sha256_fechamentos": _hash_serie(snap.closes),
            "estrategias": [],
        }
        evidencia["instrumentos"].append(item)
        if not snap.closes or snap.quality != "ok":
            print(f"  SEM DADOS: {snap.quality}")
            print("  validação recusada: histórico completo é obrigatório")
            continue
        print(f"  {len(snap.closes)} velas até {snap.cutoff:%Y-%m-%d %H:%M} "
              f"· qualidade: {snap.quality}")
        for trader in default_desk(tuple(instrumentos), exchange=args.exchange,
                                   capital=capital):
            estrategia = trader.strategy

            def criar_trader(estrategia=estrategia):
                return next(
                    candidato for candidato in default_desk(
                        tuple(instrumentos), exchange=args.exchange,
                        capital=capital)
                    if candidato.strategy == estrategia
                )

            janelas = validar_em_walk_forward(
                criar_trader, snap.closes, instrumento,
                train_size=args.treino, test_size=args.teste,
                embargo=args.embargo, exchange=args.exchange,
                capital=capital)
            if not janelas:
                print(f"\n  [reprovada] {estrategia}")
                print("    nenhuma janela OOS completa; validação inconclusiva")
                continue
            r = consolidar_walk_forward(janelas)
            v = veredito_walk_forward(janelas)
            marca = "APROVADA " if v.get("aprovada") else "reprovada"
            houve_aprovada = houve_aprovada or v.get("aprovada", False)
            print(f"\n  [{marca}] {trader.strategy}")
            print(f"    {len(janelas)} janelas OOS não sobrepostas · "
                  f"{r.velas_avaliadas} decisões avaliadas")
            print(f"    {v['resumo']}")
            if r.total:
                print(f"    acerto {v['acerto']:.0%} · bruto {v['retorno_bruto']} "
                      f"· custo {v['custo_pago']} · líquido {v['retorno_liquido']}")
                print(f"    média por operação {v['retorno_medio_por_operacao']} "
                      f"· pior sequência {v['pior_sequencia']}")
            for r_ in v.get("ressalvas", []):
                print(f"    AVISO: {r_}")
            item["estrategias"].append({
                "estrategia": estrategia,
                "janelas_oos": len(janelas),
                "intervalos_teste": [
                    [janela.test_start, janela.test_end]
                    for janela in janelas
                ],
                "velas_avaliadas": r.velas_avaliadas,
                "veredito": v,
            })

    if args.saida_json:
        args.saida_json.parent.mkdir(parents=True, exist_ok=True)
        args.saida_json.write_text(
            json.dumps(evidencia, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"\nEvidência salva em {args.saida_json}")

    print(f"\n{'=' * 72}")
    print("Nenhuma estratégia aprovada não é fracasso do sistema: é informação "
          "honesta.\nRodar com dinheiro uma estratégia reprovada aqui é que "
          "seria o erro." if not houve_aprovada else
          "Estratégia aprovada no passado não garante lucro futuro. Continue em paper "
          "antes de arriscar capital.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

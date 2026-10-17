#!/usr/bin/env python3
"""Verifica ticker e velas públicas das nove corretoras DZ23.

Não lê credenciais, não autentica e não possui caminho de envio de ordens.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")

from service.personal_exchange_catalog import EXCHANGES  # noqa: E402
from service.personal_exchange_health import probe_public_exchange  # noqa: E402
from tradingagents.research.backtest import revisao_do_codigo  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--saida-json", type=Path)
    args = parser.parse_args()
    resultados = []
    for exchange in EXCHANGES:
        if not exchange.read_only_enabled:
            continue
        resultado = probe_public_exchange(exchange.slug)
        resultados.append(resultado)
        estado = "OK" if resultado.get("ok") else "FALHOU"
        detalhe = (f"{resultado.get('symbol', '')} · "
                   f"{resultado.get('latency_ms', '-')} ms"
                   if resultado.get("ok") else
                   f"{resultado.get('error_type', '')} "
                   f"{resultado.get('detail', '')}")
        print(f"[{estado}] {exchange.name}: {detalhe}")

    evidencia = {
        "schema": "dz23-corretoras-publicas-v1",
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "revisao_codigo": revisao_do_codigo(),
        "somente_leitura": True,
        "credenciais_usadas": False,
        "ordens_enviadas": False,
        "total": len(resultados),
        "funcionando": sum(bool(item.get("ok")) for item in resultados),
        "resultados": resultados,
    }
    if args.saida_json:
        args.saida_json.parent.mkdir(parents=True, exist_ok=True)
        args.saida_json.write_text(
            json.dumps(evidencia, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"Evidência salva em {args.saida_json}")
    return 0 if evidencia["funcionando"] == evidencia["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Probe offline da divergencia contabil de taxas: DZ23 x convencao por ponta.

Achado V9-QUANT-07 (RISCO/media): `ModeloDeCusto.ida_e_volta` desconta as
DUAS taxas sobre o notional inicial; a convencao por ponta (referencia
`LocalTrade.calculate_profit`, Freqtrade) cobra a taxa de saida sobre o
notional de saida. Meta deste probe: medir a diferenca no cenario do achado
(compra 1 @ 100, venda 1 @ 110, taxa 0,1% por ponta) com numeros exatos.

Roda em qualquer venv (o nucleo de `costs.py` so usa stdlib). Se o
`freqtrade` estiver instalado no interpretador atual — e so no venv isolado
do spike que ele existe — a referencia primaria e obtida da propria
biblioteca; caso contrario o JSON registra `disponivel: false` e a
referencia por ponta fica pela aritmetica explicita.

Sem rede, sem credenciais, sem execucao financeira. Nada de GPL entra
no repositorio: a biblioteca e usada como processo/ferramenta separada.

Uso:
    python tools/v9_quant07_divergencia_custos.py [saida.json]
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from tradingagents.desk.costs import ModeloDeCusto, retorno_liquido  # noqa: E402

ENTRADA = Decimal("100")
QUANTIDADE = Decimal("1")
TAXA_POR_PONTA = Decimal("0.001")


def _dz23(entrada: Decimal, saida: Decimal) -> dict:
    bruto = (saida - entrada) / entrada
    custo = ModeloDeCusto(exchange="binance", taxa=TAXA_POR_PONTA,
                          spread=Decimal("0"), slippage=Decimal("0"))
    liquido = retorno_liquido(bruto, custo)
    return {
        "convencao": "duas taxas sobre o notional inicial "
                     "(ida_e_volta = 2 x taxa)",
        "bruto_fracao": str(bruto),
        "ida_e_volta": str(custo.ida_e_volta),
        "liquido_fracao": str(liquido),
        "liquido_unidades": str(liquido * ENTRADA),
    }


def _por_ponta(entrada: Decimal, saida: Decimal) -> dict:
    desembolso = QUANTIDADE * entrada * (Decimal("1") + TAXA_POR_PONTA)
    venda_liquida = QUANTIDADE * saida * (Decimal("1") - TAXA_POR_PONTA)
    liquido = venda_liquida - desembolso
    return {
        "convencao": "taxa de cada ponta sobre o notional da propria ponta "
                     "(referencia Freqtrade)",
        "desembolso_compra": str(desembolso),
        "venda_liquida": str(venda_liquida),
        "liquido_unidades": str(liquido),
    }


def _freqtrade(saida: Decimal) -> dict:
    try:
        import freqtrade  # type: ignore
        from freqtrade.persistence.trade_model import LocalTrade  # type: ignore
    except ImportError:
        return {"disponivel": False}
    t = LocalTrade()
    t.is_short = False
    t.leverage = 1.0
    t.amount = float(QUANTIDADE)
    t.open_rate = float(ENTRADA)
    t.fee_open = float(TAXA_POR_PONTA)
    t.fee_open_currency = None
    t.close_rate = float(saida)
    t.fee_close = float(TAXA_POR_PONTA)
    t.fee_close_currency = None
    t.realized_profit = 0.0
    t.max_stake_amount = 0.0
    t.stake_amount = float(ENTRADA * QUANTIDADE)
    t.trading_mode = "spot"
    p = t.calculate_profit(float(saida), amount=float(QUANTIDADE),
                           open_rate=float(ENTRADA))
    return {
        "disponivel": True,
        "versao": freqtrade.__version__,
        "metodo": "LocalTrade.calculate_profit(saida, amount=1.0, "
                  "open_rate=100.0)",
        "profit_abs": round(float(p.profit_abs), 10),
        "profit_ratio": round(float(p.profit_ratio), 10),
    }


def _cenario(saida: Decimal) -> dict:
    bruto = (saida - ENTRADA) / ENTRADA
    dz23 = _dz23(ENTRADA, saida)
    por_ponta = _por_ponta(ENTRADA, saida)
    divergencia = (Decimal(dz23["liquido_unidades"])
                   - Decimal(por_ponta["liquido_unidades"]))
    return {
        "saida": str(saida),
        "dz23": dz23,
        "referencia_por_ponta": por_ponta,
        "freqtrade": _freqtrade(saida),
        "divergencia_unidades": str(divergencia),
        "divergencia_fracao": str(divergencia / ENTRADA),
        "divergencia_formula": f"taxa * bruto = {TAXA_POR_PONTA} * {bruto}",
        "direcao": ("DZ23 otimista em ganhos por exatamente taxa*bruto"
                    if divergencia > 0 else
                    "DZ23 pessimista em perdas (conservador para capital)"),
    }


def main() -> int:
    resultado = {
        "achado": "V9-QUANT-07",
        "cenario": {
            "quantidade": str(QUANTIDADE),
            "entrada": str(ENTRADA),
            "taxa_por_ponta": str(TAXA_POR_PONTA),
            "notional_inicial": str(ENTRADA * QUANTIDADE),
        },
        "ganho": _cenario(Decimal("110")),
        "perda": _cenario(Decimal("90")),
        "interpretacao": (
            "a diferenca entre as duas convencoes e exatamente taxa*bruto "
            "da fracao (segunda ordem): o modelo DZ23 de primeira ordem "
            "e conservador em perdas e levemente otimista em ganhos; "
            "nenhuma alegacao de equivalencia EXATA de taxas com o "
            "Freqtrade pode ignorar esta diferenca"),
    }
    texto = json.dumps(resultado, ensure_ascii=False, indent=2)
    print(texto)
    if len(sys.argv) > 1:
        destino = Path(sys.argv[1])
        destino.write_text(texto + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

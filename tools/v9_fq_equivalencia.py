"""FQ-2 rodada 1 — confronto DZ23 x Freqtrade (instrumento de auditoria, read-only).

Executa o lado DZ23 da rodada sobre AS MESMAS velas e MESMOS sinais do backtest
freqtrade exportado (BTC/USDT 1h, SMA 10/30, janela fixa 2026-02-01..2026-07-01):

Parte A (equivalencia de motor contabil): reproduz o MESMO modelo de execucao
declarado no protocolo — fill de mercado no OPEN da vela seguinte ao sinal —
com as regras DZ23 (custo por perna sobre o notional; notional fixo de 100 USDT
sobre equity 1000; truncamento de quantidade na convencao da exchange) e confere
trade a trade contra o export do freqtrade (fills, taxas, P&L, carteira final).

Parte B (divergencia de modelos, declarada): roda o motor do laboratorio DZ23
(`tradingagents.research.backtest.run_backtest`, modelo nativo
`entry=next_open;exit=same_close`, `max_participacao=1`) sobre os mesmos dados e
os mesmos eventos de entrada, e registra o resultado como divergencia DE
PROJETO em relacao ao freqtrade (causa: modelo de fill/posicao diferente).

Nenhuma rede, nenhuma credencial, nenhuma ordem. Nao aprova estrategia alguma.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from tradingagents.research.backtest import (  # noqa: E402
    Bar,
    BacktestConfig,
    revisao_do_codigo,
    run_backtest,
)

JANELA_INICIO = datetime(2026, 2, 1, tzinfo=timezone.utc)
JANELA_FIM = datetime(2026, 7, 1, tzinfo=timezone.utc)
HORA = timedelta(hours=1)
SMA_RAPIDA = 10
SMA_LENTA = 30
TAXA = Decimal("0.001")          # 10 bps por lado, igual nos dois motores
NOTIONAL = Decimal("100")        # posicao fixa, igual ao stake_amount do freqtrade
EQUITY_INICIAL = Decimal("1000")
PRECISAO_QUANTIDADE = Decimal("0.00001")  # stepSize BTC/USDT (5 casas)


def carregar_velas(caminho: Path) -> list[tuple[datetime, float, float, float, float, float]]:
    bruto = json.loads(caminho.read_text(encoding="utf-8"))
    if isinstance(bruto, dict):
        linhas = bruto["data"]
    else:
        linhas = bruto
    velas = []
    for linha in linhas:
        ts_ms = int(linha[0])
        ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        velas.append((ts, float(linha[1]), float(linha[2]), float(linha[3]), float(linha[4]), float(linha[5])))
    velas.sort(key=lambda v: v[0])
    return velas


def smas(velas) -> tuple[list[float | None], list[float | None]]:
    fechamentos = [v[4] for v in velas]
    rapida: list[float | None] = [None] * len(velas)
    lenta: list[float | None] = [None] * len(velas)
    for i in range(len(velas)):
        if i + 1 >= SMA_RAPIDA:
            rapida[i] = sum(fechamentos[i - SMA_RAPIDA + 1 : i + 1]) / SMA_RAPIDA
        if i + 1 >= SMA_LENTA:
            lenta[i] = sum(fechamentos[i - SMA_LENTA + 1 : i + 1]) / SMA_LENTA
    return rapida, lenta


def cruzar(velas, rapida, lenta):
    """Eventos de cruzamento da MESMA regra dos dois motores (fechamento UTC)."""
    eventos = {}
    for i in range(1, len(velas)):
        if rapida[i] is None or lenta[i] is None or rapida[i - 1] is None or lenta[i - 1] is None:
            continue
        if rapida[i] > lenta[i] and rapida[i - 1] <= lenta[i - 1]:
            eventos[velas[i][0] + HORA] = "entrada"   # fill na vela seguinte (open)
        elif rapida[i] < lenta[i] and rapida[i - 1] >= lenta[i - 1]:
            eventos[velas[i][0] + HORA] = "saida"
    return eventos


def truncar_quantidade(bruto: Decimal) -> Decimal:
    return (bruto / PRECISAO_QUANTIDADE).to_integral_value(rounding="ROUND_FLOOR") * PRECISAO_QUANTIDADE


def parte_a(velas, eventos, trades_freqtrade):
    """Ponte DZ23 no modelo nativo freqtrade: fill no open da vela seguinte ao sinal."""
    carteira = EQUITY_INICIAL
    resultado_trades = []
    estado = "flat"
    entrada = None
    esperados = {t["open_timestamp"]: t for t in trades_freqtrade}
    for ts, aberto, _h, _l, _c, _v in velas:
        if ts < JANELA_INICIO or ts >= JANELA_FIM:
            continue
        evento = eventos.get(ts)
        if estado == "flat" and evento == "entrada":
            aberto_d = Decimal(str(aberto))
            quantidade = truncar_quantidade(NOTIONAL / aberto_d)
            entrada = {"ts": ts, "preco": aberto_d, "quantidade": quantidade}
            estado = "long"
        elif estado == "long" and evento == "saida":
            fechado_d = Decimal(str(aberto))
            quantidade = entrada["quantidade"]
            taxa_total = quantidade * (entrada["preco"] + fechado_d) * TAXA
            lucro = quantidade * (fechado_d - entrada["preco"]) - taxa_total
            carteira += lucro
            resultado_trades.append({
                "entrada_ts": entrada["ts"].isoformat(), "saida_ts": ts.isoformat(),
                "entrada_preco": str(entrada["preco"]), "saida_preco": str(fechado_d),
                "quantidade": str(quantidade), "taxa_total": str(taxa_total),
                "lucro_abs": str(lucro), "carteira_apos": str(carteira),
            })
            entrada = None
            estado = "flat"
    assert entrada is None, "simulacao terminou com posicao aberta — freqtrade tambem deve ter 0 left_open_trades"

    # Confronto trade a trade contra o export do freqtrade
    divergencias = []
    if len(resultado_trades) != len(trades_freqtrade):
        divergencias.append(f"contagem: sim={len(resultado_trades)} freqtrade={len(trades_freqtrade)}")
    for i, (sim, real) in enumerate(zip(resultado_trades, trades_freqtrade)):
        t_sim = datetime.fromisoformat(sim["entrada_ts"])
        t_real = datetime.fromtimestamp(real["open_timestamp"] / 1000, tz=timezone.utc)
        if t_sim != t_real:
            divergencias.append(f"trade {i}: fill entrada {t_sim} != {t_real}")
        if Decimal(sim["quantidade"]) != Decimal(str(real["amount"])):
            divergencias.append(f"trade {i}: quantidade {sim['quantidade']} != {real['amount']}")
        if Decimal(sim["entrada_preco"]) != Decimal(str(real["open_rate"])):
            divergencias.append(f"trade {i}: preco entrada {sim['entrada_preco']} != {real['open_rate']}")
        if Decimal(sim["saida_preco"]) != Decimal(str(real["close_rate"])):
            divergencias.append(f"trade {i}: preco saida {sim['saida_preco']} != {real['close_rate']}")
        dif = abs(Decimal(sim["lucro_abs"]) - Decimal(str(real["profit_abs"])))
        if dif > Decimal("0.000001"):
            divergencias.append(f"trade {i}: lucro diff {dif}")
    carteira_real = Decimal(str(round(sum(t["profit_abs"] for t in trades_freqtrade) + 1000.0, 8)))
    return {
        "trades_simulados": len(resultado_trades),
        "trades_freqtrade": len(trades_freqtrade),
        "carteira_final_sim": str(carteira),
        "carteira_final_export": str(carteira_real),
        "divergencias": divergencias,
        "veredito": "EQUIVALENTE" if not divergencias else "DIVERGENTE",
        "trades": resultado_trades,
    }


def parte_b(velas, eventos):
    """Motor do laboratorio DZ23 sobre os MESMOS eventos de entrada (modelo nativo)."""
    inicio_idx = next(i for i, v in enumerate(velas) if v[0] >= JANELA_INICIO)
    fatia = velas[max(0, inicio_idx - 40):]
    fatia = [v for v in fatia if v[0] < JANELA_FIM]
    barras = [
        Bar(timestamp=v[0], available_at=v[0], open=Decimal(str(v[1])), high=Decimal(str(v[2])),
            low=Decimal(str(v[3])), close=Decimal(str(v[4])), volume=Decimal(str(v[5])))
        for v in fatia
    ]
    fechamentos = [v[4] for v in fatia]
    rapida, lenta = smas(fatia)

    def sinal(historico):
        if len(historico) < SMA_LENTA + 1:
            return 0
        i = len(historico) - 1
        if rapida[i] is None or lenta[i] is None or rapida[i - 1] is None or lenta[i - 1] is None:
            return 0
        atuada = historico[i].timestamp + HORA
        if not (JANELA_INICIO <= atuada < JANELA_FIM):
            return 0
        if rapida[i] > lenta[i] and rapida[i - 1] <= lenta[i - 1]:
            return 1
        return 0

    config = BacktestConfig(
        initial_equity=EQUITY_INICIAL, fee_bps=Decimal("10"), slippage_bps=Decimal("0"),
        position_pct=Decimal("0.1"), max_participacao=Decimal("1"), seed=0,
    )
    resultado = run_backtest(
        barras, sinal, config,
        code_revision=revisao_do_codigo(),
        janela="2026-02-01..2026-07-01 UTC BTC/USDT 1h",
    )
    return {
        "code_revision": resultado.manifest["code_revision"],
        "modelo_execucao": resultado.manifest["execution_model"],
        "modelo_custos": resultado.manifest["cost_model"],
        "modelo_liquidez": resultado.manifest["liquidity_model"],
        "trades": resultado.trades,
        "carteira_final": str(resultado.equity_curve[-1]),
        "retorno_total": str(resultado.total_return),
        "max_drawdown": str(resultado.max_drawdown),
        "fills_limitados": resultado.fills_limitados,
        "fills_rejeitados_por_liquidez": resultado.fills_rejeitados_por_liquidez,
        "dataset_hash": resultado.dataset_hash,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--velas", required=True)
    parser.add_argument("--resultado", required=True)
    parser.add_argument("--zip", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    caminho_velas = Path(args.velas)
    velas = carregar_velas(caminho_velas)
    rapida, lenta = smas(velas)
    eventos = cruzar(velas, rapida, lenta)

    dados = json.loads(Path(args.resultado).read_text(encoding="utf-8"))
    estrategia = dados["strategy"]["Dz23SmaCross"]
    trades_freqtrade = estrategia["trades"]

    # Item 1 do protocolo: serie de decisoes barra a barra (128 cruzamentos no arquivo inteiro).
    entradas_eventos = sorted(ts for ts, tipo in eventos.items() if tipo == "entrada")
    saidas_eventos = sorted(ts for ts, tipo in eventos.items() if tipo == "saida")
    entradas_freq = sorted(datetime.fromtimestamp(t["open_timestamp"] / 1000, tz=timezone.utc) for t in trades_freqtrade)
    saidas_freq = sorted(datetime.fromtimestamp(t["close_timestamp"] / 1000, tz=timezone.utc) for t in trades_freqtrade)
    dentro = [ts for ts in entradas_eventos if JANELA_INICIO <= ts < JANELA_FIM]

    resultado_a = parte_a(velas, eventos, trades_freqtrade)
    resultado_b = parte_b(velas, eventos)

    relatorio = {
        "rodada": "FQ-2 rodada 1 — BTC/USDT 1h — SMA 10/30 — 2026-02-01..2026-07-01 UTC",
        "velas_arquivo": caminho_velas.name,
        "velas_sha256": sha256(caminho_velas.read_bytes()).hexdigest(),
        "velas_total": len(velas),
        "zip_export": Path(args.zip).name,
        "zip_sha256": sha256(Path(args.zip).read_bytes()).hexdigest(),
        "sinais": {
            "cruzamentos_entrada_arquivo": len(entradas_eventos),
            "cruzamentos_saida_arquivo": len(saidas_eventos),
            "entradas_de_sinal_na_janela": len(dentro),
            "entradas_de_fill_no_export": len(entradas_freq),
            "entrada_ts_iguais_ao_export": entradas_freq == [ts for ts in dentro],
            "saidas_de_fill_no_export": len(saidas_freq),
        },
        "parte_a_motor_contabil": resultado_a,
        "parte_b_motor_laboratorio_dz23": resultado_b,
        "freqtrade_resumo": {
            "total_trades": estrategia["total_trades"],
            "carteira_final": estrategia["final_balance"],
            "lucro_total_abs": estrategia["profit_total_abs"],
            "max_drawdown_conta": estrategia["max_drawdown_account"],
            "winrate": estrategia["winrate"],
            "left_open_trades": estrategia.get("left_open_trades", 0),
        },
        "aviso": (
            "Instrumento de auditoria da rodada FQ-2; nao aprova estrategia, nao promove a LIVE, "
            "nao e previsao. Freqtrade = ferramenta isolada de validacao (GPL, processo separado)."
        ),
    }
    Path(args.out).write_text(json.dumps(relatorio, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"velas: {len(velas)} | cruzamentos no arquivo: {len(entradas_eventos)} entrada / {len(saidas_eventos)} saida")
    print(f"entradas na janela (candidatas): {len(dentro)} | fills no export freqtrade: {len(entradas_freq)}")
    print(f"PARTE A: {resultado_a['veredito']} | trades sim={resultado_a['trades_simulados']} "
          f"freqtrade={resultado_a['trades_freqtrade']} | carteira sim={resultado_a['carteira_final_sim']} "
          f"export={resultado_a['carteira_final_export']}")
    for d in resultado_a["divergencias"][:10]:
        print(f"  divergencia: {d}")
    print(f"PARTE B (motor laboratorio DZ23): trades={resultado_b['trades']} "
          f"retorno={resultado_b['retorno_total']} drawdown={resultado_b['max_drawdown']} "
          f"carteira={resultado_b['carteira_final']} rejeitados={resultado_b['fills_rejeitados_por_liquidez']}")
    print(f"dataset_hash DZ23: {resultado_b['dataset_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Sala individual do trader (V8 TD-2, task #91) — séries e métricas
VERIFICÁVEIS, montadas para o dashboard de UM trader.

Regras do dono codificadas aqui:
- "Os gráficos financeiros devem derivar de ledger e eventos verificáveis,
  não de números simulados indevidamente apresentados como reais."
- "Não misturar resultados PAPER, TESTNET e LIVE."
- Fontes sem dado viram `None` com motivo declarado — nunca zero decorativo.
- Nada aqui envia ordem, fala com rede ou lê relógio: as entradas
  (velas, diário, lançamentos, ciclos) chegam prontas da rota.

Este módulo é PURO de propósito: a rota coleta, o módulo calcula, o painel
desenha. Cada camada pode ser testada sozinha.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .market import TIMEFRAME_SECONDS

# ------------------------------------------------------------------ utils


def _f(valor: Any) -> float | None:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _d(valor: Any) -> Decimal:
    try:
        return Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _str_d(d: Decimal) -> str:
    texto = format(d.normalize(), "f")
    return texto if texto != "-0" else "0"


# ------------------------------------------------------------- indicadores


def media_movel(closes: Sequence, janela: int) -> list[float | None]:
    """Média móvel simples sobre fechamentos. `None` até haver janela —
    indicador incompleto não vira número."""
    janela = max(1, int(janela))
    saida: list[float | None] = []
    for i in range(len(closes)):
        if i + 1 < janela:
            saida.append(None)
            continue
        fatia = [_f(c) for c in closes[i + 1 - janela: i + 1]]
        saida.append(round(sum(fatia) / janela, 8)
                     if all(v is not None for v in fatia) else None)
    return saida


def rsi(closes: Sequence, periodo: int = 14) -> list[float | None]:
    """RSI (Wilder) sobre fechamentos. Primeiro valor no índice `periodo`."""
    periodo = max(2, int(periodo))
    valores = [_f(c) for c in closes]
    saida: list[float | None] = [None] * len(valores)
    if len(valores) <= periodo:
        return saida
    ganhos, perdas = 0.0, 0.0
    for i in range(1, periodo + 1):
        delta = valores[i] - valores[i - 1]
        ganhos += max(0.0, delta)
        perdas += max(0.0, -delta)
    avg_g, avg_p = ganhos / periodo, perdas / periodo
    saida[periodo] = 100.0 if avg_p == 0 else round(
        100 - 100 / (1 + avg_g / avg_p), 8)
    for i in range(periodo + 1, len(valores)):
        delta = valores[i] - valores[i - 1]
        avg_g = (avg_g * (periodo - 1) + max(0.0, delta)) / periodo
        avg_p = (avg_p * (periodo - 1) + max(0.0, -delta)) / periodo
        saida[i] = 100.0 if avg_p == 0 else round(
            100 - 100 / (1 + avg_g / avg_p), 8)
    return saida


def indicadores(closes: Sequence, *, rapida: int = 9, lenta: int = 21,
                rsi_periodo: int = 14) -> dict:
    return {"sma_rapida": media_movel(closes, rapida),
            "sma_lenta": media_movel(closes, lenta),
            "rsi": rsi(closes, rsi_periodo)}


def suportes_e_resistencias(closes: Sequence, *, janela: int = 2,
                            maximo: int = 3) -> dict:
    """Pivôs sobre FECHAMENTOS (não há OHLC no fixture). São níveis
    aproximados calculados do que existe — não linhas de mercado."""
    valores = [_f(c) for c in closes]
    janela = max(1, int(janela))
    altas: list[float] = []
    baixas: list[float] = []
    for i in range(janela, len(valores) - janela):
        v = valores[i]
        if v is None:
            continue
        redor = valores[i - janela: i] + valores[i + 1: i + 1 + janela]
        if any(x is None for x in redor):
            continue
        if all(v > x for x in redor):
            altas.append(round(v, 8))
        elif all(v < x for x in redor):
            baixas.append(round(v, 8))
    return {"suportes": baixas[-maximo:], "resistencias": altas[-maximo:],
            "nota": ("pivôs calculados sobre fechamentos (janela ±"
                     f"{janela}); níveis aproximados — não são linhas de "
                     "mercado")}


# --------------------------------------------------------------- portfólio


def curva_de_resultado(ops: Sequence[dict]) -> list[dict]:
    """Curva a partir do DIÁRIO de operações fechadas de UM trader.

    Soma simples de retornos (fração por operação) — não composta e não é
    saldo de conta: o saldo vive no livro-razão, aqui é o acumulado de
    desfechos realizados."""
    ordenadas = sorted(ops or [], key=lambda o: str(o.get("quando", "")))
    acumulado = 0.0
    saida = []
    for op in ordenadas:
        retorno = _f(op.get("retorno_liquido"))
        if retorno is None:
            continue
        acumulado += retorno
        saida.append({"t": str(op.get("quando", "")),
                      "retorno": round(retorno, 8),
                      "acumulado": round(acumulado, 8),
                      "instrumento": str(op.get("instrumento", "")),
                      "causa": str(op.get("causa", ""))})
    return saida


def drawdown_maximo(acumulados: Sequence[float]) -> float:
    pico, pior = 0.0, 0.0
    for valor in acumulados or []:
        pico = max(pico, float(valor))
        pior = max(pior, pico - float(valor))
    return round(pior, 8)


def metricas_portfolio(ops: Sequence[dict]) -> dict:
    """Hit rate, expectativa, profit factor, custos e drawdown do DIÁRIO.

    Vazio ou sem perdas vira `None` com motivo — número que existe só
    porque o denominador é zero é número mentiroso."""
    curva = curva_de_resultado(ops)
    retornos = [c["retorno"] for c in curva]
    n = len(retornos)
    met = {"operacoes": n, "acertos": sum(1 for r in retornos if r > 0),
           "taxa_acerto": None, "expectativa": None, "profit_factor": None,
           "motivo_profit_factor": "",
           "custos_total": _str_d(sum((_d(o.get("custo")) for o in (ops or [])),
                                      Decimal("0"))),
           "soma_retornos": round(sum(retornos), 8),
           "drawdown": drawdown_maximo([c["acumulado"] for c in curva]),
           "curva": curva}
    if not n:
        met["motivo_profit_factor"] = (
            "sem operações fechadas — nada a medir (posição aberta é "
            "expectativa, não resultado)")
        return met
    met["taxa_acerto"] = met["acertos"] / n
    met["expectativa"] = sum(retornos) / n
    ganhos = sum(r for r in retornos if r > 0)
    perdas = -sum(r for r in retornos if r < 0)
    if perdas > 0:
        met["profit_factor"] = ganhos / perdas
    else:
        met["motivo_profit_factor"] = (
            "sem perdas no período — fator de lucro indefinido (não é "
            "'infinito': dividir por zero não mede nada)")
    return met


def exposicao_do_trader(lotes: Sequence[dict], agent_id: str) -> dict:
    """Posições vivas DESTE trader (lotes do livro, por agente)."""
    meus = [lote for lote in (lotes or [])
            if str(lote.get("agente", "")) == agent_id]
    posicoes = [{"instrumento": str(lote.get("instrumento", "")),
                 "quantidade": str(lote.get("quantidade", "0")),
                 "custo": str(lote.get("custo_restante", "0"))}
                for lote in meus]
    total = sum((_d(p["custo"]) for p in posicoes), Decimal("0"))
    return {"posicoes": posicoes, "custo_total": _str_d(total)}


def ordens_do_trader(ordens: Sequence[dict], agent_id: str) -> list[dict]:
    """Ordens NÃO terminadas deste trader — o que ainda pode mudar o
    resultado; ordem fechada já está no livro."""
    return [dict(o) for o in (ordens or [])
            if str(o.get("agente", "")) == agent_id and o.get("aberta")]


# ---------------------------------------------------------------- marcações


def marcacoes_de_execucao(transacoes: Sequence[dict], agent_id: str) -> list[dict]:
    """Execuções REAIS deste trader, direto dos lançamentos do livro.

    Sem preço nos lançamentos quando a aritmética não fecha: `preco=None`
    com o dado cru preservado — melhor declarar que derivar errado."""
    saida = []
    for tx in transacoes or []:
        kind = str(tx.get("kind", ""))
        if kind not in ("fill_buy", "fill_sell"):
            continue
        if str(tx.get("agent_id", "")) != agent_id:
            continue
        qtd = _d(tx.get("quantity"))
        entradas = tx.get("entries") or []
        preco = None
        if qtd > 0:
            if kind == "fill_buy":
                custo = sum((_d(e.get("amount")) for e in entradas
                             if str(e.get("account", "")).startswith("POSITION:")),
                            Decimal("0"))
                if custo > 0:
                    preco = round(float(custo / qtd), 8)
            else:
                bruto = sum((_d(e.get("amount")) for e in entradas
                             if str(e.get("account", "")).startswith(
                                 ("CASH:", "FEES:"))), Decimal("0"))
                if bruto > 0:
                    preco = round(float(bruto / qtd), 8)
        saida.append({"t": str(tx.get("at", "")),
                      "tipo": "compra" if kind == "fill_buy" else "venda",
                      "instrumento": str(tx.get("instrument", "")),
                      "quantidade": _str_d(qtd), "preco": preco})
    return sorted(saida, key=lambda x: x["t"])


def marcacoes_dos_ciclos(ciclos: Sequence[dict], agent_id: str) -> list[dict]:
    """Sinais NÃO executados deste trader: abstenções e vetos vêm dos
    ciclos da mesa (eventos verificáveis, com horário do ciclo)."""
    saida = []
    for ciclo in ciclos or []:
        quando = str(ciclo.get("em", ""))
        for linha in ciclo.get("abstencoes") or []:
            prefixo = f"{agent_id} · "
            if not str(linha).startswith(prefixo):
                continue
            resto = str(linha)[len(prefixo):]
            instrumento, _, motivo = resto.partition(": ")
            saida.append({"t": quando, "tipo": "abstencao",
                          "instrumento": instrumento.strip(),
                          "motivo": motivo.strip() or resto.strip()})
        for linha in ciclo.get("vetos") or []:
            prefixo = f"{agent_id}: "
            if not str(linha).startswith(prefixo):
                continue
            saida.append({"t": quando, "tipo": "veto", "instrumento": "",
                          "motivo": str(linha)[len(prefixo):].strip()})
    return sorted(saida, key=lambda x: x["t"])


def velas_de_rows(rows: Sequence[Sequence], timeframe: str, *,
                  agora: datetime) -> list[dict]:
    """Converte velas cruas (ms, o, h, l, c, v) em série para o gráfico,
    DESCARTANDO a vela em formação — a mesma regra do snapshot da mesa:
    usar vela aberta é olhar o futuro."""
    passo = TIMEFRAME_SECONDS.get(timeframe, 3600)
    saida = []
    for row in rows or []:
        try:
            ts_ms, o, h, low, c, v = row[:6]
        except (TypeError, ValueError):
            continue
        fechada_em = datetime.fromtimestamp(ts_ms / 1000, timezone.utc) \
            + timedelta(seconds=passo)
        if fechada_em > agora:
            continue
        saida.append({"t": fechada_em.isoformat(), "o": str(o), "h": str(h),
                      "l": str(low), "c": str(c), "v": str(v)})
    return saida


# -------------------------------------------------- experimentos e retrato


def experimentos_do_trader(registros: dict, resultados: dict,
                           trader: str) -> list[dict]:
    """Experimentos pré-registrados deste trader, com o veredito quando
    existir — sem veredito é `None`, nunca 'aprovado' por omissão."""
    saida = []
    for assinatura, registro in sorted((registros or {}).items()):
        if str(registro.get("trader", "")) != trader:
            continue
        resultado = (resultados or {}).get(assinatura)
        saida.append({"id": registro.get("id", ""),
                      "titulo": registro.get("titulo", ""),
                      "criado_em": registro.get("criado_em", ""),
                      "assinatura": assinatura,
                      "champion": registro.get("champion") or {},
                      "challenger": registro.get("challenger") or {},
                      "resultado": resultado})
    return saida


AVISOS_DE_HONESTIDADE = (
    "Os gráficos derivam das operações FECHADAS (diário), dos lançamentos "
    "do livro-razão e dos ciclos da mesa — nunca de números inventados.",
    "Posição aberta é expectativa, não resultado: ela não entra na curva.",
    "Resultado do livro é BRUTO; as taxas vivem em conta separada (FEES).",
)


def retrato_do_trader(agent_id: str, *, trader: dict, instrumento: str,
                      timeframe: str, velas: Sequence[dict] | None,
                      fechamentos: Sequence | None, fonte: str,
                      aviso_fonte: str, qualidade: str,
                      diario: Sequence[dict], transacoes: Sequence[dict],
                      lotes: Sequence[dict], ordens: Sequence[dict],
                      ciclos: Sequence[dict], experimentos: Sequence[dict],
                      atribuicao: dict, moeda: str, modo: str) -> dict:
    """Montagem da sala de UM trader. `velas` (OHLCV real) vira gráfico de
    candles; sem OHLCV (fixture), o gráfico é a linha dos FECHAMENTOS
    rotulados — jamais candles inventados."""
    tem_velas = bool(velas)
    closes = ([v.get("c") for v in velas] if tem_velas else
              list(fechamentos or []))
    sr = suportes_e_resistencias(closes)
    marcacoes = sorted(
        marcacoes_de_execucao(transacoes, agent_id)
        + marcacoes_dos_ciclos(ciclos, agent_id), key=lambda x: x["t"])
    mercado = {
        "tipo": "candles" if tem_velas else "linha",
        "instrumento": instrumento, "timeframe": timeframe,
        "fonte": fonte, "aviso": aviso_fonte, "qualidade": qualidade,
        "velas": list(velas or []) if tem_velas else [],
        "fechamentos": [] if tem_velas else [str(c) for c in closes],
        "indicadores": indicadores(closes),
        "suportes": sr["suportes"], "resistencias": sr["resistencias"],
        "nota_sr": sr["nota"],
        "marcacoes": marcacoes,
        "nota_marcacoes": ("execuções reais vêm do livro-razão; abstenções e "
                           "vetos vêm dos ciclos — o motivo aparece ao "
                           "passar o mouse"),
    }
    met = metricas_portfolio(diario)
    bruto = _d((atribuicao or {}).get(agent_id, "0"))
    liquid = bruto - _d(met["custos_total"])
    portfolio = {
        **met,
        "resultado_bruto_livro": _str_d(bruto),
        "resultado_liquido_estimado": _str_d(liquid),
        "nota_resultado": ("bruto do livro − custos do diário; taxas "
                           "individuais da corretora vivem em FEES"),
        "exposicao": exposicao_do_trader(lotes, agent_id),
        "ordens_abertas": ordens_do_trader(ordens, agent_id),
    }
    return {"trader": trader, "modo": modo, "moeda": moeda,
            "mercado": mercado, "portfolio": portfolio,
            "champion_challenger": [dict(e) for e in (experimentos or [])],
            "avisos": [
                f"Modo {str(modo).upper()} — resultados de execução simulada "
                "quando PAPER; não é promessa de lucro nem previsão exata "
                "de preços.",
                *AVISOS_DE_HONESTIDADE]}


__all__ = ["AVISOS_DE_HONESTIDADE", "curva_de_resultado", "drawdown_maximo",
           "experimentos_do_trader", "exposicao_do_trader", "indicadores",
           "marcacoes_de_execucao", "marcacoes_dos_ciclos", "media_movel",
           "metricas_portfolio", "ordens_do_trader", "retrato_do_trader",
           "rsi", "suportes_e_resistencias", "velas_de_rows"]

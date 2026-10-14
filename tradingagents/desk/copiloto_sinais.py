"""Sinais determinísticos do Copiloto Manual (V8 TD-4, task #93).

Regras do dono codificadas aqui:
- "O Copiloto Manual jamais poderá criar ou cancelar ordens na corretora."
  Este módulo não tem — por desenho — nenhum caminho de ordem, rede ou
  corretora: ele lê séries que a rota entrega prontas e devolve uma leitura.
- "Não prometer previsão exata de preços nem lucros": os avisos fixos
  ficam impressos na própria resposta; oportunidade é descrita como viés
  condicional, nunca como certeza.
- Fonte sem dado vira `None` com motivo declarado — nunca zero decorativo.

Módulo PURO: sem relógio, sem I/O, sem IA. A rota coleta a série (com o
rótulo da fonte); o módulo classifica; o painel desenha; quem executa a
operação é o dono, na corretora dele.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .sala_trader import _f, indicadores, suportes_e_resistencias

_HORIZONTES = {
    "1m": "1m — horizonte de minutos: cada vela vale 1 minuto; leitura rápida e ruidosa",
    "5m": "5m — horizonte intradiário curto: cada vela vale 5 minutos",
    "15m": "15m — horizonte intradiário curto: cada vela vale 15 minutos",
    "30m": "30m — horizonte intradiário: cada vela vale meia hora",
    "1h": "1h — horizonte intradiário: cada vela vale 1 hora; a leitura vale para o dia",
    "4h": "4h — horizonte de dias: cada vela vale 4 horas",
    "1d": "1d — horizonte de semanas: cada vela vale 1 dia",
}

_AVISOS = (
    "análise determinística de séries verificáveis — não é promessa de "
    "lucro nem previsão exata de preços",
    "o Copiloto não envia ordens, não cria e não cancela ordens — quem "
    "executa é você, na sua corretora",
    "cotação de tela pode estar defasada — o preço que vale é o "
    "efetivamente executado por você",
)

_NOTA_CUSTOS = ("taxas, spread e liquidez dependem da sua corretora e do "
                "ativo — o copiloto NUNCA estima custos por você; registre "
                "as taxas reais junto da execução para o resultado ser fiel")


def horizonte_do_timeframe(timeframe: str) -> str:
    return _HORIZONTES.get(
        timeframe,
        f"{timeframe} — horizonte não catalogado; leia a escala antes de decidir")


def _numero(valor: float) -> str:
    return f"{valor:g}"


def _tendencia(ind: dict, n: int) -> dict:
    sma_r = ind["sma_rapida"][-1] if ind["sma_rapida"] else None
    sma_l = ind["sma_lenta"][-1] if ind["sma_lenta"] else None
    if sma_r is None or sma_l is None:
        return {"estado": None, "sma_rapida": None, "sma_lenta": None,
                "motivo": (f"sem fechamentos suficientes ({n} de 21) — nada "
                           "a medir; indicador incompleto não vira número")}
    corpo = {"sma_rapida": sma_r, "sma_lenta": sma_l, "motivo": None}
    if sma_l and sma_r > sma_l * 1.001:
        return {"estado": "alta", **corpo}
    if sma_l and sma_r < sma_l * 0.999:
        return {"estado": "baixa", **corpo}
    return {"estado": "lateral", **corpo}


def _rsi(ind: dict, n: int) -> dict:
    valor = ind["rsi"][-1] if ind["rsi"] else None
    if valor is None:
        return {"valor": None, "estado": None,
                "motivo": f"RSI precisa de 15 fechamentos ({n} disponíveis)"}
    if valor >= 70:
        estado = "sobrecomprado"
    elif valor <= 30:
        estado = "sobrevendido"
    else:
        estado = "neutro"
    return {"valor": valor, "estado": estado, "motivo": None}


def _oportunidade(tendencia: dict, rs: dict) -> dict:
    estado, v = tendencia["estado"], rs["valor"]
    if estado == "alta":
        if rs["estado"] == "sobrecomprado":
            return {"vies": None, "texto": None,
                    "motivo": (f"tendência de alta, mas RSI sobrecomprado "
                               f"({v}) — comprar preço esticado não é "
                               "oportunidade; aguarde alívio")}
        return {"vies": "compra",
                "texto": (f"tendência de alta (SMA 9 acima da SMA 21) com "
                          f"RSI {rs['estado']} ({v}) — viés comprador na leitura"),
                "motivo": None}
    if estado == "baixa":
        if rs["estado"] == "sobrevendido":
            return {"vies": None, "texto": None,
                    "motivo": (f"tendência de baixa, mas RSI sobrevendido "
                               f"({v}) — vender preço esgotado não é "
                               "oportunidade; aguarde repique")}
        return {"vies": "venda",
                "texto": (f"tendência de baixa (SMA 9 abaixo da SMA 21) com "
                          f"RSI {rs['estado']} ({v}) — viés vendedor na leitura"),
                "motivo": None}
    if estado == "lateral":
        return {"vies": None, "texto": None,
                "motivo": "médias sobrepostas — sem direção clara; sinais mistos"}
    return {"vies": None, "texto": None, "motivo": tendencia["motivo"]}


def _invalidacao(vies: str | None, niveis: dict,
                 preco: float | None) -> dict:
    if vies == "compra":
        candidatos = [s for s in niveis["suportes"]
                      if preco is not None and s < preco]
        nivel = max(candidatos) if candidatos else (
            niveis["suportes"][-1] if niveis["suportes"] else None)
        if nivel is None:
            return {"texto": None,
                    "motivo": ("sem pivôs de suporte na amostra — declare a "
                               "sua própria invalidação")}
        return {"texto": (f"o viés comprador invalida se perder o suporte "
                          f"{_numero(nivel)} (pivô sobre fechamentos)"),
                "motivo": None}
    if vies == "venda":
        candidatos = [r for r in niveis["resistencias"]
                      if preco is not None and r > preco]
        nivel = min(candidatos) if candidatos else (
            niveis["resistencias"][-1] if niveis["resistencias"] else None)
        if nivel is None:
            return {"texto": None,
                    "motivo": ("sem pivôs de resistência na amostra — declare "
                               "a sua própria invalidação")}
        return {"texto": (f"o viés vendedor invalida se recuperar a "
                          f"resistência {_numero(nivel)} (pivô sobre "
                          "fechamentos)"),
                "motivo": None}
    return {"texto": None,
            "motivo": ("sem direção definida pela leitura — a invalidação "
                       "fica com a sua tese")}


def _riscos(fechamentos: Sequence, fonte: str, rs: dict) -> list[str]:
    riscos: list[str] = []
    if fonte and fonte != "real":
        riscos.append(f"fonte rotulada '{fonte}': os números provam a "
                      "fiação, não o mercado vivo")
    valores = [v for v in (_f(c) for c in fechamentos) if v is not None]
    if len(valores) >= 2:
        media = sum(valores) / len(valores)
        if media:
            amplitude = (max(valores) - min(valores)) / media * 100
            riscos.append(f"volatilidade da amostra: {round(amplitude, 2)}% "
                          f"entre o mínimo e o máximo das últimas "
                          f"{len(valores)} velas")
    if rs["estado"] in ("sobrecomprado", "sobrevendido"):
        riscos.append(f"RSI {rs['estado']} ({rs['valor']}) — risco de "
                      "movimento contrário")
    riscos.append("risco por operação ainda não definido — declare stop e "
                  "invalidação no registro")
    return riscos


def analisar_mercado(*, instrumento: str, timeframe: str,
                     fechamentos: Sequence, fonte: str = "",
                     qualidade: str = "", aviso_fonte: str = "",
                     preco_atual: Any = None) -> dict:
    """Leitura determinística do mercado para o Copiloto Manual.

    Devolve sempre a mesma estrutura; o que não é verificável vira `None`
    com motivo — nunca número decorativo. Nenhum caminho de ordem."""
    closes = list(fechamentos or [])
    n = len(closes)
    ind = indicadores(closes)
    tendencia = _tendencia(ind, n)
    rs = _rsi(ind, n)
    niveis = (suportes_e_resistencias(closes) if closes
              else {"suportes": [], "resistencias": [],
                    "nota": ("sem fechamentos — nada a calcular")})
    op = _oportunidade(tendencia, rs)
    return {
        "instrumento": str(instrumento),
        "timeframe": str(timeframe),
        "horizonte": horizonte_do_timeframe(str(timeframe)),
        "fonte": str(fonte), "qualidade": str(qualidade),
        "aviso_fonte": str(aviso_fonte),
        "amostra": n,
        "preco_atual": _f(preco_atual),
        "tendencia": tendencia,
        "rsi": rs,
        "niveis": niveis,
        "oportunidade": op,
        "riscos": _riscos(closes, str(fonte), rs),
        "custos": {"nota": _NOTA_CUSTOS},
        "invalidacao": _invalidacao(op["vies"], niveis, _f(preco_atual)),
        "avisos": list(_AVISOS),
    }

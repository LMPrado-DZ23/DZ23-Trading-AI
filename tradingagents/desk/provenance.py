"""Procedência das decisões da mesa (correção do achado 3 da auditoria).

O gate de capital do Gate A exige que toda ordem carregue prova de onde a
decisão veio: identidade do run, instrumento, data e transporte seguro. A
mesa passava `{}` — e o gate, corretamente, recusava tudo. O caminho de
produção não chegava à corretora, enquanto os testes passavam porque
injetavam um estado artificial que satisfazia o gate.

A correção NÃO é relaxar o gate nem fabricar um estado bonito. É a mesa
construir a procedência REAL da decisão: qual instrumento, qual vela de
corte, qual fonte, qual estratégia, e se o transporte foi seguro.

Se algum desses elementos faltar, aqui também se recusa — antes de chegar
ao gate. Estado inventado é pior que estado ausente.
"""
from __future__ import annotations

from tradingagents.gates import MARK_KEY, MARK_SCHEMA


class ProcedenciaInvalida(RuntimeError):
    """A decisão não pode provar sua origem — não vira ordem."""


def transporte_seguro(fonte: str) -> bool:
    """Fonte de dados falou por canal seguro?

    Fixture é local (não há transporte). Feed real só conta como seguro
    quando a URL é https — a ponte CCXT usa endpoints https por padrão, e
    qualquer outro esquema é recusado aqui.
    """
    fonte = (fonte or "").strip().lower()
    if fonte in ("fixture", "local", "memoria"):
        return True
    return fonte.startswith("https") or fonte in ("ccxt", "real", "public")


def build_state(*, instrument: str, cutoff, source: str, strategy: str,
                agent_id: str, worker_id: str = "") -> dict:
    """Monta o estado com a marca de transporte para UMA decisão.

    `cutoff` é o fechamento da vela que embasou a decisão — é ele que amarra
    a ordem ao dado, e não a hora do envio.
    """
    if not instrument:
        raise ProcedenciaInvalida("decisão sem instrumento não prova origem")
    if cutoff is None:
        raise ProcedenciaInvalida(
            "decisão sem vela de corte: não é possível ligar a ordem ao dado")
    if not strategy or not agent_id:
        raise ProcedenciaInvalida("decisão sem estratégia/agente identificados")
    if not transporte_seguro(source):
        raise ProcedenciaInvalida(
            f"fonte {source!r} não comprova transporte seguro — recusado antes "
            "de qualquer envio")

    trade_date = cutoff.date().isoformat() if hasattr(cutoff, "date") else str(cutoff)
    return {
        "company_of_interest": instrument,
        "trade_date": trade_date,
        MARK_KEY: {
            "schema": MARK_SCHEMA,
            "insecure": False,
            "insecure_targets": [],
            "run": {"ticker": instrument, "trade_date": trade_date},
            # contexto auditável — não é exigido pelo gate, mas é o que
            # permite investigar uma ordem depois
            "origem": {"fonte": source, "estrategia": strategy,
                       "agente": agent_id, "worker": worker_id,
                       "cutoff": cutoff.isoformat() if hasattr(cutoff, "isoformat")
                       else str(cutoff)},
        },
    }


def validar(state: dict) -> dict:
    """Passa o estado pelo MESMO gate que a corretora aplicará.

    Falhar aqui é melhor que falhar na fronteira da rede: o erro aparece na
    mesa, com contexto, em vez de virar uma exceção obscura no envio.
    """
    from tradingagents.gates import assert_secure_for_capital
    assert_secure_for_capital(state)
    return state


__all__ = ["build_state", "validar", "transporte_seguro", "ProcedenciaInvalida"]

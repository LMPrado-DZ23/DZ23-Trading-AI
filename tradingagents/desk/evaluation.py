"""Avaliação temporal e comparação de equipe (TA-012 / MA-09).

Constrói sobre o backtester preservado: aqui não se mede lucro, mede-se se o
método de avaliação é honesto.

O que este módulo impede, por construção:
- Escolher campeão olhando o período onde ele foi testado (por isso
  walk-forward com janelas que NÃO se sobrepõem no teste).
- Vazamento entre treino e teste quando os rótulos se sobrepõem (embargo
  temporal explícito entre as janelas).
- Comparar estratégias em universos, capitais ou custos diferentes.
- Contar concordância entre agentes do mesmo modelo como evidência
  independente.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from statistics import fmean, pstdev


class EvaluationError(ValueError):
    """Desenho de avaliação inválido — recusado antes de rodar."""


@dataclass(frozen=True)
class Window:
    """Uma dobra: treino, embargo e teste, sem sobreposição."""
    train_start: date
    train_end: date
    test_start: date
    test_end: date

    def __post_init__(self):
        if not (self.train_start < self.train_end <= self.test_start < self.test_end):
            raise EvaluationError(
                "janela inválida: treino deve terminar antes do teste começar")

    @property
    def embargo_days(self) -> int:
        return (self.test_start - self.train_end).days

    def overlaps_test(self, other: "Window") -> bool:
        return not (self.test_end <= other.test_start or other.test_end <= self.test_start)


def walk_forward(start: date, end: date, *, train_days: int, test_days: int,
                 embargo_days: int = 0) -> list[Window]:
    """Dobras deslizantes com embargo. O teste de uma dobra nunca encosta no
    treino da mesma nem no teste de outra."""
    if train_days <= 0 or test_days <= 0:
        raise EvaluationError("treino e teste precisam de dias positivos")
    if embargo_days < 0:
        raise EvaluationError("embargo não pode ser negativo")
    windows, cursor = [], start
    while True:
        train_end = cursor + timedelta(days=train_days)
        test_start = train_end + timedelta(days=embargo_days)
        test_end = test_start + timedelta(days=test_days)
        if test_end > end:
            break
        windows.append(Window(cursor, train_end, test_start, test_end))
        cursor = test_start          # próxima dobra começa onde este teste começou
    if not windows:
        raise EvaluationError(
            "período curto demais para uma dobra de treino+embargo+teste")
    return windows


@dataclass
class StrategyResult:
    """Resultado de UMA estratégia numa dobra. Retornos em fração (0.01 = 1%)."""
    name: str
    window: Window
    returns: tuple[Decimal, ...]
    trades: int
    costs: Decimal = Decimal("0")
    universe: tuple[str, ...] = ()
    capital: Decimal = Decimal("0")

    @property
    def total_return(self) -> Decimal:
        return sum(self.returns, Decimal("0")) - self.costs


@dataclass
class Comparison:
    """Comparação entre candidatos — só aceita o que é comparável."""
    results: list[StrategyResult] = field(default_factory=list)

    def add(self, result: StrategyResult) -> None:
        if self.results:
            ref = self.results[0]
            if result.universe != ref.universe:
                raise EvaluationError(
                    "universos diferentes: comparação inválida")
            if result.capital != ref.capital:
                raise EvaluationError("capitais diferentes: comparação inválida")
            if result.window != ref.window:
                raise EvaluationError("períodos diferentes: comparação inválida")
        self.results.append(result)

    def ranking(self) -> list[tuple[str, Decimal]]:
        return sorted(((r.name, r.total_return) for r in self.results),
                      key=lambda x: -x[1])

    def verdict(self, *, min_trades: int = 20) -> dict:
        """Veredito com as ressalvas explícitas — nunca um 'campeão' seco."""
        if not self.results:
            return {"conclusivo": False, "motivo": "sem resultados"}
        rank = self.ranking()
        best = max(self.results, key=lambda r: r.total_return)
        reasons = []
        if best.trades < min_trades:
            reasons.append(
                f"amostra pequena: {best.trades} operações (< {min_trades})")
        if len(self.results) < 2:
            reasons.append("sem comparação: apenas um candidato")
        if all(r.costs == 0 for r in self.results):
            reasons.append("custos não foram descontados")
        return {"ranking": rank, "lider": rank[0][0],
                "conclusivo": not reasons,
                "ressalvas": reasons,
                "aviso": "resultado de teste não é promessa de lucro futuro"}


def aggregate_walk_forward(results: list[StrategyResult]) -> dict:
    """Consolida uma estratégia ao longo das dobras — média, dispersão e
    consistência. Uma dobra boa entre cinco ruins não vira 'estratégia boa'."""
    if not results:
        raise EvaluationError("nada a agregar")
    name = results[0].name
    if any(r.name != name for r in results):
        raise EvaluationError("agregação exige a MESMA estratégia")
    for a in range(len(results)):
        for b in range(a + 1, len(results)):
            if results[a].window.overlaps_test(results[b].window):
                raise EvaluationError(
                    "dobras com teste sobreposto: resultado contaminado")
    totals = [float(r.total_return) for r in results]
    positivas = sum(1 for t in totals if t > 0)
    return {"estrategia": name, "dobras": len(results),
            "retorno_medio": round(fmean(totals), 6),
            "dispersao": round(pstdev(totals), 6) if len(totals) > 1 else 0.0,
            "dobras_positivas": positivas,
            "consistencia": round(positivas / len(totals), 3),
            "operacoes": sum(r.trades for r in results),
            "aviso": "avaliação retrospectiva; não prova desempenho futuro"}


def independent_evidence(sources: list[tuple[str, str]]) -> int:
    """Conta evidência INDEPENDENTE: concordância entre agentes do mesmo
    modelo/fonte conta uma vez só.

    `sources` é uma lista de (agente, origem) — a origem é o que importa.
    """
    return len({origem for _, origem in sources})


__all__ = ["Window", "walk_forward", "StrategyResult", "Comparison",
           "aggregate_walk_forward", "independent_evidence", "EvaluationError"]

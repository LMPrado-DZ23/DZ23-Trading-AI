"""TA-012 / MA-09 / cenário 15 — a avaliação precisa ser honesta antes de
ser favorável."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tradingagents.desk.evaluation import (
    Comparison,
    EvaluationError,
    StrategyResult,
    Window,
    aggregate_walk_forward,
    independent_evidence,
    walk_forward,
)

UNIV = ("BTC/USDT", "ETH/USDT")
CAP = Decimal("1000")


def wins(n=6):
    return walk_forward(date(2026, 1, 1), date(2026, 12, 31),
                        train_days=60, test_days=30, embargo_days=n)


class TestWalkForward:
    def test_folds_do_not_leak_train_into_test(self):
        for w in wins():
            assert w.train_end <= w.test_start
            assert w.embargo_days == 6

    def test_test_windows_never_overlap(self):
        ws = wins()
        for i in range(len(ws)):
            for j in range(i + 1, len(ws)):
                assert not ws[i].overlaps_test(ws[j])

    def test_short_period_is_refused_instead_of_faking_a_fold(self):
        with pytest.raises(EvaluationError, match="curto"):
            walk_forward(date(2026, 1, 1), date(2026, 2, 1),
                         train_days=60, test_days=30)

    def test_invalid_window_is_refused(self):
        with pytest.raises(EvaluationError):
            Window(date(2026, 3, 1), date(2026, 1, 1),
                   date(2026, 4, 1), date(2026, 5, 1))

    def test_negative_embargo_refused(self):
        with pytest.raises(EvaluationError, match="embargo"):
            walk_forward(date(2026, 1, 1), date(2026, 12, 31),
                         train_days=30, test_days=10, embargo_days=-1)


def result(name, window, rets, trades=30, costs="0"):
    return StrategyResult(name=name, window=window,
                          returns=tuple(Decimal(str(r)) for r in rets),
                          trades=trades, costs=Decimal(costs),
                          universe=UNIV, capital=CAP)


class TestComparacaoJusta:
    def test_different_universe_is_refused(self):
        w = wins()[0]
        c = Comparison()
        c.add(result("A", w, [0.02]))
        outro = StrategyResult("B", w, (Decimal("0.05"),), 30, Decimal("0"),
                               ("BTC/USDT",), CAP)
        with pytest.raises(EvaluationError, match="universos"):
            c.add(outro)

    def test_different_capital_is_refused(self):
        w = wins()[0]
        c = Comparison()
        c.add(result("A", w, [0.02]))
        with pytest.raises(EvaluationError, match="capitais"):
            c.add(StrategyResult("B", w, (Decimal("0.05"),), 30, Decimal("0"),
                                 UNIV, Decimal("5000")))

    def test_different_period_is_refused(self):
        ws = wins()
        c = Comparison()
        c.add(result("A", ws[0], [0.02]))
        with pytest.raises(EvaluationError, match="períodos"):
            c.add(result("B", ws[1], [0.05]))

    def test_verdict_flags_small_sample_and_zero_costs(self):
        w = wins()[0]
        c = Comparison()
        c.add(result("equipe", w, [0.03], trades=4))
        c.add(result("solo", w, [0.01], trades=4))
        v = c.verdict()
        assert v["lider"] == "equipe"
        assert v["conclusivo"] is False
        assert any("amostra pequena" in r for r in v["ressalvas"])
        assert any("custos" in r for r in v["ressalvas"])
        assert "não é promessa" in v["aviso"]

    def test_costs_change_the_ranking(self):
        w = wins()[0]
        c = Comparison()
        c.add(result("bruto_alto", w, [0.05], trades=50, costs="0.04"))
        c.add(result("liquido_melhor", w, [0.03], trades=50, costs="0.005"))
        assert c.verdict()["lider"] == "liquido_melhor"   # custo manda

    def test_verdict_conclusive_when_design_is_sound(self):
        w = wins()[0]
        c = Comparison()
        c.add(result("A", w, [0.04], trades=40, costs="0.01"))
        c.add(result("B", w, [0.02], trades=40, costs="0.01"))
        v = c.verdict()
        assert v["conclusivo"] is True and v["ressalvas"] == []


class TestAgregacao:
    def test_one_good_fold_does_not_make_a_good_strategy(self):
        ws = wins()[:5]
        agg = aggregate_walk_forward(
            [result("X", ws[0], [0.30]), result("X", ws[1], [-0.05]),
             result("X", ws[2], [-0.04]), result("X", ws[3], [-0.06]),
             result("X", ws[4], [-0.03])])
        assert agg["dobras_positivas"] == 1
        assert agg["consistencia"] == 0.2
        assert agg["dispersao"] > 0

    def test_overlapping_test_windows_are_refused(self):
        w = Window(date(2026, 1, 1), date(2026, 3, 1), date(2026, 3, 1), date(2026, 4, 1))
        w2 = Window(date(2026, 1, 1), date(2026, 3, 1), date(2026, 3, 15), date(2026, 4, 15))
        with pytest.raises(EvaluationError, match="sobreposto"):
            aggregate_walk_forward([result("X", w, [0.1]), result("X", w2, [0.1])])

    def test_mixing_strategies_is_refused(self):
        ws = wins()[:2]
        with pytest.raises(EvaluationError, match="MESMA"):
            aggregate_walk_forward([result("A", ws[0], [0.1]),
                                    result("B", ws[1], [0.1])])


class TestEvidenciaIndependente:
    def test_same_model_agreement_counts_once(self):
        agreement = [("analista-1", "groq:llama-3.3"), ("analista-2", "groq:llama-3.3"),
                     ("analista-3", "groq:llama-3.3")]
        assert independent_evidence(agreement) == 1

    def test_different_sources_count_separately(self):
        mixed = [("analista-1", "groq:llama-3.3"), ("analista-2", "fred:macro"),
                 ("analista-3", "alpha_vantage:fundamentos")]
        assert independent_evidence(mixed) == 3

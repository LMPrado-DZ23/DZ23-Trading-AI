from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tradingagents.research import BacktestConfig, Bar, run_backtest, run_walk_forward, windows


def bars(n=6):
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return tuple(Bar(start + timedelta(days=i), start + timedelta(days=i), Decimal(100+i), Decimal(101+i), Decimal(99+i), Decimal(100+i), Decimal(10)) for i in range(n))


def test_backtest_produces_manifest_and_costs():
    result = run_backtest(bars(), lambda history: 1, BacktestConfig())
    assert result.trades == 5
    assert result.manifest["dataset_hash"] == result.dataset_hash
    assert result.total_return < Decimal("0.01")


def test_backtest_rejects_invalid_availability():
    row = list(bars())
    row[2] = Bar(row[2].timestamp, row[2].timestamp + timedelta(days=1), row[2].open, row[2].high, row[2].low, row[2].close, row[2].volume)
    with pytest.raises(ValueError):
        run_backtest(row, lambda history: 0, BacktestConfig())


def test_walk_forward_windows_are_disjoint():
    result = windows(20, 10, 5)
    assert result[0].train_end == result[0].test_start
    assert result[0].test_end == result[1].test_start


def test_signal_only_sees_strictly_past_bars():
    """Sem esta garantia o backtest veria o futuro: a história entregue ao
    sinal termina ANTES do bar decidido — sem o próprio bar, sem o que vem."""
    vistos = []

    def espiao(history):
        vistos.append(history)
        return 0

    b = bars(6)
    run_backtest(b, espiao, BacktestConfig())
    assert [len(h) for h in vistos] == [1, 2, 3, 4, 5]
    for i, h in enumerate(vistos, start=1):
        assert h == b[:i], "a história não é o passado estrito do bar decidido"
        assert all(bar.timestamp < b[i].timestamp for bar in h), (
            "o sinal recebeu um bar do presente ou do futuro")


def test_walk_forward_marks_each_window_with_its_own_revision():
    """Cada dobra carrega a revisão REAL do código (sha do commit HEAD) e a
    SUA janela em campo próprio — dobra repetida não se confunde com a
    primeira, e nenhum resultado se passa por mais reproduzível do que é."""
    from tradingagents.research import revisao_do_codigo

    b = bars(40)
    cfg = BacktestConfig()
    resultados = run_walk_forward(b, train_size=20, test_size=5,
                                  signal=lambda history: 0, config=cfg)
    janelas = windows(40, 20, 5)
    assert len(resultados) == len(janelas) == 4
    esperada = revisao_do_codigo()
    for r, j in zip(resultados, janelas, strict=True):
        assert r.manifest["code_revision"] == esperada
        assert r.manifest["janela"] == f"{j.train_start}:{j.train_end}"
    assert len({r.manifest["janela"] for r in resultados}) == 4
    assert len({r.dataset_hash for r in resultados}) == 4

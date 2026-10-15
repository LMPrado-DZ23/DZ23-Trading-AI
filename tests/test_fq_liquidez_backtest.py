"""Liquidez no backtest (FQ-1/A). O simulador não pode preencher mais do que
o mercado ofereceu: sem teto de participação, um backtest aprova estratégia
que a execução real jamais conseguiria — falsa aprovação que a Escola
Central herdaria como evidência.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tradingagents.research import BacktestConfig, Bar, run_backtest

BASE = datetime(2025, 1, 1, tzinfo=timezone.utc)


def barra(i: int, close, volume) -> Bar:
    d = Decimal(str(close))
    t = BASE + timedelta(days=i)
    return Bar(t, t, d, d, d, d, Decimal(str(volume)))


def serie(volumes, closes=None) -> tuple[Bar, ...]:
    closes = closes or [100 + i for i in range(len(volumes))]
    return tuple(barra(i, c, v)
                 for i, (c, v) in enumerate(zip(closes, volumes, strict=True)))


def sempre_comprado(history):
    return 1


def test_volume_exato_comporta_entrada_e_saida_sem_limitar():
    r = run_backtest(serie([20000] * 6, [100] * 6), sempre_comprado, BacktestConfig())
    assert r.trades == 5
    assert r.fills_limitados == 0
    assert r.fills_rejeitados_por_liquidez == 0
    assert r.equity_curve[-1] == Decimal("99930")


def test_volume_pequeno_limita_o_fill_e_registra():
    r = run_backtest(serie([1, 1, 1]), sempre_comprado, BacktestConfig())
    assert r.trades == 2
    assert r.fills_limitados == 2
    assert r.fills_rejeitados_por_liquidez == 0
    assert r.equity_curve == (
        Decimal("100000"), Decimal("99999.999293"), Decimal("99999.998579"),
    )


def test_volume_zero_nao_preenche_nem_custa():
    r = run_backtest(serie([10, 0, 10, 10]), sempre_comprado,
                     BacktestConfig())
    assert r.trades == 2
    assert r.fills_rejeitados_por_liquidez == 1
    assert r.fills_limitados == 2
    assert r.equity_curve[1] == r.equity_curve[0], (
        "barra sem volume não pode mexer no caixa")
    assert r.equity_curve[-1] == Decimal("99999.98565")


def test_volume_negativo_e_rejeitado():
    with pytest.raises(ValueError):
        run_backtest(serie([10, -1, 10]), sempre_comprado, BacktestConfig())


def test_volume_alto_nao_limita():
    abundante = run_backtest(serie([10**9] * 6), sempre_comprado,
                             BacktestConfig())
    sem_teto = run_backtest(serie([10**9] * 6), sempre_comprado,
                            BacktestConfig(max_participacao=Decimal("1")))
    assert abundante.fills_limitados == 0
    assert abundante.equity_curve == sem_teto.equity_curve


def test_manifest_registra_modelo_de_liquidez():
    r = run_backtest(serie([10] * 4), sempre_comprado, BacktestConfig())
    assert "max_participacao" in r.manifest["liquidity_model"]

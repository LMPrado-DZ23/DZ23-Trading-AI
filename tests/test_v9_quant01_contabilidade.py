"""V9-QUANT-01 — contabilidade do backtest: preço parado não vira lucro.

Contraprova original (§5.1 do baseline, HEAD 2b7754a): três velas constantes a
100, equity inicial 1000, custos zero e uma compra devolviam equity final 1010
— o motor antigo somava o notional da posição inteira como se fosse lucro
(`gross = magnitude if desejado > 0 else -magnitude`).

O motor deste HEAD é o round-trip de uma barra: o sinal é lido no histórico
disponível e a operação acontece na barra seguinte, com entrada na abertura e
saída no fechamento dela — exatamente o que o manifesto declara em
`execution_model`. Este arquivo fixa essa aritmética: P&L da barra operada =
quantidade·(fechamento−abertura)·direção − custos das duas pernas, e nada mais.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from tradingagents.research import BacktestConfig, Bar, run_backtest

_RAIZ = Path(__file__).resolve().parents[1]
_INICIO = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _velas(ohlc: list[tuple[str, str, str, str]], volume: str = "10000") -> list[Bar]:
    return [
        Bar(
            timestamp=_INICIO + timedelta(hours=i),
            available_at=_INICIO + timedelta(hours=i),
            open=Decimal(abertura), high=Decimal(maxima), low=Decimal(minima),
            close=Decimal(fechamento), volume=Decimal(volume),
        )
        for i, (abertura, maxima, minima, fechamento) in enumerate(ohlc)
    ]


def _plana(preco: str = "100") -> tuple[str, str, str, str]:
    return (preco, preco, preco, preco)


def _compra_no_primeiro(history: tuple[Bar, ...]) -> int:
    return 1 if len(history) == 1 else 0


def _venda_no_primeiro(history: tuple[Bar, ...]) -> int:
    return -1 if len(history) == 1 else 0


def _sem_custo(equity: str = "1000") -> BacktestConfig:
    return BacktestConfig(initial_equity=Decimal(equity), fee_bps=Decimal("0"),
                          slippage_bps=Decimal("0"))


def test_preco_constante_com_custos_zero_nao_gera_lucro() -> None:
    """A contraprova do baseline: terminar em 1010 significa contar o notional
    como lucro. Com a aritmética correta a equity fica parada em 1000."""
    resultado = run_backtest(
        _velas([_plana()] * 3), _compra_no_primeiro, _sem_custo())
    assert resultado.equity_curve == (Decimal("1000"), Decimal("1000.0"), Decimal("1000.0"))
    assert resultado.total_return == 0
    assert resultado.trades == 1


def test_compra_em_queda_perfeita_nao_devolve_6x_a_perda() -> None:
    """No defeito original, uma venda a descoberto com preço constante rendia
    +1% (o notional virava lucro). Preço parado com custos zero rende zero."""
    resultado = run_backtest(
        _velas([_plana()] * 3), _venda_no_primeiro, _sem_custo())
    assert resultado.equity_curve[-1] == Decimal("1000")
    assert resultado.total_return == 0


def test_operacao_completa_da_barra_seguinte_paga_so_o_movimento_dela() -> None:
    """Sinal na primeira barra ⇒ operação na segunda: entrada na abertura (100)
    e saída no fechamento (110) da MESMA barra. Ganho bruto ≈ 10% de 100 de
    notional, menos as taxas das duas pernas — nunca ≈90 (equity×10%)."""
    resultado = run_backtest(
        _velas([_plana(), ("100", "110", "100", "110"), _plana("110")]),
        _compra_no_primeiro,
        BacktestConfig(initial_equity=Decimal("1000"), fee_bps=Decimal("10"),
                       slippage_bps=Decimal("0")),
    )
    assert resultado.equity_curve == (
        Decimal("1000"), Decimal("1009.7900"), Decimal("1009.7900"))
    assert resultado.total_return == Decimal("0.00979")
    assert resultado.trades == 1


def test_venda_a_descoberto_da_barra_seguinte_paga_o_movimento_dela() -> None:
    resultado = run_backtest(
        _velas([_plana(), ("100", "100", "90", "90"), _plana("90")]),
        _venda_no_primeiro,
        BacktestConfig(initial_equity=Decimal("1000"), fee_bps=Decimal("10"),
                       slippage_bps=Decimal("0")),
    )
    assert resultado.equity_curve == (
        Decimal("1000"), Decimal("1009.8100"), Decimal("1009.8100"))
    assert resultado.total_return == Decimal("0.00981")


def test_barra_de_entrada_nao_operada_deixa_a_equity_intacta() -> None:
    """A operação só existe se a barra seguinte se move. Aqui o sinal dispara,
    a barra executada é plana e inexiste custo: nada muda, nada vira lucro."""
    resultado = run_backtest(
        _velas([_plana()] * 2), _compra_no_primeiro, _sem_custo())
    assert resultado.equity_curve[-1] == Decimal("1000")
    assert resultado.trades == 1


def test_custos_da_ida_e_volta_saem_apenas_na_barra_operada() -> None:
    """10 bps por perna sobre notional 100 ≈ 0,2 de custo quando a barra fica
    parada: quem conta IDA E VOLTA só de preço da barra não desaparece."""
    resultado = run_backtest(
        _velas([_plana(), _plana(), _plana("100")]), _compra_no_primeiro,
        BacktestConfig(initial_equity=Decimal("1000"), fee_bps=Decimal("10"),
                       slippage_bps=Decimal("0")),
    )
    assert resultado.equity_curve[-1] == Decimal("999.8000")
    assert resultado.total_return == Decimal("-0.0002")


def test_manifesto_declara_o_modelo_de_execucao_do_motor() -> None:
    resultado = run_backtest(
        _velas([_plana()] * 2), _compra_no_primeiro, _sem_custo())
    assert resultado.manifest["execution_model"] == (
        "one_bar_round_trip;entry=next_open;exit=same_close;short=synthetic")
    assert "notional" not in resultado.manifest["execution_model"]


def test_mesma_entrada_produz_mesmo_resultado_e_hash_em_duas_execucoes() -> None:
    velas = _velas([_plana(), ("100", "110", "100", "110")])
    primeiro = run_backtest(velas, _compra_no_primeiro, _sem_custo())
    segundo = run_backtest(velas, _compra_no_primeiro, _sem_custo())
    assert primeiro.equity_curve == segundo.equity_curve
    assert primeiro.dataset_hash == segundo.dataset_hash


def test_baseline_2b7754a_continha_o_defeito_e_o_head_atual_nao() -> None:
    """Guarda de rastreabilidade da contraprova §5.1: o commit do baseline
    somava o notional como lucro (gross = magnitude). O HEAD atual calcula o
    P&L por quantidade×(fechamento−abertura). Sem o objeto no clone (zip sem
    histórico), o teste é pulado."""
    try:
        historico = subprocess.run(
            ["git", "show", "2b7754a94cc1e4233ad95ac6fa377be4078e5055:tradingagents/research/backtest.py"],
            cwd=_RAIZ, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        pytest.skip("baseline 2b7754a ausente neste clone")
    if historico.returncode != 0:
        pytest.skip("baseline 2b7754a ausente neste clone")
    assert "gross = magnitude if desejado > 0 else -magnitude" in historico.stdout
    atual = (_RAIZ / "tradingagents/research/backtest.py").read_text(encoding="utf-8")
    assert "gross = magnitude" not in atual
    assert "quantity * (barra.close - barra.open) * direction" in atual

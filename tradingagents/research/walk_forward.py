"""Janelas walk-forward para avaliação fora-de-amostra.

Limites reais deste módulo (declarados para a auditoria):

- O treino da janela é um RÓTULO de rastreabilidade carregado no manifesto;
  não existe etapa de ajuste de parâmetros que o consuma automaticamente —
  o que se mede é a fração de teste, barra a barra, via `run_backtest`.
- Sem `embargo`, o teste começa na barra imediatamente após o treino; com
  `embargo=N`, N barras ficam FORA entre treino e teste (corta o
  sangramento de resíduo de indicadores de janela longa).
- `step` default é o tamanho do teste (frações de teste contíguas, sem
  sobreposição); passo menor sobrepõe a medição entre janelas.
- Amostra que não caiba uma janela completa produz ZERO janelas — nunca uma
  janela parcial.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .backtest import BacktestConfig, BacktestResult, Bar, revisao_do_codigo, run_backtest


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    train_start: int
    train_end: int
    test_start: int
    test_end: int


def windows(size: int, train_size: int, test_size: int, step: int | None = None,
            embargo: int = 0) -> tuple[WalkForwardWindow, ...]:
    if embargo < 0:
        raise ValueError("embargo deve ser >= 0")
    if min(size, train_size, test_size) <= 0 or train_size + embargo + test_size > size:
        return ()
    stride = step or test_size
    result = []
    start = 0
    while start + train_size + embargo + test_size <= size:
        test_start = start + train_size + embargo
        result.append(WalkForwardWindow(start, start + train_size, test_start, test_start + test_size))
        start += stride
    return tuple(result)


def run_walk_forward(bars: Sequence[Bar], train_size: int, test_size: int, signal, config: BacktestConfig, *, embargo: int = 0) -> tuple[BacktestResult, ...]:
    revisao = revisao_do_codigo()
    return tuple(run_backtest(bars[w.test_start:w.test_end], signal, config, code_revision=revisao, janela=f"{w.train_start}:{w.train_end}") for w in windows(len(bars), train_size, test_size, embargo=embargo))

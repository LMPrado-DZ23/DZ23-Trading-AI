from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[2]
# Ligado no import: o except não pode resolver via `subprocess.` depois de
# o módulo ser trocado em teste (o falso `subprocess` não conhece a exceção).
_SUBPROCESS_ERROR = subprocess.SubprocessError


def revisao_do_codigo() -> str:
    """Revisão do código que produziu o resultado — sha do commit HEAD.

    Diretório sujo ⇒ sufixo `-dirty` (resultado não reproduzível tal como
    está). Sem git (zip instalado, ambiente estranho) ⇒ `"working-tree"`
    honesto em vez de sha inventado.
    """
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=_RAIZ,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        if not sha:
            return "working-tree"
        sujo = subprocess.run(
            ["git", "diff", "--quiet"], cwd=_RAIZ,
            capture_output=True, timeout=10,
        ).returncode != 0
        return f"{sha}-dirty" if sujo else sha
    except (OSError, _SUBPROCESS_ERROR):
        return "working-tree"


@dataclass(frozen=True, slots=True)
class Bar:
    timestamp: datetime
    available_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_equity: Decimal = Decimal("100000")
    fee_bps: Decimal = Decimal("5")
    slippage_bps: Decimal = Decimal("2")
    position_pct: Decimal = Decimal("0.1")
    # Fração máxima do volume da barra que um fill pode consumir: backtest
    # sem teto aprova estratégia que a execução real não conseguiria preencher.
    max_participacao: Decimal = Decimal("0.01")
    seed: int = 0


@dataclass(frozen=True, slots=True)
class BacktestResult:
    equity_curve: tuple[Decimal, ...]
    trades: int
    total_return: Decimal
    max_drawdown: Decimal
    dataset_hash: str
    manifest: dict[str, str]
    fills_limitados: int = 0
    fills_rejeitados_por_liquidez: int = 0


def run_backtest(bars: Iterable[Bar], signal: Callable[[tuple[Bar, ...]], int], config: BacktestConfig, *, code_revision: str = "working-tree", janela: str = "") -> BacktestResult:
    values = (config.initial_equity, config.position_pct, config.fee_bps,
              config.slippage_bps, config.max_participacao)
    if any(not value.is_finite() for value in values):
        raise ValueError("configuração econômica deve ser finita")
    if config.initial_equity <= 0:
        raise ValueError("initial_equity deve ser positivo")
    if not 0 < config.position_pct <= 1 or not 0 < config.max_participacao <= 1:
        raise ValueError("position_pct e max_participacao devem estar em (0, 1]")
    if config.fee_bps < 0 or config.slippage_bps < 0:
        raise ValueError("custos não podem ser negativos")
    ordered = tuple(sorted(bars, key=lambda bar: bar.timestamp))
    if len({bar.timestamp for bar in ordered}) != len(ordered):
        raise ValueError("timestamps duplicados")
    if any(bar.available_at > bar.timestamp for bar in ordered):
        raise ValueError("available_at não pode ser posterior ao timestamp observado")
    for bar in ordered:
        if any(not value.is_finite() for value in
               (bar.open, bar.high, bar.low, bar.close, bar.volume)):
            raise ValueError("preços e volume devem ser finitos")
        if bar.volume < 0:
            raise ValueError("volume não pode ser negativo")
        if not (0 < bar.low <= min(bar.open, bar.close)
                <= max(bar.open, bar.close) <= bar.high):
            raise ValueError("OHLC deve ser positivo e consistente")
    dataset_hash = sha256(json.dumps([asdict(bar) for bar in ordered], default=str, sort_keys=True).encode()).hexdigest()
    equity = config.initial_equity
    curve = [equity]
    trades = 0
    fills_limitados = 0
    fills_rejeitados = 0
    cost_rate = (config.fee_bps + config.slippage_bps) / Decimal("10000")
    notional = config.initial_equity * config.position_pct
    for index in range(1, len(ordered)):
        history = ordered[:index]
        direction = signal(history)
        if direction not in (-1, 0, 1):
            raise ValueError("signal deve retornar -1, 0 ou 1")
        if direction and equity > 0:
            barra = ordered[index]
            desejado = min(notional, equity / (1 + cost_rate)) / barra.open
            capacidade = barra.volume * config.max_participacao / 2
            quantity = min(desejado, capacidade)
            if quantity == 0:
                fills_rejeitados += 1
            else:
                gross = quantity * (barra.close - barra.open) * direction
                cost = quantity * (barra.open + barra.close) * cost_rate
                equity += gross - cost
                trades += 1
                if quantity < desejado:
                    fills_limitados += 1
        curve.append(equity)
    peak = config.initial_equity
    drawdowns = []
    for value in curve:
        peak = max(peak, value)
        drawdowns.append((peak - value) / peak)
    manifest = {
        "dataset_hash": dataset_hash, "code_revision": code_revision,
        "janela": janela, "seed": str(config.seed),
        "execution_model": "one_bar_round_trip;entry=next_open;exit=same_close;short=synthetic",
        "sizing_model": f"fixed_initial_equity;position_pct={config.position_pct}",
        "cost_model": f"per_leg_notional;fee_bps={config.fee_bps};slippage_bps={config.slippage_bps}",
        "liquidity_model": f"shared_two_leg_bar_volume;max_participacao={config.max_participacao}",
    }
    return BacktestResult(tuple(curve), trades, (equity / config.initial_equity) - 1, max(drawdowns, default=Decimal("0")), dataset_hash, manifest, fills_limitados, fills_rejeitados)

"""Três traders operacionais com estratégias distintas (MA-04).

Cada um tem universo, regime de elegibilidade, entrada, saída, invalidação e
razão explícita de NO_TRADE. O cálculo é determinístico (código); o LLM entra
como interpretação opcional e NUNCA decide tamanho, risco ou autorização.
"""
from __future__ import annotations

from decimal import Decimal
from statistics import fmean, pstdev

from .models import (Action, AgentSpec, AgentStage, MarketSnapshot,
                     TradeProposal, default_expiry)


def _sma(values, n: int) -> Decimal:
    return Decimal(str(fmean([float(v) for v in values[-n:]])))


class BaseTrader:
    strategy = "base"
    needs = 20

    def __init__(self, agent_id: str, universe: tuple[str, ...], version: str = "1"):
        self.spec = AgentSpec(agent_id=agent_id, role="trader",
                              strategy=self.strategy, version=version,
                              stage=AgentStage.REGISTERED,
                              tools=("market_data", "indicators"))
        self.universe = universe

    def _no(self, snap: MarketSnapshot, reason: str, action=Action.NO_TRADE) -> TradeProposal:
        return TradeProposal(agent_id=self.spec.agent_id, strategy=self.strategy,
                             instrument=snap.instrument, action=action, reason=reason,
                             snapshot_cutoff=snap.cutoff)

    def manage(self, snap: MarketSnapshot, position) -> TradeProposal:
        """Acompanha posição aberta: propõe SAÍDA quando a tese morre.
        `position` traz entry_price e agent_id de quem abriu."""
        if not snap.sufficient(self.needs):
            return self._no(snap, "sem dados para reavaliar a posição — mantendo",
                            action=Action.HOLD)
        entry = position.entry_price
        pnl_pct = (snap.last - entry) / entry if entry else Decimal("0")
        stop, alvo = Decimal("-0.05"), Decimal("0.08")
        if pnl_pct <= stop:
            return self._exit(snap, f"stop: {pnl_pct:.2%} desde a entrada")
        if pnl_pct >= alvo:
            return self._exit(snap, f"alvo atingido: {pnl_pct:.2%}")
        reason = self._thesis_dead(snap)
        if reason:
            return self._exit(snap, f"tese invalidada: {reason}")
        return self._no(snap, f"tese de pé ({pnl_pct:+.2%}) — mantendo",
                        action=Action.HOLD)

    def _thesis_dead(self, snap: MarketSnapshot) -> str:
        return ""

    def _exit(self, snap: MarketSnapshot, reason: str) -> TradeProposal:
        return TradeProposal(
            agent_id=self.spec.agent_id, strategy=self.strategy,
            instrument=snap.instrument, action=Action.REDUCE, reason=reason,
            desired_exposure_pct=Decimal("1"), confidence=Decimal("0.9"),
            valid_until=default_expiry(10), snapshot_cutoff=snap.cutoff)

    def propose(self, snap: MarketSnapshot) -> TradeProposal:
        if snap.instrument not in self.universe:
            return self._no(snap, "instrumento fora do universo deste trader")
        if not self.spec.can_propose():
            return self._no(snap, f"agente no estágio {self.spec.stage.value} não propõe")
        if not snap.sufficient(self.needs):
            # Cenário 06: dado insuficiente nunca vira ordem.
            return self._no(snap, f"dados insuficientes (precisa de {self.needs} velas)",
                            action=Action.DATA_INSUFFICIENT)
        return self._evaluate(snap)

    def _evaluate(self, snap: MarketSnapshot) -> TradeProposal:  # pragma: no cover
        raise NotImplementedError

    def _buy(self, snap, pct: str, reason: str, invalidation: str, conf: str) -> TradeProposal:
        return TradeProposal(
            agent_id=self.spec.agent_id, strategy=self.strategy,
            instrument=snap.instrument, action=Action.BUY, reason=reason,
            desired_exposure_pct=Decimal(pct), confidence=Decimal(conf),
            valid_until=default_expiry(10), invalidation=invalidation,
            snapshot_cutoff=snap.cutoff)


class TrendTrader(BaseTrader):
    """Segue tendência: compra quando a média curta cruza acima da longa."""
    strategy = "trend_following"
    needs = 30

    def _thesis_dead(self, snap):
        fast, slow = _sma(snap.closes, 10), _sma(snap.closes, 30)
        return "média curta cruzou abaixo da longa" if fast < slow else ""

    def _evaluate(self, snap):
        fast, slow = _sma(snap.closes, 10), _sma(snap.closes, 30)
        if fast <= slow:
            return self._no(snap, "regime sem tendência de alta (média curta <= longa)")
        return self._buy(snap, "0.15",
                         f"tendência de alta: média curta {fast:.2f} acima da longa {slow:.2f}",
                         "média curta voltar abaixo da longa", "0.6")


class MeanReversionTrader(BaseTrader):
    """Reversão à média: compra exagero de queda contra a média."""
    strategy = "mean_reversion"
    needs = 20

    def _thesis_dead(self, snap):
        window = [float(c) for c in snap.closes[-20:]]
        mean = Decimal(str(fmean(window)))
        return "preço voltou à média (tese cumprida)" if snap.last >= mean else ""

    def _evaluate(self, snap):
        window = [float(c) for c in snap.closes[-20:]]
        mean, sd = Decimal(str(fmean(window))), Decimal(str(pstdev(window)))
        if sd == 0:
            return self._no(snap, "volatilidade nula: sem desvio para reverter")
        z = (snap.last - mean) / sd
        if z > Decimal("-2"):
            return self._no(snap, f"preço dentro da banda (z={z:.2f}); só opero abaixo de -2")
        return self._buy(snap, "0.10",
                         f"exagero de queda: z={z:.2f} contra a média de 20",
                         "z voltar acima de 0", "0.55")


class BreakoutTrader(BaseTrader):
    """Rompimento: compra quando supera a máxima recente com folga."""
    strategy = "breakout"
    needs = 25

    def _evaluate(self, snap):
        prior = snap.closes[-25:-1]
        high = max(prior)
        if snap.last <= high * Decimal("1.01"):
            return self._no(snap, f"sem rompimento: {snap.last:.2f} não superou {high:.2f} + 1%")
        return self._buy(snap, "0.12",
                         f"rompimento de {high:.2f} com folga",
                         "perder a máxima rompida", "0.5")


def default_desk(universe: tuple[str, ...]) -> list[BaseTrader]:
    """Mesa padrão: três estratégias distintas, não três cópias do mesmo prompt."""
    return [TrendTrader("trader-trend", universe),
            MeanReversionTrader("trader-reversion", universe),
            BreakoutTrader("trader-breakout", universe)]

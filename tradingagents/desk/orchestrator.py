"""Ciclo persistente da mesa (MA-03).

evento elegível → snapshot → propostas → arbitragem → risco/reserva → intenção.
Nada aqui espera comando humano nem navegador aberto: o ciclo é chamado pelo
worker durável a cada evento (candle fechado, fill, manutenção).

Limites codificados:
- A execução em si fica FORA deste módulo: o orquestrador para na intenção
  autorizada. Quem envia é o executor, com posse e reconciliação próprias.
- Transação financeira nunca fica aberta esperando LLM ou rede.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .models import Action, DeskMandate, MandateError, OrderIntent, utcnow
from .risk import PortfolioAllocator, RiskEngine, RiskVeto


@dataclass
class CycleReport:
    """O que aconteceu num ciclo — vira linha do painel Mesa de Agentes."""
    started_at: object = field(default_factory=utcnow)
    proposals: int = 0
    admitted: int = 0
    vetoed: list[tuple[str, str]] = field(default_factory=list)
    intents: list[OrderIntent] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return (f"{self.proposals} propostas, {self.admitted} admitidas, "
                f"{len(self.intents)} intenções, {len(self.vetoed)} vetos")


class DeskOrchestrator:
    """Executa o mandato; distribui trabalho. NÃO amplia limites."""

    def __init__(self, mandate: DeskMandate, traders, *, worker_id: str,
                 risk: RiskEngine | None = None, positions_provider=None):
        self.mandate = mandate
        self.traders = list(traders)
        self.worker_id = worker_id
        self.risk = risk or RiskEngine(mandate)
        self.allocator = PortfolioAllocator()
        self.owner_by_instrument: dict[str, str] = {}
        # Posição EXECUTADA não é reserva: depois do fill a reserva é
        # consumida, mas a exposição continua. Sem esta fonte a mesa
        # recompraria o mesmo ativo no ciclo seguinte (achado em teste vivo).
        self.positions_provider = positions_provider or (lambda: set())

    def open_exposure(self) -> set[str]:
        return set(self.risk.open_instruments()) | set(self.positions_provider())

    def run_cycle(self, snapshots, prices: dict[str, Decimal]) -> CycleReport:
        report = CycleReport()
        try:
            self.mandate.assert_active()
        except MandateError as exc:
            report.errors.append(f"mandato: {exc}")
            return report  # mandato inválido ⇒ ciclo não propõe nada

        proposals = []
        for snap in snapshots:
            for trader in self.traders:
                try:
                    proposals.append(trader.propose(snap))
                except Exception as exc:  # um trader defeituoso não derruba a mesa
                    report.errors.append(f"{trader.spec.agent_id}: {type(exc).__name__}")
        report.proposals = len(proposals)

        actionable = [p for p in proposals if p.is_actionable()]
        decision = self.allocator.arbitrate(
            actionable,
            open_positions=self.open_exposure(),
            owner_by_instrument=self.owner_by_instrument)
        report.admitted = len(decision.admitted)
        report.vetoed = [(p.agent_id, why) for p, why in decision.vetoed]

        for proposal in decision.admitted:
            price = prices.get(proposal.instrument)
            if price is None:
                report.vetoed.append((proposal.agent_id, "sem preço de execução"))
                continue
            try:
                reservation = self.risk.reserve(proposal, price)
                intent = self.risk.intent(reservation, proposal.action,
                                          self.mandate.mode, self.worker_id)
                intent.assert_sendable(worker_id=self.worker_id, mandate=self.mandate)
            except (RiskVeto, MandateError) as exc:
                report.vetoed.append((proposal.agent_id, str(exc)))
                continue
            self.owner_by_instrument[proposal.instrument] = proposal.agent_id
            report.intents.append(intent)
        return report

    # -- kill switch com semânticas distintas (cenário 13) -------------
    def pause_entries(self) -> None:
        """Para de abrir; posições e reservas seguem sob o guardião."""
        self.traders = []

    def drain(self) -> list[str]:
        """Pede saída controlada: devolve os instrumentos a encerrar.
        Não finge que fechou — quem fecha é o executor com reconciliação."""
        return sorted(self.open_exposure())


__all__ = ["DeskOrchestrator", "CycleReport"]

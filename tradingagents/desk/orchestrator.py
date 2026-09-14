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

from dataclasses import dataclass, dataclass as _dataclass, field
from decimal import Decimal

from .models import Action, DeskMandate, MandateError, OrderIntent, utcnow
from .risk import PortfolioAllocator, RiskEngine, RiskVeto


@_dataclass
class Position:
    """Exposição aberta sob responsabilidade de um agente."""
    instrument: str
    quantity: Decimal
    entry_price: Decimal
    agent_id: str
    cost_basis: Decimal


@dataclass
class CycleReport:
    """O que aconteceu num ciclo — vira linha do painel Mesa de Agentes."""
    started_at: object = field(default_factory=utcnow)
    proposals: int = 0
    admitted: int = 0
    vetoed: list[tuple[str, str]] = field(default_factory=list)
    intents: list[OrderIntent] = field(default_factory=list)
    exits: list[tuple[str, str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # Por que a mesa NÃO operou — para o leigo, isto é tão importante
    # quanto a ordem enviada.
    abstentions: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.intents or self.exits:
            return (f"{self.proposals} propostas, {self.admitted} admitidas, "
                    f"{len(self.intents)} entradas, {len(self.exits)} saídas, "
                    f"{len(self.vetoed)} vetos")
        if self.errors:
            return f"ciclo com {len(self.errors)} erro(s) — ver detalhes"
        return (f"{self.proposals} propostas, nenhuma operação: "
                f"critérios não atendidos ({len(self.abstentions)} abstenções)")


class DeskOrchestrator:
    """Executa o mandato; distribui trabalho. NÃO amplia limites."""

    def __init__(self, mandate: DeskMandate, traders, *, worker_id: str,
                 risk: RiskEngine | None = None, positions_provider=None,
                 memoria=None, exchange: str = "binance"):
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
        self.memoria = memoria
        self.exchange = exchange
        # Livro da mesa: quem abriu o quê, a que preço. É daqui que sai a
        # gestão de saída — sem isso a mesa só compra e nunca realiza.
        self.book: dict[str, Position] = {}

    def open_exposure(self) -> set[str]:
        return set(self.risk.open_instruments()) | set(self.positions_provider())

    def run_cycle(self, snapshots, prices: dict[str, Decimal]) -> CycleReport:
        report = CycleReport()
        try:
            self.mandate.assert_active()
        except MandateError as exc:
            report.errors.append(f"mandato: {exc}")
            return report  # mandato inválido ⇒ ciclo não propõe nada

        # 1) ACOMPANHAR o que já está aberto (saída vem antes de entrada)
        for snap in snapshots:
            pos = self.book.get(snap.instrument)
            if not pos:
                continue
            trader = next((t for t in self.traders
                           if t.spec.agent_id == pos.agent_id), None)
            if trader is None:
                report.exits.append((pos.agent_id, snap.instrument,
                                     "trader ausente — guardião assume"))
                continue
            call = trader.manage(snap, pos)
            if call.action is Action.REDUCE:
                report.exits.append((pos.agent_id, snap.instrument, call.reason))

        # 2) Só então procurar entradas novas
        proposals = []
        for snap in snapshots:
            for trader in self.traders:
                try:
                    proposals.append(trader.propose(snap))
                except Exception as exc:  # um trader defeituoso não derruba a mesa
                    report.errors.append(f"{trader.spec.agent_id}: {type(exc).__name__}")
        report.proposals = len(proposals)

        actionable = []
        for p in proposals:
            if p.is_actionable():
                actionable.append(p)
            else:
                report.abstentions.append((p.agent_id, p.instrument, p.reason))
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

    def register_fill(self, instrument: str, quantity, price, agent_id: str,
                      cost_basis) -> None:
        """Chamado quando uma compra executa: a posição entra no livro."""
        self.book[instrument] = Position(instrument, quantity, price, agent_id,
                                         cost_basis)

    def close_position(self, instrument: str, preco_saida=None) -> Position | None:
        """Posição encerrada sai do livro, o instrumento fica livre — e o
        resultado vira APRENDIZADO (só aqui, porque só agora é realizado)."""
        self.owner_by_instrument.pop(instrument, None)
        pos = self.book.pop(instrument, None)
        if pos and preco_saida is not None and self.memoria and pos.entry_price:
            from datetime import timezone
            from decimal import Decimal as _D

            from .learning import Desfecho
            bruto = (_D(str(preco_saida)) - pos.entry_price) / pos.entry_price
            # Aprender com retorno BRUTO ensinaria a estratégia errada: o que
            # importa é o que sobra depois de taxa, spread e slippage.
            from .costs import custo_de_operar, retorno_liquido
            custo = custo_de_operar(instrument, getattr(self, "exchange", "binance"),
                                    pos.cost_basis)
            retorno = retorno_liquido(bruto, custo)
            trader = next((t for t in self.traders
                           if t.spec.agent_id == pos.agent_id), None)
            leitura = getattr(trader, "ultima_leitura", None)
            self.memoria.registrar(Desfecho(
                estrategia=getattr(trader, "strategy", pos.agent_id),
                regime=(leitura.regime.value if leitura else "indefinido"),
                instrumento=instrument, retorno=retorno,
                fechado_em=utcnow().astimezone(timezone.utc),
                forca_do_sinal=(leitura.forca if leitura else 0.0)))
        return pos

    # -- kill switch com semânticas distintas (cenário 13) -------------
    def pause_entries(self) -> None:
        """Para de abrir; posições e reservas seguem sob o guardião."""
        self.traders = []

    def drain(self) -> list[str]:
        """Pede saída controlada: devolve os instrumentos a encerrar.
        Não finge que fechou — quem fecha é o executor com reconciliação."""
        return sorted(self.open_exposure())


__all__ = ["DeskOrchestrator", "CycleReport"]

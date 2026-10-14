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
from datetime import datetime
from decimal import Decimal

from .models import Action, DeskMandate, MandateError, OrderIntent, utcnow
from .provenance import build_state, validar
from .risk import PortfolioAllocator, RiskEngine, RiskVeto


@_dataclass
class Position:
    """Exposição aberta sob responsabilidade de um agente."""
    instrument: str
    quantity: Decimal
    entry_price: Decimal
    agent_id: str
    cost_basis: Decimal
    # Momento da entrada: sem ele a regra de tempo da política de saída
    # nunca dispara em produção, e o backtest passa a medir outra coisa.
    opened_at: datetime | None = None


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
                 memoria=None, exchange: str = "binance", analista=None,
                 supervisor=None, conselheiro=None, vigia=None, sala=None):
        self.mandate = mandate
        self.traders = list(traders)
        self.worker_id = worker_id
        # Kill switch "pausar entradas": um flag, não esvaziar a lista — o
        # supervisor guarda a MESMA lista e continuaria propondo com a
        # referência antiga (bug de referência obsoleta).
        self._entries_paused = False
        self.risk = risk or RiskEngine(mandate)
        self.allocator = PortfolioAllocator()
        self.owner_by_instrument: dict[str, str] = {}
        # Posição EXECUTADA não é reserva: depois do fill a reserva é
        # consumida, mas a exposição continua. Sem esta fonte a mesa
        # recompraria o mesmo ativo no ciclo seguinte (achado em teste vivo).
        self.positions_provider = positions_provider or (lambda: set())
        self.memoria = memoria
        self.exchange = exchange
        # IA de contexto: só veta ou reduz, nunca aumenta nem autoriza.
        self.analista = analista
        # Leitura quant consultiva (item 6): quando disponível e em desacordo
        # com a entrada, só REDUZ exposição — jamais aumenta ou autoriza.
        self.conselheiro = conselheiro
        # Vigia do Piloto supervisionado (BT-3): chamável sem argumentos que
        # devolve {agent_id: motivo} de quem NÃO pode abrir posição agora.
        # O orquestrador só consulta e corta ENTRADAS — quem decide o estado
        # é o SupervisorDePiloto, nunca o próprio trader.
        self.vigia = vigia
        # Livro da mesa: quem abriu o quê, a que preço. É daqui que sai a
        # gestão de saída — sem isso a mesa só compra e nunca realiza.
        self.book: dict[str, Position] = {}
        # Sala da ESCOLA (BT-10): a evidência de operação vive por sala
        # (corretora/conta/mercado/modo). Espelho de registro: falha vira
        # aviso auditável e NÃO derruba a operação — a ordem já foi decidida
        # pelo risco; a sala é registro, não a corretora.
        self.sala = sala
        self.avisos_sala: list[str] = []

        if supervisor is not None:
            self.supervisor = supervisor
        else:
            from .traders import MarketRegimeSupervisor
            self.supervisor = MarketRegimeSupervisor(self.traders, self.mandate.capital)

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

        # 2) Só então procurar entradas novas (a não ser que estejam pausadas)
        proposals = []
        if not self._entries_paused:
            try:
                proposals = self.supervisor.supervise(snapshots, self.book)
            except Exception as exc:
                report.errors.append(f"supervisor: {type(exc).__name__}: {exc}")
                # fallback to legacy loop if supervisor fails
                proposals = []
                for snap in snapshots:
                    for trader in self.traders:
                        try:
                            proposals.append(trader.propose(snap))
                        except Exception as e:
                            report.errors.append(f"{trader.spec.agent_id}: {type(e).__name__}")
        report.proposals = len(proposals)

        actionable = []
        for p in proposals:
            if p.is_actionable():
                actionable.append(p)
            else:
                report.abstentions.append((p.agent_id, p.instrument, p.reason))

        # 3) VIGIA do Maestro: corta ENTRADAS de quem o piloto bloqueou
        # (suspenso, expirado, revogado ou fora dos limites autorizados).
        # Falha do vigia é fail-closed: nenhuma entrada passa sem supervisão.
        if actionable and self.vigia is not None:
            try:
                bloqueados = dict(self.vigia() or {})
            except Exception as exc:
                report.errors.append(f"vigia: {type(exc).__name__}: {exc}")
                bloqueados = {p.agent_id: "vigia indisponível (fail-closed)"
                              for p in actionable if p.action is Action.BUY}
            liberados = []
            for p in actionable:
                motivo = (bloqueados.get(p.agent_id)
                          if p.action is Action.BUY else None)
                if motivo:
                    report.abstentions.append(
                        (p.agent_id, p.instrument, f"Maestro/piloto: {motivo}"))
                else:
                    liberados.append(p)
            actionable = liberados

        decision = self.allocator.arbitrate(
            actionable,
            open_positions=self.open_exposure(),
            owner_by_instrument=self.owner_by_instrument,
            exposicoes={i: p.cost_basis / self.mandate.capital
                        for i, p in self.book.items() if self.mandate.capital})
        report.admitted = len(decision.admitted)
        report.vetoed = [(p.agent_id, why) for p, why in decision.vetoed]

        snaps_por_instrumento = {s.instrument: s for s in (snapshots or [])}

        for proposal in decision.admitted:
            snap = snaps_por_instrumento.get(proposal.instrument)
            if self.analista is not None:
                from .context import aplicar_parecer
                parecer = self.analista.avaliar(
                    instrumento=proposal.instrument, acao=proposal.action.value,
                    exposicao=proposal.desired_exposure_pct,
                    leitura=proposal.reason[:400], quando=utcnow().isoformat())
                nova, nota = aplicar_parecer(proposal.desired_exposure_pct, parecer)
                if nova <= 0:
                    report.vetoed.append((proposal.agent_id, f"IA vetou — {nota}"))
                    continue
                if nova < proposal.desired_exposure_pct:
                    from dataclasses import replace
                    proposal = replace(proposal, desired_exposure_pct=nova,
                                       reason=proposal.reason + f" | {nota}")
                elif not parecer.disponivel:
                    report.errors.append(nota)
            if self.conselheiro is not None and snap is not None:
                # Leitura quant consultiva: discordância só REDUZ (item 6);
                # disponível e de acordo não muda nada — conselho não comanda.
                parecer_quant = self.conselheiro.avaliar(proposal.instrument,
                                                         snap.closes)
                if parecer_quant.disponivel and parecer_quant.direcao == "vender":
                    from dataclasses import replace
                    proposta_original = proposal
                    proposal = replace(
                        proposta_original,
                        desired_exposure_pct=proposta_original.desired_exposure_pct
                        * Decimal("0.5"),
                        reason=proposta_original.reason
                        + f" | quant discorda: {parecer_quant.motivo}")
            price = prices.get(proposal.instrument)
            if price is None:
                report.vetoed.append((proposal.agent_id, "sem preço de execução"))
                continue
            # PROCEDÊNCIA: a ordem precisa provar de qual dado nasceu. Sem
            # isto o gate de capital recusa — e recusava, silenciosamente,
            # todo o caminho de produção.
            try:
                provenance = validar(build_state(
                    instrument=proposal.instrument,
                    cutoff=snap.cutoff if snap else None,
                    source=(snap.source if snap and getattr(snap, "source", None)
                            else "fixture"),
                    strategy=proposal.strategy, agent_id=proposal.agent_id,
                    worker_id=self.worker_id))
            except Exception as exc:
                report.vetoed.append(
                    (proposal.agent_id,
                     f"sem procedência verificável: {type(exc).__name__}: {exc}"[:200]))
                continue
            try:
                reservation = self.risk.reserve(proposal, price)
                intent = self.risk.intent(reservation, proposal.action,
                                          self.mandate.mode, self.worker_id,
                                          provenance=provenance)
                intent.assert_sendable(worker_id=self.worker_id, mandate=self.mandate)
            except (RiskVeto, MandateError) as exc:
                report.vetoed.append((proposal.agent_id, str(exc)))
                continue
            self.owner_by_instrument[proposal.instrument] = proposal.agent_id
            report.intents.append(intent)
        return report

    def _avisar_sala(self, texto: str) -> None:
        self.avisos_sala.append(texto[:200])
        del self.avisos_sala[:-20]

    def register_fill(self, instrument: str, quantity, price, agent_id: str,
                      cost_basis) -> None:
        """Chamado quando uma compra executa: a posição entra no livro."""
        self.book[instrument] = Position(instrument, quantity, price, agent_id,
                                         cost_basis, opened_at=utcnow())
        if self.sala is not None:
            trader = next((t for t in self.traders
                           if t.spec.agent_id == agent_id), None)
            leitura = getattr(trader, "ultima_leitura", None)
            try:
                self.sala.registrar_entrada(
                    instrument, quantity, price, agente=agent_id,
                    estrategia=(getattr(trader, "strategy", "") or agent_id),
                    versao=str(getattr(trader, "versao", "1")),
                    horizonte=str(getattr(trader, "timeframe", "1h")),
                    regime=(leitura.regime.value if leitura else "indefinido"))
            except Exception as exc:
                self._avisar_sala(
                    f"entrada {instrument} não registrada na sala: "
                    f"{type(exc).__name__}: {exc}")

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
                forca_do_sinal=(leitura.forca if leitura else 0.0),
                exchange=getattr(self, "exchange", "binance"),
                versao=str(getattr(trader, "versao", "1")),
                agente=pos.agent_id,
                horizonte=str(getattr(trader, "timeframe", "1h"))))
        if pos is not None and preco_saida is not None and self.sala is not None:
            try:
                self.sala.registrar_saida(instrument, preco_saida)
            except Exception as exc:
                self._avisar_sala(
                    f"saída {instrument} não registrada na sala: "
                    f"{type(exc).__name__}: {exc}")
        return pos

    # -- kill switch com semânticas distintas (cenário 13) -------------
    def pause_entries(self) -> None:
        """Para de abrir; posições e reservas seguem sob o guardião."""
        self._entries_paused = True

    def drain(self) -> list[str]:
        """Pede saída controlada: devolve os instrumentos a encerrar.
        Não finge que fechou — quem fecha é o executor com reconciliação."""
        return sorted(self.open_exposure())


__all__ = ["DeskOrchestrator", "CycleReport"]

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
from datetime import datetime, timezone
from decimal import Decimal

from .models import Action, DeskMandate, MandateError, OrderIntent, utcnow
from .provenance import build_state, validar
from .risk import PortfolioAllocator, RiskEngine, RiskVeto
from .saida import PoliticaDeSaida, avaliar_posicao, politica_por


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
    # POLÍTICA VERSIONADA da posição (motor global, saida.py): registrada na
    # entrada e IMUTÁVEL — endurecer é aceito e auditado; afrouxar é recusado.
    politica: PoliticaDeSaida | None = None
    versao_estrategia: str = "1"
    # Acompanhamento do gerente: topo (para trailing) e último preço visto.
    high_water: Decimal | None = None
    parcial_tomada: bool = False
    ultimo_preco: Decimal | None = None
    visto_em: datetime | None = None
    fonte: str = ""
    historico_politica: list = field(default_factory=list)
    quantidade_entrada: Decimal | None = None

    @property
    def stop_preco(self) -> Decimal | None:
        if self.politica is None or not self.entry_price:
            return None
        return self.entry_price * (Decimal("1") + self.politica.stop)

    @property
    def alvo_preco(self) -> Decimal | None:
        if self.politica is None or not self.entry_price:
            return None
        return self.entry_price * (Decimal("1") + self.politica.alvo)


@dataclass
class CycleReport:
    """O que aconteceu num ciclo — vira linha do painel Mesa de Agentes."""
    started_at: object = field(default_factory=utcnow)
    proposals: int = 0
    admitted: int = 0
    vetoed: list[tuple[str, str]] = field(default_factory=list)
    intents: list[OrderIntent] = field(default_factory=list)
    exits: list[tuple[str, str, str]] = field(default_factory=list)
    # Saídas ESTRUTURADAS e executáveis (motor global): fração, motivo
    # determinístico, urgência, procedência. `exits` continua sendo o
    # registro legível; `saidas` é o que o executor consome.
    saidas: list[dict] = field(default_factory=list)
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
                 supervisor=None, conselheiro=None, vigia=None, sala=None,
                 saidas_em_curso=None, evolucao=None):
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
        # Consulta do coordenador: já existe ordem de venda em curso para o
        # instrumento? Sem isto o ciclo autônomo emitiria saída DUPLICADA a
        # cada avaliação enquanto a primeira ainda não liquidou.
        self.saidas_em_curso = saidas_em_curso or (lambda instrument: False)
        # Sala da ESCOLA (BT-10): a evidência de operação vive por sala
        # (corretora/conta/mercado/modo). Espelho de registro: falha vira
        # aviso auditável e NÃO derruba a operação — a ordem já foi decidida
        # pelo risco; a sala é registro, não a corretora.
        self.sala = sala
        self.avisos_sala: list[str] = []
        # Evolução autônoma (E5): diário auditável por trader. Alimentado na
        # MESMA fonte do aprendizado (posição FECHADA) — falha vira aviso, a
        # evolução observa a execução, nunca a governa.
        self.evolucao = evolucao
        self.avisos_de_evolucao: list[str] = []

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

        # 1) ACOMPANHAR o que já está aberto (saída vem antes de entrada).
        # A decisão de saída passa pelo MOTOR GLOBAL (saida.py): política
        # versionada por posição, números determinísticos (stop/alvo/trailing/
        # tempo) e resultado LÍQUIDO. O trader opina depois — só sobre a tese.
        agora = utcnow()
        for snap in snapshots:
            pos = self.book.get(snap.instrument)
            if not pos:
                continue
            trader = next((t for t in self.traders
                           if t.spec.agent_id == pos.agent_id), None)
            if trader is None:
                motivo = "trader ausente — guardião assume"
                report.exits.append((pos.agent_id, snap.instrument, motivo))
                report.saidas.append(self._saida_estruturada(
                    snap, pos, motivo, "GUARDIAN", "alta"))
                continue
            if pos.politica is None:
                # Adoção explícita e auditada: posição sem política registrada
                # herda a do trader responsável (uma vez, nunca em silêncio).
                pos.politica = politica_por(trader)
                pos.versao_estrategia = str(getattr(trader, "versao", "1"))
                pos.historico_politica.append(
                    {"de": "", "para": pos.politica.chave,
                     "em": agora.astimezone(timezone.utc).isoformat()})
            if snap.last and snap.last > 0:
                topo = pos.high_water or pos.entry_price
                pos.high_water = snap.last if snap.last > topo else topo
                pos.ultimo_preco = snap.last
                pos.visto_em = snap.cutoff or agora
                pos.fonte = snap.source or ""
            velas = trader._velas_na_posicao(snap, pos)
            from .costs import custo_de_operar
            custo = custo_de_operar(snap.instrument, self.exchange,
                                    pos.cost_basis)
            decisao = avaliar_posicao(
                pos, snap.last, velas=velas, agora=snap.cutoff or agora,
                custo_ida_e_volta=custo.ida_e_volta, fonte=snap.source or "",
                saida_em_curso=bool(self.saidas_em_curso(snap.instrument)))
            if decisao.acao in ("CLOSE", "REDUCE"):
                report.exits.append((pos.agent_id, snap.instrument,
                                     decisao.motivo))
                report.saidas.append(self._saida_estruturada(
                    snap, pos, decisao.motivo, decisao.reason_code,
                    decisao.urgencia, fracao=decisao.fracao,
                    politica=decisao.politica))
                continue
            # Motor disse manter: a TESE da estratégia ainda pode morrer.
            call = trader.manage(snap, pos)
            if call.action is Action.REDUCE:
                report.exits.append((pos.agent_id, snap.instrument, call.reason))
                report.saidas.append(self._saida_estruturada(
                    snap, pos, call.reason, "THESIS_INVALIDATED", "alta"))

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

    def _saida_estruturada(self, snap, pos, motivo, reason_code, urgencia,
                           fracao=Decimal("1"), politica="") -> dict:
        """Saída executável: o executor (scheduler) consome este contrato."""
        seen = snap.cutoff or utcnow()
        return {"instrumento": snap.instrument, "agente": pos.agent_id,
                "fracao": str(fracao if fracao is not None else Decimal("1")),
                "motivo": motivo, "reason_code": reason_code,
                "urgencia": urgencia,
                "politica": politica or (pos.politica.chave if pos.politica else ""),
                "preco_referencia": str(snap.last) if snap.last else "",
                "fonte": snap.source or "",
                "visto_em": seen.isoformat()}

    def _avisar_sala(self, texto: str) -> None:
        self.avisos_sala.append(texto[:200])
        del self.avisos_sala[:-20]

    def register_fill(self, instrument: str, quantity, price, agent_id: str,
                      cost_basis) -> None:
        """Chamado quando uma compra executa: a posição entra no livro —
        já com a POLÍTICA VERSIONADA registrada (sem ela a gestão de saída
        não existe e o afrouxamento futuro não teria referência)."""
        trader = next((t for t in self.traders
                       if t.spec.agent_id == agent_id), None)
        politica = politica_por(trader) if trader is not None else None
        pos = Position(instrument, quantity, price, agent_id, cost_basis,
                       opened_at=utcnow(), politica=politica,
                       versao_estrategia=(str(getattr(trader, "versao", "1"))
                                          if trader is not None else "1"),
                       quantidade_entrada=quantity)
        if politica is not None:
            pos.historico_politica.append(
                {"de": "", "para": politica.chave,
                 "em": utcnow().astimezone(timezone.utc).isoformat()})
        self.book[instrument] = pos
        if self.sala is not None:
            leitura = getattr(trader, "ultima_leitura", None)
            try:
                self.sala.registrar_entrada(
                    instrument, quantity, price, agente=agent_id,
                    estrategia=(getattr(trader, "strategy", "") or agent_id),
                    versao=str(getattr(trader, "versao", "1")),
                    horizonte=str(getattr(trader, "timeframe", "1h")),
                    regime=(leitura.regime.value if leitura else "indefinido"),
                    politica=(politica.chave if politica else ""))
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
            desfecho = Desfecho(
                estrategia=getattr(trader, "strategy", pos.agent_id),
                regime=(leitura.regime.value if leitura else "indefinido"),
                instrumento=instrument, retorno=retorno,
                fechado_em=utcnow().astimezone(timezone.utc),
                forca_do_sinal=(leitura.forca if leitura else 0.0),
                exchange=getattr(self, "exchange", "binance"),
                versao=str(getattr(trader, "versao", "1")),
                agente=pos.agent_id,
                horizonte=str(getattr(trader, "timeframe", "1h")))
            self.memoria.registrar(desfecho)
            if self.evolucao is not None:
                try:
                    self.evolucao.registrar_operacao(
                        desfecho, decisao=f"encerrou {instrument} a {preco_saida}",
                        custo=custo.ida_e_volta)
                except Exception as exc:
                    self.avisos_de_evolucao.append(
                        f"diário da evolução não registrou {instrument}: "
                        f"{type(exc).__name__}: {exc}"[:200])
                    del self.avisos_de_evolucao[:-10]
        if pos is not None and preco_saida is not None and self.sala is not None:
            try:
                self.sala.registrar_saida(instrument, preco_saida)
            except Exception as exc:
                self._avisar_sala(
                    f"saída {instrument} não registrada na sala: "
                    f"{type(exc).__name__}: {exc}")
        return pos

    def registrar_saida_parcial(self, instrument: str, quantidade, preco,
                                *, tomou_parcial_no_alvo: bool = False
                                ) -> Position | None:
        """Redução parcial: o livro de gestão acompanha — quantidade, custo
        remanescente e a marca de parcial tomada (a política não realiza a
        mesma parcial duas vezes). Fechamento total delega a close_position."""
        pos = self.book.get(instrument)
        if pos is None:
            return None
        qtd = Decimal(str(quantidade))
        if qtd <= 0:
            return pos
        if qtd >= pos.quantity:
            return self.close_position(instrument, preco_saida=preco)
        frac = qtd / pos.quantity
        pos.cost_basis = pos.cost_basis * (Decimal("1") - frac)
        pos.quantity = pos.quantity - qtd
        if tomou_parcial_no_alvo:
            pos.parcial_tomada = True
        if self.sala is not None:
            try:
                self.sala.registrar_reducao(instrument, qtd, preco)
            except Exception as exc:
                self._avisar_sala(
                    f"redução {instrument} não registrada na sala: "
                    f"{type(exc).__name__}: {exc}")
        return pos

    # -- livro durável do gerente (M3/M4 + item 22) --------------------
    def livro_doc(self) -> dict:
        """Estado durável do LIVRO DE GESTÃO. Sem isto, reiniciar zerava
        política/topo/parcial e as posições seguiam abertas sem saída
        automática — o defeito que a auditoria do item 22 expôs."""
        return {"schema": "dz23.livro/1",
                "posicoes": {
                    inst: {
                        "instrumento": p.instrument,
                        "quantidade": str(p.quantity),
                        "quantidade_entrada": str(
                            p.quantidade_entrada if p.quantidade_entrada
                            is not None else p.quantity),
                        "preco_entrada": str(p.entry_price),
                        "agente": p.agent_id,
                        "custo": str(p.cost_basis),
                        "aberto_em": (p.opened_at.isoformat()
                                      if p.opened_at else None),
                        "versao_estrategia": p.versao_estrategia,
                        "high_water": (str(p.high_water)
                                       if p.high_water is not None else None),
                        "parcial_tomada": bool(p.parcial_tomada),
                        "politica": (p.politica.doc() if p.politica else None),
                    } for inst, p in self.book.items()}}

    def restaurar_livro(self, doc: dict | None, ledger=None) -> dict:
        """Reinício: reconstrói a gestão e CONFRONTA com o livro financeiro.

        O financeiro MANDA: quantidade do diário que não tem lastro no livro
        não vira venda; posição viva no financeiro sem gestão registrada é
        ADOTADA na hora — nunca fica descoberta de política.
        """
        avisos: list[str] = []
        adotadas: list[str] = []
        descartadas: list[str] = []
        if doc and doc.get("schema") == "dz23.livro/1":
            for inst, pd in (doc.get("posicoes") or {}).items():
                try:
                    qtd = Decimal(str(pd.get("quantidade")))
                    entrada = Decimal(str(pd.get("preco_entrada")))
                except (ArithmeticError, TypeError):
                    avisos.append(f"{inst}: documento de posição ilegível "
                                  "— descartado")
                    continue
                agente = str(pd.get("agente") or "")
                if ledger is not None and agente:
                    lote = ledger.lot_quantity(inst, agente)
                    if lote <= 0:
                        avisos.append(f"{inst}: diário sem lote vivo no "
                                      "livro financeiro — entrada descartada")
                        descartadas.append(inst)
                        continue
                    if lote != qtd:
                        avisos.append(f"{inst}: diário {qtd} vs financeiro "
                                      f"{lote} — vale o financeiro")
                        qtd = lote
                if qtd <= 0 or entrada <= 0:
                    avisos.append(f"{inst}: posição inválida no diário "
                                  "— descartada")
                    descartadas.append(inst)
                    continue
                aberto_em = None
                if pd.get("aberto_em"):
                    try:
                        aberto_em = datetime.fromisoformat(str(pd["aberto_em"]))
                    except ValueError:
                        aberto_em = None
                custo = Decimal(str(pd.get("custo") or "0"))
                if custo <= 0:
                    custo = qtd * entrada
                hw = pd.get("high_water")
                self.book[inst] = Position(
                    inst, qtd, entrada, agente, custo, opened_at=aberto_em,
                    politica=PoliticaDeSaida.from_doc(pd.get("politica") or {}),
                    versao_estrategia=str(pd.get("versao_estrategia") or "1"),
                    high_water=(Decimal(str(hw)) if hw is not None else None),
                    parcial_tomada=bool(pd.get("parcial_tomada")),
                    quantidade_entrada=(
                        Decimal(str(pd["quantidade_entrada"]))
                        if pd.get("quantidade_entrada") is not None else qtd))
        # Posição viva no financeiro SEM gestão: adota com política do trader.
        if ledger is not None:
            for inst in sorted(ledger.open_positions()):
                if inst in self.book:
                    continue
                agente = ledger.position_owner(inst)
                qtd = ledger.lot_quantity(inst, agente)
                custo = ledger.lot_cost(inst, agente)
                if qtd <= 0:
                    continue
                entrada = (custo / qtd) if custo > 0 else Decimal("0")
                trader = next((t for t in self.traders
                               if t.spec.agent_id == agente), None)
                politica = politica_por(trader) if trader is not None else None
                pos = Position(inst, qtd, entrada, agente, custo,
                               opened_at=None, politica=politica,
                               versao_estrategia=(str(getattr(trader, "versao", "1"))
                                                  if trader is not None else "1"),
                               quantidade_entrada=qtd)
                if politica is not None:
                    pos.historico_politica.append(
                        {"de": "", "para": politica.chave,
                         "em": utcnow().astimezone(timezone.utc).isoformat()})
                self.book[inst] = pos
                adotadas.append(inst)
                avisos.append(f"{inst}: posição do livro financeiro adotada "
                              "pelo gerente de saída (política padrão do trader)")
        for aviso in avisos[-20:]:
            self._avisar_sala(aviso)
        return {"restaurado": bool(doc and doc.get("schema") == "dz23.livro/1"),
                "posicoes": sorted(self.book), "adotadas": adotadas,
                "descartadas": descartadas, "avisos": avisos}

    def livro_para_painel(self) -> list[dict]:
        """O que o dono precisa ver de cada posição: entrada, stop, alvo,
        líquido estimado, trailing, horizonte, tempo e política — números
        do gerente, não promessas."""
        from .costs import custo_de_operar
        minutos = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60,
                   "4h": 240, "1d": 1440}
        agora = utcnow()
        saida = []
        for p in self.book.values():
            pol = p.politica
            custo = None
            try:
                custo = custo_de_operar(p.instrument, self.exchange, p.cost_basis)
            except Exception:
                custo = None
            bruto = liquido = None
            if p.ultimo_preco is not None and p.entry_price:
                bruto = (p.ultimo_preco - p.entry_price) / p.entry_price
                liquido = (bruto - custo.ida_e_volta) if custo else bruto
            velas = ""
            if p.opened_at is not None and pol is not None:
                janela = minutos.get(pol.horizonte, 60)
                segundos = (agora - p.opened_at).total_seconds()
                velas = max(0, int(segundos // (janela * 60)))
            saida.append({
                "instrumento": p.instrument, "agente": p.agent_id,
                "quantidade": str(p.quantity),
                "quantidade_entrada": str(
                    p.quantidade_entrada if p.quantidade_entrada
                    is not None else p.quantity),
                "preco_entrada": str(p.entry_price),
                "custo": str(p.cost_basis),
                "aberto_em": p.opened_at.isoformat() if p.opened_at else None,
                "horizonte": pol.horizonte if pol else "",
                "politica": pol.chave if pol else "",
                "stop_pct": str(pol.stop) if pol else "",
                "alvo_pct": str(pol.alvo) if pol else "",
                "stop": (str(p.stop_preco) if p.stop_preco is not None else ""),
                "alvo": (str(p.alvo_preco) if p.alvo_preco is not None else ""),
                "trailing_pct": (str(pol.trailing_pct)
                                 if pol and pol.trailing_pct is not None else ""),
                "parcial_no_alvo": (str(pol.parcial_no_alvo)
                                    if pol and pol.parcial_no_alvo is not None
                                    else ""),
                "parcial_tomada": bool(p.parcial_tomada),
                "high_water": (str(p.high_water)
                               if p.high_water is not None else ""),
                "ultimo_preco": (str(p.ultimo_preco)
                                 if p.ultimo_preco is not None else ""),
                "visto_em": p.visto_em.isoformat() if p.visto_em else "",
                "fonte": p.fonte,
                "velas_na_posicao": velas,
                "lucro_bruto_pct": (f"{bruto:.4%}" if bruto is not None else ""),
                "lucro_liquido_pct": (f"{liquido:.4%}"
                                      if liquido is not None else ""),
            })
        return saida

    # -- kill switch com semânticas distintas (cenário 13) -------------
    def pause_entries(self) -> None:
        """Para de abrir; posições e reservas seguem sob o guardião."""
        self._entries_paused = True

    def drain(self) -> list[str]:
        """Pede saída controlada: devolve os instrumentos a encerrar.
        Não finge que fechou — quem fecha é o executor com reconciliação."""
        return sorted(self.open_exposure())


__all__ = ["DeskOrchestrator", "CycleReport"]

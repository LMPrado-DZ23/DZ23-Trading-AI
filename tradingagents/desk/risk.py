"""Arbitragem de carteira e risco determinístico (MA-05).

O RiskEngine é código puro com poder de veto: nenhum LLM aumenta limite,
aprova a própria reserva ou altera modo. Reserva é transacional — o mesmo
capital não pode ser prometido duas vezes.
"""
from __future__ import annotations

import threading
import uuid
from decimal import ROUND_DOWN, Decimal

from .models import (
    Action,
    DeskMandate,
    MandateError,
    OrderIntent,
    PortfolioDecision,
    RiskReservation,
    TradeProposal,
    default_expiry,
    utcnow,
)
from .sizing import limite_de_correlacao


def _instante_de(valor):
    """Converte para datetime com fuso — comparar strings ISO é enganoso.

    "2026-09-14T20:00:00-03:00" e "2026-09-14T23:00:00+00:00" são o MESMO
    instante, mas a ordem textual diz o contrário. Data inválida falha
    fechado: melhor recusar que decidir sobre dinheiro com data inventada.
    """
    from datetime import datetime, timezone
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(valor))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"instante inválido: {valor!r}") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# O armazenamento não oferece commit conjunto de Ledger e Risk: são duas
# escritas separadas, e um crash pode cair entre elas. É exatamente por isso
# que existe protocolo de recuperação — afirmar atomicidade de um banco que
# não temos seria pior que admitir a limitação.
ATOMIC_STORAGE_COMMIT = "NOT_AVAILABLE"


class RecoveryConflict(RuntimeError):
    """As duas visões discordam sobre qual liquidação pagou o incidente.

    Escolher uma arbitrariamente pode pagar duas vezes ou dar por paga uma
    dívida que não foi. Parar é a única saída segura.
    """


class IdempotencyConflict(RuntimeError):
    """Mesmo identificador de liquidação com conteúdo diferente."""


class RiskVeto(RuntimeError):
    """Recusa determinística — não negociável por agente algum."""


class PortfolioAllocator:
    """Arbitra propostas concorrentes antes de qualquer reserva."""

    policy = "dz23.allocator/1"

    def arbitrate(self, proposals, *, open_positions=(), owner_by_instrument=None,
                  exposicoes: dict | None = None) -> PortfolioDecision:
        owner_by_instrument = owner_by_instrument or {}
        exposicoes = dict(exposicoes) if exposicoes is not None else {}
        admitted, vetoed, claimed = [], [], {}
        for p in sorted(proposals, key=lambda x: (-x.confidence, x.agent_id)):
            if not p.is_actionable():
                vetoed.append((p, f"não acionável ({p.action.value})"))
                continue
            owner = owner_by_instrument.get(p.instrument)
            if owner and owner != p.agent_id:
                # Um dono operacional por conta/instrumento na v1.
                vetoed.append((p, f"{p.instrument} já é operado por {owner}"))
                continue
            if p.instrument in claimed:
                winner = claimed[p.instrument]
                if winner.action != p.action:
                    # Nunca abrir compra e venda por discordância de agentes.
                    vetoed.append((p, f"conflito com {winner.agent_id}: direção oposta vetada"))
                else:
                    vetoed.append((p, f"{winner.agent_id} venceu a disputa por confiança"))
                continue
            if p.instrument in open_positions:
                vetoed.append((p, "já existe posição aberta neste instrumento"))
                continue
            # CORRELAÇÃO: BTC e ETH andam juntos. Comprar os dois não é
            # diversificar — é dobrar a mesma aposta, e perder o dobro junto.
            if exposicoes:
                cabe, motivo = limite_de_correlacao(
                    p.instrument, p.desired_exposure_pct, exposicoes)
                if not cabe:
                    vetoed.append((p, motivo))
                    continue
                exposicoes[p.instrument] = p.desired_exposure_pct
            claimed[p.instrument] = p
            admitted.append(p)
        return PortfolioDecision(tuple(admitted), tuple(vetoed), self.policy)


class RiskEngine:
    """Calcula quantidade, aplica limites e trava capital de forma atômica."""

    def __init__(self, mandate: DeskMandate, carteira=None):
        """`carteira` é o Ledger — a AUTORIDADE econômica.

        Sem ele o risco só conhece reservas e usa o capital do mandato como
        referência, o que ignora o dinheiro já transformado em posição. O
        risco aplica política; não mantém uma segunda contabilidade.
        """
        self.mandate = mandate
        self.carteira = carteira
        self._lock = threading.Lock()
        self._reservations: dict[str, RiskReservation] = {}
        self._concluidas: dict[str, dict] = {}
        self._deficits: dict[str, Decimal] = {}
        self._incidentes: dict[str, dict] = {}
        self._provas_usadas: dict[str, str] = {}
        self._settlements: dict[str, dict] = {}
        self._lock_settlement = threading.RLock()

    # -- consultas -----------------------------------------------------
    @property
    def reserved_total(self) -> Decimal:
        # O que ainda está travado é o RESTANTE, não o valor original.
        return sum((r.remaining_amount for r in self._reservations.values()),
                   Decimal("0"))

    @property
    def available(self) -> Decimal:
        """Capital que pode ser alocado AGORA.

        Antes era `capital - reservas`: depois do fill terminal a reserva
        some e os mesmos 250 voltavam a parecer livres, com a posição ainda
        aberta — o mesmo dinheiro podia ser alocado indefinidamente. Agora o
        caixa vem do livro, que já desconta o que virou posição.

        O capital do mandato continua valendo como TETO de política: lucro
        realizado não amplia sozinho o limite estrutural da mesa.
        """
        if self.carteira is None:
            return self.mandate.capital - self.reserved_total
        caixa = self.carteira.cash_available()
        return caixa - self.reserved_total + self._reservado_no_livro()

    def _reservado_no_livro(self) -> Decimal:
        """O livro já move o reservado para fora do caixa; sem isto a reserva
        seria descontada duas vezes."""
        if self.carteira is None:
            return Decimal("0")
        return self.carteira.reserved_cash()

    def attach_portfolio_ledger(self, carteira) -> None:
        """Religa o risco à carteira econômica, validando o contrato.

        Setter livre deixaria passar um objeto sem a visão de capital — e o
        erro só apareceria como número errado, não como falha.
        """
        for metodo in ("cash_available", "reserved_cash", "invested_cost",
                       "open_positions_detail"):
            if not callable(getattr(carteira, metodo, None)):
                raise TypeError(
                    f"carteira sem {metodo}(): não serve como fonte econômica")
        self.carteira = carteira

    def invested_cost(self) -> Decimal:
        if self.carteira is None:
            return Decimal("0")
        return self.carteira.invested_cost()

    def gross_exposure(self) -> Decimal:
        """Exposição bruta: posição aberta + reservas ainda executáveis.

        Uma posição totalmente preenchida não some do risco só porque a
        reserva dela acabou.
        """
        return self.invested_cost() + self.reserved_total

    def open_instruments(self) -> set[str]:
        """Instrumentos com exposição viva: reservas E posições abertas.

        Derivar só das reservas fazia o instrumento sumir da lista assim que
        o fill terminava — e a política de 'uma entrada por instrumento'
        deixava de valer exatamente quando havia posição de verdade.
        """
        abertos = {r.instrument for r in self._reservations.values()}
        if self.carteira is not None:
            abertos |= {i for i, d in self.carteira.open_positions_detail().items()
                        if Decimal(str(d["quantidade"])) > 0}
        return abertos

    # -- reserva -------------------------------------------------------
    def reserve(self, proposal: TradeProposal, price: Decimal, *,
                agent_id: str | None = None) -> RiskReservation:
        if price <= 0 or not price.is_finite():
            raise RiskVeto("preço inválido")
        self.mandate.assert_active(instrument=proposal.instrument)
        if proposal.action is Action.SELL:
            raise RiskVeto("venda a descoberto não existe em spot; use redução de posição")
        pct = min(proposal.desired_exposure_pct, self.mandate.max_position_pct)
        if pct <= 0:
            raise RiskVeto("exposição desejada nula")
        with self._lock:  # atômico: duas propostas não travam o mesmo capital
            # Posições ABERTAS contam para o limite: uma posição preenchida
            # sem reserva continua sendo uma posição.
            if len(self.open_instruments()) >= self.mandate.max_open_positions:
                raise RiskVeto("limite de posições simultâneas atingido")
            if proposal.instrument in self.open_instruments():
                raise RiskVeto(f"já há reserva ativa para {proposal.instrument}")
            amount = (self.mandate.capital * pct).quantize(Decimal("0.01"), ROUND_DOWN)
            if amount <= 0:
                raise RiskVeto("valor calculado nulo")
            # COBERTURA DURA = alvo + folga. A folga precisa caber no caixa
            # agora: reservar só o alvo torcendo para os centavos aparecerem
            # depois é o que deixava a folga a descoberto.
            folga = (amount * getattr(self.mandate, "slippage_buffer_pct",
                                      Decimal("0"))).quantize(
                Decimal("0.00000001"))
            dura = amount + folga
            if dura > self.available:
                raise RiskVeto(
                    f"capital insuficiente: cobertura necessária {dura} "
                    f"(alvo {amount} + folga {folga}), disponível {self.available}")
            # A quantidade sai do ALVO: folga não aumenta o que se negocia.
            qty = (amount / price).quantize(Decimal("0.00000001"), ROUND_DOWN)
            if qty <= 0:
                raise RiskVeto("quantidade abaixo do mínimo negociável")
            res = RiskReservation(
                reservation_id=uuid.uuid4().hex[:16], account=self.mandate.account,
                instrument=proposal.instrument, amount=amount, quantity=qty,
                buffer_amount=folga,
                mandate_version=self.mandate.version, expires_at=default_expiry(10),
                agent_id=agent_id or proposal.agent_id)
            self._reservations[res.reservation_id] = res
            return res

    def reserve_exato(self, *, instrument: str, alvo: Decimal,
                      agent_id: str = "") -> RiskReservation:
        """Reserva um notional explícito — usada quando o alvo não vem de
        percentual do mandato. Aplica a MESMA regra de cobertura dura."""
        with self._lock:
            folga = (alvo * getattr(self.mandate, "slippage_buffer_pct",
                                    Decimal("0"))).quantize(Decimal("0.00000001"))
            if alvo + folga > self.available:
                raise RiskVeto(
                    f"capital insuficiente: cobertura necessária {alvo + folga}, "
                    f"disponível {self.available}")
            res = RiskReservation(
                reservation_id=uuid.uuid4().hex[:16],
                account=self.mandate.account, instrument=instrument,
                amount=alvo, quantity=Decimal("1"), buffer_amount=folga,
                mandate_version=self.mandate.version,
                expires_at=default_expiry(10), agent_id=agent_id)
            self._reservations[res.reservation_id] = res
            return res

    def consume(self, reservation_id: str, amount: Decimal,
                quantidade: Decimal | None = None) -> Decimal:
        """Consome PARTE de uma reserva, devolvendo o saldo restante.

        O livro consumia a reserva proporcionalmente a cada fill enquanto o
        risco mantinha o valor integral até o terminal: entre o primeiro
        parcial e o último, `reserved_total` e `available` mentiam — e é
        `available` que decide se a próxima ordem cabe.

        Consumir MAIS que o reservado é recusado: seria admitir que a ordem
        executou além da cobertura, o que só pode ser erro de contabilidade.
        """
        with self._lock:
            reserva = self._reservations.get(reservation_id)
            if reserva is None:
                raise RiskVeto(
                    f"reserva {reservation_id} não está viva: nada a consumir")
            valor = Decimal(str(amount))
            if valor <= 0:
                raise RiskVeto("consumo precisa ser positivo")
            if valor > reserva.remaining_amount:
                raise RiskVeto(
                    f"consumo de {valor} excede o restante da reserva "
                    f"({reserva.remaining_amount})")
            qtd = Decimal(str(quantidade)) if quantidade is not None else None
            if qtd is not None:
                if qtd <= 0:
                    raise RiskVeto("quantidade consumida precisa ser positiva")
                if reserva.filled_quantity + qtd > reserva.original_quantity:
                    raise RiskVeto(
                        f"quantidade {qtd} excede a quantidade restante da "
                        f"reserva ({reserva.remaining_quantity})")
            # O ORIGINAL nunca é tocado: só os acumulados crescem. Valor e
            # quantidade avançam JUNTOS, no mesmo lock — estado parcial entre
            # os dois deixaria a cobertura inconsistente.
            from dataclasses import replace
            consumido = reserva.consumed_amount + valor
            preenchida = reserva.filled_quantity + (qtd or Decimal("0"))
            atualizada = replace(reserva, consumed_amount=consumido,
                                 filled_quantity=preenchida)
            if atualizada.remaining_amount <= 0:
                del self._reservations[reservation_id]
                return Decimal("0")
            self._reservations[reservation_id] = atualizada
            return atualizada.remaining_amount

    # ------------------------------------------------ custo real vs cobertura
    def registrar_execucao(self, reservation_id: str, *, custo: Decimal,
                           quantidade: Decimal, terminal: bool) -> dict:
        """Confronta o CUSTO REAL com a cobertura e conclui a reserva.

        Consumir proporcionalmente à quantidade estima a alocação; não prova
        cobertura. O custo real é quantidade × preço executado + taxa, e com
        slippage ele pode passar do reservado. Quando passa, a execução NÃO
        é desfeita — a corretora já executou — mas o déficit é registrado e o
        risco novo é travado.
        """
        with self._lock:
            reserva = self._reservations.get(reservation_id)
            if reserva is None:
                # Reserva já concluída: um custo extra aqui é déficit puro.
                self.abrir_incidente(
                    "RESERVATION_DEFICIT", amount=custo,
                    reservation_id=reservation_id,
                    evidence="execução sobre reserva já concluída")
                return {"estado": "DEFICIT", "deficit": str(custo),
                        "sobra": "0"}

            # A folga JÁ está dentro de remaining_amount, porque foi
            # separada no caixa na hora da reserva. Somá-la de novo aqui
            # autorizaria gasto não financiado.
            limite = reserva.remaining_amount
            deficit = custo - limite if custo > limite else Decimal("0")
            consumido = min(custo, limite)

            from dataclasses import replace
            atualizada = replace(
                reserva,
                consumed_amount=reserva.consumed_amount + consumido,
                filled_quantity=reserva.filled_quantity + quantidade)
            if deficit > 0:
                self.abrir_incidente(
                    "RESERVATION_DEFICIT", amount=deficit,
                    reservation_id=reservation_id,
                    evidence=f"execução custou {custo} contra cobertura {limite}")

            if terminal or atualizada.remaining_quantity <= 0:
                sobra = atualizada.remaining_amount
                # Conclusão fica registrada: apagar a reserva sem deixar
                # rastro tiraria a única evidência do consumo terminal.
                self._concluidas[reservation_id] = {
                    "reservation_id": reservation_id,
                    "estado": "DEFICIT" if deficit > 0 else (
                        "CONSUMED" if sobra <= 0 else "RELEASED"),
                    "original_amount": str(reserva.original_amount),
                    "consumed_amount": str(atualizada.consumed_amount),
                    "sobra_liberada": str(max(sobra, Decimal("0"))),
                    "original_quantity": str(reserva.original_quantity),
                    "filled_quantity": str(atualizada.filled_quantity),
                    "deficit": str(deficit),
                }
                del self._reservations[reservation_id]
                return {"estado": self._concluidas[reservation_id]["estado"],
                        "deficit": str(deficit), "sobra": str(max(sobra, Decimal("0")))}

            self._reservations[reservation_id] = atualizada
            return {"estado": "PARTIALLY_CONSUMED", "deficit": str(deficit),
                    "sobra": "0"}

    def incidentes_doc(self) -> dict:
        """Incidentes para persistência — reiniciar não resolve déficit."""
        # `deficits` NÃO é mais escrito: o incidente é a única fonte. O
        # leitor continua aceitando o campo antigo para migrar snapshots.
        return {"conclusoes": self._concluidas,
                "provas_usadas": self._provas_usadas,
                "settlements": self._settlements,
                # Reiniciar não é resolução.
                "incidentes": self._incidentes}

    def restaurar_incidentes(self, doc: dict | None) -> None:
        if not doc:
            return

        self._concluidas.update(doc.get("conclusoes") or {})
        self._incidentes.update(doc.get("incidentes") or {})
        self._provas_usadas.update(doc.get("provas_usadas") or {})
        self._settlements.update(doc.get("settlements") or {})
        # MIGRAÇÃO: snapshot antigo guardava déficit em `deficits`, sem
        # incidente. Ignorar significaria perder a violação no reinício —
        # justamente o tipo de esquecimento que esta rodada veio corrigir.
        for rid, valor in (doc.get("deficits") or {}).items():
            chave = f"RESERVATION_DEFICIT:{rid}"
            if chave in self._incidentes:
                continue        # já convertido: não duplica
            self.abrir_incidente(
                "RESERVATION_DEFICIT", amount=Decimal(str(valor)),
                reservation_id=rid,
                evidence="déficit migrado de snapshot anterior ao modelo de "
                         "incidentes")

    def terminalizar(self, reservation_id: str, *, motivo: str) -> Decimal:
        """Encerra a reserva e devolve QUANTO deve voltar ao caixa.

        Atômico: dois eventos de cancelamento simultâneos não podem devolver
        o dinheiro duas vezes. Quem chega depois recebe zero.
        """
        with self._lock:
            reserva = self._reservations.pop(reservation_id, None)
            if reserva is None:
                return Decimal("0")     # já terminalizada: no-op seguro
            restante = reserva.remaining_amount
            self._concluidas[reservation_id] = {
                "reservation_id": reservation_id,
                "estado": "RELEASED",
                "motivo": motivo[:200],
                "original_amount": str(reserva.original_amount),
                "hard_reserved_amount": str(reserva.hard_reserved_amount),
                "consumed_amount": str(reserva.consumed_amount),
                "sobra_liberada": str(max(restante, Decimal("0"))),
                "original_quantity": str(reserva.original_quantity),
                "filled_quantity": str(reserva.filled_quantity),
                "deficit": "0",
            }
            return max(restante, Decimal("0"))

    # ============================================ incidentes financeiros
    # Conclusão responde "como a reserva terminou?"; incidente responde "há
    # violação financeira ainda não resolvida?". Gravar a divergência apenas
    # na conclusão fazia a violação sumir da decisão de risco assim que a
    # reserva era terminalizada.
    def abrir_incidente(self, tipo: str, *, amount: Decimal,
                        reservation_id: str = "", order_id: str = "",
                        evidence: str = "") -> str:
        import uuid as _uuid
        chave = f"{tipo}:{reservation_id or order_id}"
        ocorrencia = {"amount": str(amount), "order_id": order_id,
                      "reservation_id": reservation_id,
                      "at": utcnow().isoformat(), "evidence": evidence}
        existente = self._incidentes.get(chave)
        if existente and existente["status"] == "OPEN":
            # Mesma violação econômica: soma o total e GUARDA a origem de
            # cada excesso. Só somar perderia de onde veio cada parcela.
            existente["amount"] = str(Decimal(existente["amount"]) + amount)
            existente.setdefault("occurrences", []).append(ocorrencia)
            return existente["incident_id"]
        inc = {
            "incident_id": "inc_" + _uuid.uuid4().hex[:12],
            "type": tipo,
            "reservation_id": reservation_id,
            "order_id": order_id,
            "amount": str(amount),
            "currency": getattr(self.carteira, "currency", "USDT"),
            "opened_at": utcnow().isoformat(),
            "evidence": evidence,
            "status": "OPEN",
            "resolved_at": "", "resolved_by": "", "resolution_evidence": "",
            "resolution_refs": {}, "validated_by_policy": False,
            "policy_version": "",
            "occurrences": [ocorrencia],
        }
        self._incidentes[chave] = inc
        return inc["incident_id"]

    def incidentes(self) -> list[dict]:
        return list(self._incidentes.values())

    def incidentes_abertos(self) -> list[dict]:
        # COMPENSATING e ACKNOWLEDGED_BLOCKING ainda são violação viva: só
        # RESOLVED deixa de bloquear.
        return [i for i in self._incidentes.values()
                if i["status"] in self.ESTADOS_BLOQUEANTES]

    CAPACIDADE_RESOLVER = "RESOLVE_RISK_INCIDENT"

    POLICY_VERSION = "dz23.resolucao/1"

    def resolve_risk_incident(self, incident_id: str, *, actor,
                              evidence: str, reason: str = "",
                              resolution_refs: dict | None = None) -> dict:
        """Fecha um incidente: autorização real + evidência + estado corrigido.

        Saldos voltarem a bater sozinhos não resolve nada, e evidência
        textual é trilha de auditoria — não prova de conserto. A ordem é:
        corrigir o estado, verificar invariantes, registrar evidência,
        resolver.
        """
        # AUTORIZAÇÃO: o NOME do ator não concede poder. Quem pode encerrar
        # uma violação financeira precisa portar a capacidade.
        capacidades = getattr(actor, "capabilities", None)
        actor_id = getattr(actor, "actor_id", None)
        if capacidades is None or not actor_id:
            raise PermissionError(
                "resolução exige um ator autenticado com capacidades — uma "
                "string de nome não identifica ninguém")
        if self.CAPACIDADE_RESOLVER not in capacidades:
            raise PermissionError(
                f"{actor_id} não possui {self.CAPACIDADE_RESOLVER}")
        if not evidence or not evidence.strip():
            raise ValueError(
                "resolução exige evidência: sem ela não há como auditar por "
                "que a violação deixou de valer")

        # PROVA: cada tipo de incidente define o que aceita como conserto.
        # As invariantes gerais de saldo não servem sozinhas — depois de um
        # déficit elas já estão verdes (o dinheiro saiu do caixa e a
        # aritmética fecha), então bastaria uma frase para destravar a mesa.
        refs = dict(resolution_refs or {})
        alvo = next((i for i in self._incidentes.values()
                     if i["incident_id"] == incident_id), None)
        if alvo is not None and alvo["status"] in ("OPEN",
                                                   "ACKNOWLEDGED_BLOCKING"):
            # Reconhecido TAMBÉM resolve depois de corrigido: sem isto, quem
            # registrou a decisão ficava preso no estado para sempre.
            self.validate_incident_resolution(alvo, refs)
        for inc in self._incidentes.values():
            if inc["incident_id"] != incident_id:
                continue
            if inc["status"] not in ("OPEN", "ACKNOWLEDGED_BLOCKING"):
                return inc
            # O histórico NUNCA é apagado: o registro muda de status.
            inc["status"] = "RESOLVED"
            inc["resolved_at"] = utcnow().isoformat()
            inc["resolved_by"] = actor_id
            inc["resolution_evidence"] = f"{evidence.strip()} | {reason}".strip(" |")
            inc["resolution_refs"] = refs
            if inc["type"] == "UNCOVERED_ASSET_BALANCE":
                # Guarda o descoberto REAL no instante da resolução — não o
                # valor do incidente. Se o ativo foi aportado, ele é zero, e
                # qualquer descoberto posterior é fato novo. Gravar o valor
                # antigo faria um descoberto menor passar despercebido.
                atual = self.ativos_descobertos().get(inc.get("fee_currency"),
                                                      Decimal("0"))
                inc["saldo_na_resolucao"] = str(atual)
            inc["validated_by_policy"] = True
            inc["policy_version"] = self.POLICY_VERSION
            # A prova econômica é consumida: uma compensação de 10 não paga
            # duas dívidas de 10.
            for tx_id in refs.get("ledger_tx_ids", []):
                self._provas_usadas[tx_id] = incident_id
            return inc
        raise ValueError(f"incidente {incident_id} não encontrado")

    def settle_risk_incident(self, incident_id: str, *, actor,
                             amount: Decimal, external_reference: str,
                             evidence: str, reason: str = "",
                             settlement_id: str = "") -> dict:
        """Compensa e resolve como UMA transição serializada.

        Antes a sequência era: ler OPEN, lançar no livro, marcar RESOLVED —
        sem nada serializando. Duas chamadas simultâneas criavam DUAS
        compensações para a mesma dívida, e um crash entre o lançamento e a
        resolução fazia o retry lançar de novo.

        Máquina de estados: OPEN -> COMPENSATING -> RESOLVED, tudo sob o
        mesmo lock, com idempotência DURÁVEL por settlement_id — lock só
        resolve concorrência dentro do processo; crash exige identidade que
        sobreviva ao reinício.
        """
        import uuid as _uuid
        capacidades = getattr(actor, "capabilities", None)
        actor_id = getattr(actor, "actor_id", None)
        if capacidades is None or not actor_id:
            raise PermissionError(
                "liquidação exige um ator autenticado com capacidades")
        if self.CAPACIDADE_RESOLVER not in capacidades:
            raise PermissionError(
                f"{actor_id} não possui {self.CAPACIDADE_RESOLVER}")
        if self.carteira is None:
            raise ValueError("sem carteira não há como registrar a compensação")

        stl = settlement_id or f"stl_{_uuid.uuid4().hex[:12]}"

        with self._lock_settlement:
            alvo = next((i for i in self._incidentes.values()
                         if i["incident_id"] == incident_id), None)
            if alvo is None:
                raise ValueError(f"incidente {incident_id} não encontrado")

            registro = self._settlements.get(stl)
            if registro:
                # Registro sem valor veio da marcação de recuperação (crash
                # antes do lançamento): ele não conflita, é o mesmo trabalho
                # sendo retomado.
                pendente = Decimal(registro["amount"]) == 0
                if registro["incident_id"] != incident_id or (
                        not pendente and Decimal(registro["amount"]) != amount):
                    raise IdempotencyConflict(
                        f"liquidação {stl} já existe com outro conteúdo "
                        f"(incidente {registro['incident_id']}, valor "
                        f"{registro['amount']})")
                if registro["status"] == "RESOLVED":
                    return alvo          # retry idempotente: nada se move

            if alvo["status"] == "RESOLVED":
                return alvo

            devido = Decimal(alvo["amount"])
            if amount != devido:
                # Aporte a mais é outra coisa: CAPITAL_CONTRIBUTION separado.
                # Misturar os dois faria a compensação parecer maior do que a
                # dívida que ela quita.
                raise ValueError(
                    f"a liquidação compensa exatamente o devido ({devido}); "
                    f"recebido {amount}. Para aportar a mais, registre uma "
                    "contribuição de capital separada")

            atual = self._settlements.setdefault(stl, {
                "settlement_id": stl, "incident_id": incident_id,
                "amount": str(amount), "status": "COMPENSATING",
                "compensation_tx_id": "",
                "started_at": utcnow().isoformat(), "completed_at": ""})
            atual["amount"] = str(amount)     # retomada fixa o valor real
            atual["status"] = "COMPENSATING"
            alvo["status"] = "COMPENSATING"

            # RECUPERAÇÃO por incident_id E settlement_id.
            minha = self.carteira.find_incident_compensation(incident_id, stl)
            if minha is not None:
                # Mesma liquidação: o dinheiro já saiu, adota-se.
                tx_id = minha["tx_id"]
            elif self.carteira.find_incident_compensations(incident_id):
                # Existe pagamento para este incidente com OUTRA identidade.
                # Nem adotar (renomearia a linhagem) nem lançar de novo
                # (pagaria duas vezes): o portão fica fechado.
                outras = [t.get("settlement_id") or "(sem id)"
                          for t in self.carteira.find_incident_compensations(
                              incident_id)]
                raise RecoveryConflict(
                    f"o incidente {incident_id} já tem compensação da "
                    f"liquidação {', '.join(outras)}, e esta é {stl}: "
                    "recupere a liquidação original em vez de criar outra")
            else:
                tx_id = self.carteira.risk_incident_compensation(
                    incident_id=incident_id, amount=amount,
                    external_reference=external_reference, actor=actor_id,
                    reason=reason, settlement_id=stl).tx_id
            self._settlements[stl]["compensation_tx_id"] = tx_id

            resolvido = self._finalize_compensating_incident(
                alvo, actor_id=actor_id, evidence=evidence, reason=reason,
                tx_id=tx_id, settlement_id=stl)
            self._settlements[stl]["status"] = "RESOLVED"
            self._settlements[stl]["completed_at"] = utcnow().isoformat()
            return resolvido

    def _finalize_compensating_incident(self, alvo: dict, *, actor_id: str,
                                        evidence: str, reason: str,
                                        tx_id: str,
                                        settlement_id: str) -> dict:
        """Fecha um incidente que está COMPENSATING.

        Antes o código voltava o estado para OPEN só para reaproveitar a
        validação padrão. Isso quebrava o significado do estado: por um
        instante o incidente parecia nunca ter entrado em compensação, e
        qualquer leitor concorrente veria uma máquina andando para trás.
        """
        refs = {"ledger_tx_ids": [tx_id], "settlement_id": settlement_id}
        self.validate_incident_resolution(alvo, refs)
        alvo["status"] = "RESOLVED"
        alvo["resolved_at"] = utcnow().isoformat()
        alvo["resolved_by"] = actor_id
        alvo["resolution_evidence"] = f"{evidence.strip()} | {reason}".strip(" |")
        alvo["resolution_refs"] = refs
        alvo["validated_by_policy"] = True
        alvo["policy_version"] = self.POLICY_VERSION
        self._provas_usadas[tx_id] = alvo["incident_id"]
        return alvo

    def _marcar_compensando(self, incident_id: str, settlement_id: str) -> None:
        """Usado pela recuperação e pelos testes de crash."""
        with self._lock_settlement:
            for inc in self._incidentes.values():
                if inc["incident_id"] == incident_id:
                    inc["status"] = "COMPENSATING"
            self._settlements.setdefault(settlement_id, {
                "settlement_id": settlement_id, "incident_id": incident_id,
                "amount": "0", "status": "COMPENSATING",
                "compensation_tx_id": "",
                "started_at": utcnow().isoformat(), "completed_at": ""})

    def validate_incident_resolution(self, incidente: dict, refs: dict) -> None:
        """Despacha por TIPO — cada violação tem seu próprio critério."""
        tipo = incidente["type"]
        if tipo == "RESERVATION_DEFICIT":
            return self._validar_deficit(incidente, refs)
        if tipo == "UNCOVERED_ASSET_BALANCE":
            # Aceitar uma frase aqui encerraria o alarme com o ativo ainda
            # faltando — e a mesa voltaria a operar devendo o que não tem.
            moeda = incidente.get("fee_currency") or incidente.get("currency")
            saldo = (self.carteira.balances().get(f"ASSET:{moeda}", Decimal("0"))
                     if self.carteira is not None else Decimal("0"))
            if saldo < 0:
                raise ValueError(
                    f"ASSET:{moeda} ainda está em {saldo}: cubra o ativo "
                    "(aporte ou compra) antes de resolver. Para registrar que "
                    "a diferença foi aceita sem cobri-la, use "
                    "reconhecer_incidente — que NÃO libera execução")
            return None
        if tipo in ("COVERAGE_LEDGER_MISMATCH", "UNKNOWN_RESERVATION_OWNERSHIP"):
            ok, motivo = self._invariantes_de_saldo()
            if not ok:
                raise ValueError(
                    f"estado econômico ainda inconsistente ({motivo}): "
                    "corrija primeiro, depois resolva o incidente")
            return None
        ok, motivo = self._invariantes_de_saldo()
        if not ok:
            raise ValueError(f"estado econômico ainda inconsistente ({motivo})")
        return None

    def _validar_deficit(self, incidente: dict, refs: dict) -> None:
        """Déficit só se fecha com COMPENSAÇÃO registrada no livro."""
        devido = Decimal(incidente["amount"])
        tx_ids = list(refs.get("ledger_tx_ids") or [])
        if not tx_ids:
            raise ValueError(
                f"déficit de {devido} exige prova de compensação: registre um "
                "ajuste de capital no livro e informe o tx_id em "
                "resolution_refs['ledger_tx_ids']")
        if self.carteira is None:
            raise ValueError("sem carteira não há como verificar a prova")
        coberto = Decimal("0")
        for tx_id in tx_ids:
            if tx_id in self._provas_usadas:
                raise ValueError(
                    f"lançamento {tx_id} já foi usado para resolver o "
                    f"incidente {self._provas_usadas[tx_id]}: uma compensação "
                    "não paga duas dívidas")
            tx = self.carteira.find_transaction(tx_id)
            if tx is None:
                raise ValueError(f"lançamento {tx_id} não encontrado no livro")
            if tx.get("kind") != "risk_incident_compensation":
                raise ValueError(
                    f"lançamento {tx_id} não é uma compensação de incidente "
                    f"(é {tx.get('kind')}): aporte genérico pode ter sido "
                    "feito por qualquer motivo e não prova nada aqui")
            vinculo = tx.get("incident_id") or ""
            if vinculo != incidente["incident_id"]:
                raise ValueError(
                    f"lançamento {tx_id} está vinculado ao incidente "
                    f"{vinculo or '(nenhum)'}, não a este: compensação de um "
                    "incidente não resolve outro")
            # ORDEM DOS FATOS: compensar antes de a violação existir é
            # coincidência, não conserto.
            if (tx.get("at") and incidente.get("opened_at")
                    and _instante_de(tx["at"])
                    < _instante_de(incidente["opened_at"])):
                raise ValueError(
                    f"lançamento {tx_id} é anterior à abertura do incidente: "
                    "não pode ser a compensação dele")
            for entrada in tx.get("entries", []):
                if entrada["account"].startswith("CASH:"):
                    coberto += Decimal(str(entrada["amount"]))
        if coberto < devido:
            raise ValueError(
                f"compensação de {coberto} não cobre o déficit de {devido}")

    def registrar_taxa_nao_valorada(self, *, moeda: str, quantidade: Decimal,
                                    order_id: str = "", instrumento: str = "",
                                    fill_id: str = "") -> str:
        """Taxa paga em moeda diferente da cotação — custo REAL desconhecido.

        Sem cotação não há como dizer quanto a operação custou. Chutar um
        câmbio produz um número que parece certo; ignorar a taxa faz a
        execução parecer mais barata do que foi. As duas saídas mentem, então
        o estado fica declarado como desconhecido e o risco novo trava.

        Um incidente POR MOEDA: várias taxas em BNB são o mesmo problema
        econômico, e abrir um por ordem viraria ruído.
        """
        chave = f"FEE_VALUATION_UNKNOWN:{moeda}"
        ocorrencia = {"amount": str(quantidade), "order_id": order_id,
                      "fill_id": fill_id, "instrumento": instrumento,
                      "at": utcnow().isoformat(),
                      "evidence": f"taxa de {quantidade} {moeda} sem cotação"}
        existente = self._incidentes.get(chave)
        if existente and existente["status"] == "OPEN":
            existente["fee_amount"] = str(
                Decimal(existente["fee_amount"]) + quantidade)
            existente["amount"] = existente["fee_amount"]
            existente.setdefault("occurrences", []).append(ocorrencia)
            return existente["incident_id"]
        import uuid as _uuid
        inc = {
            "incident_id": "inc_" + _uuid.uuid4().hex[:12],
            "type": "FEE_VALUATION_UNKNOWN",
            "fee_currency": moeda,
            "fee_amount": str(quantidade),
            "quote_currency": getattr(self.carteira, "currency", "USDT"),
            "amount": str(quantidade),
            "reservation_id": "", "order_id": order_id,
            "currency": moeda,
            "opened_at": utcnow().isoformat(),
            "evidence": f"taxa em {moeda} sem cotação para valorar",
            "status": "OPEN",
            "resolved_at": "", "resolved_by": "", "resolution_evidence": "",
            "resolution_refs": {}, "validated_by_policy": False,
            "policy_version": "", "valuation": {},
            "occurrences": [ocorrencia],
        }
        self._incidentes[chave] = inc
        return inc["incident_id"]

    def valorar_taxa(self, incident_id: str, *, rate, fonte: str, em) -> dict:
        """Informa a cotação usada para valorar a taxa.

        Exige cotação, FONTE e instante: sem procedência, ninguém consegue
        auditar depois de onde saiu o número — e uma cotação sem origem é
        indistinguível de um chute.
        """
        alvo = next((i for i in self._incidentes.values()
                     if i["incident_id"] == incident_id), None)
        if alvo is None:
            raise ValueError(f"incidente {incident_id} não encontrado")
        if rate is None or Decimal(str(rate)) <= 0:
            raise ValueError(
                "valorar exige a cotação (taxa de câmbio) usada, positiva")
        if not fonte or not str(fonte).strip():
            raise ValueError(
                "valorar exige a fonte da cotação: sem origem, o número é "
                "indistinguível de um chute")
        if not em:
            raise ValueError("valorar exige o instante da cotação")
        cotacao = Decimal(str(rate))
        quantidade = Decimal(alvo["fee_amount"])
        alvo["valuation"] = {
            # PROPOSTA: anotar a cotação não move dinheiro nem encerra nada.
            # Sem essa distinção, o incidente parecia resolvido e o livro
            # continuava sem saber quanto a operação custou.
            "status": "PROPOSTA",
            "rate": str(cotacao), "fonte": str(fonte).strip(),
            "em": str(em), "moeda": alvo["fee_currency"],
            "quote_currency": alvo["quote_currency"],
            "valor_em_cotacao": str(quantidade * cotacao),
        }
        return alvo

    CAPACIDADE_VALORAR = "VALUE_FEE_INCIDENT"

    def aplicar_valoracao(self, incident_id: str, *, actor,
                          evidence: str) -> dict:
        """APLICA a cotação proposta: lança no livro e resolve o incidente.

        Separado de `valorar_taxa` porque são atos diferentes: propor uma
        cotação é análise; aplicá-la move a contabilidade e libera a mesa.
        Juntar os dois deixaria quem só queria simular um número alterando o
        livro sem perceber.
        """
        capacidades = getattr(actor, "capabilities", None)
        actor_id = getattr(actor, "actor_id", None)
        if capacidades is None or not actor_id:
            raise PermissionError("aplicação exige ator autenticado")
        if self.CAPACIDADE_VALORAR not in capacidades:
            raise PermissionError(
                f"{actor_id} não possui {self.CAPACIDADE_VALORAR}")
        alvo = next((i for i in self._incidentes.values()
                     if i["incident_id"] == incident_id), None)
        if alvo is None:
            raise ValueError(f"incidente {incident_id} não encontrado")
        if alvo["status"] != "OPEN":
            return alvo                  # idempotente: não move dinheiro de novo
        v = alvo.get("valuation") or {}
        if not v or v.get("status") != "PROPOSTA":
            raise ValueError(
                "não há cotação proposta para aplicar: chame valorar_taxa "
                "antes, com cotação, fonte e instante")
        if self.carteira is None:
            raise ValueError("sem carteira não há onde lançar a valoração")

        tx = self.carteira.fee_valuation(
            moeda=alvo["fee_currency"], quantidade=Decimal(alvo["fee_amount"]),
            rate=Decimal(v["rate"]), fonte=v["fonte"], em=v["em"],
            incident_id=incident_id)
        alvo["valuation"] = {**v, "status": "APLICADA",
                             "ledger_tx_id": tx.tx_id,
                             "aplicada_por": actor_id,
                             "aplicada_em": utcnow().isoformat()}
        alvo["status"] = "RESOLVED"
        alvo["resolved_at"] = utcnow().isoformat()
        alvo["resolved_by"] = actor_id
        alvo["resolution_evidence"] = evidence
        alvo["resolution_refs"] = {"ledger_tx_ids": [tx.tx_id]}
        alvo["validated_by_policy"] = True
        alvo["policy_version"] = self.POLICY_VERSION
        return alvo

    def conferir_ativos_descobertos(self) -> list[str]:
        """Saldo negativo em ASSET:<moeda> é ativo que a mesa NÃO tem.

        Acontece quando a corretora cobra taxa numa moeda que a conta não
        possui. O livro fecha — a contrapartida existe — mas a posição real
        está a descoberto.

        UM incidente POR MOEDA, com o valor SINCRONIZADO ao saldo atual. A
        versão anterior somava a cada chamada: o número crescia só de alguém
        ler o estado, e duas moedas viravam um registro com a moeda da
        última.
        """
        if self.carteira is None:
            return []
        encontrados = []
        for conta, saldo in sorted(self.carteira.balances().items()):
            if not conta.startswith("ASSET:") or saldo >= 0:
                continue
            moeda = conta.split(":", 1)[1]
            faltante = abs(saldo)
            encontrados.append(moeda)
            chave = f"UNCOVERED_ASSET_BALANCE:{moeda}"
            existente = self._incidentes.get(chave)
            if existente and existente["status"] in self.ESTADOS_BLOQUEANTES:
                # SINCRONIZA o valor com a realidade em vez de acumular — e
                # NÃO mexe no status: rebaixar um ACKNOWLEDGED_BLOCKING para
                # OPEN apagaria a decisão de quem já analisou o caso.
                existente["amount"] = str(faltante)
                existente["evidence"] = (
                    f"saldo de {saldo} em {conta}: taxa paga em {moeda} sem o "
                    "ativo em conta")
                continue
            if existente and existente["status"] == "RESOLVED":
                if Decimal(existente.get("saldo_na_resolucao") or 0) >= faltante:
                    # Já resolvido e o descoberto não piorou: reabrir aqui
                    # apagaria o trabalho de quem consertou.
                    continue
                # Piorou depois da resolução: é um FATO NOVO, e ganha uma
                # chave estável por GERAÇÃO. Usar o instante da varredura
                # criaria um incidente a cada passada; usar geração+1 do
                # registro base SOBRESCREVIA a geração anterior no terceiro
                # ciclo. A geração vem do MAIOR já existente para a moeda.
                existentes_da_moeda = [
                    int(i.get("geracao") or 0) for k, i in self._incidentes.items()
                    if i.get("type") == "UNCOVERED_ASSET_BALANCE"
                    and (i.get("fee_currency") or i.get("currency")) == moeda]
                geracao = (max(existentes_da_moeda) if existentes_da_moeda else 0) + 1
                chave = f"UNCOVERED_ASSET_BALANCE:{moeda}#{geracao}"
                viva = next(
                    (i for i in self._incidentes.values()
                     if i.get("type") == "UNCOVERED_ASSET_BALANCE"
                     and (i.get("fee_currency") or i.get("currency")) == moeda
                     and i["status"] in self.ESTADOS_BLOQUEANTES), None)
                if viva is not None:
                    viva["amount"] = str(faltante)
                    continue
            import uuid as _uuid
            self._incidentes[chave] = {
                "incident_id": "inc_" + _uuid.uuid4().hex[:12],
                "type": "UNCOVERED_ASSET_BALANCE",
                "fee_currency": moeda, "currency": moeda,
                "amount": str(faltante), "reservation_id": "", "order_id": "",
                "opened_at": utcnow().isoformat(),
                "evidence": (f"saldo de {saldo} em {conta}: taxa paga em "
                             f"{moeda} sem o ativo em conta"),
                "status": "OPEN",
                "resolved_at": "", "resolved_by": "", "resolution_evidence": "",
                "resolution_refs": {}, "validated_by_policy": False,
                "policy_version": "", "occurrences": [],
                "geracao": int(chave.split("#")[1]) if "#" in chave else 0,
            }
        return encontrados

    ESTADOS_BLOQUEANTES = ("OPEN", "COMPENSATING", "ACKNOWLEDGED_BLOCKING")
    # Reconhecer é para violação que não tem conserto automático. Um
    # FEE_VALUATION_UNKNOWN reconhecido travaria a valoração para sempre —
    # ela é o conserto dele.
    TIPOS_RECONHECIVEIS = ("UNCOVERED_ASSET_BALANCE",)

    def reconhecer_incidente(self, incident_id: str, *, actor,
                             evidence: str) -> dict:
        """Registra que a diferença foi vista e aceita — sem liberar nada.

        Existe porque "resolvido" e "conhecido" são coisas diferentes:
        forçar os dois no mesmo estado empurra quem precisa registrar a
        decisão a marcar como resolvido algo que continua valendo.
        """
        capacidades = getattr(actor, "capabilities", None)
        actor_id = getattr(actor, "actor_id", None)
        if capacidades is None or not actor_id:
            raise PermissionError("reconhecer exige ator autenticado")
        if self.CAPACIDADE_RESOLVER not in capacidades:
            raise PermissionError(
                f"{actor_id} não possui {self.CAPACIDADE_RESOLVER}")
        if not evidence or not evidence.strip():
            raise ValueError("reconhecer exige evidência")
        alvo = next((i for i in self._incidentes.values()
                     if i["incident_id"] == incident_id), None)
        if alvo is None:
            raise ValueError(f"incidente {incident_id} não encontrado")
        if alvo["type"] not in self.TIPOS_RECONHECIVEIS:
            raise ValueError(
                f"tipo {alvo['type']} não é reconhecível nesta versão: para "
                "ele existe caminho de conserto próprio")
        if alvo["status"] == "RESOLVED":
            raise ValueError(
                "incidente RESOLVED é terminal: reconhecer aqui reabriria o "
                "que alguém já consertou")
        if alvo["status"] == "COMPENSATING":
            raise ValueError(
                "incidente em COMPENSATING está no meio de uma liquidação: "
                "reconhecer agora perderia a transição")
        if alvo["status"] == "ACKNOWLEDGED_BLOCKING":
            return alvo          # idempotente: preserva a evidência original
        alvo["status"] = "ACKNOWLEDGED_BLOCKING"
        alvo["acknowledged_by"] = actor_id
        alvo["acknowledged_at"] = utcnow().isoformat()
        alvo["acknowledged_evidence"] = evidence.strip()
        return alvo

    def can_execute(self, intent) -> dict:
        """Decide POR INTENÇÃO: entrar é diferente de sair.

        Aplicar o mesmo bloqueio aos dois prendia o dinheiro DENTRO do
        risco — o oposto de proteger. Reduzir posição diminui exposição e
        continua permitido; a exceção não pode virar porta para aumentar.
        """
        motivos = self.execution_blockers()
        if not motivos:
            return {"decisao": "ALLOW", "motivos": []}
        kind = str(getattr(intent, "kind", "") or "")
        side = str(getattr(intent, "side", "") or "")
        reducao = ("EXIT" in kind.upper()) or ("SELL" in side.upper())
        if not reducao:
            return {"decisao": "BLOCK_ENTRY", "motivos": motivos}

        # REDUCE-ONLY DE VERDADE: só passa o que comprovadamente diminui.
        quantidade = Decimal(str(getattr(intent, "quantity", 0) or 0))
        if quantidade <= 0:
            return {"decisao": "BLOCK_ENTRY",
                    "motivos": motivos + ["quantidade precisa ser positiva"]}
        instrumento = getattr(intent, "instrument", "")
        agente = getattr(intent, "agent_id", "")
        lote = self._lote_do_agente(instrumento, agente)
        if lote <= 0:
            return {"decisao": "BLOCK_ENTRY",
                    "motivos": motivos + [
                        f"{agente} não tem lote aberto em {instrumento}: "
                        "venda a descoberto não é redução"]}
        if quantidade > lote:
            return {"decisao": "BLOCK_ENTRY",
                    "motivos": motivos + [
                        f"quantidade {quantidade} maior que o lote {lote}: "
                        "o excedente aumentaria risco"]}
        return {"decisao": "ALLOW_REDUCE_ONLY", "motivos": motivos}

    def _lote_do_agente(self, instrumento: str, agente: str) -> Decimal:
        """Quanto ESTE agente tem aberto neste instrumento.

        Usa lot_quantity do livro, que já é a autoridade sobre lotes por
        dono — eu havia escrito três caminhos alternativos para algo que já
        existia.
        """
        if self.carteira is None:
            return Decimal("0")
        try:
            return Decimal(str(self.carteira.lot_quantity(instrumento, agente)))
        except Exception:
            return Decimal("0")

    def preflight_de_consistencia(self, ignorar_reserva: str = "") -> list[str]:
        """Divergências entre livro e risco, SEM efeito colateral.

        A reserva da própria intenção ainda não foi travada — quem trava é o
        passo seguinte do envio. Acusá-la impediria qualquer ordem; acusar
        só ela é o que separa 'ainda não aconteceu' de 'está errado'.
        """
        problemas = []
        if self.carteira is None:
            return problemas
        with self._lock:
            vivas = dict(self._reservations)
        no_livro = self.carteira.reservation_balances()
        orfas = set(no_livro) - set(vivas)
        if orfas:
            problemas.append(
                f"ORPHAN_LEDGER_RESERVATION: capital travado em "
                f"{', '.join(sorted(orfas))} sem reserva viva no risco")
        # Só é divergência a reserva JÁ VINCULADA a uma ordem sem lastro no
        # livro. Uma reserva criada e ainda não enviada está legitimamente
        # ausente — acusá-la impediria qualquer ciclo em que vários agentes
        # reservam antes de enviar.
        faltando = {
            rid for rid in set(vivas) - set(no_livro)
            if rid != ignorar_reserva
            and getattr(vivas[rid], "bound_client_order_id", "")}
        if faltando:
            problemas.append(
                f"MISSING_LEDGER_RESERVATION: risco tem "
                f"{', '.join(sorted(faltando))} vinculada a ordem sem lastro "
                "no livro")
        for rid, reserva in vivas.items():
            if rid == ignorar_reserva or rid not in no_livro:
                continue
            if no_livro[rid] != reserva.remaining_amount:
                problemas.append(
                    f"RESERVATION_LEDGER_MISMATCH reservation_id={rid}: "
                    f"livro {no_livro[rid]} vs risco {reserva.remaining_amount}")
        if self.carteira.cash_available() < 0:
            problemas.append(
                f"caixa negativo: {self.carteira.cash_available()}")
        return problemas

    def execution_blockers(self) -> list[str]:
        """Tudo que impede assumir risco novo. SÓ LEITURA.

        Consultar apenas incidentes abertos deixava passar o que ninguém
        tinha registrado ainda — um ativo negativo sem varredura recente
        liberava ordem. Aqui o estado REAL do livro entra junto.
        """
        motivos = []
        for inc in self._incidentes.values():
            if inc["status"] in self.ESTADOS_BLOQUEANTES:
                motivos.append(
                    f"{inc['type']} incident_id={inc['incident_id']} "
                    f"status={inc['status']} valor={inc.get('amount')}")
        for moeda, falta in sorted(self.ativos_descobertos().items()):
            motivos.append(
                f"ativo descoberto ASSET:{moeda} em -{falta}")
        deficit = self.deficit_total()
        if deficit > 0:
            motivos.append(f"déficit ativo de {deficit}")
        return motivos

    def conversao_acumulada(self) -> Decimal:
        """Diferença de unidade acumulada nas valorações de taxa.

        Fica numa conta própria (FX:CONVERSION) e NÃO vira resultado de
        trader: o que a estratégia fez e o que a conversão cambial fez são
        coisas diferentes, e somar as duas premiaria ou puniria o agente por
        um câmbio que ele não escolheu.
        """
        if self.carteira is None:
            return Decimal("0")
        return self.carteira.balances().get("FX:CONVERSION", Decimal("0"))

    def ativos_descobertos(self) -> dict:
        """Só LEITURA: moedas com saldo negativo, sem tocar em incidente."""
        if self.carteira is None:
            return {}
        return {c.split(":", 1)[1]: abs(s)
                for c, s in self.carteira.balances().items()
                if c.startswith("ASSET:") and s < 0}

    def registrar_divergencia_de_cobertura(self, reservation_id: str, *,
                                           faltante: Decimal) -> None:
        """O risco tinha reserva que o livro não travou."""
        doc = self._concluidas.get(reservation_id) or {}
        doc["cobertura_nao_travada"] = str(faltante)
        doc["estado"] = "DIVERGENTE"
        self._concluidas[reservation_id] = doc
        self.abrir_incidente(
            "COVERAGE_LEDGER_MISMATCH", amount=faltante,
            reservation_id=reservation_id,
            evidence=f"reserva viva de {faltante} sem lastro no livro")

    def conclusao(self, reservation_id: str) -> dict | None:
        """Como esta reserva terminou — evidência para auditoria."""
        return self._concluidas.get(reservation_id)

    def deficit_total(self) -> Decimal:
        """Déficit ATIVO — o que ainda bloqueia a mesa.

        Vinha de `_deficits`, uma segunda máquina de estado paralela ao
        incidente: resolver o incidente não zerava esta, e o déficit
        resolvido travava a mesa para sempre, sem caminho de saída. Agora a
        única fonte é o incidente aberto.
        """
        return sum((Decimal(i["amount"]) for i in self.incidentes_abertos()
                    if i["type"] == "RESERVATION_DEFICIT"), Decimal("0"))

    def historical_deficit_total(self) -> Decimal:
        """Tudo que já foi executado além da cobertura, resolvido ou não.

        'Aconteceu' e 'continua não resolvido' são perguntas diferentes:
        misturar as duas ou esconde o histórico, ou bloqueia para sempre.
        """
        return sum((Decimal(i["amount"]) for i in self._incidentes.values()
                    if i["type"] == "RESERVATION_DEFICIT"), Decimal("0"))

    def capital_state_consistent(self) -> tuple[bool, str]:
        """O estado de capital fecha com a política?

        Livro balanceado prova aritmética; isto prova POLÍTICA.
        """
        # 1) Incidente financeiro em aberto bloqueia, ponto. Só resolução
        #    explícita com evidência tira o bloqueio.
        # LEITURA PURA: detectar aqui escreveria incidente a cada consulta.
        # O saldo negativo ainda BLOQUEIA — só não cria registro de lado.
        descobertos = self.ativos_descobertos()
        if descobertos:
            moeda, falta = sorted(descobertos.items())[0]
            return False, (
                f"ativo descoberto: ASSET:{moeda} em -{falta} — taxa paga em "
                f"{moeda} sem o ativo em conta (UNCOVERED_ASSET_BALANCE)")
        abertos = self.incidentes_abertos()
        if abertos:
            i = abertos[0]
            return False, (
                f"incidente financeiro em aberto: {i['type']} "
                f"incident_id={i['incident_id']} valor={i['amount']} "
                f"reserva={i['reservation_id'] or '-'}")
        return self._invariantes_de_saldo()

    def _invariantes_de_saldo(self) -> tuple[bool, str]:
        """Conjuntos e valores — sem olhar incidentes.

        Separado porque a RESOLUÇÃO precisa exatamente disto: saber se o
        estado econômico foi corrigido, sem cair no incidente que está
        justamente sendo resolvido.
        """
        if self.carteira is not None:
            reservado_livro = self.carteira.reserved_cash()
            if reservado_livro < 0:
                return False, f"reservado negativo no livro ({reservado_livro})"
            # POR RESERVA, não só pelo total: R1=50/R2=150 soma igual a
            # R1=100/R2=100, e a conferência agregada passaria com o dinheiro
            # de uma ordem no lugar da outra.
            with self._lock:
                vivas = dict(self._reservations)
            no_livro_ids = set(self.carteira.reservation_balances())
            orfas = no_livro_ids - set(vivas)
            if orfas:
                return False, (
                    f"ORPHAN_LEDGER_RESERVATION: o livro tem capital travado "
                    f"em {', '.join(sorted(orfas))} sem reserva viva no risco")
            faltando = set(vivas) - no_livro_ids
            if faltando:
                return False, (
                    f"MISSING_LEDGER_RESERVATION: o risco tem "
                    f"{', '.join(sorted(faltando))} sem lastro no livro")
            for rid, reserva in vivas.items():
                no_livro = self.carteira.reserved_cash(rid)
                if no_livro != reserva.remaining_amount:
                    return False, (
                        f"RESERVATION_LEDGER_MISMATCH reservation_id={rid}: "
                        f"livro {no_livro} vs risco {reserva.remaining_amount}")
            if reservado_livro != self.reserved_total:
                return False, (
                    f"reservado divergente: livro {reservado_livro} vs risco "
                    f"{self.reserved_total}")
        if self.carteira is not None and self.carteira.cash_available() < 0:
            return False, (f"caixa negativo ({self.carteira.cash_available()}): "
                           "há dinheiro comprometido além do disponível")
        if self.reserved_total < 0:
            return False, "reservado negativo"
        return True, "capital consistente"

    def bind(self, reservation_id: str, client_order_id: str) -> None:
        """Vincula a reserva a UMA ordem. Idempotente para o mesmo vínculo.

        Dentro do mesmo lock do consumo: dois workers tentando vincular a
        mesma reserva a ordens diferentes não podem vencer os dois, senão o
        mesmo capital financiaria duas ordens na corretora.
        """
        if not client_order_id:
            raise RiskVeto("vínculo exige identificador de ordem")
        with self._lock:
            reserva = self._reservations.get(reservation_id)
            if reserva is None:
                raise RiskVeto(
                    f"reserva {reservation_id} não está viva: nada a vincular")
            atual = reserva.bound_client_order_id
            if atual and atual != client_order_id:
                raise RiskVeto(
                    f"reserva {reservation_id} já está vinculada à ordem "
                    f"{atual}: uma reserva financia uma ordem só")
            if atual == client_order_id:
                return                      # idempotente
            from dataclasses import replace
            self._reservations[reservation_id] = replace(
                reserva, bound_client_order_id=client_order_id)

    def vinculo(self, reservation_id: str) -> str:
        with self._lock:
            reserva = self._reservations.get(reservation_id)
            return reserva.bound_client_order_id if reserva else ""

    def reserva(self, reservation_id: str):
        """A reserva viva, com original/consumido/restante."""
        with self._lock:
            return self._reservations.get(reservation_id)

    def reservation_amount(self, reservation_id: str) -> Decimal:
        with self._lock:
            reserva = self._reservations.get(reservation_id)
            return reserva.remaining_amount if reserva else Decimal("0")

    def active_reservations(self) -> list[dict]:
        """Reservas vivas, para persistência.

        O snapshot chamava este método sem ele existir: `getattr` devolvia
        nada e a lista persistida era SEMPRE vazia. Depois de um reinício, o
        capital reservado de uma ordem incerta voltava a parecer disponível.
        """
        with self._lock:
            return [{"reservation_id": res.reservation_id,
                     "account": res.account,
                     "agent_id": res.agent_id,
                     "instrument": res.instrument,
                     "amount": str(res.amount),
                     "quantity": str(res.quantity),
                     # Acumulados: sem eles o reinício devolveria a reserva
                     # ao valor cheio, e o consumo já feito sumiria.
                     "consumed_amount": str(res.consumed_amount),
                     "filled_quantity": str(res.filled_quantity),
                     # Derivados, para quem lê o documento não precisar
                     # recalcular (e errar) a conta.
                     "bound_client_order_id": res.bound_client_order_id,
                     "buffer_amount": str(res.buffer_amount),
                     "remaining_amount": str(res.remaining_amount),
                     "remaining_quantity": str(res.remaining_quantity),
                     "mandate_version": res.mandate_version,
                     "expires_at": res.expires_at.isoformat()
                     if res.expires_at else None}
                    for res in self._reservations.values()]

    def restore_reservations(self, docs: list[dict]) -> int:
        """Reidrata reservas. Capital reservado não pode ressuscitar como
        disponível só porque o processo reiniciou."""
        from .models import RiskReservation
        restauradas = 0
        with self._lock:
            for doc in docs or []:
                try:
                    from datetime import datetime as _dt
                    expira = doc.get("expires_at")
                    res = RiskReservation(
                        reservation_id=doc["reservation_id"],
                        account=doc.get("account", ""),
                        instrument=doc["instrument"],
                        amount=Decimal(str(doc["amount"])),
                        consumed_amount=Decimal(str(
                            doc.get("consumed_amount") or 0)),
                        filled_quantity=Decimal(str(
                            doc.get("filled_quantity") or 0)),
                        bound_client_order_id=doc.get(
                            "bound_client_order_id") or "",
                        buffer_amount=Decimal(str(doc.get("buffer_amount") or 0)),
                        quantity=Decimal(str(doc.get("quantity") or 0)),
                        mandate_version=doc.get("mandate_version", 1),
                        expires_at=_dt.fromisoformat(expira) if expira else None,
                        agent_id=doc.get("agent_id", ""))
                except (KeyError, ValueError, TypeError):
                    continue
                self._reservations[res.reservation_id] = res
                restauradas += 1
        return restauradas

    def release(self, reservation_id: str) -> bool:
        """Libera capital. Só deve ser chamada quando nenhum efeito externo
        pode mais existir (cenário 09: timeout NÃO libera)."""
        with self._lock:
            return self._reservations.pop(reservation_id, None) is not None

    # -- intenção ------------------------------------------------------
    def intent(self, reservation: RiskReservation, action: Action, mode,
               worker_id: str, *, idempotency_key: str = "",
               provenance: dict | None = None) -> OrderIntent:
        if reservation.reservation_id not in self._reservations:
            raise RiskVeto("reserva inexistente ou já liberada")
        if utcnow() >= reservation.expires_at:
            raise RiskVeto("reserva expirada")
        self.mandate.assert_active(instrument=reservation.instrument)
        return OrderIntent(intent_id=uuid.uuid4().hex[:16], reservation=reservation,
                           action=action, mode=mode, owner_worker=worker_id,
                           expires_at=default_expiry(5),
                           idempotency_key=idempotency_key or uuid.uuid4().hex,
                           provenance=provenance)


__all__ = ["PortfolioAllocator", "RiskEngine", "RiskVeto", "MandateError"]

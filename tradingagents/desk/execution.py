"""Execução e reconciliação da mesa (MA-06 / MA-07 / TA-009).

Regras codificadas, cada uma com teste:
- Chave idempotente amarrada ao FINGERPRINT canônico do comando: mesma chave
  com conteúdo diferente é conflito, não retry.
- Timeout depois de enviar é AMBÍGUO: vira UNKNOWN_RECONCILIATION_REQUIRED.
  Não há reenvio cego e a reserva NÃO é liberada enquanto o efeito externo
  puder existir.
- Fills chegam por identificador; duplicata e mensagem fora de ordem não
  contam duas vezes.
- Cancelamento solicitado ≠ cancelamento confirmado.
- O executor é o ÚNICO caminho para a corretora e revalida posse, mandato,
  expiração e política antes de cada envio.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from .ledger import Ledger
from .models import Action, DeskMandate, MandateError, Mode, OrderIntent, utcnow


class OrderState(str, Enum):
    DRAFT = "draft"
    VALIDATED = "validated"
    APPROVED = "approved"
    SUBMITTING = "submitting"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_PENDING = "cancel_pending"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    UNKNOWN_RECONCILIATION_REQUIRED = "unknown_reconciliation_required"


TERMINAL = {OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED}


class IdempotencyConflict(RuntimeError):
    """Mesma chave, conteúdo diferente — jamais tratado como retry."""


class ExecutionRefused(RuntimeError):
    """Envio recusado antes de qualquer efeito externo."""


class BrokerTimeout(Exception):
    """A corretora não respondeu: o efeito pode ou não existir."""


def fingerprint(intent: OrderIntent) -> str:
    canonical = json.dumps({
        "instrument": intent.reservation.instrument,
        "action": intent.action.value,
        "quantity": str(intent.reservation.quantity),
        "account": intent.reservation.account,
        "mode": intent.mode.value,
        "reservation": intent.reservation.reservation_id,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


# Estados em que a ordem ainda pode existir e mudar na corretora. TODOS
# precisam ser conferidos após um reinício: o defeito encontrado pela
# auditoria era consultar apenas UNKNOWN e declarar SAFE com uma ordem
# ACKNOWLEDGED restaurada que podia ter sido preenchida no meio-tempo.
ESTADOS_NAO_TERMINAIS = frozenset({
    OrderState.SUBMITTING,
    OrderState.ACKNOWLEDGED,
    OrderState.PARTIALLY_FILLED,
    OrderState.CANCEL_PENDING,
    OrderState.UNKNOWN_RECONCILIATION_REQUIRED,
})


@dataclass


class OrderRecord:
    order_id: str
    intent: OrderIntent
    state: OrderState = OrderState.DRAFT
    fingerprint: str = ""
    filled_qty: Decimal = Decimal("0")
    avg_price: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    seen_fills: set = field(default_factory=set)
    external_id: str | None = None
    note: str = ""

    @property
    def open(self) -> bool:
        return self.state not in TERMINAL

    @property
    def needs_reconciliation(self) -> bool:
        """Ordem cujo destino é DESCONHECIDO para nós.

        Cuidado: isto é mais estreito que 'precisa ser conferida na
        corretora'. Uma ordem ACKNOWLEDGED restaurada do disco não é
        'unknown' localmente, mas pode ter sido preenchida enquanto o
        processo estava morto. Para isso existe `ESTADOS_NAO_TERMINAIS`.
        """
        return self.state is OrderState.UNKNOWN_RECONCILIATION_REQUIRED

    @property
    def nao_terminal(self) -> bool:
        return self.state in ESTADOS_NAO_TERMINAIS

    # Registro restaurado do disco não tem `intent` (a intenção é de outro
    # processo). Estas propriedades dão a MESMA leitura nos dois casos — sem
    # elas o painel quebrava ao listar uma ordem recuperada.
    @property
    def instrumento(self) -> str:
        if self.intent is not None:
            return self.intent.reservation.instrument
        return getattr(self, "instrument", "")

    @property
    def lado(self) -> str:
        if self.intent is not None:
            from .models import Action as _A
            return "buy" if self.intent.action is _A.BUY else "sell"
        return getattr(self, "side", "")

    @property
    def quantidade(self):
        if self.intent is not None:
            return self.intent.reservation.quantity
        return getattr(self, "quantity", Decimal("0"))

    @property
    def client_order_id(self) -> str:
        if self.intent is not None:
            return self.intent.idempotency_key
        return getattr(self, "_client_order_id", "")

    @client_order_id.setter
    def client_order_id(self, valor: str) -> None:
        self._client_order_id = valor

    @property
    def agente(self) -> str:
        if self.intent is not None:
            return self.intent.reservation.agent_id
        return getattr(self, "agent_id", "")

    @property
    def reserva_id(self) -> str:
        if self.intent is not None:
            return self.intent.reservation.reservation_id
        return getattr(self, "reservation_id", "")

    @property
    def procedencia(self):
        if self.intent is not None:
            return self.intent.provenance
        return getattr(self, "provenance", None)


class PaperBroker:
    """Corretora simulada — dinheiro fictício, comportamento honesto.
    Serve PAPER/SHADOW; testnet/live usam a ponte CCXT pelo mesmo contrato."""

    def __init__(self, price: Decimal, fee_rate: Decimal = Decimal("0.001"),
                 price_provider=None):
        self.price = price
        self.fee_rate = fee_rate
        # Com feed real, o preenchimento simulado precisa usar o preço do
        # mercado — senão a contabilidade do paper mente (achado em teste vivo).
        self.price_provider = price_provider
        self.submitted: dict[str, dict] = {}

    def price_for(self, instrument: str) -> Decimal:
        if self.price_provider:
            try:
                found = self.price_provider().get(instrument)
                if found:
                    return Decimal(str(found))
            except Exception:
                pass
        return self.price

    def submit(self, client_order_id: str, instrument: str, side: str,
               quantity: Decimal, final_state: dict | None = None) -> dict:
        # `final_state` é a procedência exigida pela corretora real. O paper
        # não a valida (não há capital em risco), mas aceita o mesmo contrato:
        # interface diferente entre paper e real é como o caminho de produção
        # deixa de ser exercitado pelos testes.
        if client_order_id in self.submitted:
            return self.submitted[client_order_id]  # idempotente do lado da corretora
        price = self.price_for(instrument)
        fee = (quantity * price * self.fee_rate).quantize(Decimal("0.00000001"))
        oid = f"paper-{len(self.submitted) + 1}"
        # O simulador emite a EXECUÇÃO individualizada, com identidade
        # própria. Devolver só o acumulado obrigaria o coordenador a inventar
        # um fill sintético — que é justamente o defeito que estamos tirando.
        order = {"id": oid, "status": "filled", "filled": quantity,
                 "average": price, "fee": fee,
                 "trades": [{"id": f"{oid}-t1", "amount": quantity,
                             "price": price, "fee": {"cost": fee}}]}
        self.submitted[client_order_id] = order
        return order


class ExecutionCoordinator:
    """Aplica SOMENTE comandos autorizados; mantém estado e contabilidade."""

    def __init__(self, mandate: DeskMandate, ledger: Ledger, risk, *,
                 worker_id: str, broker=None):
        self.mandate = mandate
        self.ledger = ledger
        self.risk = risk
        self.worker_id = worker_id
        self.broker = broker
        self.orders: dict[str, OrderRecord] = {}
        self.perda_do_dia = Decimal("0")
        self.dia_corrente = ""
        # Versão do estado financeiro: um relatório SAFE só vale para a
        # versão em que foi emitido (freshness).
        self.oms_version = 0
        self.ultimo_relatorio: dict | None = None
        self.ordens_restauradas: list = []
        self.posicoes_restauradas: dict = {}
        # PORTÃO DE RECONCILIAÇÃO. Enquanto ligado, nenhum risco novo sai.
        # Estado desconhecido bloqueia: UNKNOWN => BLOCK, nunca UNKNOWN =>
        # "deve estar tudo bem".
        self.reconciliacao_pendente = False
        self.motivo_bloqueio = ""
        self._by_key: dict[str, str] = {}     # idempotency_key -> order_id

    # -- envio ---------------------------------------------------------
    def exigir_reconciliacao(self, motivo: str) -> None:
        self.reconciliacao_pendente = True
        self.motivo_bloqueio = motivo

    def reconciliar(self, consultar_corretora=None, *,
                    consultar_posicoes=None) -> dict:
        """Confere o estado local com a CORRETORA. Exchange-first.

        A auditoria encontrou três buracos na versão anterior, todos capazes
        de declarar SAFE sobre uma mentira:
        1. só ordens UNKNOWN eram consultadas — uma ACKNOWLEDGED restaurada
           podia ter sido preenchida enquanto o processo estava morto;
        2. posições nunca eram comparadas com a corretora, e ainda assim
           `posicoes_restauradas` era limpo;
        3. ordem FILLED na corretora só mudava campos do registro: o livro
           ficava sem a posição e com a reserva presa.

        `ledger.is_balanced()` continua sendo exigido, mas NÃO é prova de
        igualdade com a corretora — só prova que a aritmética interna fecha.
        """
        relatorio = {
            "schema": "dz23.reconciliacao/1",
            "em": utcnow().isoformat(),
            "ordens_conferidas": 0, "ordens_status": "UNKNOWN",
            "posicoes_status": "UNKNOWN", "livro_status": "UNKNOWN",
            "divergencias": [], "seguro": False,
        }
        if consultar_corretora is None:
            relatorio["divergencias"].append(
                "sem acesso à corretora: nada foi conferido")
            return self._selar(relatorio)

        # --- ORDENS: todas as não terminais, não apenas as UNKNOWN ---
        for rec in list(self.orders.values()):
            if not rec.nao_terminal:
                continue
            chave = rec.client_order_id or rec.order_id
            try:
                remota = consultar_corretora(chave)
            except Exception as exc:
                relatorio["divergencias"].append(
                    f"{rec.order_id}: consulta falhou ({type(exc).__name__})")
                continue
            relatorio["ordens_conferidas"] += 1
            self._aplicar_ordem_remota(rec, remota, relatorio)

        restantes = [r.order_id for r in self.orders.values() if r.nao_terminal]
        if restantes:
            relatorio["divergencias"].append(
                f"ordens ainda abertas na corretora: {', '.join(restantes[:5])}")
        relatorio["ordens_status"] = "MATCHED" if not restantes else "MISMATCH"

        # --- POSIÇÕES: comparação real, não limpeza otimista ---
        relatorio.update(self._conferir_posicoes(consultar_posicoes, relatorio))

        # --- LIVRO: invariante interno (necessário, não suficiente) ---
        balanceado = self.ledger.is_balanced()
        relatorio["livro_status"] = "BALANCED" if balanceado else "IMBALANCED"
        if not balanceado:
            relatorio["divergencias"].append("livro-razão desbalanceado")
        return self._selar(relatorio)

    def _selar(self, relatorio: dict) -> dict:
        seguro = (not relatorio["divergencias"]
                  and relatorio["ordens_status"] == "MATCHED"
                  and relatorio["posicoes_status"] == "MATCHED"
                  and relatorio["livro_status"] == "BALANCED")
        relatorio["seguro"] = seguro
        relatorio["oms_version"] = self.oms_version
        if seguro:
            self.reconciliacao_concluida("conferido com a corretora")
            self.ultimo_relatorio = relatorio
        else:
            self.exigir_reconciliacao(
                "; ".join(relatorio["divergencias"])[:300] or "reconciliação incompleta")
            self.ultimo_relatorio = relatorio
        return relatorio

    def _conferir_posicoes(self, consultar_posicoes, relatorio: dict) -> dict:
        """Compara posição local com a da corretora. Sem a consulta, o
        status é UNKNOWN — e UNKNOWN não libera nada."""
        locais = {i: Decimal(str(d["quantidade"]))
                  for i, d in self.ledger.open_positions_detail().items()}
        # As posições restauradas NÃO entram como verdade paralela: depois de
        # aplicar os fills encontrados, o livro é a fonte. Somá-las aqui fazia
        # uma posição já encerrada pela reconciliação reaparecer como
        # divergência — e travava o portão para sempre.
        if consultar_posicoes is None:
            # Ausência de posição LOCAL não prova ausência na CORRETORA: é
            # exatamente o caso da posição órfã remota. Sem consulta, o
            # status é UNKNOWN — sempre — e UNKNOWN não libera nada.
            relatorio["divergencias"].append(
                "posições não foram comparadas com a corretora (sem consulta "
                "disponível): pode existir posição remota desconhecida")
            return {"posicoes_status": "UNKNOWN"}
        try:
            remotas_brutas = consultar_posicoes() or {}
        except Exception as exc:
            relatorio["divergencias"].append(
                f"consulta de posições falhou ({type(exc).__name__})")
            return {"posicoes_status": "UNKNOWN"}
        remotas = {i: Decimal(str(q)) for i, q in remotas_brutas.items()
                   if Decimal(str(q)) != 0}
        for inst in sorted(set(locais) | set(remotas)):
            local, remota = locais.get(inst, Decimal("0")), remotas.get(inst, Decimal("0"))
            if local == remota:
                continue
            if local == 0:
                relatorio["divergencias"].append(
                    f"POSICAO_ORFA_REMOTA {inst}: corretora tem {remota} e o "
                    "sistema não conhece essa posição")
            elif remota == 0:
                relatorio["divergencias"].append(
                    f"POSICAO_AUSENTE_REMOTA {inst}: sistema tem {local} e a "
                    "corretora não — NÃO apagada automaticamente")
            else:
                relatorio["divergencias"].append(
                    f"QUANTIDADE_DIVERGENTE {inst}: local {local} vs corretora {remota}")
        status = "MATCHED" if not any(
            d.startswith(("POSICAO_", "QUANTIDADE_"))
            for d in relatorio["divergencias"]) else "MISMATCH"
        if status == "MATCHED":
            self.posicoes_restauradas = {}     # só agora, após comparar
        return {"posicoes_status": status}

    def _aplicar_ordem_remota(self, rec, remota, relatorio: dict) -> None:
        """Traz o que a corretora diz para dentro do OMS, do livro e do risco.

        Passa pelo MESMO caminho de fill do fluxo normal — duplicar a lógica
        contábil aqui seria criar uma segunda verdade.
        """
        # Contrato de três respostas. Devolver capital exige CONFIRMAÇÃO de
        # ausência — "não achei na janela que consultei" não é confirmação.
        if isinstance(remota, dict) and "resultado" in remota:
            resultado = remota.get("resultado")
            if resultado == "UNKNOWN":
                relatorio["divergencias"].append(
                    f"{rec.order_id}: destino desconhecido na corretora "
                    f"({remota.get('motivo', 'sem motivo')}) — reserva mantida")
                return
            if resultado == "CONFIRMED_NOT_FOUND":
                rec.state = OrderState.REJECTED
                rec.note = "reconciliada: corretora CONFIRMA que não existe"
                self._liberar_reserva_de(rec)
                self.oms_version += 1
                return
            remota = remota.get("ordem")
        if remota is None:
            # Contrato antigo (dublês de teste): sem confirmação explícita,
            # trata-se como desconhecido — fail-closed.
            relatorio["divergencias"].append(
                f"{rec.order_id}: consulta sem confirmação de ausência — "
                "reserva mantida")
            return
        estado = str(remota.get("status", "")).lower()
        if estado in ("closed", "filled"):
            qtd = remota.get("filled")
            preco = remota.get("average") or remota.get("price")
            if qtd is None or preco is None:
                # Corretora diz "fechada" mas não dá os números: não se
                # inventa preço nem taxa. Fica incompleto e o portão fecha.
                relatorio["divergencias"].append(
                    f"{rec.order_id}: corretora reporta preenchida sem "
                    "quantidade/preço — RECONCILIACAO_INCOMPLETA")
                return
            # fill_id REAL: o id da ORDEM não serve — uma ordem tem vários
            # fills, e deduplicar por ela esconderia execuções parciais.
            fid = (remota.get("fill_id") or remota.get("trade_id")
                   or (f"{remota.get('id') or rec.order_id}:{qtd}@{preco}"))
            self.aplicar_fill(rec, side=rec.lado or "buy",
                              quantidade=Decimal(str(qtd)),
                              preco=Decimal(str(preco)),
                              taxa=Decimal(str(remota.get("fee") or 0)),
                              fill_id=str(fid))
            rec.external_id = remota.get("id") or rec.external_id
            rec.note = "reconciliada: preenchida na corretora"
        elif estado in ("canceled", "cancelled", "rejected", "expired"):
            rec.state = OrderState.REJECTED
            rec.note = f"reconciliada: {estado} na corretora"
            self._liberar_reserva_de(rec)   # capital preso é estado incoerente
            self.oms_version += 1
        else:
            relatorio["divergencias"].append(
                f"{rec.order_id}: corretora reporta {estado!r} — ainda aberta")

    def aplicar_fill(self, rec, *, side: str, quantidade: Decimal,
                     preco: Decimal, taxa: Decimal, fill_id: str) -> bool:
        """Operação canônica de execução — usada pelo fluxo normal E pela
        reconciliação.

        Antes a reconciliação chamava `fill_buy` incondicionalmente: uma
        VENDA reconciliada criava posição em vez de fechá-la, e o resultado
        realizado nunca aparecia. Duas implementações financeiras é uma
        verdade a mais do que o sistema pode ter.

        Devolve False quando o fill já foi contabilizado (idempotência).
        """
        if not fill_id:
            raise ValueError("fill sem identificador não pode ser aplicado")
        if fill_id in rec.seen_fills:
            return False                    # mesmo fill nunca conta duas vezes
        rec.seen_fills.add(fill_id)
        lado = (side or "").lower()
        instrumento = rec.instrumento

        if lado == "buy":
            reserva = self._reserva_de(rec)
            if reserva is not None:
                # RESERVA PROPORCIONAL: liberar tudo no primeiro parcial
                # deixaria a parte que ainda pode executar sem cobertura.
                alvo_total = rec.quantidade or quantidade
                proporcao = (quantidade / alvo_total) if alvo_total else Decimal("1")
                consumida = (reserva["amount"] * proporcao).quantize(
                    Decimal("0.00000001"))
                self.ledger.fill_buy(instrumento, quantidade, preco, taxa,
                                     reserved=consumida,
                                     ref=rec.order_id, agent_id=rec.agente)
                terminal = (rec.filled_qty + quantidade) >= alvo_total
                if terminal:
                    self.risk.release(reserva["reservation_id"])
        else:
            # SAÍDA: reduz a posição, realiza o resultado e alimenta o freio.
            custo = self._custo_proporcional(instrumento, quantidade)
            if custo <= 0:
                # Livro sem a posição (ex.: posição aberta fora deste livro).
                # Usa o custo declarado pela saída, proporcional à fatia — e
                # nunca zero, que transformaria a venda inteira em "lucro".
                ref = getattr(rec, "cost_basis_ref", Decimal("0")) or Decimal("0")
                alvo = rec.quantidade or quantidade
                custo = (Decimal(str(ref)) * quantidade / alvo) if alvo else Decimal(str(ref))
            self.ledger.fill_sell(instrumento, quantidade, preco, taxa,
                                  cost_basis=custo, agent_id=rec.agente,
                                  ref=rec.order_id)
            self.registrar_resultado_realizado(
                (preco * quantidade) - custo - taxa)

        anterior = rec.filled_qty
        rec.filled_qty = anterior + quantidade
        # preço médio ponderado pelos fills, não o último preço
        if rec.filled_qty > 0:
            rec.avg_price = ((anterior * rec.avg_price + quantidade * preco)
                             / rec.filled_qty)
        rec.fees = rec.fees + taxa
        alvo = rec.quantidade
        rec.state = (OrderState.FILLED if alvo and rec.filled_qty >= alvo
                     else OrderState.PARTIALLY_FILLED)
        self.oms_version += 1
        return True

    def _custo_proporcional(self, instrumento: str, quantidade: Decimal) -> Decimal:
        """Custo de aquisição da fatia vendida, proporcional à posição."""
        detalhe = self.ledger.open_positions_detail().get(instrumento)
        if not detalhe:
            return Decimal("0")
        qtd = Decimal(str(detalhe["quantidade"]))
        custo = Decimal(str(detalhe["custo_total"]))
        if qtd <= 0:
            return Decimal("0")
        return (custo * quantidade / qtd).quantize(Decimal("0.00000001"))

    def _reserva_de(self, rec) -> dict | None:
        if rec.intent is not None:
            return {"reservation_id": rec.intent.reservation.reservation_id,
                    "amount": rec.intent.reservation.amount}
        res_id = rec.reserva_id
        for doc in self.risk.active_reservations():
            if doc["reservation_id"] == res_id:
                return {"reservation_id": res_id, "amount": Decimal(str(doc["amount"]))}
        return None

    def reconciliacao_concluida(self, resultado: str = "conferido") -> None:
        """Só quem comparou com a corretora pode liberar.

        Limpa também as posições restauradas: enquanto elas constarem como
        'não conferidas', o servidor mantém a parada — e era isso que criava
        um impasse depois de uma reconciliação legítima.
        """
        self.reconciliacao_pendente = False
        self.motivo_bloqueio = ""
        self.posicoes_restauradas = {}

    def submit(self, intent: OrderIntent) -> OrderRecord:
        if self.reconciliacao_pendente:
            raise ExecutionRefused(
                "reconciliação pendente: nenhuma exposição nova até conferir "
                f"com a corretora ({self.motivo_bloqueio})")
        if intent.mode is Mode.LIVE and not self.mandate.live_authorized:
            raise ExecutionRefused(
                "ordem em LIVE sem autorização ativa no mandato")
        fp = fingerprint(intent)
        known = self._by_key.get(intent.idempotency_key)
        if known:
            rec = self.orders[known]
            if rec.fingerprint != fp:
                raise IdempotencyConflict(
                    "mesma chave idempotente com conteúdo diferente")
            return rec                      # retry legítimo: devolve a MESMA ordem
        try:
            intent.assert_sendable(worker_id=self.worker_id, mandate=self.mandate)
        except MandateError as exc:
            raise ExecutionRefused(str(exc)) from exc

        rec = OrderRecord(order_id=f"ord-{len(self.orders) + 1}", intent=intent,
                          state=OrderState.APPROVED, fingerprint=fp)
        self.orders[rec.order_id] = rec
        self._by_key[intent.idempotency_key] = rec.order_id

        rec.state = OrderState.SUBMITTING
        self.ledger.reserve(intent.reservation.amount, ref=rec.order_id)
        side = "buy" if intent.action is Action.BUY else "sell"
        try:
            result = self.broker.submit(intent.idempotency_key,
                                        intent.reservation.instrument, side,
                                        intent.reservation.quantity,
                                        **({"final_state": intent.provenance}
                                           if intent.provenance else {}))
        except BrokerTimeout:
            # Ambíguo: pode ter chegado. Nada de retry cego, nada de liberar.
            rec.state = OrderState.UNKNOWN_RECONCILIATION_REQUIRED
            rec.note = "timeout no envio — exige reconciliação"
            return rec
        except Exception as exc:
            rec.state = OrderState.REJECTED
            rec.note = f"{type(exc).__name__}: {exc}"[:200]
            self.ledger.release(intent.reservation.amount, ref=rec.order_id)
            self.risk.release(intent.reservation.reservation_id)
            return rec
        rec.external_id = result.get("id")
        rec.state = OrderState.ACKNOWLEDGED
        self.oms_version += 1
        # ACK NÃO É EXECUÇÃO. A resposta do envio confirma o ciclo de vida da
        # ordem; os efeitos econômicos vêm dos TRADES. Antes, o id da ORDEM
        # era usado como identificador de execução e `filled` cumulativo
        # entrava como delta: dois fills da mesma ordem seriam contados como
        # um, e um segundo relatório cumulativo somaria em cima do anterior.
        for fill in self._fills_da_resposta(result):
            self.aplicar_fill(rec, side=rec.lado or "buy", **fill)
        return rec

    @staticmethod
    def _fills_da_resposta(result: dict) -> list[dict]:
        """Extrai execuções INDIVIDUAIS da resposta do envio, se houver.

        Sem trades individualizados não se inventa um fill sintético a partir
        do acumulado: a ordem fica como ACK e os efeitos econômicos virão da
        consulta de execuções (fetch_order_fills) ou do evento da corretora.
        """
        trades = result.get("trades") or result.get("fills") or []
        saida = []
        for t in trades:
            fid = t.get("id") or t.get("fill_id") or t.get("tradeId")
            if not fid:
                continue        # execução sem identidade não é contabilizável
            saida.append({"fill_id": str(fid),
                          "quantidade": Decimal(str(t.get("amount")
                                                    or t.get("quantity") or 0)),
                          "preco": Decimal(str(t.get("price") or 0)),
                          "taxa": Decimal(str((t.get("fee") or {}).get("cost")
                                              if isinstance(t.get("fee"), dict)
                                              else t.get("fee") or 0))})
        return [f for f in saida if f["quantidade"] > 0]

    # -- eventos -------------------------------------------------------
    def apply_fill(self, order_id: str, *, fill_id: str, quantity: Decimal,
                   price: Decimal, fee: Decimal = Decimal("0")) -> OrderRecord:
        """ALIAS ESTRITO de `aplicar_fill`. Não tem lógica própria.

        Existiam duas implementações econômicas: esta só lançava no livro
        quando a ordem ficava totalmente preenchida (parciais não existiam
        economicamente), consumia a reserva inteira e assumia compra. A
        reconciliação usava a outra. Duas verdades sobre o mesmo dinheiro é
        exatamente o que um sistema financeiro não pode ter.
        """
        rec = self.orders[order_id]
        if rec.state in TERMINAL:
            return rec                      # fora de ordem após terminal: ignora
        self.aplicar_fill(rec, side=rec.lado or "buy", quantidade=quantity,
                          preco=price, taxa=fee, fill_id=fill_id)
        return rec

    def close_position(self, *, instrument: str, quantity: Decimal,
                       price: Decimal, cost_basis: Decimal, agent_id: str,
                       fee: Decimal = Decimal("0"), client_order_id: str = "",
                       provenance: dict | None = None) -> dict:
        """Encerra posição PELO OMS — saída é ordem financeira como entrada.

        Antes isto chamava broker.submit direto: sem OrderRecord, sem estado
        durável, sem idempotência, e o timeout devolvia um dicionário solto
        que morria com o processo. Era a segunda porta de dinheiro do
        sistema, e a auditoria cobrou quatro vezes.
        """
        coid = client_order_id or f"exit-{instrument}-{len(self.orders) + 1}"

        # IDEMPOTÊNCIA: mesma saída pedida de novo devolve a MESMA ordem.
        existente = self._by_key.get(coid)
        if existente:
            rec = self.orders[existente]
            return {"estado": rec.state.value, "client_order_id": coid,
                    "order_id": rec.order_id, "reaproveitada": True}

        rec = OrderRecord.__new__(OrderRecord)
        rec.order_id = f"ord-{len(self.orders) + 1}"
        rec.intent = None
        rec.state = OrderState.SUBMITTING
        rec.filled_qty = Decimal("0")
        rec.avg_price = Decimal("0")
        rec.fees = Decimal("0")
        rec.external_id = ""
        rec.fingerprint = f"exit|{instrument}|{quantity}|{cost_basis}"
        rec.note = ""
        rec.seen_fills = set()
        rec.instrument = instrument
        rec.side = "sell"
        rec.quantity = quantity
        rec.provenance = provenance
        rec._client_order_id = coid
        rec.agent_id = agent_id
        rec.reservation_id = ""
        rec.cost_basis_ref = cost_basis
        self.orders[rec.order_id] = rec
        self._by_key[coid] = rec.order_id
        self.oms_version += 1

        try:
            result = self.broker.submit(coid, instrument, "sell", quantity,
                                        **({"final_state": provenance}
                                           if provenance else {}))
        except BrokerTimeout:
            # AMBÍGUO: a corretora pode ter recebido. Nada de reenvio cego, e
            # o registro fica DURÁVEL para a reconciliação encontrar.
            rec.state = OrderState.UNKNOWN_RECONCILIATION_REQUIRED
            rec.note = "timeout na saída — reconciliar antes de qualquer envio"
            self.exigir_reconciliacao(
                f"saída {coid} em estado incerto: pode ter executado")
            self.oms_version += 1
            return {"estado": "incerto", "client_order_id": coid,
                    "order_id": rec.order_id,
                    "motivo": "timeout na saída — reconciliar"}
        except Exception as exc:
            rec.state = OrderState.REJECTED
            rec.note = f"{type(exc).__name__}: {exc}"[:200]
            self.oms_version += 1
            return {"estado": "rejeitada", "client_order_id": coid,
                    "order_id": rec.order_id, "motivo": rec.note}

        rec.external_id = result.get("id") or ""
        rec.state = OrderState.ACKNOWLEDGED
        for fill in self._fills_da_resposta(result):
            self.aplicar_fill(rec, side="sell", **fill)
        realizado = self.ledger.attribution().get(agent_id, Decimal("0"))
        return {"estado": rec.state.value, "client_order_id": coid,
                "order_id": rec.order_id, "preco": str(rec.avg_price),
                "quantidade": str(rec.filled_qty),
                "resultado": str(realizado)}

    def request_cancel(self, order_id: str) -> OrderRecord:
        rec = self.orders[order_id]
        if rec.state in TERMINAL:
            return rec
        rec.state = OrderState.CANCEL_PENDING   # pedido ≠ confirmado
        return rec

    def confirm_cancel(self, order_id: str) -> OrderRecord:
        rec = self.orders[order_id]
        if rec.state is not OrderState.CANCEL_PENDING:
            return rec
        rec.state = OrderState.CANCELLED
        self.ledger.release(rec.intent.reservation.amount, ref=rec.order_id)
        self.risk.release(rec.intent.reservation.reservation_id)
        return rec

    # -- reconciliação (MA-07) -----------------------------------------
    def reconcile(self, order_id: str, exchange_view: dict | None) -> OrderRecord:
        """Consulta o estado real na corretora antes de qualquer decisão.
        `exchange_view=None` significa 'ainda não sei' — permanece incerto."""
        rec = self.orders[order_id]
        if exchange_view is None:
            rec.note = "corretora indisponível — segue incerto, reserva mantida"
            return rec
        status = exchange_view.get("status")
        if status in ("filled", "closed"):
            self.apply_fill(order_id, fill_id=exchange_view.get("id", "recon"),
                            quantity=Decimal(str(exchange_view.get("filled", 0))),
                            price=Decimal(str(exchange_view.get("average", 0))),
                            fee=Decimal(str(exchange_view.get("fee", 0))))
            rec.note = "reconciliado: ordem existia e executou"
        elif status in ("canceled", "cancelled", "rejected", "absent"):
            rec.state = OrderState.CANCELLED if status != "rejected" else OrderState.REJECTED
            self.ledger.release(rec.intent.reservation.amount, ref=rec.order_id)
            self.risk.release(rec.intent.reservation.reservation_id)
            rec.note = f"reconciliado: {status} — reserva liberada com segurança"
        return rec

    def registrar_resultado_realizado(self, resultado: Decimal) -> Decimal:
        """Soma o resultado realizado ao acumulado do dia e propaga à ponte.

        Prejuízo entra como número positivo no contador de perda; lucro
        abate. A fronteira do dia é UTC explícita — usar hora local faria o
        limite "reiniciar" em horário arbitrário.
        """
        from datetime import timezone as _tz
        hoje = utcnow().astimezone(_tz.utc).date().isoformat()
        if self.dia_corrente != hoje:
            self.dia_corrente = hoje
            self.perda_do_dia = Decimal("0")
        if resultado < 0:
            self.perda_do_dia += -resultado
        else:
            self.perda_do_dia = max(Decimal("0"), self.perda_do_dia - resultado)
        ponte = getattr(self.broker, "bridge", None)
        if ponte is not None and hasattr(ponte, "set_daily_loss"):
            ponte.set_daily_loss(self.perda_do_dia)
        return self.perda_do_dia

    def estado_do_dia(self) -> dict:
        """Estado durável do freio: precisa sobreviver a reinício."""
        return {"dia": self.dia_corrente, "perda_do_dia": str(self.perda_do_dia)}

    def restaurar_estado_do_dia(self, doc: dict | None) -> None:
        if not doc:
            return
        from datetime import timezone as _tz
        hoje = utcnow().astimezone(_tz.utc).date().isoformat()
        if doc.get("dia") == hoje:          # dia anterior não contamina hoje
            self.dia_corrente = hoje
            self.perda_do_dia = Decimal(str(doc.get("perda_do_dia") or "0"))
            ponte = getattr(self.broker, "bridge", None)
            if ponte is not None and hasattr(ponte, "set_daily_loss"):
                ponte.set_daily_loss(self.perda_do_dia)



    # ============================== ESTADO FINANCEIRO DURÁVEL ==============
    # A auditoria encontrou o pior estado possível: reiniciar o processo
    # recriava orders={} e um Ledger zerado enquanto a exchange continuava
    # com posição aberta. O sistema então se achava livre para comprar de
    # novo. Aqui o estado vira documento persistível e restaurável.
    def snapshot_estado(self) -> dict:
        """Tudo o que precisa sobreviver a um reinício.

        Auditoria encontrou que este método chamava `open_positions_detail` e
        `active_reservations` que NÃO EXISTIAM: os `hasattr` devolviam falso
        e o documento saía com posições {} e reservas [] — silenciosamente.
        Agora os contratos existem e são exercitados; e o livro é persistido
        pelos LANÇAMENTOS, não por um resumo de saldos, para não destruir a
        auditabilidade.
        """
        return {
            "schema": "dz23.oms/2",
            "worker_id": self.worker_id,
            "dia": self.dia_corrente,
            "perda_do_dia": str(self.perda_do_dia),
            "oms_version": self.oms_version,
            "ultimo_relatorio": self.ultimo_relatorio,
            "ordens": [self._ordem_para_doc(r) for r in self.orders.values()],
            "posicoes": self.ledger.open_positions_detail(),
            "caixa": str(self.ledger.cash()),
            "moeda": self.ledger.quote_currency(),
            "lancamentos": self.ledger.export_transactions(),
            "reservas": self.risk.active_reservations(),
        }

    @staticmethod
    def _ordem_para_doc(r: OrderRecord) -> dict:
        """Documento COMPLETO da ordem, funcione ela com ou sem `intent`.

        Antes isto lia `r.intent.reservation` direto: uma ordem restaurada
        (que não tem intenção, por ser de outro processo) explodia ao ser
        salva de novo. Ou seja, o estado sobrevivia a UM reinício e se
        perdia no segundo — o pior tipo de defeito, porque só aparece tarde.
        """
        return {"order_id": r.order_id, "estado": r.state.value,
                "fingerprint": r.fingerprint, "filled": str(r.filled_qty),
                "preco_medio": str(r.avg_price), "fees": str(r.fees),
                "fills_vistos": sorted(str(f) for f in r.seen_fills),
                "external_id": r.external_id, "nota": r.note,
                "instrumento": r.instrumento,
                "quantidade": str(r.quantidade),
                "lado": r.lado,
                "client_order_id": r.client_order_id,
                "agente": r.agente,
                "reserva_id": r.reserva_id,
                "procedencia": r.procedencia}

    def restaurar_estado(self, doc: dict | None) -> dict:
        """REIDRATA o estado financeiro e diz o que ficou pendente.

        Antes isto só copiava documentos para listas auxiliares e ligava o
        portão: bloqueava operar (bom) mas NÃO reconstruía ordens, livro nem
        reservas — o sistema voltava sem saber quanto tinha e de quem era.

        O que NÃO se reconstrói aqui: qualquer coisa que dependa da
        corretora. Ordem incerta continua incerta; quem decide é a
        reconciliação, não este método.
        """
        if not doc or str(doc.get("schema", "")).startswith("dz23.oms") is False:
            return {"restaurado": False, "motivo": "sem estado anterior",
                    "pendencias": [], "exige_reconciliacao": False}
        self.restaurar_estado_do_dia(doc)
        # Sem isto o frescor vira teatro: versão zerada no restart faria um
        # relatório antigo (também zero) parecer atual.
        # Nunca REGREDIR: restaurar um documento antigo não pode fazer a
        # versão andar para trás, senão um relatório velho voltaria a valer.
        self.oms_version = max(self.oms_version, int(doc.get("oms_version") or 0))
        self.ultimo_relatorio = doc.get("ultimo_relatorio")

        # 1) LIVRO: reconstruído dos lançamentos (mantém a auditabilidade).
        lancamentos = doc.get("lancamentos")
        if lancamentos:
            from .ledger import Ledger as _L
            self.ledger = _L.from_transactions(
                lancamentos, currency=doc.get("moeda") or "USDT")

        # 2) RESERVAS: capital reservado não pode virar disponível.
        reservas_restauradas = self.risk.restore_reservations(doc.get("reservas", []))

        # 3) ORDENS: reconstruídas com estado, fills e idempotência.
        pendentes, abertas = [], []
        for od in doc.get("ordens", []):
            rec = self._ordem_de_doc(od)
            if rec is not None:
                self.orders[rec.order_id] = rec
                # A chave de idempotência é o client_order_id — a mesma usada
                # no fluxo normal. Restaurar por fingerprint quebrava o retry
                # após reinício: o mesmo pedido criaria uma SEGUNDA ordem.
                if rec.client_order_id:
                    self._by_key[rec.client_order_id] = rec.order_id
            estado = od.get("estado")
            if estado == OrderState.UNKNOWN_RECONCILIATION_REQUIRED.value:
                pendentes.append(od)
            elif estado in (OrderState.SUBMITTING.value,
                            OrderState.ACKNOWLEDGED.value,
                            OrderState.PARTIALLY_FILLED.value,
                            OrderState.CANCEL_PENDING.value):
                abertas.append(od)

        self.ordens_restauradas = doc.get("ordens", [])
        self.posicoes_restauradas = doc.get("posicoes", {})
        posicoes = doc.get("posicoes", {})
        exige = bool(pendentes or abertas or posicoes)
        if exige:
            self.exigir_reconciliacao(
                f"{len(pendentes)} ordem(ns) incerta(s), {len(abertas)} aberta(s) "
                f"e {len(posicoes)} posição(ões) restauradas do estado anterior")
        return {"restaurado": True,
                "ordens": len(self.orders),
                "lancamentos": len(lancamentos or []),
                "reservas": reservas_restauradas,
                "caixa": str(self.ledger.cash()),
                "pendencias": [o["order_id"] for o in pendentes],
                "abertas": [o["order_id"] for o in abertas],
                "posicoes": list(posicoes),
                "exige_reconciliacao": exige}

    def _liberar_reserva_de(self, rec) -> bool:
        """Libera a reserva de uma ordem, com ou sem a intenção em memória."""
        if rec.intent is not None:
            self.ledger.release(rec.intent.reservation.amount, ref=rec.order_id)
            self.risk.release(rec.intent.reservation.reservation_id)
            return True
        res_id = rec.reserva_id
        if not res_id:
            return False
        valor = None
        for doc in self.risk.active_reservations():
            if doc["reservation_id"] == res_id:
                valor = Decimal(str(doc["amount"]))
                break
        if valor is None:
            return False
        self.ledger.release(valor, ref=rec.order_id)
        self.risk.release(res_id)
        return True

    def _ordem_de_doc(self, od: dict):
        """Reconstrói um OrderRecord do documento persistido.

        Documento inválido é DESCARTADO com o portão já ligado — inventar
        uma ordem seria pior que não tê-la.
        """
        try:
            estado = OrderState(od["estado"])
        except (KeyError, ValueError):
            return None
        rec = OrderRecord.__new__(OrderRecord)
        rec.order_id = od.get("order_id", "")
        rec.intent = None
        rec.state = estado
        rec.filled_qty = Decimal(str(od.get("filled") or 0))
        rec.avg_price = Decimal(str(od.get("preco_medio") or 0))
        rec.fees = Decimal(str(od.get("fees") or 0))
        rec.external_id = od.get("external_id", "")
        rec.fingerprint = od.get("fingerprint", "")
        rec.note = od.get("nota", "")
        rec._client_order_id = od.get("client_order_id", "")
        rec.agent_id = od.get("agente", "")
        rec.reservation_id = od.get("reserva_id", "")
        rec.seen_fills = set(od.get("fills_vistos") or [])
        rec.instrument = od.get("instrumento", "")
        rec.side = od.get("lado", "")
        rec.quantity = Decimal(str(od.get("quantidade") or 0))
        rec.provenance = od.get("procedencia")
        return rec

    def pending_reconciliation(self) -> list[OrderRecord]:
        return [r for r in self.orders.values() if r.needs_reconciliation]


class PositionGuardian:
    """Cuida das posições quando o trader é pausado ou a IA falha (MA-06)."""

    def __init__(self, coordinator: ExecutionCoordinator):
        self.coordinator = coordinator
        self.adopted: dict[str, str] = {}

    def adopt(self, instrument: str, from_agent: str) -> str:
        self.adopted[instrument] = from_agent
        return f"guardião assumiu {instrument} (antes: {from_agent})"

    def watchlist(self) -> list[str]:
        return sorted({r.instrumento
                       for r in self.coordinator.orders.values() if r.open}
                      | set(self.adopted))


__all__ = ["ExecutionCoordinator", "PaperBroker", "PositionGuardian", "OrderState",
           "IdempotencyConflict", "ExecutionRefused", "BrokerTimeout", "fingerprint"]

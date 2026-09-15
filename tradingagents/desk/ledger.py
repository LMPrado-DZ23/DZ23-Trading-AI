"""Contabilidade da mesa (TA-010).

Lançamentos imutáveis em partidas dobradas: toda linha tem contrapartida e a
soma dos débitos é igual à dos créditos, sempre. Correção só por compensação
— nada é editado retroativamente para o resultado parecer melhor.

Contas usadas:
  CASH:<moeda>          caixa disponível
  POSITION:<instrumento> posição em quantidade valorizada ao custo
  FEES                   custos de corretagem
  PNL:<agente>           resultado atribuído ao trader
  RESERVED               capital travado por reserva de risco
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from .models import utcnow

ZERO = Decimal("0")


class LedgerError(RuntimeError):
    """Lançamento desbalanceado ou conta inexistente."""


@dataclass(frozen=True)
class Entry:
    """Uma linha imutável. Débito positivo, crédito negativo."""
    account: str
    amount: Decimal
    memo: str = ""


def _instante(valor):
    """Converte o instante persistido, preservando o que foi gravado."""
    from datetime import datetime
    if isinstance(valor, datetime):
        return valor
    try:
        return datetime.fromisoformat(str(valor))
    except ValueError:
        return utcnow()


@dataclass(frozen=True)
class Transaction:
    tx_id: str
    entries: tuple[Entry, ...]
    kind: str
    at: datetime = field(default_factory=utcnow)
    ref: str = ""
    # Quantidade e dono não são deriváveis dos saldos (o livro guarda CUSTO,
    # não unidades). Sem isto, restaurar a posição era impossível.
    instrument: str = ""
    quantity: Decimal = ZERO
    agent_id: str = ""
    # Movimento de capital: a que se destina e a qual incidente pertence.
    # Sem o vínculo, um aporte feito para o incidente A resolvia o B.
    purpose: str = ""
    incident_id: str = ""
    settlement_id: str = ""

    def validate(self) -> None:
        total = sum((e.amount for e in self.entries), ZERO)
        if total != ZERO:
            raise LedgerError(f"transação {self.kind} desbalanceada em {total}")
        if len(self.entries) < 2:
            raise LedgerError("partida dobrada exige ao menos duas linhas")


class Ledger:
    """Livro-razão append-only. Saldos derivam das linhas, nunca de um campo."""

    def __init__(self, opening_cash: Decimal = ZERO, currency: str = "USDT"):
        self.currency = currency
        self._txs: list[Transaction] = []
        if opening_cash:
            self._post("opening", [Entry(f"CASH:{currency}", opening_cash, "abertura"),
                                   Entry("EQUITY", -opening_cash, "aporte inicial")])

    # -- núcleo --------------------------------------------------------
    def _post(self, kind: str, entries, ref: str = "", *, instrument: str = "",
              quantity: Decimal = ZERO, agent_id: str = "", purpose: str = "",
              incident_id: str = "", settlement_id: str = "",
              at=None) -> Transaction:
        tx = Transaction(uuid.uuid4().hex[:16], tuple(entries), kind, ref=ref,
                         instrument=instrument, quantity=quantity,
                         agent_id=agent_id, purpose=purpose,
                         incident_id=incident_id, settlement_id=settlement_id,
                         **({"at": at} if at is not None else {}))
        tx.validate()
        self._txs.append(tx)
        return tx

    def balance(self, account: str) -> Decimal:
        return sum((e.amount for tx in self._txs for e in tx.entries
                    if e.account == account), ZERO)

    def balances(self) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for tx in self._txs:
            for e in tx.entries:
                out[e.account] = out.get(e.account, ZERO) + e.amount
        return {k: v for k, v in out.items() if v != ZERO}

    @property
    def transactions(self) -> tuple[Transaction, ...]:
        return tuple(self._txs)

    def is_balanced(self) -> bool:
        return sum(self.balances().values(), ZERO) == ZERO

    # -- operações da mesa ---------------------------------------------
    @staticmethod
    def conta_reserva(reservation_id: str = "") -> str:
        """Conta de capital travado DESTA reserva.

        Uma conta global fazia o livro saber apenas o total: uma ordem podia
        consumir ou liberar dinheiro de outra, e a conferência por soma não
        via nada de errado (R1=50/R2=150 soma igual a R1=100/R2=100).
        """
        return f"RESERVED:{reservation_id}" if reservation_id else "RESERVED"

    def reserve(self, amount: Decimal, ref: str,
                reservation_id: str = "") -> Transaction:
        conta = self.conta_reserva(reservation_id)
        return self._post("reserve", [Entry(conta, amount, "capital travado"),
                                      Entry(f"CASH:{self.currency}", -amount)], ref)

    def release(self, amount: Decimal, ref: str,
                reservation_id: str = "") -> Transaction:
        # Liberar mais do que ESTA reserva tem levaria a conta abaixo de zero
        # — dinheiro criado do nada, com o livro ainda "balanceado".
        if amount <= 0:
            raise ValueError(f"liberação precisa ser positiva (recebido {amount})")
        conta = self.conta_reserva(reservation_id)
        reservado = self.balances().get(conta, ZERO)
        if amount > reservado:
            raise ValueError(
                f"liberação de {amount} maior que o reservado em {conta} "
                f"({reservado})")
        return self._post("release", [Entry(f"CASH:{self.currency}", amount),
                                      Entry(conta, -amount, "reserva liberada")], ref)

    def transferir_reserva_legada(self, amount: Decimal,
                                  reservation_id: str) -> Transaction:
        """Move o saldo da conta global antiga para a conta identificada.

        Só é chamada quando há evidência de quem é o dono — nunca por rateio.
        """
        return self._post("migracao_reserva",
                          [Entry(self.conta_reserva(reservation_id), amount,
                                 "reserva identificada na migração"),
                           Entry("RESERVED", -amount, "saldo legado movido")],
                          f"migracao:{reservation_id}")

    def fill_buy(self, instrument: str, quantity: Decimal, price: Decimal,
                 fee: Decimal, *, reserved: Decimal, ref: str = "",
                 agent_id: str = "", reservation_id: str = "",
                 fee_in_other_currency: tuple | None = None) -> Transaction:
        """Compra executada: reserva vira posição; sobra volta ao caixa.

        `reserved` sai da conta DESTA reserva — nunca do bolo comum.
        """
        cost = (quantity * price).quantize(Decimal("0.00000001"))
        # A taxa tem MOEDA. Guardar tudo numa conta "FEES" fazia 0,001 BNB
        # somar 0,001 ao custo em USDT — número errado num livro que fecha,
        # porque a aritmética está certa e o significado não.
        entries = [Entry(f"POSITION:{instrument}", cost, "posição ao custo"),
                   Entry(f"FEES:{self.currency}", fee, "corretagem"),
                   Entry(self.conta_reserva(reservation_id), -reserved,
                         "reserva consumida")]
        rest = reserved - cost - fee
        if rest != ZERO:
            entries.append(Entry(f"CASH:{self.currency}", rest, "sobra devolvida"))
        if fee_in_other_currency:
            valor, moeda = fee_in_other_currency
            valor = Decimal(str(valor))
            if moeda == self.currency:
                raise ValueError(
                    "fee_in_other_currency é para moeda DIFERENTE da cotação; "
                    f"use o parâmetro fee para {self.currency}")
            if valor != ZERO:
                # Sai do ativo pago e entra na despesa daquela moeda. Nenhuma
                # conversão: converter com cotação adivinhada produz um número
                # que parece certo e não é.
                entries.append(Entry(f"FEES:{moeda}", valor,
                                     "corretagem em outra moeda"))
                entries.append(Entry(f"ASSET:{moeda}", -valor,
                                     "ativo usado para pagar a corretagem"))
        return self._post("fill_buy", entries, ref, instrument=instrument,
                          quantity=quantity, agent_id=agent_id)

    def fill_sell(self, instrument: str, quantity: Decimal, price: Decimal,
                  fee: Decimal, *, cost_basis: Decimal, agent_id: str,
                  ref: str = "",
                  fee_in_other_currency: tuple | None = None) -> Transaction:
        """Venda executada: baixa a posição pelo custo e atribui o resultado.

        A conta PNL:<agente> carrega o resultado BRUTO (venda − custo); a
        corretagem vive em FEES, visível e nunca embutida. Somar as duas é
        o resultado líquido — contar a taxa nos dois lugares desbalanceia o
        livro (erro pego por teste ao implementar as saídas).
        """
        proceeds = (quantity * price).quantize(Decimal("0.00000001"))
        bruto = proceeds - cost_basis
        entries = [Entry(f"CASH:{self.currency}", proceeds - fee, "caixa da venda"),
                   Entry(f"FEES:{self.currency}", fee, "corretagem da saída"),
                   Entry(f"POSITION:{instrument}", -cost_basis, "baixa ao custo"),
                   Entry(f"PNL:{agent_id}", -bruto, "resultado bruto atribuído")]
        if fee_in_other_currency:
            valor, moeda = fee_in_other_currency
            valor = Decimal(str(valor))
            if moeda == self.currency:
                raise ValueError(
                    "fee_in_other_currency é para moeda DIFERENTE da cotação")
            if valor != ZERO:
                # Fora do PnL em cotação: descontar 5 BNB de um resultado em
                # USDT misturaria unidades.
                entries.append(Entry(f"FEES:{moeda}", valor,
                                     "corretagem da saída em outra moeda"))
                entries.append(Entry(f"ASSET:{moeda}", -valor,
                                     "ativo usado para pagar a corretagem"))
        return self._post("fill_sell", entries, ref, instrument=instrument,
                          quantity=quantity, agent_id=agent_id)

    def ai_cost(self, amount: Decimal, agent_id: str, ref: str = "") -> Transaction:
        """Custo de IA é custo real — entra na conta, não some."""
        return self._post("ai_cost", [Entry("COST:AI", amount, f"IA {agent_id}"),
                                      Entry(f"CASH:{self.currency}", -amount)], ref)

    def compensate(self, tx: Transaction, why: str) -> Transaction:
        """Corrige por compensação — a linha errada continua no histórico."""
        return self._post("compensation",
                          [Entry(e.account, -e.amount, why) for e in tx.entries],
                          ref=tx.tx_id)

    # -- relatórios -----------------------------------------------------
    def attribution(self) -> dict[str, Decimal]:
        """Resultado BRUTO realizado por trader (custos ficam em FEES)."""
        return {acc.split(":", 1)[1]: -bal for acc, bal in self.balances().items()
                if acc.startswith("PNL:")}

    def quote_currency(self) -> str:
        return self.currency

    def cash(self) -> Decimal:
        """Caixa na moeda de cotação do livro.

        O snapshot procurava a conta 'CASH' fixa, que não existe (a conta é
        'CASH:USDT'), e persistia zero. Caixa errado no restore é perda de
        verdade financeira.
        """
        return self.balances().get(f"CASH:{self.currency}", ZERO)

    def open_positions_detail(self) -> dict:
        """Posições abertas com custo e dono — o necessário para reconstruir.

        Derivado dos lançamentos, como todo o resto: o livro não guarda
        objeto de posição, e inventar um seria criar uma segunda verdade.
        """
        saldos = self.balances()
        detalhe: dict[str, dict] = {}
        for conta, custo in saldos.items():
            if not conta.startswith("POSITION:") or custo <= ZERO:
                continue
            instrumento = conta.split(":", 1)[1]
            qtd = self.position_quantity(instrumento)
            detalhe[instrumento] = {
                "instrumento": instrumento,
                "quantidade": str(qtd),
                "custo_total": str(custo),
                "preco_medio": str((custo / qtd) if qtd else ZERO),
                "agente": self.position_owner(instrumento),
            }
        return detalhe

    # ---------------------------------------------- visão de capital
    # Estados DISTINTOS do dinheiro. Tratá-los como sinônimos é o que fazia o
    # capital investido reaparecer como disponível:
    #   caixa + reservado + investido = patrimônio (antes do resultado)
    def cash_available(self) -> Decimal:
        """Dinheiro livre: nem reservado, nem transformado em posição."""
        return self.cash()

    def reserved_cash(self, reservation_id: str | None = None) -> Decimal:
        """Dinheiro travado — de UMA reserva, ou o total.

        Sem identificador devolve a soma, que serve para métricas. Toda
        MOVIMENTAÇÃO, porém, exige a reserva específica: é o que impede uma
        ordem de gastar a cobertura da outra.
        """
        saldos = self.balances()
        if reservation_id is not None:
            return saldos.get(self.conta_reserva(reservation_id), ZERO)
        return sum((s for c, s in saldos.items()
                    if c == "RESERVED" or c.startswith("RESERVED:")), ZERO)

    def find_incident_compensations(self, incident_id: str) -> list[dict]:
        """Compensações já lançadas para este incidente.

        É o que permite ao retry pós-crash DESCOBRIR que o dinheiro já saiu,
        em vez de lançar de novo.
        """
        return [tx for tx in self.export_transactions()
                if tx.get("kind") == "risk_incident_compensation"
                and tx.get("incident_id") == incident_id]

    def fee_valuation(self, *, moeda: str, quantidade: Decimal,
                      rate: Decimal, fonte: str, em: str,
                      incident_id: str = "") -> Transaction:
        """Converte a taxa registrada em outra moeda para a de cotação.

        Enquanto isto não acontece, a taxa existe no livro na moeda original
        e o custo real em cotação é desconhecido. A procedência da cotação
        vai no memo: sem ela, ninguém audita de onde saiu o número.
        """
        if quantidade <= ZERO or rate <= ZERO:
            raise ValueError("valoração exige quantidade e cotação positivas")
        memo = (f"taxa {quantidade} {moeda} valorada a {rate} "
                f"{self.currency}/{moeda} | fonte {fonte} | em {em}")
        valor = (quantidade * rate).quantize(Decimal("0.00000001"))
        # Baixa apenas o que REALMENTE está registrado naquela moeda: zerar
        # mais do que existe criaria saldo negativo — o oposto do conserto.
        registrado = self.balances().get(f"FEES:{moeda}", ZERO)
        baixa = min(quantidade, registrado) if registrado > ZERO else ZERO
        entradas = [Entry(f"FEES:{self.currency}", valor, memo)]
        if baixa > ZERO:
            entradas.append(Entry(f"FEES:{moeda}", -baixa, memo))
        # A diferença de unidade fica explícita numa conta de conversão, em
        # vez de sumir dentro do custo.
        entradas.append(Entry("FX:CONVERSION", baixa - valor, memo))
        return self._post("fee_valuation", entradas,
                          incident_id or f"valoracao:{moeda}",
                          incident_id=incident_id)

    def find_incident_compensation(self, incident_id: str,
                                   settlement_id: str) -> dict | None:
        """A compensação DESTA liquidação — exige os dois identificadores.

        Buscar só pelo incidente fazia uma liquidação adotar a transação de
        outra e reescrever a linhagem: o sistema seguia como se tivesse
        pago, sem saber qual das duas identidades era a verdadeira.
        """
        for tx in self.find_incident_compensations(incident_id):
            if tx.get("settlement_id") == settlement_id:
                return tx
        return None

    def risk_incident_compensation(self, *, incident_id: str, amount: Decimal,
                                   external_reference: str, actor: str,
                                   reason: str = "",
                                   settlement_id: str = "") -> Transaction:
        """Compensação de UM incidente específico.

        Um aporte genérico não serve de prova: ele pode ter sido feito por
        qualquer motivo, e aceitar qualquer um ainda não usado deixava um
        aporte destinado ao incidente A resolver o B. A transação nasce
        amarrada ao incidente.
        """
        if amount <= ZERO:
            raise ValueError(
                f"compensação precisa ser positiva (recebido {amount}): "
                "retirada não cobre déficit")
        if not incident_id:
            raise ValueError("compensação exige o incidente de destino")
        return self.capital_adjustment(
            amount, reason=reason or f"compensação do incidente {incident_id}",
            actor=actor, external_reference=external_reference,
            kind="risk_incident_compensation",
            purpose="RISK_INCIDENT_COMPENSATION", incident_id=incident_id,
            settlement_id=settlement_id)

    def capital_withdrawal(self, amount: Decimal, *, reason: str, actor: str,
                           external_reference: str = "") -> Transaction:
        """Retirada de capital — nunca cobre déficit."""
        if amount <= ZERO:
            raise ValueError("retirada precisa ser positiva")
        return self.capital_adjustment(-amount, reason=reason, actor=actor,
                                       external_reference=external_reference,
                                       kind="capital_withdrawal",
                                       purpose="CAPITAL_WITHDRAWAL")

    def capital_adjustment(self, amount: Decimal, *, reason: str, actor: str,
                           external_reference: str = "",
                           kind: str = "capital_adjustment",
                           purpose: str = "",
                           incident_id: str = "",
                           settlement_id: str = "") -> Transaction:
        """Aporte ou retirada de capital EXTERNO, com partida dobrada.

        Entra como CONTRIBUTED_CAPITAL, não como resultado: dinheiro que o
        dono colocou não é lucro de trader, e misturar os dois faria a
        atribuição de performance mentir na direção mais perigosa — a de
        parecer que a estratégia ganhou.

        O lançamento de abertura NUNCA é reescrito: reescrever história
        apaga a evidência de que houve um ajuste.
        """
        if amount == ZERO:
            raise ValueError("ajuste de capital não pode ser zero")
        if not reason or not actor:
            raise ValueError("ajuste de capital exige motivo e responsável")
        memo = f"{reason} | por {actor}"
        if external_reference:
            memo += f" | ref {external_reference}"
        return self._post(kind,
                          [Entry(f"CASH:{self.currency}", amount, memo),
                           Entry("CONTRIBUTED_CAPITAL", -amount, memo)],
                          external_reference or reason,
                          purpose=purpose or ("CAPITAL_CONTRIBUTION"
                                              if amount > 0
                                              else "CAPITAL_WITHDRAWAL"),
                          incident_id=incident_id,
                          settlement_id=settlement_id)

    def find_transaction(self, tx_id: str) -> dict | None:
        for tx in self.export_transactions():
            if tx["tx_id"] == tx_id:
                return tx
        return None

    def reservation_balances(self) -> dict:
        """Saldo travado POR reserva — uma leitura só, sem parsing espalhado."""
        saldos = {}
        for conta, saldo in self.balances().items():
            if conta.startswith("RESERVED:") and saldo != ZERO:
                saldos[conta.split(":", 1)[1]] = saldo
        return saldos

    def invested_cost(self) -> Decimal:
        """Custo de aquisição das posições ABERTAS.

        Este é o valor que sumia da conta: depois do fill a reserva acaba, e
        sem esta parcela o mesmo dinheiro voltava a parecer livre.
        """
        total = ZERO
        for conta, saldo in self.balances().items():
            if conta.startswith("POSITION:"):
                total += saldo
        return total

    def portfolio_equity(self) -> Decimal:
        """Patrimônio contábil: caixa + reservado + custo das posições.

        Não inclui resultado NÃO realizado — posição a mercado exigiria preço
        corrente, que o livro não conhece e não deve adivinhar.
        """
        return self.cash_available() + self.reserved_cash() + self.invested_cost()

    def position_lots(self, instrument: str) -> list[dict]:
        """Lotes vivos POR AGENTE, derivados dos lançamentos.

        O dono da posição era o agente da COMPRA MAIS RECENTE: com dois
        agentes no mesmo instrumento, o último a comprar podia fechar o lote
        do outro. Aqui cada agente tem seu lote, e a soma dos lotes é
        exatamente a posição agregada — não existe segunda verdade, tudo sai
        do mesmo histórico append-only.

        Saídas consomem FIFO dentro do lote do próprio agente.
        """
        compras: dict[str, list[dict]] = {}
        for tx in self._txs:
            if tx.instrument != instrument or tx.quantity <= ZERO:
                continue
            agente = tx.agent_id or "desconhecido"
            if tx.kind == "fill_buy":
                custo = sum((e.amount for e in tx.entries
                             if e.account.startswith("POSITION:")), ZERO)
                compras.setdefault(agente, []).append(
                    {"quantidade": tx.quantity, "custo": custo,
                     "origem": tx.ref, "em": tx.at})
            elif tx.kind == "fill_sell":
                restante = tx.quantity
                for lote in compras.get(agente, []):
                    if restante <= ZERO:
                        break
                    consome = min(lote["quantidade"], restante)
                    if lote["quantidade"] > 0:
                        lote["custo"] -= (lote["custo"] * consome
                                          / lote["quantidade"])
                    lote["quantidade"] -= consome
                    restante -= consome
        saida = []
        for agente, lotes in compras.items():
            viva = sum((lote["quantidade"] for lote in lotes), ZERO)
            custo = sum((lote["custo"] for lote in lotes), ZERO)
            saida.append({"agente": agente, "instrumento": instrument,
                          "quantidade": str(viva), "custo_restante": str(custo),
                          "origem": "DZ23"})
        return saida

    def lot_quantity(self, instrument: str, agent_id: str) -> Decimal:
        """Quanto ESTE agente ainda tem — a base para autorizar uma saída."""
        for lote in self.position_lots(instrument):
            if lote["agente"] == agent_id:
                return Decimal(lote["quantidade"])
        return ZERO

    def lot_cost(self, instrument: str, agent_id: str) -> Decimal:
        for lote in self.position_lots(instrument):
            if lote["agente"] == agent_id:
                return Decimal(lote["custo_restante"])
        return ZERO

    def position_quantity(self, instrument: str) -> Decimal:
        """Quantidade viva do instrumento, somada dos fills registrados."""
        qtd = ZERO
        for tx in self._txs:
            if tx.instrument != instrument:
                continue
            if tx.kind == "fill_buy":
                qtd += tx.quantity
            elif tx.kind == "fill_sell":
                qtd -= tx.quantity
        return qtd

    def position_owner(self, instrument: str) -> str:
        for tx in reversed(self._txs):
            if tx.instrument == instrument and tx.kind == "fill_buy":
                return tx.agent_id or ""
        return ""

    def export_transactions(self) -> list[dict]:
        """Lançamentos para persistência. Guardar só saldos destruiria a
        auditabilidade — e é o histórico que prova o resultado."""
        return [
            {"tx_id": tx.tx_id, "kind": tx.kind, "ref": tx.ref,
             # O INSTANTE é parte do histórico: sem ele, cada reinício
             # reescrevia a data e a ordem dos fatos se perdia — justamente
             # o que a auditoria precisa para saber o que veio antes.
             "at": tx.at.isoformat() if hasattr(tx.at, "isoformat") else str(tx.at),
             "incident_id": getattr(tx, "incident_id", ""),
             "settlement_id": getattr(tx, "settlement_id", ""),
             "purpose": getattr(tx, "purpose", ""),
             "instrument": getattr(tx, "instrument", ""),
             "quantity": str(getattr(tx, "quantity", ZERO)),
             "agent_id": getattr(tx, "agent_id", ""),
             "entries": [{"account": e.account, "amount": str(e.amount),
                          "memo": e.memo} for e in tx.entries]}
            for tx in self._txs]

    @classmethod
    def from_transactions(cls, docs: list[dict], currency: str = "USDT") -> Ledger:
        """Reconstrói o livro a partir dos lançamentos persistidos.

        Sem abertura automática: o caixa vem do histórico, senão o restore
        criaria dinheiro que não existe.
        """
        led = cls(opening_cash=ZERO, currency=currency)
        for doc in docs or []:
            entries = tuple(
                Entry(e["account"], Decimal(str(e["amount"])), e.get("memo", ""))
                for e in doc.get("entries", []))
            if not entries:
                continue
            tx = Transaction(
                doc.get("tx_id") or uuid.uuid4().hex[:16], entries,
                doc.get("kind", "restored"), ref=doc.get("ref", ""),
                instrument=doc.get("instrument", ""),
                quantity=Decimal(str(doc.get("quantity") or 0)),
                agent_id=doc.get("agent_id", ""),
                purpose=doc.get("purpose", ""),
                incident_id=doc.get("incident_id", ""),
                settlement_id=doc.get("settlement_id", ""),
                # O instante ORIGINAL, não o de agora: substituir por utcnow
                # reescreveria a data de todo o histórico a cada reinício.
                **({"at": _instante(doc["at"])} if doc.get("at") else {}))
            tx.validate()               # lançamento desbalanceado não entra
            led._txs.append(tx)
        return led

    def open_positions(self) -> set[str]:
        """Instrumentos com posição viva (saldo de custo > 0)."""
        return {acc.split(":", 1)[1] for acc, bal in self.balances().items()
                if acc.startswith("POSITION:") and bal > ZERO}

    def equity(self) -> Decimal:
        """Caixa + reservado + posições ao custo. Não estima preço de mercado."""
        b = self.balances()
        return (b.get(f"CASH:{self.currency}", ZERO) + b.get("RESERVED", ZERO)
                + sum((v for k, v in b.items() if k.startswith("POSITION:")), ZERO))


__all__ = ["Ledger", "Entry", "Transaction", "LedgerError"]

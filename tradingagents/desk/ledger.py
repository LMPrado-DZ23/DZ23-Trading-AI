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
              quantity: Decimal = ZERO, agent_id: str = "") -> Transaction:
        tx = Transaction(uuid.uuid4().hex[:16], tuple(entries), kind, ref=ref,
                         instrument=instrument, quantity=quantity,
                         agent_id=agent_id)
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
    def reserve(self, amount: Decimal, ref: str) -> Transaction:
        return self._post("reserve", [Entry("RESERVED", amount, "capital travado"),
                                      Entry(f"CASH:{self.currency}", -amount)], ref)

    def release(self, amount: Decimal, ref: str) -> Transaction:
        return self._post("release", [Entry(f"CASH:{self.currency}", amount),
                                      Entry("RESERVED", -amount, "reserva liberada")], ref)

    def fill_buy(self, instrument: str, quantity: Decimal, price: Decimal,
                 fee: Decimal, *, reserved: Decimal, ref: str = "",
                 agent_id: str = "") -> Transaction:
        """Compra executada: reserva vira posição; sobra volta ao caixa."""
        cost = (quantity * price).quantize(Decimal("0.00000001"))
        entries = [Entry(f"POSITION:{instrument}", cost, "posição ao custo"),
                   Entry("FEES", fee, "corretagem"),
                   Entry("RESERVED", -reserved, "reserva consumida")]
        rest = reserved - cost - fee
        if rest != ZERO:
            entries.append(Entry(f"CASH:{self.currency}", rest, "sobra devolvida"))
        return self._post("fill_buy", entries, ref, instrument=instrument,
                          quantity=quantity, agent_id=agent_id)

    def fill_sell(self, instrument: str, quantity: Decimal, price: Decimal,
                  fee: Decimal, *, cost_basis: Decimal, agent_id: str,
                  ref: str = "") -> Transaction:
        """Venda executada: baixa a posição pelo custo e atribui o resultado.

        A conta PNL:<agente> carrega o resultado BRUTO (venda − custo); a
        corretagem vive em FEES, visível e nunca embutida. Somar as duas é
        o resultado líquido — contar a taxa nos dois lugares desbalanceia o
        livro (erro pego por teste ao implementar as saídas).
        """
        proceeds = (quantity * price).quantize(Decimal("0.00000001"))
        bruto = proceeds - cost_basis
        entries = [Entry(f"CASH:{self.currency}", proceeds - fee, "caixa da venda"),
                   Entry("FEES", fee, "corretagem da saída"),
                   Entry(f"POSITION:{instrument}", -cost_basis, "baixa ao custo"),
                   Entry(f"PNL:{agent_id}", -bruto, "resultado bruto atribuído")]
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
                agent_id=doc.get("agent_id", ""))
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

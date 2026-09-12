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
from decimal import Decimal
from datetime import datetime

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
    def _post(self, kind: str, entries, ref: str = "") -> Transaction:
        tx = Transaction(uuid.uuid4().hex[:16], tuple(entries), kind, ref=ref)
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
                 fee: Decimal, *, reserved: Decimal, ref: str = "") -> Transaction:
        """Compra executada: reserva vira posição; sobra volta ao caixa."""
        cost = (quantity * price).quantize(Decimal("0.00000001"))
        entries = [Entry(f"POSITION:{instrument}", cost, "posição ao custo"),
                   Entry("FEES", fee, "corretagem"),
                   Entry("RESERVED", -reserved, "reserva consumida")]
        rest = reserved - cost - fee
        if rest != ZERO:
            entries.append(Entry(f"CASH:{self.currency}", rest, "sobra devolvida"))
        return self._post("fill_buy", entries, ref)

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
        return self._post("fill_sell", entries, ref)

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

"""Intenção de execução — contrato ÚNICO para entrada e saída.

Motivo desta existência: entrada e saída eram construídas por caminhos
diferentes, com validações diferentes e `OrderRecord` montado à mão em um
deles. Quase todos os defeitos das últimas auditorias nasceram dessa
divergência — timeout que ligava o portão de um lado e não do outro,
`intent` que existia num caminho e não no outro, idempotência avaliada em
ordens diferentes, posse verificada só na saída.

Aqui as duas viram a MESMA coisa: uma intenção com lado, instrumento,
quantidade, dono e procedência. O que muda entre comprar e vender são as
regras de autorização, declaradas explicitamente — não o caminho.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class Kind(str, Enum):
    ENTRY = "entry"
    EXIT = "exit"


class IntentInvalid(ValueError):
    """Intenção malformada — recusada antes de qualquer validação de estado."""


@dataclass(frozen=True)
class ExecutionIntent:
    """O que se quer executar. Imutável: uma intenção não muda de ideia."""

    kind: Kind
    side: Side
    instrument: str
    quantity: Decimal
    agent_id: str
    account: str
    mandate_version: int = 1
    client_order_id: str = ""
    provenance: dict | None = None
    # Entrada: identifica a reserva que cobre a ordem.
    reservation_id: str = ""
    reserved_amount: Decimal = Decimal("0")
    # Saída: custo de aquisição declarado, usado quando o livro não conhece
    # a posição. Nunca autoriza a venda — só mede o resultado.
    cost_basis_ref: Decimal = Decimal("0")
    metadata: dict = field(default_factory=dict)
    # Stop protetivo da entrada como fração NEGATIVA do preço executado.
    # Viaja na intenção porque é o executor que converte em preço absoluto
    # depois do fill — usar o preço do sinal erraria pelo slippage.
    stop_pct: Decimal | None = None

    def __post_init__(self):
        if not self.instrument:
            raise IntentInvalid("intenção sem instrumento")
        if self.quantity is None or self.quantity <= 0:
            raise IntentInvalid(
                f"quantidade precisa ser positiva (recebido: {self.quantity})")
        if not self.agent_id:
            raise IntentInvalid("intenção sem agente responsável")
        if self.kind is Kind.ENTRY and self.side is not Side.BUY:
            raise IntentInvalid("entrada só existe como compra neste sistema")
        if self.kind is Kind.EXIT and self.side is not Side.SELL:
            raise IntentInvalid("saída só existe como venda neste sistema")
        if self.stop_pct is not None:
            if self.kind is not Kind.ENTRY:
                raise IntentInvalid("stop protetivo só existe em entrada")
            if not (Decimal("-1") < self.stop_pct < Decimal("0")):
                raise IntentInvalid(
                    f"stop_pct inválido ({self.stop_pct}); precisa estar "
                    "entre -1 e 0")

    @property
    def is_exit(self) -> bool:
        return self.kind is Kind.EXIT

    def fingerprint(self) -> str:
        """Impressão digital CANÔNICA do pedido.

        Mesma fórmula para entrada e saída: dois pedidos iguais têm a mesma
        impressão, e o mesmo identificador com conteúdo diferente é conflito.
        Fórmulas distintas por caminho foi o que deixou a entrada sem
        detecção de conflito enquanto a saída tinha.
        """
        base = "|".join([
            self.kind.value, self.side.value, self.instrument,
            f"{self.quantity:f}", self.agent_id, self.account,
            str(self.mandate_version),
            f"{self.reserved_amount:f}", f"{self.cost_basis_ref:f}",
        ])
        return "dz23:" + hashlib.sha256(base.encode()).hexdigest()[:24]

    def descricao(self) -> str:
        return (f"{self.kind.value} {self.side.value} {self.quantity} "
                f"{self.instrument} por {self.agent_id}")


def from_order_intent(order_intent, provenance: dict | None = None
                      ) -> ExecutionIntent:
    """Converte a intenção de ENTRADA autorizada pelo risco."""
    from .models import Action
    reserva = order_intent.reservation
    return ExecutionIntent(
        kind=Kind.ENTRY,
        side=Side.BUY if order_intent.action is Action.BUY else Side.SELL,
        instrument=reserva.instrument,
        quantity=reserva.quantity,
        agent_id=reserva.agent_id or "",
        account=reserva.account or "",
        mandate_version=getattr(reserva, "mandate_version", 1),
        client_order_id=order_intent.idempotency_key,
        provenance=provenance if provenance is not None else order_intent.provenance,
        reservation_id=reserva.reservation_id,
        reserved_amount=reserva.amount,
        stop_pct=getattr(reserva, "stop_pct", None),
    )


def exit_intent(*, instrument: str, quantity: Decimal, agent_id: str,
                account: str, cost_basis: Decimal = Decimal("0"),
                client_order_id: str = "", provenance: dict | None = None,
                mandate_version: int = 1) -> ExecutionIntent:
    """Constrói a intenção de SAÍDA."""
    return ExecutionIntent(
        kind=Kind.EXIT, side=Side.SELL, instrument=instrument,
        quantity=quantity, agent_id=agent_id, account=account,
        mandate_version=mandate_version, client_order_id=client_order_id,
        provenance=provenance, cost_basis_ref=cost_basis)


__all__ = ["ExecutionIntent", "Side", "Kind", "IntentInvalid",
           "from_order_intent", "exit_intent"]

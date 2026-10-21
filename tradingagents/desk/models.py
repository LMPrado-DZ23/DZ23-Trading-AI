"""Contratos da mesa autônoma DZ23 (MA-01 / MA-02).

Princípios inegociáveis codificados aqui:
- Proposta NÃO é autorização. Quantidade final é calculada pelo servidor.
- Autoridade nasce no backend: nada que venha de LLM ou navegador vira
  permissão. Mandato revogado ou expirado invalida tudo que dele depende.
- Tudo versionado e datado; Decimal para dinheiro, nunca float.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum

SCHEMA = "dz23.desk/1"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Mode(str, Enum):
    ANALYSIS = "analysis"
    BACKTEST = "backtest"
    PAPER = "paper"
    SHADOW = "shadow"
    TESTNET = "testnet"
    LIVE = "live"          # existe, mas fora da autorização desta missão


SIMULATED_MODES = frozenset({Mode.ANALYSIS, Mode.BACKTEST, Mode.PAPER, Mode.SHADOW})


class Action(str, Enum):
    BUY = "buy"
    SELL = "sell"
    REDUCE = "reduce"
    HOLD = "hold"
    NO_TRADE = "no_trade"
    DATA_INSUFFICIENT = "data_insufficient"


class AgentStage(str, Enum):
    REGISTERED = "registered"
    VALIDATED = "validated"
    SHADOW = "shadow"
    PAPER_ENABLED = "paper_enabled"
    PAUSED = "paused"
    RETIRED = "retired"


ALLOWED_STAGE_MOVES = {
    AgentStage.REGISTERED: {AgentStage.VALIDATED, AgentStage.RETIRED},
    AgentStage.VALIDATED: {AgentStage.SHADOW, AgentStage.PAUSED, AgentStage.RETIRED},
    AgentStage.SHADOW: {AgentStage.PAPER_ENABLED, AgentStage.PAUSED, AgentStage.RETIRED},
    AgentStage.PAPER_ENABLED: {AgentStage.PAUSED, AgentStage.RETIRED},
    AgentStage.PAUSED: {AgentStage.VALIDATED, AgentStage.SHADOW,
                        AgentStage.PAPER_ENABLED, AgentStage.RETIRED},
    AgentStage.RETIRED: set(),
}


class MandateError(RuntimeError):
    """Mandato ausente, revogado, expirado ou fora de escopo."""


@dataclass(frozen=True)
class DeskMandate:
    """Quem pode operar o quê, com quanto, até quando (MA-01)."""
    mandate_id: str
    owner: str
    account: str
    mode: Mode
    instruments: tuple[str, ...]
    capital: Decimal
    max_position_pct: Decimal = Decimal("0.25")
    max_open_positions: int = 4
    version: int = 1
    # Folga DECLARADA para custo de execução acima do reservado: o
    # dimensionamento reserva quantidade × preço esperado, mas a execução
    # ainda paga taxa e pode sofrer slippage. Sem uma folga explícita,
    # qualquer taxa viraria "déficit" e travaria a mesa a cada compra; com
    # folga implícita, um slippage grave passaria despercebido. 1% cobre
    # taxa típica de corretora (0,1%–0,6%) com margem.
    slippage_buffer_pct: Decimal = Decimal("0.01")
    valid_until: datetime | None = None
    revoked_at: datetime | None = None
    live_authorized: bool = False      # só verdadeiro com a frase do dono
    schema: str = SCHEMA

    def assert_active(self, *, instrument: str | None = None,
                      now: datetime | None = None) -> None:
        now = now or utcnow()
        if self.revoked_at is not None:
            raise MandateError(f"mandato {self.mandate_id} revogado")
        if self.valid_until is not None and now >= self.valid_until:
            raise MandateError(f"mandato {self.mandate_id} expirado")
        if instrument is not None and instrument not in self.instruments:
            raise MandateError(f"{instrument} fora do mandato {self.mandate_id}")
        # Modo LIVE é permitido por decisão explícita do dono. A proteção não
        # é proibir: é exigir que a autorização esteja viva no mandato, com
        # validade e limites — e que a ponte cobre a frase e os tetos.
        if self.mode is Mode.LIVE and not self.live_authorized:
            raise MandateError(
                "mandato em LIVE sem autorização ativa: confirme em "
                "'Plano e risco' antes de operar com dinheiro real")


@dataclass
class AgentSpec:
    """Identidade e poderes de um agente da mesa (MA-01)."""
    agent_id: str
    role: str                      # trader | analyst | risk | guardian | researcher
    strategy: str = ""
    version: str = "1"
    stage: AgentStage = AgentStage.REGISTERED
    tools: tuple[str, ...] = ()
    ai_budget_usd: Decimal = Decimal("0")
    last_heartbeat: datetime | None = None

    def can_propose(self) -> bool:
        return self.role == "trader" and self.stage in (
            AgentStage.SHADOW, AgentStage.PAPER_ENABLED, AgentStage.VALIDATED)

    def move_to(self, stage: AgentStage) -> None:
        if stage not in ALLOWED_STAGE_MOVES[self.stage]:
            raise ValueError(f"transição {self.stage.value} → {stage.value} não autorizada")
        self.stage = stage

    def may_use(self, tool: str) -> bool:
        """Permissão de ferramenta é do backend — nunca do prompt."""
        return tool in self.tools


@dataclass(frozen=True)
class MarketSnapshot:
    """Fatia imutável de mercado compartilhada entre agentes (MA-02)."""
    instrument: str
    timeframe: str
    cutoff: datetime
    closes: tuple[Decimal, ...]
    source: str = "fixture"
    quality: str = "ok"

    def sufficient(self, need: int) -> bool:
        return self.quality == "ok" and len(self.closes) >= need

    @property
    def last(self) -> Decimal:
        return self.closes[-1]


@dataclass(frozen=True)
class TradeProposal:
    """Hipótese de um trader. NÃO autoriza nada (MA-02)."""
    agent_id: str
    strategy: str
    instrument: str
    action: Action
    reason: str
    desired_exposure_pct: Decimal = Decimal("0")
    confidence: Decimal = Decimal("0")
    valid_until: datetime | None = None
    invalidation: str = ""
    snapshot_cutoff: datetime | None = None
    created_at: datetime = field(default_factory=utcnow)
    schema: str = SCHEMA
    # Fração NEGATIVA do preço de entrada (ex.: -0.01), a MESMA da
    # ExitPolicy. O executor converte em preço absoluto no fill e coloca o
    # stop na exchange; None = estratégia sem stop protetivo.
    stop_pct: Decimal | None = None

    def is_actionable(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        if self.action not in (Action.BUY, Action.SELL, Action.REDUCE):
            return False
        return self.valid_until is None or now < self.valid_until


@dataclass(frozen=True)
class PortfolioDecision:
    """Veredito do allocator: quem entra, quem fica de fora e por quê."""
    admitted: tuple[TradeProposal, ...]
    vetoed: tuple[tuple[TradeProposal, str], ...]
    policy: str


@dataclass(frozen=True)
class RiskReservation:
    """Capital efetivamente travado. Emitido só pelo RiskEngine.

    `amount` e `quantity` são os valores ORIGINAIS e nunca mudam. Antes o
    `amount` era sobrescrito a cada consumo: deixava de ser o valor
    reservado e passava a ser o saldo, então o consumo proporcional seguinte
    era calculado sobre uma base que já havia encolhido — 50% + 25% dava
    37,5 em vez de 25 restantes.
    """
    reservation_id: str
    account: str
    instrument: str
    amount: Decimal
    quantity: Decimal
    mandate_version: int
    expires_at: datetime
    agent_id: str = ""
    # Acumulados de consumo. Só estes mudam.
    consumed_amount: Decimal = Decimal("0")
    filled_quantity: Decimal = Decimal("0")
    # Ordem que esta reserva financia. Uma reserva cobre UMA ordem: sem este
    # vínculo, uma segunda ordem apresentando o mesmo id e o valor original
    # passava na autorização, e o mesmo capital cobria duas ordens.
    bound_client_order_id: str = ""
    # Folga de execução efetivamente separada no caixa. `amount` é o ALVO
    # (notional) que dimensiona a quantidade; a cobertura dura é alvo+folga.
    # Sem separar os dois, a folga autorizava gasto que ninguém reservou.
    buffer_amount: Decimal = Decimal("0")
    # Stop protetivo declarado pela estratégia (fração negativa). Viaja com a
    # reserva porque é ela que autoriza a ordem: o stop não pode ser
    # "esquecido" entre a decisão e o envio.
    stop_pct: Decimal | None = None

    @property
    def target_amount(self) -> Decimal:
        """Notional alvo — base do dimensionamento da quantidade."""
        return self.amount

    @property
    def hard_reserved_amount(self) -> Decimal:
        """Dinheiro realmente travado: alvo + folga."""
        return self.amount + self.buffer_amount

    @property
    def original_amount(self) -> Decimal:
        return self.amount

    @property
    def original_quantity(self) -> Decimal:
        return self.quantity

    @property
    def remaining_amount(self) -> Decimal:
        """Cobertura DURA ainda disponível (inclui a folga não usada)."""
        return self.hard_reserved_amount - self.consumed_amount

    @property
    def remaining_quantity(self) -> Decimal:
        return self.quantity - self.filled_quantity


@dataclass(frozen=True)
class OrderIntent:
    """Ponte entre decisão e execução; expira e checa posse."""
    intent_id: str
    reservation: RiskReservation
    action: Action
    mode: Mode
    owner_worker: str
    expires_at: datetime
    idempotency_key: str = ""
    # Prova de origem da decisão, exigida pelo gate de capital. Ausente =>
    # a corretora recusa (fail-closed), e é assim que deve ser.
    provenance: dict | None = None

    def assert_sendable(self, *, worker_id: str, mandate: DeskMandate,
                        now: datetime | None = None) -> None:
        now = now or utcnow()
        mandate.assert_active(instrument=self.reservation.instrument, now=now)
        if mandate.version != self.reservation.mandate_version:
            raise MandateError("política do mandato mudou desde a reserva")
        if now >= self.expires_at:
            raise MandateError("intenção expirada — decisão antiga não autoriza ordem atual")
        if worker_id != self.owner_worker:
            raise MandateError("worker sem posse da intenção")


def default_expiry(minutes: int = 5) -> datetime:
    return utcnow() + timedelta(minutes=minutes)

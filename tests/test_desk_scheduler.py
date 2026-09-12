"""Cenário 02 (ciclo sem cliente aberto) e 11 (reinício reconcilia antes de
retomar; oportunidade vencida não vira ordem atrasada)."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from tradingagents.desk.execution import (BrokerTimeout, ExecutionCoordinator,
                                          OrderState, PaperBroker)
from tradingagents.desk.ledger import Ledger
from tradingagents.desk.models import (AgentStage, DeskMandate, MarketSnapshot, Mode,
                                       utcnow)
from tradingagents.desk.orchestrator import DeskOrchestrator
from tradingagents.desk.risk import RiskEngine
from tradingagents.desk.scheduler import DeskScheduler, SchedulerState
from tradingagents.desk.traders import default_desk

UNIVERSE = ("BTC/USDT",)


def uptrend(n=40):
    return tuple(Decimal(str(100 + i * 1.5)) for i in range(n))


def build(tmp_path, broker=None, age_minutes=0):
    mandate = DeskMandate(mandate_id="m", owner="p", account="a", mode=Mode.PAPER,
                          instruments=UNIVERSE, capital=Decimal("1000"))
    traders = default_desk(UNIVERSE)
    for t in traders:
        t.spec.move_to(AgentStage.VALIDATED)
        t.spec.move_to(AgentStage.SHADOW)
        t.spec.move_to(AgentStage.PAPER_ENABLED)
    risk = RiskEngine(mandate)
    ledger = Ledger(opening_cash=mandate.capital)
    coord = ExecutionCoordinator(mandate, ledger, risk, worker_id="w1",
                                 broker=broker or PaperBroker(Decimal("100")))
    orch = DeskOrchestrator(mandate, traders, worker_id="w1", risk=risk,
                            positions_provider=ledger.open_positions)

    def snaps():
        cutoff = utcnow() - timedelta(minutes=age_minutes)
        return [MarketSnapshot("BTC/USDT", "1h", cutoff, uptrend(), source="fixture")]

    sched = DeskScheduler(orch, coord, snapshot_provider=snaps,
                          price_provider=lambda: {"BTC/USDT": Decimal("160")},
                          state_path=tmp_path / "desk-state.json",
                          interval_seconds=1)
    return sched, orch, coord, ledger, risk


class TestCenario02SemClienteAberto:
    def test_loop_runs_cycles_by_itself(self, tmp_path):
        sched, *_ = build(tmp_path)
        assert sched.start() is True
        deadline = utcnow() + timedelta(seconds=6)
        while sched.state.cycles_run < 2 and utcnow() < deadline:
            pass
        sched.stop()
        assert sched.state.cycles_run >= 2      # rodou sozinho, sem comando
        assert not sched.running

    def test_state_survives_on_disk(self, tmp_path):
        sched, *_ = build(tmp_path)
        sched.run_once()
        assert (tmp_path / "desk-state.json").exists()
        revived = SchedulerState.from_json((tmp_path / "desk-state.json").read_text())
        assert revived.cycles_run == 1
        assert revived.open_positions == ["BTC/USDT"]

    def test_stop_is_controlled_not_a_position_wipe(self, tmp_path):
        sched, orch, *_ = build(tmp_path)
        sched.run_once()
        sched.stop()
        assert sched.state.stopped is True
        assert orch.open_exposure() == {"BTC/USDT"}   # posição continua
        assert orch.drain() == ["BTC/USDT"]


class TestCenario11ReinicioReconcilia:
    class TimeoutBroker:
        def submit(self, *a, **k):
            raise BrokerTimeout("sem resposta")

    def test_restart_recovers_state_from_disk(self, tmp_path):
        sched, *_ = build(tmp_path)
        sched.run_once()
        # "processo caiu": nova instância lendo o mesmo caminho
        sched2, *_ = build(tmp_path)
        assert sched2.state.cycles_run == 1
        assert sched2.state.open_positions == ["BTC/USDT"]

    def test_uncertain_order_blocks_until_reconciled(self, tmp_path):
        sched, orch, coord, ledger, risk = build(tmp_path, broker=self.TimeoutBroker())
        sched.run_once()
        pend = coord.pending_reconciliation()
        assert pend and pend[0].state is OrderState.UNKNOWN_RECONCILIATION_REQUIRED
        assert risk.reserved_total > 0
        # reinício SEM resposta da corretora: continua incerto, reserva travada
        assert sched.recover(exchange_lookup=lambda rec: None)["ainda_incertas"]
        assert risk.reserved_total > 0
        # agora a corretora responde: resolve e só então libera
        out = sched.recover(exchange_lookup=lambda rec: {"status": "absent"})
        assert out["reconciliadas"] and risk.reserved_total == Decimal("0")
        assert ledger.is_balanced()

    def test_stale_snapshot_never_becomes_a_late_order(self, tmp_path):
        sched, *_ = build(tmp_path, age_minutes=120)
        out = sched.run_once()
        assert not out["ordens"]
        assert out["descartados"] and "vencido" in out["descartados"][0]

    def test_broken_cycle_does_not_kill_the_loop(self, tmp_path):
        sched, orch, *_ = build(tmp_path)

        def explode():
            raise RuntimeError("provedor de dados caiu")

        sched.snapshot_provider = explode
        assert sched.start() is True
        deadline = utcnow() + timedelta(seconds=4)
        while sched.state.errors < 1 and utcnow() < deadline:
            pass
        assert sched.running          # laço continua vivo apesar do erro
        sched.stop()
        assert sched.state.errors >= 1

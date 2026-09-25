"""§8: o laço autônomo revalida billing a cada ciclo. Sem assinatura, NÃO cria
exposição nova e NÃO liquida posições; a transição vale mesmo com o laço já
iniciado (não só antes do start). Puro — sem fastapi/pg."""
from __future__ import annotations

from types import SimpleNamespace

from tradingagents.desk.models import utcnow
from tradingagents.desk.scheduler import DeskScheduler


class _StubReport:
    def __init__(self, intents):
        self.intents = intents
        self.summary = "ciclo"
        self.vetoed: list = []
        self.abstentions: list = []
        self.errors: list = []


class _StubOrchestrator:
    def __init__(self):
        self.run_calls = 0
        self._exposure: set[str] = set()

    def run_cycle(self, snaps, prices):
        self.run_calls += 1
        self._exposure.add("BTC/USDT")            # criou exposição
        return _StubReport(intents=[SimpleNamespace(symbol="BTC/USDT")])

    def open_exposure(self):
        return set(self._exposure)


class _StubCoordinator:
    def __init__(self):
        self.submits = 0

    def submit(self, intent):
        self.submits += 1
        return SimpleNamespace(order_id=f"o{self.submits}",
                               state=SimpleNamespace(value="new"))

    def pending_reconciliation(self):
        return []


def _snap():
    return SimpleNamespace(instrument="BTC/USDT", cutoff=utcnow(), closes=[1.0])


def _build(tmp_path, entitled_flag):
    orch, coord = _StubOrchestrator(), _StubCoordinator()
    sched = DeskScheduler(
        orch, coord,
        snapshot_provider=lambda: [_snap()],
        price_provider=lambda: {},
        state_path=tmp_path / "state.json",
        tenant_id="t",
        entitlement_check=lambda: entitled_flag["ok"],
    )
    return sched, orch, coord


def test_com_assinatura_cria_ordem(tmp_path):
    flag = {"ok": True}
    sched, orch, coord = _build(tmp_path, flag)
    out = sched.run_once()
    assert coord.submits == 1 and out["ordens"]           # criou exposição
    assert not out.get("pausado_por_billing")


def test_sem_assinatura_pausa_sem_liquidar(tmp_path):
    flag = {"ok": False}
    sched, orch, coord = _build(tmp_path, flag)
    out = sched.run_once()
    assert out["pausado_por_billing"] is True
    assert out["ordens"] == [] and coord.submits == 0      # NENHUMA ordem nova
    assert orch.run_calls == 0                              # nem rodou o ciclo
    assert orch.open_exposure() == set()                   # nada liquidado nem aberto
    assert sched.state.cycles_run == 1                     # laço segue vivo


def test_transicao_durante_o_laco_ja_iniciado(tmp_path):
    # entitled no começo -> cria ordem; assinatura CAI -> próximo ciclo pausa.
    flag = {"ok": True}
    sched, orch, coord = _build(tmp_path, flag)
    sched.run_once()
    assert coord.submits == 1
    flag["ok"] = False                                     # assinatura expira agora
    out = sched.run_once()
    assert out["pausado_por_billing"] is True
    assert coord.submits == 1                              # não criou nova exposição
    assert "BTC/USDT" in orch.open_exposure()              # posição preexistente preservada

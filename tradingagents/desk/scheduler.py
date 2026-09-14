"""Agendador persistente da mesa (MA-03, cenário 11).

O ciclo passa a acontecer sozinho: laço em thread própria, disparado por
intervalo/evento, independente de navegador aberto ou de qualquer cliente.

Invariantes codificadas:
- Ao (re)iniciar, RECONCILIA antes de retomar: ordens incertas são
  resolvidas contra a corretora antes de qualquer proposta nova.
- Sinal velho não vira ordem atrasada: snapshots com idade acima do limite
  são descartados com motivo.
- O estado durável (posições, ciclos, ordens pendentes) é salvo a cada
  ciclo; reinício restaura de disco, não da memória de um chat.
- Falha de um ciclo não derruba o laço; erros são contados e expostos.
"""
from __future__ import annotations

import contextlib
import json
import threading
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from .models import utcnow


@dataclass
class SchedulerState:
    """Instantâneo durável — o que precisa sobreviver a um reinício."""
    cycles_run: int = 0
    last_cycle_at: str | None = None
    errors: int = 0
    open_positions: list[str] = field(default_factory=list)
    pending_reconciliation: list[str] = field(default_factory=list)
    stopped: bool = False

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, raw: str) -> SchedulerState:
        try:
            data = json.loads(raw)
        except ValueError:
            return cls()
        return cls(**{k: v for k, v in data.items() if k in cls.__annotations__})


class DeskScheduler:
    """Laço de vida da mesa. Não conhece navegador, chat nem sessão."""

    def __init__(self, orchestrator, coordinator, *, snapshot_provider,
                 price_provider, state_path: str | Path, on_cycle=None,
                 armazem=None, tenant_id: str = "legacy",
                 interval_seconds: int = 300, max_snapshot_age_minutes: int = 15):
        self.orchestrator = orchestrator
        self.coordinator = coordinator
        self.snapshot_provider = snapshot_provider
        self.price_provider = price_provider
        self.state_path = Path(state_path)
        self.armazem = armazem
        self.tenant_id = tenant_id
        self.interval = interval_seconds
        self.max_age = timedelta(minutes=max_snapshot_age_minutes)
        self.on_cycle = on_cycle       # o painel escuta aqui
        self.state = self._load()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # -- persistência --------------------------------------------------
    def _load(self) -> SchedulerState:
        if self.armazem is not None:
            doc = self.armazem.carregar(self.tenant_id, "mesa")
            if doc is not None:
                return SchedulerState(**{k: v for k, v in doc.items()
                                         if k in SchedulerState.__annotations__})
        if self.state_path.exists():
            return SchedulerState.from_json(self.state_path.read_text())
        return SchedulerState()

    def save(self) -> None:
        self.state.open_positions = sorted(self.orchestrator.open_exposure())
        self.state.pending_reconciliation = [
            r.order_id for r in self.coordinator.pending_reconciliation()]
        if self.armazem is not None:
            self.armazem.salvar(self.tenant_id, "mesa", dict(self.state.__dict__))
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(self.state.to_json())      # escrita atômica
        tmp.replace(self.state_path)

    # -- recuperação ---------------------------------------------------
    def recover(self, exchange_lookup=None) -> dict:
        """Reconcilia ANTES de retomar. Sem resposta da corretora, a ordem
        segue incerta e a reserva continua travada."""
        resolved, still_unknown = [], []
        for rec in list(self.coordinator.pending_reconciliation()):
            view = exchange_lookup(rec) if exchange_lookup else None
            self.coordinator.reconcile(rec.order_id, view)
            (resolved if not rec.needs_reconciliation else still_unknown).append(rec.order_id)
        self.save()
        return {"reconciliadas": resolved, "ainda_incertas": still_unknown,
                "posicoes": sorted(self.orchestrator.open_exposure())}

    # -- ciclo ---------------------------------------------------------
    def fresh_snapshots(self, now=None) -> tuple[list, list[str]]:
        now = now or utcnow()
        fresh, discarded = [], []
        for snap in self.snapshot_provider():
            if now - snap.cutoff > self.max_age:
                discarded.append(f"{snap.instrument}: snapshot vencido, não vira ordem")
            else:
                fresh.append(snap)
        return fresh, discarded

    def run_once(self) -> dict:
        snaps, discarded = self.fresh_snapshots()
        out = {"descartados": discarded, "ordens": [], "resumo": "sem dados elegíveis"}
        if snaps:
            report = self.orchestrator.run_cycle(snaps, self.price_provider())
            for intent in report.intents:
                try:
                    rec = self.coordinator.submit(intent)
                    out["ordens"].append({"ordem": rec.order_id, "estado": rec.state.value})
                except Exception as exc:
                    self.state.errors += 1
                    out.setdefault("erros", []).append(f"{type(exc).__name__}: {exc}"[:160])
            out["resumo"] = report.summary
            out["vetos"] = [f"{a}: {w}" for a, w in report.vetoed][:6]
            out["abstencoes"] = [f"{a} · {i}: {r}" for a, i, r in report.abstentions][:6]
            out["erros"] = report.errors[:4]
        self.state.cycles_run += 1
        self.state.last_cycle_at = utcnow().isoformat()
        self.save()
        if self.on_cycle:
            with contextlib.suppress(Exception):
                self.on_cycle(out)   # painel nunca derruba o ciclo
        return out

    # -- laço ----------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:                      # ciclo ruim não mata a mesa
                self.state.errors += 1
                self.save()
            self._stop.wait(self.interval)

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return False
        self.state.stopped = False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="desk-scheduler",
                                        daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout: float = 2.0) -> None:
        """Parada controlada: encerra o laço, mantém posições e estado."""
        self._stop.set()
        self.state.stopped = True
        self.save()
        if self._thread:
            self._thread.join(timeout=timeout)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def status(self) -> dict:
        return {"rodando": self.running, "intervalo_s": self.interval,
                "ciclos": self.state.cycles_run, "erros": self.state.errors,
                "ultimo_ciclo": self.state.last_cycle_at,
                "posicoes": self.state.open_positions,
                "pendencias": self.state.pending_reconciliation}


__all__ = ["DeskScheduler", "SchedulerState"]

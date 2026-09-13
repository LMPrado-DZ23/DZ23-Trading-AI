from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tradingagents.execution.reconciliation import ReconciliationReport, reconcile_state


@dataclass(slots=True)
class ReconciliationMonitor:
    local_snapshot: Callable[[], dict[str, Any]]
    remote_snapshot: Callable[[], dict[str, Any]]
    on_report: Callable[[ReconciliationReport], None] | None = None
    last_report: ReconciliationReport | None = None

    def run_once(self) -> ReconciliationReport:
        try:
            report = reconcile_state(self.local_snapshot(), self.remote_snapshot())
        except Exception as exc:
            report = ReconciliationReport("UNKNOWN", mismatches=(f"monitor:{type(exc).__name__}",))
        self.last_report = report
        if self.on_report:
            self.on_report(report)
        return report

    def health(self) -> dict[str, Any]:
        report = self.last_report
        return {"status": report.status if report else "NOT_RUN", "safe": report.is_safe if report else False, "mismatches": list(report.mismatches) if report else []}

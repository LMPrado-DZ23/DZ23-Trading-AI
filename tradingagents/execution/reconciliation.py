from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    status: str
    missing_local: tuple[str, ...] = ()
    missing_remote: tuple[str, ...] = ()
    mismatches: tuple[str, ...] = ()

    @property
    def is_safe(self) -> bool:
        return self.status == "MATCHED" and not (self.missing_local or self.missing_remote or self.mismatches)


def reconcile_ids(local_ids: set[str], remote_ids: set[str]) -> ReconciliationReport:
    missing_local = tuple(sorted(remote_ids - local_ids))
    missing_remote = tuple(sorted(local_ids - remote_ids))
    status = "MATCHED" if not missing_local and not missing_remote else "DIVERGED"
    return ReconciliationReport(status, missing_local, missing_remote)

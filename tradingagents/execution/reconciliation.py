from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


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


def reconcile_state(local: dict[str, Any], remote: dict[str, Any], *, tolerance: Decimal = Decimal("0.00000001")) -> ReconciliationReport:
    """Compara snapshots normalizados; qualquer estado desconhecido é inseguro."""
    missing_local: list[str] = []
    missing_remote: list[str] = []
    mismatches: list[str] = []
    for field in ("orders", "fills", "positions"):
        local_items = {str(item["id"]): item for item in local.get(field, [])}
        remote_items = {str(item["id"]): item for item in remote.get(field, [])}
        missing_local.extend(f"{field}:{item_id}" for item_id in sorted(remote_items.keys() - local_items.keys()))
        missing_remote.extend(f"{field}:{item_id}" for item_id in sorted(local_items.keys() - remote_items.keys()))
        for item_id in sorted(local_items.keys() & remote_items.keys()):
            for key in set(local_items[item_id]) & set(remote_items[item_id]):
                if key == "id":
                    continue
                left, right = local_items[item_id][key], remote_items[item_id][key]
                try:
                    if abs(Decimal(str(left)) - Decimal(str(right))) > tolerance:
                        mismatches.append(f"{field}:{item_id}:{key}")
                except (ArithmeticError, ValueError):
                    if left != right:
                        mismatches.append(f"{field}:{item_id}:{key}")
    for key in ("balance", "equity", "available_margin"):
        if key in local or key in remote:
            if key not in local:
                missing_remote.append(f"account:{key}")
            elif key not in remote:
                missing_local.append(f"account:{key}")
            else:
                try:
                    if abs(Decimal(str(local[key])) - Decimal(str(remote[key]))) > tolerance:
                        mismatches.append(f"account:{key}")
                except (ArithmeticError, ValueError):
                    if local[key] != remote[key]:
                        mismatches.append(f"account:{key}")
    status = "MATCHED" if not missing_local and not missing_remote and not mismatches else "DIVERGED"
    return ReconciliationReport(status, tuple(missing_local), tuple(missing_remote), tuple(mismatches))

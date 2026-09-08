"""Point-in-time policy for fundamental data.

Fiscal-period end dates are not publication timestamps.  Historical runs may
only consume rows carrying a verifiable publication/filing timestamp at or
before the run's logical decision cutoff.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from typing import Any

from tradingagents.runtime import RunContext

PUBLICATION_FIELDS = (
    "publishedAt", "publicationDate", "filingDate", "acceptedDate", "reportedDate"
)


def historical_cutoff(
    curr_date: str | None, run_context: RunContext | None
) -> datetime | None:
    if run_context is not None:
        return run_context.decision_at if run_context.historical else None
    if not curr_date:
        return None
    requested = date.fromisoformat(str(curr_date))
    today = datetime.now(timezone.utc).date()
    if requested >= today:
        return None
    return datetime.combine(requested, time.max, tzinfo=timezone.utc)


def _published_at(record: dict[str, Any]) -> datetime | None:
    for field in PUBLICATION_FIELDS:
        value = record.get(field)
        if not value:
            continue
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            try:
                parsed = datetime.combine(date.fromisoformat(text[:10]), time.min)
            except ValueError:
                continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


def filter_reports_by_publication(result: str, cutoff: datetime) -> str:
    """Keep only reports demonstrably public by cutoff; malformed data is excluded."""
    try:
        payload = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return pit_excluded("fundamentals payload", cutoff)
    if not isinstance(payload, dict):
        return pit_excluded("fundamentals payload", cutoff)

    found_collection = False
    kept = 0
    for key in ("annualReports", "quarterlyReports"):
        reports = payload.get(key)
        if not isinstance(reports, list):
            continue
        found_collection = True
        safe_reports = []
        for report in reports:
            if not isinstance(report, dict):
                continue
            published = _published_at(report)
            if published is not None and published <= cutoff:
                safe_reports.append(report)
        payload[key] = safe_reports
        kept += len(safe_reports)

    if not found_collection:
        published = _published_at(payload)
        if published is None or published > cutoff:
            return pit_excluded("fundamentals snapshot", cutoff)
    elif kept == 0:
        return pit_excluded("financial statements", cutoff)

    payload["_point_in_time"] = {
        "cutoff": cutoff.isoformat(),
        "policy": "verified-publication-timestamp-only",
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def pit_excluded(source: str, cutoff: datetime) -> str:
    return (
        f"PIT_DATA_EXCLUDED: {source} has no verifiable publication timestamp at or "
        f"before {cutoff.isoformat()}. Fiscal period end is not publication time. "
        "Do not infer or reconstruct the missing fundamentals."
    )

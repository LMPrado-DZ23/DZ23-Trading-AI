"""Reproducible run manifest with source, dependency, config and PIT identity."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from tradingagents.runtime import RunContext


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_commit(project_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=project_root,
            check=True, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value if len(value) == 40 else None


def build_run_manifest(
    project_root: str | Path,
    run_context: RunContext,
    selected_analysts: Iterable[str],
) -> dict:
    root = Path(project_root).resolve()
    if not (root / "pyproject.toml").is_file() and (root.parent / "pyproject.toml").is_file():
        root = root.parent
    reproducible = {
        "schema": "tradingagents/run-manifest/1",
        "workflow_schema_version": run_context.workflow_schema_version,
        "run_context": run_context.to_dict(),
        "selected_analysts": list(selected_analysts),
        "source_commit": _source_commit(root),
        "artifacts": {
            "pyproject.toml": _sha256_file(root / "pyproject.toml"),
            "requirements.txt": _sha256_file(root / "requirements.txt"),
        },
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
        },
    }
    canonical = json.dumps(reproducible, sort_keys=True, separators=(",", ":"))
    return {
        **reproducible,
        "manifest_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def write_run_manifest(path: str | Path, manifest: dict) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    return destination

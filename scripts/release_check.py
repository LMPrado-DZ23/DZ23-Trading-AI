from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "README.md", "DEPLOY.md", "docs/OPERATIONS-USER-READY.md", "docs/progress.md",
    "docs/CLASS-A-RELEASE-GATES.md", ".env.agency.example", ".env.enterprise.example",
    "docker-compose.yml", "tradingagents/job_store.py",
)


def main() -> None:
    missing = [name for name in REQUIRED if not (ROOT / name).is_file()]
    if missing:
        raise SystemExit(f"arquivos ausentes: {missing}")
    env = {"LIVE_TRADING": os.getenv("LIVE_TRADING", "false"), "PAPER_TRADING": os.getenv("PAPER_TRADING", "true"), "REAL_MONEY_EXECUTION": os.getenv("REAL_MONEY_EXECUTION", "false")}
    if env != {"LIVE_TRADING": "false", "PAPER_TRADING": "true", "REAL_MONEY_EXECUTION": "false"}:
        raise SystemExit(f"defaults inseguros: {env}")
    compose = (ROOT / "docker-compose.yml").read_text()
    for forbidden in ("ports:\n      - \"5432", "ports:\n      - \"6379"):
        if forbidden in compose:
            raise SystemExit(f"serviço interno exposto publicamente: {forbidden}")
    pyproject = (ROOT / "pyproject.toml").read_text()
    if '"psycopg[binary]' not in pyproject:
        raise SystemExit("dependência psycopg ausente")
    print("release checklist: arquivos, defaults, isolamento de serviços e PostgreSQL verificados")


if __name__ == "__main__":
    main()

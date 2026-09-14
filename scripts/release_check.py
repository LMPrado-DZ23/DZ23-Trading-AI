from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ("README.md", "DEPLOY.md", "docs/OPERATIONS-USER-READY.md", "docs/progress.md", ".env.agency.example", "docker-compose.yml")


def main() -> None:
    missing = [name for name in REQUIRED if not (ROOT / name).is_file()]
    if missing:
        raise SystemExit(f"arquivos ausentes: {missing}")
    env = {"LIVE_TRADING": os.getenv("LIVE_TRADING", "false"), "PAPER_TRADING": os.getenv("PAPER_TRADING", "true"), "REAL_MONEY_EXECUTION": os.getenv("REAL_MONEY_EXECUTION", "false")}
    if env != {"LIVE_TRADING": "false", "PAPER_TRADING": "true", "REAL_MONEY_EXECUTION": "false"}:
        raise SystemExit(f"defaults inseguros: {env}")
    print("release checklist: arquivos presentes e defaults seguros")


if __name__ == "__main__":
    main()

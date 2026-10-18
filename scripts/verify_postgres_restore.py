"""Ensaia backup/restore PostgreSQL sem receber credenciais na CLI."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from tradingagents.desk.postgres_recovery import rehearse_postgres_restore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Restaura as tabelas DZ23 em um PostgreSQL vazio e distinto; "
            "lê DATABASE_URL e POSTGRES_RESTORE_DATABASE_URL do ambiente"
        )
    )
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    args = parser.parse_args(argv)

    source = os.environ.get("DATABASE_URL", "")
    target = os.environ.get("POSTGRES_RESTORE_DATABASE_URL", "")
    proof = rehearse_postgres_restore(
        args.data_dir,
        source,
        target,
        timeout_seconds=args.timeout_seconds,
    )
    # A prova contém somente hashes, nomes fixos de tabelas e datas; DSNs e
    # dados do banco nunca são impressos.
    print(json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

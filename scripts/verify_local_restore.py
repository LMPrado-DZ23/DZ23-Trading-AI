"""Ensaia um restore local sem iniciar serviço nem enviar ordens."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tradingagents.desk.recovery import rehearse_local_restore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Restaura um backup em área temporária e registra a prova")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args(argv)

    proof = rehearse_local_restore(args.data_dir, args.archive)
    print(json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

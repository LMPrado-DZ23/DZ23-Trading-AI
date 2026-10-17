"""CLI do backup local verificável do DZ23."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tradingagents.desk.backup import (
    BackupError,
    create_backup,
    inspect_backup,
    restore_backup,
)


def backup(data_dir: Path, destination: Path) -> Path:
    create_backup(data_dir, destination)
    return destination


def restore(archive_path: Path, data_dir: Path, *, overwrite: bool = False) -> None:
    restore_backup(archive_path, data_dir, overwrite=overwrite)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cria ou restaura o estado operacional local do DZ23")
    parser.add_argument("action", choices=("backup", "restore", "inspect"))
    parser.add_argument("data_dir", type=Path,
                        help="DATA_DIR no backup/restore; arquivo no inspect")
    parser.add_argument("archive", type=Path, nargs="?",
                        help="arquivo .tar.gz no backup/restore")
    parser.add_argument("--overwrite", action="store_true",
                        help="permite substituir estado existente no restore")
    args = parser.parse_args(argv)

    try:
        if args.action == "inspect":
            print(json.dumps(inspect_backup(args.data_dir), ensure_ascii=False,
                             indent=2, sort_keys=True))
            return 0
        if args.archive is None:
            parser.error("archive é obrigatório para backup/restore")
        if args.action == "backup":
            result = create_backup(args.data_dir, args.archive)
        else:
            result = restore_backup(args.archive, args.data_dir,
                                    overwrite=args.overwrite)
    except BackupError as exc:
        print(f"backup recusado: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

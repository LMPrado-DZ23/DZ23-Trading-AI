from __future__ import annotations

import argparse
import tarfile
from datetime import datetime, timezone
from pathlib import Path

FILES = ("config.json", "jobs.db", "agency-events.jsonl", "execution-events.jsonl", "paper-state.json", "shadow-state.json")


def backup(data_dir: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with tarfile.open(temporary, "w:gz") as archive:
        for name in FILES:
            path = data_dir / name
            if path.exists():
                archive.add(path, arcname=name, recursive=False)
    temporary.replace(destination)
    return destination


def restore(archive_path: Path, data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as archive:
        names = {member.name for member in archive.getmembers()}
        unsafe = names - set(FILES)
        if unsafe or any(".." in Path(name).parts or Path(name).is_absolute() for name in names):
            raise ValueError("backup contém caminhos não permitidos")
        archive.extractall(data_dir, filter="data")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("backup", "restore"))
    parser.add_argument("data_dir", type=Path)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    if args.action == "backup":
        stamp = datetime.now(timezone.utc).isoformat()
        print(f"backup criado em {backup(args.data_dir, args.archive)} ({stamp})")
    else:
        restore(args.archive, args.data_dir)
        print(f"backup restaurado em {args.data_dir}")


if __name__ == "__main__":
    main()

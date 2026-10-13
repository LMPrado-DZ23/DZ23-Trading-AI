"""Build de DIAGNÓSTICO do exe — console (padrão) ou sem console (--windowed).

Replica `desktop/packaging/build_exe.py` (mesmo entrypoint por padrão, mesmos
collects, mesmos hidden-imports) com diretórios separados para não tocar no
bundle oficial. Dois usos:

  1. console — ver o backend rodando empacotado com stdout/stderr reais;
  2. --windowed --entry — reproduzir o modo de produção (sem console) com um
     launcher instrumentado e capturar no arquivo de log o traceback que o
     modo windowed engolia.

Uso:
    uv run --with pyinstaller python scripts/build_diag_console.py
    uv run --with pyinstaller python scripts/build_diag_console.py --windowed
    uv run --with pyinstaller python scripts/build_diag_console.py --windowed \
        --entry build-diag/old_launcher_diag.py --name DZ23-Trading-AI-diag-old
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windowed", action="store_true", help="build sem console (modo produção)")
    parser.add_argument("--entry", default="desktop/launcher.py", help="script de entrada")
    parser.add_argument("--name", default="DZ23-Trading-AI-diag", help="nome do exe")
    args = parser.parse_args()

    entry = (ROOT / args.entry).resolve()
    if not entry.exists():
        print(f"entry não existe: {entry}", file=sys.stderr)
        return 2

    static_dir = ROOT / "service" / "static"
    icon = static_dir / "marca" / "dz23-icone.ico"
    modo = "--windowed" if args.windowed else "--console"
    workdir = ROOT / "build-diag" / args.name
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onedir",
        modo,
        f"--name={args.name}",
        f"--icon={icon}",
        f"--add-data={static_dir}{os.pathsep}service/static",
        "--collect-all=service",
        "--collect-all=tradingagents",
        "--hidden-import=uvicorn.logging",
        "--hidden-import=uvicorn.loops.auto",
        "--hidden-import=uvicorn.protocols.http.h11_impl",
        "--hidden-import=uvicorn.lifespan.on",
        str(entry),
        "--distpath",
        str(ROOT / "dist-diag"),
        "--workpath",
        str(workdir),
        "--specpath",
        str(workdir),
    ]
    print("Buscando pyinstaller...", flush=True)
    bom = subprocess.run([sys.executable, "-c", "import PyInstaller"], capture_output=True)
    if bom.returncode != 0:
        print(
            "PyInstaller ausente. Rode: uv run --with pyinstaller python scripts/build_diag_console.py",
            file=sys.stderr,
        )
        return 2
    print(f"Build diag ({modo}) começando: {args.name} <- {entry}", flush=True)
    r = subprocess.run(cmd, cwd=str(ROOT))
    exe = ROOT / "dist-diag" / args.name / f"{args.name}.exe"
    print(f"build exit={r.returncode}; exe existe: {exe.exists()} -> {exe}")
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())

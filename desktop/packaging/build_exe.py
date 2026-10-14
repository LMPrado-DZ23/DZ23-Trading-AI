"""
DZ23 Trading AI — PyInstaller Executable Builder for Windows x64
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def build_windows_exe():
    """Build standalone DZ23-Trading-AI.exe bundle using PyInstaller."""
    print("Building DZ23 Trading AI Standalone Windows Executable...")

    dist_dir = ROOT / "dist"
    static_dir = ROOT / "service" / "static"
    icon = static_dir / "marca" / "dz23-icone.ico"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",
        "--name=DZ23-Trading-AI",
        f"--icon={icon}",
        f"--add-data={static_dir}{os.pathsep}service/static",
        "--collect-all=service",
        "--collect-all=tradingagents",
        "--hidden-import=uvicorn.logging",
        "--hidden-import=uvicorn.loops.auto",
        "--hidden-import=uvicorn.protocols.http.h11_impl",
        "--hidden-import=uvicorn.lifespan.on",
        str(ROOT / "desktop" / "launcher.py"),
    ]

    try:
        subprocess.run(cmd, check=True, cwd=str(ROOT))
        print("Build completed successfully! Output in:", dist_dir / "DZ23-Trading-AI")
    except Exception as exc:
        print(f"PyInstaller build failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    build_windows_exe()

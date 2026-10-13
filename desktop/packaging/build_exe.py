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

    # Ensure pyinstaller is installed or run via uv
    dist_dir = ROOT / "dist"
    static_dir = ROOT / "service" / "static"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",
        "--name=DZ23-Trading-AI",
        f"--add-data={static_dir}{os.pathsep}service/static",
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

"""DZ23 Trading AI — PyInstaller Executable Builder for Windows x64.

A versão vem SEMPRE do pyproject.toml (fonte única): vira recurso de versão
do exe (Propriedades → Detalhes no Windows) e metadado do pacote embarcado
(`--copy-metadata`), que é o que o launcher/app reportam em runtime.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def ler_versao_pyproject() -> str:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    achado = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    if not achado:
        raise SystemExit("pyproject.toml sem campo version — não invento número de versão")
    return achado.group(1)


def _tupla_versao(versao: str) -> tuple[int, int, int, int]:
    numeros = [int(m) for m in re.findall(r"\d+", versao)]
    return tuple((numeros + [0, 0, 0, 0])[:4])  # type: ignore[return-value]


def escrever_version_file(versao: str) -> Path:
    """Recurso de versão do exe (VSVersionInfo usado pelo PyInstaller)."""
    v = _tupla_versao(versao)
    destino = ROOT / "dist" / "pyinstaller-version-info.txt"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={v},
    prodvers={v},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', 'DZ23'),
         StringStruct('FileDescription', 'DZ23 Trading AI'),
         StringStruct('FileVersion', '{versao}'),
         StringStruct('InternalName', 'DZ23-Trading-AI'),
         StringStruct('OriginalFilename', 'DZ23-Trading-AI.exe'),
         StringStruct('ProductName', 'DZ23 Trading AI'),
         StringStruct('ProductVersion', '{versao}')])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
        encoding="utf-8",
    )
    return destino


def build_windows_exe():
    """Build standalone DZ23-Trading-AI.exe bundle using PyInstaller."""
    versao = ler_versao_pyproject()
    print(f"Building DZ23 Trading AI Standalone Windows Executable v{versao}...")

    dist_dir = ROOT / "dist"
    static_dir = ROOT / "service" / "static"
    icon = static_dir / "marca" / "dz23-icone.ico"
    version_file = escrever_version_file(versao)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",
        "--name=DZ23-Trading-AI",
        f"--icon={icon}",
        f"--version-file={version_file}",
        f"--add-data={static_dir}{os.pathsep}service/static",
        "--collect-all=service",
        "--collect-all=tradingagents",
        "--copy-metadata=tradingagents",
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

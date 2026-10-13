"""Enumera janelas nativas de um processo Windows (PyInstaller windowed não tem console).

Uso: python scripts/diagnostico_janelas_win.py <PID>
Imprime classe, título e o texto de cada janela/filho — permite ler o
conteúdo de diálogos MessageBox (#32770) que ficariam invisíveis no CI.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

user32 = ctypes.windll.user32

WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E


def texto_janela(hwnd: int) -> str:
    n = user32.SendMessageW(hwnd, WM_GETTEXTLENGTH, 0, 0)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 2)
    user32.SendMessageW(hwnd, WM_GETTEXT, n + 1, ctypes.byref(buf))
    return buf.value


def classe_janela(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def info_janela(hwnd: int) -> dict:
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return {
        "hwnd": hwnd,
        "pid": pid.value,
        "classe": classe_janela(hwnd),
        "titulo": texto_janela(hwnd),
        "visivel": bool(user32.IsWindowVisible(hwnd)),
    }


def enum_filhos(hwnd: int) -> list[dict]:
    filhos: list[dict] = []
    EnumChildProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def _cb(child: int, _lparam: int) -> bool:
        filhos.append(info_janela(child))
        return True

    user32.EnumChildWindows(hwnd, EnumChildProc(_cb), 0)
    return filhos


def main() -> int:
    if len(sys.argv) < 2:
        print("uso: python scripts/diagnostico_janelas_win.py <PID>")
        return 2
    alvo = int(sys.argv[1])
    janelas: list[dict] = []
    EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def _cb(hwnd: int, _lparam: int) -> bool:
        pid = wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == alvo:
            janelas.append(info_janela(hwnd))
        return True

    user32.EnumWindows(EnumProc(_cb), 0)
    print(f"janelas de topo do PID {alvo}: {len(janelas)}")
    for j in janelas:
        print(f"- hwnd={j['hwnd']} classe={j['classe']!r} visivel={j['visivel']} titulo={j['titulo']!r}")
        for f in enum_filhos(j["hwnd"]):
            if f["titulo"] or f["classe"]:
                print(f"    filho: classe={f['classe']!r} texto={f['titulo']!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Reproduz o start do backend do launcher COM tracebacks visíveis.

Roda o mesmo caminho do launcher (env HOST/PORT/DATA_DIR/MULTI_TENANT_ENABLED,
thread + uvicorn.Server) mas instala threading.excepthook e faulthandler para
que a causa da morte da thread apareça no console — o exe --windowed engole
isso (sem stderr), por isso a falha era invisível.

Uso: PYTHONUTF8=1 python scripts/diagnostico_backend.py [porta]
"""

from __future__ import annotations

import faulthandler
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def prepara_env(porta: int) -> None:
    os.environ["HOST"] = "127.0.0.1"
    os.environ["PORT"] = str(porta)
    os.environ["MULTI_TENANT_ENABLED"] = "false"
    dados = Path(os.environ.get("APPDATA", ".")) / "DZ23-Trading-AI"
    dados.mkdir(parents=True, exist_ok=True)
    os.environ["DATA_DIR"] = str(dados)
    print(f"[diag] DATA_DIR={dados}", flush=True)


def main() -> int:
    porta = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    prepara_env(porta)

    def _hooko(args: threading.ExceptHookArgs) -> None:  # noqa: ANN001
        print(
            f"[diag] THREAD {args.thread.name!r} morreu com "
            f"{args.exc_type.__name__}: {args.exc_value}",
            flush=True,
        )
        import traceback

        traceback.print_exception(args.exc_type, args.exc_value, args.exc_traceback)
        sys.stdout.flush()

    threading.excepthook = _hooko
    faulthandler.dump_traceback_later(30, exit=False, repeat=True)

    from desktop.launcher import run_backend

    t = threading.Thread(target=run_backend, args=("127.0.0.1", porta), daemon=True, name="backend")
    t.start()

    inicio = time.time()
    ok = False
    while time.time() - inicio < 20:
        if not t.is_alive():
            print(f"[diag] thread do backend MORREU após {time.time() - inicio:.1f}s", flush=True)
            break
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{porta}/healthz", timeout=1) as r:
                if r.status == 200:
                    print(f"[diag] healthz=200 após {time.time() - inicio:.1f}s — backend VIVO", flush=True)
                    ok = True
                    break
        except Exception:
            pass
        time.sleep(0.5)
    if not ok and t.is_alive():
        print("[diag] 20s sem healthz e thread viva (hang?) — dump de stacks acima", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

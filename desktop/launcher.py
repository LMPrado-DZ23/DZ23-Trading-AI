"""
DZ23 Trading AI — Windows Native Launcher & Local Server Supervisor
"""

import ctypes
import logging
import os
import secrets
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("dz23.desktop")


def owner_password_path() -> Path:
    return Path(os.environ.get("APPDATA", ".")) / "DZ23-Trading-AI" / "owner_password.txt"


def ensure_owner_password(notify=None) -> str:
    """Garante ACCESS_PASSWORD definido: do ambiente, do arquivo do dono ou criado na 1ª execução."""
    if os.environ.get("ACCESS_PASSWORD"):
        return os.environ["ACCESS_PASSWORD"]
    path = owner_password_path()
    if path.exists():
        saved = path.read_text(encoding="utf-8").strip()
        if saved:
            os.environ["ACCESS_PASSWORD"] = saved
            return saved
    password = secrets.token_urlsafe(12)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(password, encoding="utf-8")
    os.environ["ACCESS_PASSWORD"] = password
    if notify:
        notify(password, path)
    return password


def show_first_run_dialog(password: str, path: Path) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            f"Sua senha de acesso ao painel foi criada:\n\n{password}\n\n"
            f"Ela foi salva em:\n{path}\n\nGuarde esta senha.",
            "DZ23 Trading AI — primeira execução",
            0x40,
        )
    except Exception:
        logger.warning("Senha do dono criada em %s (sem diálogo disponível)", path)


def run_backend(host: str = "127.0.0.1", port: int = 8000):
    """Start local FastAPI service bound exclusively to loopback."""
    os.environ["HOST"] = host
    os.environ["PORT"] = str(port)
    os.environ["MULTI_TENANT_ENABLED"] = "false"

    # Ensure data directory exists
    user_data = Path(os.environ.get("APPDATA", ".")) / "DZ23-Trading-AI"
    user_data.mkdir(parents=True, exist_ok=True)
    os.environ["DATA_DIR"] = str(user_data)

    from service.app import app
    uvicorn.run(app, host=host, port=port, log_level="info")


def wait_and_open_ui(url: str, timeout: int = 15):
    """Poll localhost server until ready, then open browser window."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with urllib.request.urlopen(f"{url}/healthz", timeout=1) as resp:
                if resp.status == 200:
                    logger.info("Local backend is ready. Opening UI...")
                    webbrowser.open(url)
                    return
        except Exception:
            time.sleep(0.5)
    logger.warning("Backend start timed out. Opening UI anyway...")
    webbrowser.open(url)


def main():
    host = "127.0.0.1"
    port = 8000
    url = f"http://{host}:{port}"

    logger.info("Starting DZ23 Trading AI Desktop Environment...")

    # Dono Único: senha criada na 1ª execução (mostrada no diálogo) — o painel
    # não abre sessão sem ela; sem isso o app empacotado nega todo acesso.
    ensure_owner_password(notify=show_first_run_dialog)

    # Run uvicorn in a daemon thread
    backend_thread = threading.Thread(
        target=run_backend, args=(host, port), daemon=True
    )
    backend_thread.start()

    # Wait for backend and launch webview/browser UI
    wait_and_open_ui(f"{url}/app")

    try:
        while backend_thread.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down DZ23 Trading AI Desktop...")


if __name__ == "__main__":
    main()

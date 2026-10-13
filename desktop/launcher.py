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

    from service.app import app, set_shutdown_hook

    # uvicorn.Server (não uvicorn.run) para dar ao painel um gatilho de
    # encerramento: o endpoint /v1/app/quit liga should_exit e o servidor
    # desliga graciosamente — o leigo não fica preso ao Gerenciador de Tarefas.
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    set_shutdown_hook(lambda: setattr(server, "should_exit", True))
    server.run()


def _healthz_ok(base_url: str, timeout: int = 1) -> bool:
    """True se algo responde 200 em /healthz (o painel vive em /app)."""
    try:
        with urllib.request.urlopen(f"{base_url}/healthz", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def wait_and_open_ui(base_url: str, ui_path: str = "/app", timeout: int = 15):
    """Poll o /healthz da raiz até o backend responder, então abre o painel."""
    ui_url = f"{base_url}{ui_path}"
    start_time = time.time()
    while time.time() - start_time < timeout:
        if _healthz_ok(base_url):
            logger.info("Local backend is ready. Opening UI...")
            webbrowser.open(ui_url)
            return
        time.sleep(0.5)
    logger.warning("Backend start timed out. Opening UI anyway...")
    webbrowser.open(ui_url)


def main():
    host = "127.0.0.1"
    port = 8000
    url = f"http://{host}:{port}"

    logger.info("Starting DZ23 Trading AI Desktop Environment...")

    # Instância já ativa (duplo clique repetido): abre o painel existente em
    # vez de subir um segundo backend e disputar a porta 8000.
    if _healthz_ok(url):
        logger.info("Já existe uma instância servindo %s — abrindo o painel.", url)
        webbrowser.open(f"{url}/app")
        return

    # Dono Único: senha criada na 1ª execução (mostrada no diálogo) — o painel
    # não abre sessão sem ela; sem isso o app empacotado nega todo acesso.
    ensure_owner_password(notify=show_first_run_dialog)

    # Run uvicorn in a daemon thread
    backend_thread = threading.Thread(
        target=run_backend, args=(host, port), daemon=True
    )
    backend_thread.start()

    # Wait for backend and launch webview/browser UI
    wait_and_open_ui(url)

    try:
        while backend_thread.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down DZ23 Trading AI Desktop...")


if __name__ == "__main__":
    main()

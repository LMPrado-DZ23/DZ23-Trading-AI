"""
DZ23 Trading AI — Windows Native Launcher & Local Server Supervisor
"""

import ctypes
import logging
import os
import socket
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


def owner_data_dir() -> Path:
    return Path(os.environ.get("APPDATA", ".")) / "DZ23-Trading-AI"


def owner_password_path() -> Path:
    """Local do ANTIGO owner_password.txt — só a migração toca neste arquivo."""
    return owner_data_dir() / "owner_password.txt"


def owner_credentials_path() -> Path:
    return owner_data_dir() / "owner_credentials.json"


def migrate_legacy_owner_password() -> None:
    """Migra a senha legada em texto puro para hash Argon2id, sem perder acesso.

    - ACCESS_PASSWORD no ambiente: modo explícito (dev/teste) — não mexe em nada.
    - owner_credentials.json em uso: apaga só o txt órfão, se houver.
    - owner_password.txt presente: cria o hash com a MESMA senha (o dono
      continua entrando com o que já sabia), gera código de recuperação e
      apaga o texto puro do disco.

    Silencioso de propósito: o assistente de primeira execução vive no
    navegador; um diálogo bloqueante aqui quebraria a automação e o próprio
    fluxo de encerramento limpo do app empacotado.
    """
    if os.environ.get("ACCESS_PASSWORD"):
        return
    from service.api.owner_credentials import CredenciaisDoDono

    credenciais = CredenciaisDoDono(owner_credentials_path())
    texto_puro = owner_password_path()
    if credenciais.existe():
        texto_puro.unlink(missing_ok=True)
        return
    if not texto_puro.exists():
        return
    senha = texto_puro.read_text(encoding="utf-8").strip()
    if not senha:
        texto_puro.unlink(missing_ok=True)
        return
    try:
        credenciais.criar(senha)
    except Exception:
        # Migração que falha não pode trancar o dono fora: mantém o txt e o
        # caminho legado (ACCESS_PASSWORD) funcionando até a próxima execução.
        logger.exception("migração da senha legada falhou; mantendo owner_password.txt")
        os.environ["ACCESS_PASSWORD"] = senha
        return
    texto_puro.unlink(missing_ok=True)
    logger.info("senha legada migrada para hash Argon2id em %s", owner_credentials_path())


def run_backend(host: str = "127.0.0.1", port: int = 8000):
    """Start local FastAPI service bound exclusively to loopback."""
    os.environ["HOST"] = host
    os.environ["PORT"] = str(port)
    os.environ["MULTI_TENANT_ENABLED"] = "false"

    # Ensure data directory exists (a MESMA pasta que a migração de senha usa)
    user_data = owner_data_dir()
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


def _port_in_use(host: str, port: int, timeout: float = 0.5) -> bool:
    """True se qualquer programa já aceita conexões na porta."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_for_existing_instance(url: str, timeout: float = 15) -> bool:
    """A porta aceita conexão mas ninguém responde /healthz ainda: pode ser
    uma instância DZ23 iniciando. Espera ficar saudável; False se não ficar."""
    start = time.time()
    while time.time() - start < timeout:
        if _healthz_ok(url):
            return True
        time.sleep(1)
    return False


def show_port_busy_dialog(port: int) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            f"O aplicativo não conseguiu iniciar porque a porta {port} já está "
            f"sendo usada por outro programa.\n\nFeche o outro programa (ou "
            f"reinicie o computador) e abra o DZ23 Trading AI novamente.",
            "DZ23 Trading AI — porta ocupada",
            0x30,
        )
    except Exception:
        logger.error(
            "Porta %s ocupada por outro programa; encerrando. Feche o programa que usa a porta.",
            port,
        )


def show_startup_failed_dialog() -> None:
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            "O aplicativo não conseguiu iniciar o servidor local.\n\n"
            "Feche programas que possam usar a porta 8000, reinicie o "
            "computador e abra o DZ23 Trading AI novamente.",
            "DZ23 Trading AI — falha ao iniciar",
            0x30,
        )
    except Exception:
        logger.error("Servidor local não iniciou; encerrando.")


def wait_and_open_ui(base_url: str, ui_path: str = "/app", timeout: int = 15) -> bool:
    """Poll o /healthz da raiz até o backend responder, então abre o painel.
    True se o backend ficou saudável dentro do tempo."""
    ui_url = f"{base_url}{ui_path}"
    start_time = time.time()
    while time.time() - start_time < timeout:
        if _healthz_ok(base_url):
            logger.info("Local backend is ready. Opening UI...")
            webbrowser.open(ui_url)
            return True
        time.sleep(0.5)
    logger.warning("Backend start timed out. Opening UI anyway...")
    webbrowser.open(ui_url)
    return False


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

    # Porta ocupada por OUTRO programa (ou 1ª instância ainda iniciando):
    # espera a instância existente ficar pronta; se nada saudável aparecer,
    # avisa o dono — antes o launcher falhava em silêncio e abria o
    # navegador numa página alheia.
    if _port_in_use(host, port):
        if _wait_for_existing_instance(url):
            logger.info("Instância existente pronta — abrindo o painel.")
            webbrowser.open(f"{url}/app")
            return
        show_port_busy_dialog(port)
        return

    # Dono Único: senha legada em texto puro vira hash Argon2id (a MESMA senha
    # continua valendo) e o texto puro sai do disco. Primeira execução sem
    # senha nenhuma: o assistente no navegador cria — o launcher não deve
    # bloquear em diálogo (automação e encerramento limpo dependem disso).
    migrate_legacy_owner_password()

    # Run uvicorn in a daemon thread
    backend_thread = threading.Thread(
        target=run_backend, args=(host, port), daemon=True
    )
    backend_thread.start()

    # Wait for backend and launch webview/browser UI
    ready = wait_and_open_ui(url)
    if not ready and not backend_thread.is_alive():
        # Backend morreu no start (ex.: porta tomada entre a checagem e o
        # bind) — avisa em vez de fingir que abriu.
        show_startup_failed_dialog()
        return

    try:
        while backend_thread.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down DZ23 Trading AI Desktop...")


if __name__ == "__main__":
    main()

"""
DZ23 Trading AI — Windows Native Launcher & Local Server Supervisor
"""

import logging
import os
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

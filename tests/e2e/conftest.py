"""Suíte E2E com navegador real (Playwright) contra o app de verdade.

Onde playwright + um navegador não existem (ex.: CI Linux padrão), os testes
são pulados de forma explícita — nunca fingem sucesso. Para rodar localmente:

    uv run --with playwright pytest tests/e2e -v

O navegador usa os canais instalados na máquina (Chrome/Edge) e, como último
recurso, o chromium do próprio Playwright.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
E2E_OWNER_PASSWORD = "s3nh4-dz23-e2e"
EVIDENCE_DIR = Path(
    os.environ.get(
        "DZ23_E2E_EVIDENCE_DIR",
        REPO_ROOT / "docs" / "mission" / "evidence" / "e2e",
    )
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _spawn_server(tmp_path_factory, extra_env: dict, nome: str):
    """Sobe o app real (uvicorn) num DATA_DIR temporário e devolve (proc, url)."""
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        pytest.skip("uvicorn ausente; instale o extra 'desk' para rodar a suíte E2E")

    data_dir = tmp_path_factory.mktemp(nome)
    port = _free_port()
    env = os.environ.copy()
    env.pop("ACCESS_PASSWORD", None)
    env.update(
        {
            "DATA_DIR": str(data_dir),
            "MULTI_TENANT_ENABLED": "false",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        }
    )
    env.update(extra_env)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "service.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 60
    up = False
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"uvicorn encerrou cedo (exit {proc.returncode})")
        try:
            with urllib.request.urlopen(f"{base_url}/healthz", timeout=2) as resp:
                if resp.status == 200:
                    up = True
                    break
        except OSError:
            time.sleep(0.4)
    if not up:
        proc.terminate()
        raise RuntimeError("servidor E2E não respondeu /healthz em 60s")
    return proc, base_url


def _stop(proc) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
    """App real com a senha vinda do ambiente (caminho legado ainda aceito),
    como numa estação que já tinha instalação."""
    proc, base_url = _spawn_server(
        tmp_path_factory,
        {"ACCESS_PASSWORD": E2E_OWNER_PASSWORD, "ALERTA_WINDOWS": "false"},
        "dz23-e2e-data",
    )
    yield base_url
    _stop(proc)


@pytest.fixture()
def setup_server(tmp_path_factory):
    """Instalação NOVA de verdade: sem senha no ambiente e sem hash salvo —
    é o estado em que o assistente de primeira execução aparece."""
    proc, base_url = _spawn_server(tmp_path_factory, {}, "dz23-e2e-setup")
    yield base_url
    _stop(proc)


@pytest.fixture(scope="session")
def browser():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright não instalado (uv run --with playwright pytest tests/e2e)")

    with sync_playwright() as pw:
        erros: list[str] = []
        for kwargs in ({"channel": "chrome"}, {"channel": "msedge"}, {}):
            try:
                instance = pw.chromium.launch(headless=True, **kwargs)
            except Exception as exc:  # noqa: BLE001 - canal ausente é esperado
                erros.append(f"{kwargs.get('channel', 'chromium padrão')}: {exc}")
                continue
            yield instance
            instance.close()
            return
        pytest.skip("nenhum navegador disponível para o Playwright: " + " | ".join(erros))


@pytest.fixture()
def page(browser):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    pagina = context.new_page()
    yield pagina
    context.close()


@pytest.fixture()
def mobile_page(browser):
    # Celular típico (iPhone 12/13): o dono acessa a Oracle Cloud pelo celular.
    context = browser.new_context(viewport={"width": 390, "height": 844})
    pagina = context.new_page()
    yield pagina
    context.close()


@pytest.fixture(scope="session")
def evidence_dir():
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    return EVIDENCE_DIR


@pytest.fixture(scope="session")
def owner_password() -> str:
    return E2E_OWNER_PASSWORD

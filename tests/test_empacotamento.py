"""TA-002 — instalação limpa e imagem do produto.

Regressão dos dois achados: (1) o serviço dependia de fastapi sem declarar;
(2) o Dockerfile da árvore integrada subia a CLI, não o painel — o deploy
ficaria sem interface.
"""
from __future__ import annotations

from pathlib import Path

try:
    import tomllib  # Python 3.11+
except (ImportError, ModuleNotFoundError):  # Python 3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]


def pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


class TestExtras:
    def test_desk_extra_declares_the_whole_product(self):
        extras = pyproject()["project"]["optional-dependencies"]
        assert "desk" in extras, "falta o extra [desk] com o produto completo"
        joined = " ".join(extras["desk"]).lower()
        for need in ("fastapi", "uvicorn", "ccxt", "psycopg"):
            assert need in joined, f"[desk] não declara {need}"

    def test_dev_extra_covers_the_test_suite(self):
        dev = " ".join(pyproject()["project"]["optional-dependencies"]["dev"]).lower()
        for need in ("pytest", "httpx", "ccxt", "fastapi"):
            assert need in dev, f"[dev] não declara {need}"

    def test_console_scripts_still_exist(self):
        scripts = pyproject()["project"]["scripts"]
        assert "tradingagents" in scripts            # CLI preservada


class TestImagem:
    def test_dockerfile_installs_product_extras(self):
        df = (ROOT / "Dockerfile").read_text()
        assert '".[desk]"' in df, "a imagem precisa instalar o extra [desk]"

    def test_dockerfile_serves_the_panel_not_the_cli(self):
        df = (ROOT / "Dockerfile").read_text()
        assert "service.app:app" in df, "a imagem tem de subir o serviço com painel"
        assert "uvicorn" in df and "8080" in df
        assert 'ENTRYPOINT ["tradingagents"]' not in df   # achado corrigido

    def test_image_persists_data_and_runs_unprivileged(self):
        df = (ROOT / "Dockerfile").read_text()
        assert 'VOLUME ["/data"]' in df and "USER appuser" in df
        assert "HEALTHCHECK" in df

    def test_deploy_doc_matches_the_image(self):
        deploy = (ROOT / "DEPLOY.md").read_text()
        assert "ACCESS_PASSWORD" in deploy and "/data" in deploy


class TestWheelAutossuficiente:
    """O wheel precisa levar o serviço E o painel — senão 'pip install'
    entrega um produto sem interface (achado do teste de instalação limpa)."""

    def test_service_package_is_shipped(self):
        include = pyproject()["tool"]["setuptools"]["packages"]["find"]["include"]
        assert any(i.startswith("service") for i in include)

    def test_panel_html_is_package_data(self):
        data = pyproject()["tool"]["setuptools"]["package-data"]
        assert "service" in data
        assert any("static" in pattern and "html" in pattern
                   for pattern in data["service"])

    def test_package_data_declared_once(self):
        raw = (ROOT / "pyproject.toml").read_text()
        assert raw.count("[tool.setuptools.package-data]") == 1

import os
from unittest.mock import patch

from desktop.launcher import run_backend, wait_and_open_ui


def test_desktop_launcher_loopback_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))

    with patch("uvicorn.run") as mock_uvicorn:
        run_backend(host="127.0.0.1", port=8000)

        assert os.environ["HOST"] == "127.0.0.1"
        assert os.environ["PORT"] == "8000"
        assert os.environ["MULTI_TENANT_ENABLED"] == "false"
        mock_uvicorn.assert_called_once()


def test_wait_and_open_ui_success():
    class DummyResponse:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass

    with patch("urllib.request.urlopen", return_value=DummyResponse()), patch("webbrowser.open") as mock_browser:
        wait_and_open_ui("http://127.0.0.1:8000", timeout=1)
        mock_browser.assert_called_once_with("http://127.0.0.1:8000")

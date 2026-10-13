import os
from unittest.mock import patch

from desktop.launcher import ensure_owner_password, owner_password_path, run_backend, wait_and_open_ui


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

    with patch("urllib.request.urlopen", return_value=DummyResponse()) as mock_urlopen, patch("webbrowser.open") as mock_browser:
        wait_and_open_ui("http://127.0.0.1:8000", timeout=1)
        mock_urlopen.assert_called_once_with("http://127.0.0.1:8000/healthz", timeout=1)
        mock_browser.assert_called_once_with("http://127.0.0.1:8000")


def test_owner_password_created_on_first_run_and_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("ACCESS_PASSWORD", raising=False)

    notificacoes = []
    senha = ensure_owner_password(notify=lambda pw, path: notificacoes.append((pw, path)))

    assert senha and len(senha) >= 12
    assert os.environ["ACCESS_PASSWORD"] == senha
    assert owner_password_path().read_text(encoding="utf-8").strip() == senha
    assert len(notificacoes) == 1 and notificacoes[0][0] == senha

    monkeypatch.delenv("ACCESS_PASSWORD")
    senha2 = ensure_owner_password(notify=lambda pw, path: notificacoes.append((pw, path)))
    assert senha2 == senha
    assert len(notificacoes) == 1  # sem novo diálogo nas execuções seguintes


def test_owner_password_env_wins_without_touching_file(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4-do-dono")
    assert ensure_owner_password() == "s3nh4-do-dono"
    assert not owner_password_path().exists()

import os
from unittest.mock import patch

from desktop.launcher import (
    ensure_owner_password,
    main,
    owner_password_path,
    run_backend,
    wait_and_open_ui,
)


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
        # O healthz vive na RAIZ; o painel vive em /app. Misturar os dois
        # fazia o launcher pollar /app/healthz (404) e abrir o navegador
        # 15 s atrasado com um aviso falso de timeout.
        mock_urlopen.assert_called_once_with("http://127.0.0.1:8000/healthz", timeout=1)
        mock_browser.assert_called_once_with("http://127.0.0.1:8000/app")


def test_wait_and_open_ui_retries_until_healthz_ok():
    class DummyResponse:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass

    chamadas = []

    def urlopen_indisponivel_primeiro(url, timeout=1):
        chamadas.append(url)
        if len(chamadas) == 1:
            raise OSError("connection refused")
        return DummyResponse()

    with patch("urllib.request.urlopen", side_effect=urlopen_indisponivel_primeiro), \
            patch("webbrowser.open") as mock_browser, \
            patch("desktop.launcher.time.sleep"):
        wait_and_open_ui("http://127.0.0.1:8000", timeout=2)
        assert chamadas == ["http://127.0.0.1:8000/healthz"] * 2
        mock_browser.assert_called_once_with("http://127.0.0.1:8000/app")


def test_main_opens_existing_instance_without_starting_backend():
    # Instância duplicada: se já há um DZ23 servindo o loopback, apenas abre
    # o painel — sem segunda travinha de senha e sem disputar a porta 8000.
    with patch("desktop.launcher._healthz_ok", return_value=True) as mock_health, \
            patch("desktop.launcher.ensure_owner_password") as mock_pwd, \
            patch("desktop.launcher.run_backend") as mock_backend, \
            patch("webbrowser.open") as mock_browser:
        main()
        mock_health.assert_called_once()
        mock_browser.assert_called_once_with("http://127.0.0.1:8000/app")
        mock_pwd.assert_not_called()
        mock_backend.assert_not_called()


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

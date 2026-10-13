import os
from unittest.mock import patch

from desktop.launcher import (
    _port_in_use,
    _wait_for_existing_instance,
    ensure_owner_password,
    main,
    owner_password_path,
    run_backend,
    wait_and_open_ui,
)


class _FakeServer:
    def __init__(self, config):
        self.config = config
        self.should_exit = False
        self.rodou = False

    def run(self):
        self.rodou = True


class _FakeTime:
    """Relógio falso para testar esperas sem dormir de verdade."""

    def __init__(self):
        self.t = 0.0

    def time(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def test_desktop_launcher_loopback_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    criados: list[_FakeServer] = []

    def fakeserver(config):
        criados.append(_FakeServer(config))
        return criados[-1]

    with patch("uvicorn.Config") as mock_cfg, \
            patch("uvicorn.Server", side_effect=fakeserver), \
            patch("service.app.set_shutdown_hook") as mock_hook:
        run_backend(host="127.0.0.1", port=8000)

        assert os.environ["HOST"] == "127.0.0.1"
        assert os.environ["PORT"] == "8000"
        assert os.environ["MULTI_TENANT_ENABLED"] == "false"
        kwargs = mock_cfg.call_args.kwargs
        assert kwargs["host"] == "127.0.0.1" and kwargs["port"] == 8000
        assert criados and criados[0].rodou  # servidor real, não uvicorn.run solto
        mock_hook.assert_called_once()  # C009: gancho de encerramento ligado


def test_quit_endpoint_stops_the_launcher_server(tmp_path, monkeypatch):
    # C009: o gancho registrado pelo launcher precisa ser capaz de parar o
    # servidor de verdade — é o que o botão "Fechar aplicativo" dispara.
    monkeypatch.setenv("APPDATA", str(tmp_path))
    capturado: dict = {}
    criados: list[_FakeServer] = []

    def fakeserver(config):
        criados.append(_FakeServer(config))
        return criados[-1]

    with patch("uvicorn.Config"), \
            patch("uvicorn.Server", side_effect=fakeserver), \
            patch("service.app.set_shutdown_hook", side_effect=lambda fn: capturado.setdefault("fn", fn)):
        run_backend(host="127.0.0.1", port=8000)

    assert capturado["fn"] is not None
    capturado["fn"]()
    assert criados[0].should_exit is True


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


def test_port_in_use_detects_real_listener():
    # C010: com um processo estrangeiro de verdade escutando a porta, o
    # launcher precisa PERCEBER — antes ele tentava subir um segundo backend
    # e falhava em silêncio (navegador aberto numa página alheia).
    import socket

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    porta = srv.getsockname()[1]
    try:
        assert _port_in_use("127.0.0.1", porta) is True
    finally:
        srv.close()
    assert _port_in_use("127.0.0.1", porta) is False


def test_port_busy_by_foreign_program_shows_dialog_without_starting_backend():
    with patch("desktop.launcher._healthz_ok", return_value=False), \
            patch("desktop.launcher._port_in_use", return_value=True), \
            patch("desktop.launcher._wait_for_existing_instance", return_value=False), \
            patch("desktop.launcher.show_port_busy_dialog") as mock_dialog, \
            patch("desktop.launcher.ensure_owner_password") as mock_pwd, \
            patch("desktop.launcher.run_backend") as mock_backend, \
            patch("webbrowser.open") as mock_browser:
        main()
        mock_dialog.assert_called_once_with(8000)
        mock_browser.assert_not_called()
        mock_pwd.assert_not_called()
        mock_backend.assert_not_called()


def test_slow_existing_instance_wins_over_flagging_the_port():
    # Duplo clique enquanto a 1ª instância ainda inicia: a porta já aceita
    # conexão, mas o DZ23 ainda não responde /healthz — espera e abre o
    # painel, sem diálogo de porta ocupada e sem segundo backend.
    with patch("desktop.launcher._healthz_ok", return_value=False), \
            patch("desktop.launcher._port_in_use", return_value=True), \
            patch("desktop.launcher._wait_for_existing_instance", return_value=True), \
            patch("desktop.launcher.show_port_busy_dialog") as mock_dialog, \
            patch("desktop.launcher.run_backend") as mock_backend, \
            patch("webbrowser.open") as mock_browser:
        main()
        mock_dialog.assert_not_called()
        mock_backend.assert_not_called()
        mock_browser.assert_called_once_with("http://127.0.0.1:8000/app")


def test_wait_for_existing_instance_polls_until_healthy():
    respostas = [False, False, True]
    with patch("desktop.launcher._healthz_ok", side_effect=lambda url: respostas.pop(0)), \
            patch("desktop.launcher.time", _FakeTime()):
        assert _wait_for_existing_instance("http://127.0.0.1:8000", timeout=10) is True


def test_wait_for_existing_instance_gives_up_after_timeout():
    with patch("desktop.launcher._healthz_ok", return_value=False), \
            patch("desktop.launcher.time", _FakeTime()):
        assert _wait_for_existing_instance("http://127.0.0.1:8000", timeout=3) is False


def test_backend_death_during_startup_is_reported_not_hidden():
    # Porta tomada entre a checagem e o bind: o backend morre no start e o
    # dono recebe um aviso claro — antes o navegador abria numa página que
    # não existe e o app sumia sem explicação.
    class _DeadThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def is_alive(self):
            return False

    with patch("desktop.launcher._healthz_ok", return_value=False), \
            patch("desktop.launcher._port_in_use", return_value=False), \
            patch("desktop.launcher.threading.Thread", _DeadThread), \
            patch("desktop.launcher.ensure_owner_password"), \
            patch("desktop.launcher.wait_and_open_ui", return_value=False), \
            patch("desktop.launcher.show_startup_failed_dialog") as mock_dialog, \
            patch("webbrowser.open") as mock_browser:
        main()
        mock_dialog.assert_called_once()
        mock_browser.assert_not_called()

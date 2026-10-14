"""Testes do launcher Windows endurecido (C021): portas, reuso, fallback,
logs persistentes e healthz real — a matriz pedida (inicialização, porta
ocupada, falha de backend, múltiplas instâncias, reinício e encerramento)."""

import logging
import socket
import sys
import threading
import time as _time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import desktop.launcher as launcher
from desktop.launcher import (
    PortaInfo,
    _pid_da_porta,
    _processo_por_pid,
    configurar_logs,
    descreve_porta,
    escolher_arranque,
    esperar_healthz,
    investigar_porta,
    log_path,
    main,
    migrate_legacy_owner_password,
    owner_credentials_path,
    owner_password_path,
    run_backend,
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


class _FakeThread:
    """Thread de mentira com semântica "viva uma vez": nasce viva e morre na
    consulta seguinte (o loop do main roda uma volta e sai), ou imediatamente
    no join(). `alive=False` cria uma thread já morta."""

    def __init__(self, alive=True, *args, **kwargs):
        self.vivo = alive
        self.juntou = False
        self._target = kwargs.get("target")
        self._args = kwargs.get("args", ())

    def start(self):
        if self._target is not None:
            self._target(*self._args)

    def is_alive(self):
        if self.juntou or not self.vivo:
            return False
        self.vivo = False
        return True

    def join(self, timeout=None):
        self.juntou = True
        self.vivo = False


@pytest.fixture()
def launcher_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(launcher, "_LOG_CONFIGURADO", False)
    monkeypatch.setattr(launcher, "_HANDLERS", [])
    hooko_thread_antes, hooko_main_antes = threading.excepthook, sys.excepthook
    yield tmp_path
    threading.excepthook, sys.excepthook = hooko_thread_antes, hooko_main_antes
    raiz = logging.getLogger()
    for handler in launcher._HANDLERS:
        raiz.removeHandler(handler)
        handler.close()
    launcher._HANDLERS.clear()


# ---------------------------------------------------------------------------
# run_backend — loopback, porta efetiva e gancho de encerramento (C009)
# ---------------------------------------------------------------------------

def test_desktop_launcher_loopback_configuration(launcher_tmp):
    criados: list[_FakeServer] = []

    def fakeserver(config):
        criados.append(_FakeServer(config))
        return criados[-1]

    with patch("uvicorn.Config") as mock_cfg, \
            patch("uvicorn.Server", side_effect=fakeserver), \
            patch("service.app.set_shutdown_hook") as mock_hook:
        run_backend(host="127.0.0.1", port=8123)

        assert launcher.os.environ["HOST"] == "127.0.0.1"
        assert launcher.os.environ["PORT"] == "8123"
        assert launcher.os.environ["DZ23_PORTA_EFETIVA"] == "8123"
        assert launcher.os.environ["MULTI_TENANT_ENABLED"] == "false"
        kwargs = mock_cfg.call_args.kwargs
        assert kwargs["host"] == "127.0.0.1" and kwargs["port"] == 8123
        assert kwargs["log_config"] is None  # logs do uvicorn vão para o log persistente
        assert criados and criados[0].rodou  # servidor real, não uvicorn.run solto
        mock_hook.assert_called_once()  # C009: gancho de encerramento ligado


def test_quit_endpoint_stops_the_launcher_server(launcher_tmp):
    # C009: o gancho registrado pelo launcher precisa ser capaz de parar o
    # servidor de verdade — é o que o botão "Fechar aplicativo" dispara.
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


# ---------------------------------------------------------------------------
# Migração da senha legada (preservação do acesso do dono)
# ---------------------------------------------------------------------------

def test_senha_legada_migra_para_hash_sem_perder_acesso(launcher_tmp, monkeypatch):
    # A senha do dono estava em texto puro num txt. A migração cria o hash com
    # a MESMA senha (o dono continua entrando com o que já sabia) e apaga o
    # texto puro do disco — o arquivo antigo não pode sobrar para trás e a
    # senha não pode aparecer no log persistente.
    monkeypatch.delenv("ACCESS_PASSWORD", raising=False)
    texto = owner_password_path()
    texto.parent.mkdir(parents=True, exist_ok=True)
    texto.write_text("senha-antiga-do-dono", encoding="utf-8")

    migrate_legacy_owner_password()

    from service.api.owner_credentials import CredenciaisDoDono
    credenciais = CredenciaisDoDono(owner_credentials_path())
    assert credenciais.verificar_senha("senha-antiga-do-dono") is True
    assert not texto.exists()
    conteudo = owner_credentials_path().read_text(encoding="utf-8")
    assert "$argon2id$" in conteudo
    assert "senha-antiga-do-dono" not in conteudo


def test_migracao_repetida_nao_mexe_no_hash_e_limpa_txt_orfao(launcher_tmp, monkeypatch):
    monkeypatch.delenv("ACCESS_PASSWORD", raising=False)
    from service.api.owner_credentials import CredenciaisDoDono
    credenciais = CredenciaisDoDono(owner_credentials_path())
    credenciais.criar("senha-do-dono-2026")
    antes = owner_credentials_path().read_text(encoding="utf-8")
    orfao = owner_password_path()
    orfao.parent.mkdir(parents=True, exist_ok=True)
    orfao.write_text("um-txt-que-sobrou", encoding="utf-8")

    migrate_legacy_owner_password()

    assert owner_credentials_path().read_text(encoding="utf-8") == antes
    assert not orfao.exists()  # txt órfão não fica no disco
    assert credenciais.verificar_senha("senha-do-dono-2026") is True


def test_migracao_nao_toca_nada_quando_o_ambiente_define_a_senha(launcher_tmp, monkeypatch):
    # Modo explícito (dev/teste): ACCESS_PASSWORD manda; nada é migrado.
    monkeypatch.setenv("ACCESS_PASSWORD", "senha-via-ambiente")
    texto = owner_password_path()
    texto.parent.mkdir(parents=True, exist_ok=True)
    texto.write_text("nao-mexer", encoding="utf-8")

    migrate_legacy_owner_password()

    assert texto.exists()
    assert not owner_credentials_path().exists()


def test_primeira_execucao_sem_senha_nao_cria_nada_aqui(launcher_tmp, monkeypatch):
    # Sem txt legado: quem cria as credenciais é o assistente no navegador.
    monkeypatch.delenv("ACCESS_PASSWORD", raising=False)

    migrate_legacy_owner_password()

    assert not owner_credentials_path().exists()
    assert not owner_password_path().exists()


# ---------------------------------------------------------------------------
# Pré-checagem de portas: identificação sem NUNCA encerrar outros processos
# ---------------------------------------------------------------------------

def test_port_in_use_detects_real_listener():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    porta = srv.getsockname()[1]
    try:
        assert launcher._port_in_use("127.0.0.1", porta) is True
    finally:
        srv.close()
    assert launcher._port_in_use("127.0.0.1", porta) is False


def test_pid_da_porta_parseia_netstat_do_windows(monkeypatch):
    monkeypatch.setattr(launcher, "_WINDOWS", True)
    saida = (
        b"  TCP    127.0.0.1:8000         0.0.0.0:0              LISTENING       4242\r\n"
        b"  TCP    127.0.0.1:8017         0.0.0.0:0              LISTENING       13836\r\n"
        b"  TCP    192.168.1.5:55555      192.168.1.9:8000       ESTABLISHED     9999\r\n"
    )
    monkeypatch.setattr(launcher, "_rodar_sem_console", lambda cmd: saida)
    assert _pid_da_porta(8000) == 4242
    assert _pid_da_porta(8017) == 13836
    assert _pid_da_porta(8080) is None


def test_processo_por_pid_le_tasklist_csv(monkeypatch):
    monkeypatch.setattr(launcher, "_WINDOWS", True)
    csv = b'"OmniRoute.exe","4242","Console","1","10.000 K"\r\n'
    monkeypatch.setattr(launcher, "_rodar_sem_console", lambda cmd: csv)
    assert _processo_por_pid(4242) == "OmniRoute.exe"
    monkeypatch.setattr(launcher, "_rodar_sem_console", lambda cmd: b"INFO: No tasks...\r\n")
    assert _processo_por_pid(777) is None


def test_investigar_porta_livre():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    porta = srv.getsockname()[1]
    srv.close()  # porta conhecidamente livre agora
    info = investigar_porta("127.0.0.1", porta)
    assert info.ocupada is False and info.dz23 is False


def test_investigar_porta_ocupada_por_terceiro(monkeypatch):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    porta = srv.getsockname()[1]
    try:
        monkeypatch.setattr(launcher, "_healthz_payload", lambda *a, **k: None)
        info = investigar_porta("127.0.0.1", porta)
        assert info.ocupada is True and info.dz23 is False
        assert "ocupada" in descreve_porta(info)
    finally:
        srv.close()


def test_investigar_porta_reconhece_instancia_dz23(monkeypatch):
    monkeypatch.setattr(launcher, "_port_in_use", lambda host, p, timeout=0.5: True)
    monkeypatch.setattr(
        launcher, "_healthz_payload",
        lambda *a, **k: {"status": "ok", "app": "tradingagents-agency/1"},
    )
    info = investigar_porta("127.0.0.1", 8000)
    assert info.ocupada is True and info.dz23 is True
    assert "reutilizável" in descreve_porta(info)


# ---------------------------------------------------------------------------
# Decisão de arranque: reutilizar instância saudável × porta livre × esgotado
# ---------------------------------------------------------------------------

def test_escolher_arranque_reutiliza_instancia_saudavel(monkeypatch):
    monkeypatch.setattr(launcher, "_port_in_use", lambda host, p, timeout=0.5: p == 8003)
    monkeypatch.setattr(
        launcher, "_healthz_payload",
        lambda base, timeout=1.0: {"app": "tradingagents-agency/1"} if base.endswith(":8003") else None,
    )
    decisao, porta, infos = escolher_arranque("127.0.0.1", primeira=8000, ultima=8010, carencia=0)
    assert (decisao, porta) == ("reutilizar", 8003)
    assert any(i.dz23 for i in infos)


def test_escolher_arranque_pula_porta_de_terceiro_e_usa_a_proxima_livre(monkeypatch):
    # 8000 ocupada por OUTRO programa (sem marcador DZ23): NUNCA encerrar —
    # iniciar na primeira porta livre da faixa e registrar o ocupante.
    monkeypatch.setattr(launcher, "_port_in_use", lambda host, p, timeout=0.5: p == 8000)
    monkeypatch.setattr(launcher, "_healthz_payload", lambda *a, **k: None)
    decisao, porta, infos = escolher_arranque("127.0.0.1", primeira=8000, ultima=8010, carencia=0)
    assert (decisao, porta) == ("iniciar", 8001)
    ocupada = next(i for i in infos if i.porta == 8000)
    assert ocupada.ocupada and not ocupada.dz23


def test_escolher_arranque_carencia_encontra_dz23_subindo(monkeypatch):
    # Duplo clique enquanto a 1ª instância sobe: a porta aceita conexão mas
    # o DZ23 ainda não responde — a carência identifica e reutiliza.
    monkeypatch.setattr(launcher, "_port_in_use", lambda host, p, timeout=0.5: p == 8000)
    respostas = [None, {"app": "tradingagents-agency/1"}]  # 1ª sonda falha; carência vê o DZ23
    monkeypatch.setattr(launcher, "_healthz_payload", lambda *a, **k: respostas.pop(0))
    monkeypatch.setattr(launcher, "time", _FakeTime())
    decisao, porta, _ = escolher_arranque("127.0.0.1", primeira=8000, ultima=8010, carencia=5.0)
    assert (decisao, porta) == ("reutilizar", 8000)


def test_escolher_arranque_sem_porta_quando_faixa_toda_ocupada(monkeypatch):
    monkeypatch.setattr(launcher, "_port_in_use", lambda host, p, timeout=0.5: True)
    monkeypatch.setattr(launcher, "_healthz_payload", lambda *a, **k: None)
    decisao, porta, infos = escolher_arranque("127.0.0.1", primeira=8000, ultima=8010, carencia=0)
    assert decisao == "sem_porta" and porta is None
    assert all(i.ocupada for i in infos)


def test_escolher_arranque_com_socket_real_e_sem_mocks():
    # Integração de verdade: um listener real empurra o DZ23 para a próxima
    # porta livre — base dinâmica para não colidir com o ambiente.
    tmp = socket.socket()
    tmp.bind(("127.0.0.1", 0))
    base = tmp.getsockname()[1]
    tmp.close()
    srv = socket.socket()
    srv.bind(("127.0.0.1", base))
    srv.listen(1)
    try:
        decisao, porta, infos = escolher_arranque("127.0.0.1", primeira=base, ultima=base + 2, carencia=0)
        assert decisao == "iniciar" and porta is not None and porta > base
        assert infos[0].ocupada and not infos[0].dz23
    finally:
        srv.close()


# ---------------------------------------------------------------------------
# Healthcheck: só considera iniciado quando o PRÓPRIO DZ23 responde
# ---------------------------------------------------------------------------

def test_esperar_healthz_exige_marcador_do_dz23(monkeypatch):
    chamadas = {"n": 0}

    def payload(base, timeout=1.0):
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            return {"app": "outro-app-qualquer"}  # 200, mas NÃO é o DZ23
        return {"app": "tradingagents-agency/1", "status": "ok"}

    monkeypatch.setattr(launcher, "_healthz_payload", payload)
    monkeypatch.setattr(launcher.time, "sleep", lambda _s: None)  # sem dormir de verdade
    assert esperar_healthz("127.0.0.1", 8000, timeout=5) is True
    assert chamadas["n"] == 2


def test_esperar_healthz_falha_rapido_quando_backend_morre(monkeypatch):
    monkeypatch.setattr(launcher, "_healthz_payload", lambda *a, **k: None)
    t0 = _time.perf_counter()
    assert esperar_healthz("127.0.0.1", 8000, timeout=60, thread=_FakeThread(alive=False)) is False
    assert _time.perf_counter() - t0 < 2.0  # não espera o timeout inteiro


def test_esperar_healthz_expira_sem_resposta(monkeypatch):
    monkeypatch.setattr(launcher, "_healthz_payload", lambda *a, **k: None)
    monkeypatch.setattr(launcher, "time", _FakeTime())
    assert esperar_healthz("127.0.0.1", 8000, timeout=3) is False


# ---------------------------------------------------------------------------
# Logs persistentes — traceback completo, sem credenciais
# ---------------------------------------------------------------------------

def test_configurar_logs_cria_arquivo_rotativo(launcher_tmp):
    caminho = configurar_logs()
    assert caminho == launcher_tmp / "DZ23-Trading-AI" / "logs" / "launcher.log"
    launcher.logger.info("linha de teste do launcher")
    for handler in launcher._HANDLERS:
        handler.flush()
    assert "linha de teste do launcher" in caminho.read_text(encoding="utf-8")


def test_hooko_thread_grava_traceback_completo(launcher_tmp):
    configurar_logs()
    args = SimpleNamespace(
        thread=SimpleNamespace(name="backend"),
        exc_type=ValueError,
        exc_value=ValueError("porta explodiu"),
        exc_traceback=None,
    )
    launcher._hooko_thread(args)
    for handler in launcher._HANDLERS:
        handler.flush()
    conteudo = log_path().read_text(encoding="utf-8")
    assert "backend" in conteudo and "porta explodiu" in conteudo and "ValueError" in conteudo


def test_launcher_log_nao_contem_senha_do_dono(launcher_tmp, monkeypatch):
    monkeypatch.delenv("ACCESS_PASSWORD", raising=False)
    monkeypatch.setattr(launcher, "_LOG_CONFIGURADO", False)
    configurar_logs()
    texto = owner_password_path()
    texto.parent.mkdir(parents=True, exist_ok=True)
    texto.write_text("segredo-que-nao-vaza", encoding="utf-8")

    migrate_legacy_owner_password()

    for handler in launcher._HANDLERS:
        handler.flush()
    assert "segredo-que-nao-vaza" not in log_path().read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Fluxos do main(): reuso, arranque na porta efetiva, falhas visíveis
# ---------------------------------------------------------------------------

def test_main_reutiliza_instancia_sem_subir_backend(launcher_tmp):
    infos = [PortaInfo(8003, True, True, None, None)]
    with patch("desktop.launcher.escolher_arranque", return_value=("reutilizar", 8003, infos)), \
            patch("desktop.launcher.migrate_legacy_owner_password") as mock_migrar, \
            patch("desktop.launcher.run_backend") as mock_backend, \
            patch("webbrowser.open") as mock_browser:
        assert main() == 0
        mock_browser.assert_called_once_with("http://127.0.0.1:8003/app")
        mock_migrar.assert_not_called()
        mock_backend.assert_not_called()


def test_main_inicia_na_porta_efetiva_e_so_abre_apos_healthz(launcher_tmp):
    infos = [PortaInfo(8000, True, False, 4242, "OmniRoute.exe"), PortaInfo(8001, False, False, None, None)]
    with patch("desktop.launcher.escolher_arranque", return_value=("iniciar", 8001, infos)), \
            patch("desktop.launcher.migrate_legacy_owner_password") as mock_migrar, \
            patch("desktop.launcher.run_backend") as mock_backend, \
            patch("desktop.launcher.esperar_healthz", return_value=True) as mock_healthz, \
            patch("desktop.launcher.threading.Thread", _FakeThread), \
            patch("webbrowser.open") as mock_browser:
        assert main() == 0
        mock_migrar.assert_called_once()
        mock_backend.assert_called_once()
        assert mock_backend.call_args.args[1] == 8001  # porta EFETIVA propagada ao backend
        mock_healthz.assert_called_once()
        assert mock_healthz.call_args.args[1] == 8001  # healthcheck na porta efetiva
        mock_browser.assert_called_once_with("http://127.0.0.1:8001/app")


def test_main_nao_abre_navegador_quando_healthz_falha(launcher_tmp):
    infos = [PortaInfo(8000, False, False, None, None)]
    with patch("desktop.launcher.escolher_arranque", return_value=("iniciar", 8000, infos)), \
            patch("desktop.launcher.migrate_legacy_owner_password"), \
            patch("desktop.launcher.run_backend"), \
            patch("desktop.launcher.esperar_healthz", return_value=False), \
            patch("desktop.launcher.threading.Thread", _FakeThread), \
            patch("desktop.launcher.show_startup_failed_dialog") as mock_dialog, \
            patch("webbrowser.open") as mock_browser:
        assert main() == 1
        mock_browser.assert_not_called()
        porta, caminho = mock_dialog.call_args.args
        assert porta == 8000 and Path(caminho).name == "launcher.log"


def test_main_avisa_quando_todas_as_portas_estao_ocupadas(launcher_tmp):
    infos = [PortaInfo(p, True, False, 1000 + p, "AlgumPrograma.exe") for p in range(8000, 8011)]
    with patch("desktop.launcher.escolher_arranque", return_value=("sem_porta", None, infos)), \
            patch("desktop.launcher.migrate_legacy_owner_password") as mock_migrar, \
            patch("desktop.launcher.show_port_busy_dialog") as mock_dialog, \
            patch("webbrowser.open") as mock_browser:
        assert main() == 1
        mock_dialog.assert_called_once_with(infos)
        mock_migrar.assert_not_called()
        mock_browser.assert_not_called()


def test_main_falha_da_pre_checagem_vira_log_e_aviso(launcher_tmp):
    with patch("desktop.launcher.escolher_arranque", side_effect=RuntimeError("netstat sumiu")), \
            patch("desktop.launcher.show_startup_failed_dialog") as mock_dialog, \
            patch("webbrowser.open") as mock_browser:
        assert main() == 1
        mock_dialog.assert_called_once()
        mock_browser.assert_not_called()
    for handler in launcher._HANDLERS:
        handler.flush()
    assert "netstat sumiu" in log_path().read_text(encoding="utf-8")


def test_main_backend_real_morre_e_avisa_com_log(launcher_tmp):
    # Falha de backend de verdade: a thread morre, o excepthook grava o
    # traceback no log e o main devolve 1 SEM abrir o navegador.
    def backend_que_morre(host, port):
        raise RuntimeError("falha simulada de import do backend")

    infos = [PortaInfo(8000, False, False, None, None)]
    with patch("desktop.launcher.escolher_arranque", return_value=("iniciar", 8000, infos)), \
            patch("desktop.launcher.migrate_legacy_owner_password"), \
            patch("desktop.launcher.run_backend", backend_que_morre), \
            patch("desktop.launcher.show_startup_failed_dialog") as mock_dialog, \
            patch("webbrowser.open") as mock_browser:
        assert main() == 1
        mock_browser.assert_not_called()
        mock_dialog.assert_called_once()
    for handler in launcher._HANDLERS:
        handler.flush()
    assert "falha simulada de import do backend" in log_path().read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Contrato com o instalador: --version, --quit, mutex e saída graciosa
# ---------------------------------------------------------------------------

def test_run_backend_registra_gancho_global_de_encerramento(launcher_tmp, monkeypatch):
    # É este gancho que o loop do main() aciona quando o instalador pede a
    # saída graciosa — precisa desligar o mesmo servidor do /v1/app/quit.
    monkeypatch.setattr(launcher, "_ENCERRAR_SERVIDOR", None)
    criados: list[_FakeServer] = []

    def fakeserver(config):
        criados.append(_FakeServer(config))
        return criados[-1]

    with patch("uvicorn.Config"), \
            patch("uvicorn.Server", side_effect=fakeserver), \
            patch("service.app.set_shutdown_hook"):
        run_backend(host="127.0.0.1", port=8000)

    assert launcher._ENCERRAR_SERVIDOR is not None
    launcher._ENCERRAR_SERVIDOR()
    assert criados[0].should_exit is True


def test_main_version_imprime_e_sai_sem_subir_backend(launcher_tmp, capsys):
    with patch("tradingagents.versao.versao_do_pacote", return_value="9.9.9-teste"), \
            patch("desktop.launcher.escolher_arranque") as mock_arranque:
        assert main(["--version"]) == 0
    assert "9.9.9-teste" in capsys.readouterr().out
    mock_arranque.assert_not_called()


def test_main_quit_delega_para_o_evento_de_saida(launcher_tmp):
    with patch("desktop.launcher._atender_pedido_de_saida", return_value=0) as mock_quit:
        assert main(["--quit"]) == 0
    mock_quit.assert_called_once()


def test_main_quit_sem_instancia_retorna_1(launcher_tmp, monkeypatch):
    # Sem API do Windows (ou sem instância viva), --quit não finge sucesso.
    monkeypatch.setattr(launcher, "_WINDOWS", False)
    assert main(["--quit"]) == 1


def test_main_cria_mutex_quando_inicia(launcher_tmp):
    # O mutex nomeado é o sinal lido pelo instalador ("app em execução").
    infos = [PortaInfo(8000, False, False, None, None)]
    with patch("desktop.launcher.escolher_arranque", return_value=("iniciar", 8000, infos)), \
            patch("desktop.launcher.migrate_legacy_owner_password"), \
            patch("desktop.launcher.run_backend"), \
            patch("desktop.launcher.esperar_healthz", return_value=True), \
            patch("desktop.launcher.threading.Thread", _FakeThread), \
            patch("desktop.launcher._criar_mutex_instancia", return_value=4242) as mock_mutex, \
            patch("desktop.launcher._criar_evento_saida", return_value=777), \
            patch("webbrowser.open"):
        assert main([]) == 0
    mock_mutex.assert_called_once()


def test_main_reuso_nao_cria_mutex(launcher_tmp):
    infos = [PortaInfo(8003, True, True, None, None)]
    with patch("desktop.launcher.escolher_arranque", return_value=("reutilizar", 8003, infos)), \
            patch("desktop.launcher._criar_mutex_instancia") as mock_mutex, \
            patch("webbrowser.open"):
        assert main([]) == 0
    mock_mutex.assert_not_called()


def test_main_encerra_graciosamente_quando_instalador_pede(launcher_tmp):
    # Pedido de saída (evento do --quit): desliga o uvicorn pelo MESMO gancho
    # do painel, espera a thread, limpa o evento e sai com exit 0.
    infos = [PortaInfo(8000, False, False, None, None)]
    chamado = {"encerrar": False, "limpar": 0}

    def encerrar():
        chamado["encerrar"] = True

    with patch("desktop.launcher.escolher_arranque", return_value=("iniciar", 8000, infos)), \
            patch("desktop.launcher.migrate_legacy_owner_password"), \
            patch("desktop.launcher.run_backend"), \
            patch("desktop.launcher.esperar_healthz", return_value=True), \
            patch("desktop.launcher.threading.Thread", _FakeThread), \
            patch("desktop.launcher._criar_mutex_instancia", return_value=1), \
            patch("desktop.launcher._criar_evento_saida", return_value=1), \
            patch("desktop.launcher._saida_solicitada", return_value=True), \
            patch("desktop.launcher._ENCERRAR_SERVIDOR", encerrar), \
            patch("desktop.launcher._limpar_evento_saida", side_effect=lambda: chamado.__setitem__("limpar", chamado["limpar"] + 1)), \
            patch("webbrowser.open") as mock_browser:
        assert main([]) == 0
    assert chamado["encerrar"] is True
    assert chamado["limpar"] == 1
    mock_browser.assert_called_once()  # abriu o painel antes de encerrar


def test_main_nao_encerra_sem_pedido(launcher_tmp):
    # Sem pedido de saída e com a thread viva... o loop seguiria; com thread
    # encerrada o main sai normalmente sem acionar o gancho de encerramento.
    infos = [PortaInfo(8000, False, False, None, None)]
    with patch("desktop.launcher.escolher_arranque", return_value=("iniciar", 8000, infos)), \
            patch("desktop.launcher.migrate_legacy_owner_password"), \
            patch("desktop.launcher.run_backend"), \
            patch("desktop.launcher.esperar_healthz", return_value=True), \
            patch("desktop.launcher.threading.Thread", _FakeThread), \
            patch("desktop.launcher._criar_mutex_instancia", return_value=1), \
            patch("desktop.launcher._criar_evento_saida", return_value=1), \
            patch("desktop.launcher._ENCERRAR_SERVIDOR") as mock_encerrar, \
            patch("webbrowser.open"):
        assert main([]) == 0
    mock_encerrar.assert_not_called()


# ---------------------------------------------------------------------------
# INST-5 (causa-raiz da matriz do instalador): o `--quit` precisa (1) saber
# que não há instância (mutex ausente → 1, sem criar evento órfão) e (2) o
# evento de saída precisa EXISTIR e ser segurado pela instância viva — sem o
# handle, o objeto nomeado morre junto com o processo `--quit` e o pedido se
# perde (foi assim que C4–C9 da matriz encadearam 25 falhas).
# ---------------------------------------------------------------------------

class _K32FalsoQuit:
    """Kernel32 de mentira: só o que o --quit toca."""

    def __init__(self, *, mutex_existe: bool):
        self.mutex_existe = mutex_existe
        self.eventos_criados = 0
        self.sinalizados = 0

    def OpenMutexW(self, acesso, herdar, nome):
        return 123 if self.mutex_existe else 0

    def CloseHandle(self, handle):
        return True

    def CreateEventW(self, atributos, manual, inicial, nome):
        self.eventos_criados += 1
        return 999

    def SetEvent(self, handle):
        self.sinalizados += 1
        return True


def test_quit_sem_instancia_viva_retorna_1_e_nao_cria_evento(monkeypatch):
    # Sem mutex (nada rodando), o contrato é 1 — e o evento nem é tocado:
    # criá-lo aqui produzia o objeto órfão do defeito original.
    k32 = _K32FalsoQuit(mutex_existe=False)
    monkeypatch.setattr(launcher, "_kernel32", lambda: k32)
    monkeypatch.setattr(launcher, "_WINDOWS", True)
    assert launcher._atender_pedido_de_saida() == 1
    assert k32.eventos_criados == 0
    assert k32.sinalizados == 0


def test_quit_com_instancia_viva_sinaliza_e_retorna_0(monkeypatch):
    k32 = _K32FalsoQuit(mutex_existe=True)
    monkeypatch.setattr(launcher, "_kernel32", lambda: k32)
    monkeypatch.setattr(launcher, "_WINDOWS", True)
    assert launcher._atender_pedido_de_saida() == 0
    assert k32.eventos_criados == 1
    assert k32.sinalizados == 1


def test_main_instancia_cria_e_segura_o_evento_de_saida(launcher_tmp):
    # A instância viva PRECISA criar o evento (e manter o handle até sair):
    # é o que faz o pedido do --quit persistir entre processos.
    infos = [PortaInfo(8000, False, False, None, None)]
    with patch("desktop.launcher.escolher_arranque", return_value=("iniciar", 8000, infos)), \
            patch("desktop.launcher.migrate_legacy_owner_password"), \
            patch("desktop.launcher.run_backend"), \
            patch("desktop.launcher.esperar_healthz", return_value=True), \
            patch("desktop.launcher.threading.Thread", _FakeThread), \
            patch("desktop.launcher._criar_mutex_instancia", return_value=4242), \
            patch("desktop.launcher._criar_evento_saida", return_value=777) as mock_evento, \
            patch("webbrowser.open"):
        assert main([]) == 0
    mock_evento.assert_called_once()


@pytest.mark.skipif(sys.platform != "win32", reason="objetos nomeados do Windows")
def test_quit_real_sem_instancia_retorna_1_e_nao_deixa_evento_orfao(monkeypatch):
    # Prova no kernel REAL: sem instância, --quit devolve 1 e nenhum evento
    # fica para trás. Nomes únicos deixam o teste hermético (não enxerga a
    # instalação real do dono).
    import uuid

    sufixo = uuid.uuid4().hex
    monkeypatch.setattr(launcher, "NOME_MUTEX_INSTANCIA", f"Local\\DZ23-Test-{sufixo}")
    monkeypatch.setattr(launcher, "NOME_EVENTO_SAIDA", f"Local\\DZ23-Test-{sufixo}")
    monkeypatch.setattr(launcher, "_WINDOWS", True)

    assert launcher._instancia_viva() is False
    assert launcher._atender_pedido_de_saida() == 1
    assert launcher._saida_solicitada() is False


@pytest.mark.skipif(sys.platform != "win32", reason="objetos nomeados do Windows")
def test_evento_de_saida_persiste_enquanto_a_instancia_segura_o_handle(monkeypatch):
    # Prova no kernel REAL da semântica que causou o defeito: o objeto
    # nomeado só existe enquanto ALGUÉM segura um handle. Com a instância
    # segurando (como o main faz), o --quit sinaliza e o pedido PERSISTE.
    import ctypes
    import uuid

    sufixo = uuid.uuid4().hex
    monkeypatch.setattr(launcher, "NOME_MUTEX_INSTANCIA", f"Local\\DZ23-TestMutex-{sufixo}")
    monkeypatch.setattr(launcher, "NOME_EVENTO_SAIDA", f"Local\\DZ23-TestEvento-{sufixo}")
    monkeypatch.setattr(launcher, "_WINDOWS", True)

    k32 = ctypes.windll.kernel32
    mutex = k32.CreateMutexW(None, False, launcher.NOME_MUTEX_INSTANCIA)
    evento = launcher._criar_evento_saida()  # a instância segura o handle
    assert mutex and evento
    try:
        assert launcher._instancia_viva() is True
        assert launcher._saida_solicitada() is False
        assert launcher._atender_pedido_de_saida() == 0
        assert launcher._saida_solicitada() is True  # o pedido PERSISTIU
    finally:
        k32.CloseHandle(evento)
        k32.CloseHandle(mutex)

"""DZ23 Trading AI — Windows Native Launcher & Local Server Supervisor.

Garantias deste launcher (endurecido no C021):

- Pré-checagem de portas ANTES de subir: identifica quem ocupa 8000–8010
  (PID e nome do processo) sem NUNCA encerrar outros aplicativos.
- Instância saudável do próprio DZ23 (marcador do /healthz) é REUTILIZADA:
  duplo clique abre o painel existente em vez de subir um segundo backend.
- Conflito legítimo (outro programa na porta): escolhe a próxima porta
  livre e propaga a PORTA EFETIVA ao healthcheck e ao navegador.
- O aplicativo só é considerado iniciado quando o /healthz responde;
  caso contrário o dono vê um aviso nativo apontando o log persistente.
- Logs persistentes em %APPDATA%/DZ23-Trading-AI/logs (rotativos, sem
  credenciais) e traceback completo de qualquer thread que morra — o exe
  --windowed não tem stderr, então o log é o diagnóstico visível.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

logger = logging.getLogger("dz23.desktop")

PORTA_PREFERIDA = 8000
PORTA_FINAL = 8010
MARCADOR_APP = "tradingagents-agency"  # campo "app" do /healthz do DZ23
ESPERA_HEALTHZ_SEGUNDOS = 60.0
CARENCIA_DZ23_SUBINDO_SEGUNDOS = 6.0
_CRIACOES_SEM_JANELA = 0x08000000  # CREATE_NO_WINDOW: windowed não pisca consoles
_WINDOWS = os.name == "nt"
_LOG_CONFIGURADO = False
_HANDLERS: list[logging.Handler] = []


def owner_data_dir() -> Path:
    return Path(os.environ.get("APPDATA", ".")) / "DZ23-Trading-AI"


def owner_password_path() -> Path:
    """Local do ANTIGO owner_password.txt — só a migração toca neste arquivo."""
    return owner_data_dir() / "owner_password.txt"


def owner_credentials_path() -> Path:
    return owner_data_dir() / "owner_credentials.json"


def log_path() -> Path:
    return owner_data_dir() / "logs" / "launcher.log"


def configurar_logs(caminho: Path | None = None) -> Path:
    """Log persistente (rotativo) + tracebacks de thread/exceção no arquivo.

    No exe --windowed o stderr é None: sem isto, a causa da morte do backend
    some. Chamado no início do main(); idempotente.
    """
    global _LOG_CONFIGURADO
    caminho = caminho or log_path()
    if _LOG_CONFIGURADO:
        return caminho
    caminho.parent.mkdir(parents=True, exist_ok=True)
    formato = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    raiz = logging.getLogger()
    raiz.setLevel(logging.INFO)
    arquivo = RotatingFileHandler(caminho, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    arquivo.setFormatter(formato)
    raiz.addHandler(arquivo)
    _HANDLERS.append(arquivo)
    if sys.stderr is not None:  # console só existe fora do modo windowed
        console = logging.StreamHandler()
        console.setFormatter(formato)
        raiz.addHandler(console)
        _HANDLERS.append(console)
    threading.excepthook = _hooko_thread
    sys.excepthook = _hooko_main
    _LOG_CONFIGURADO = True
    return caminho


def _hooko_thread(args: threading.ExceptHookArgs) -> None:
    nome = args.thread.name if args.thread else "?"
    logging.getLogger("dz23.crash").error(
        "thread %r morreu com %s", nome, args.exc_value,
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )


def _hooko_main(tipo, valor, traceback) -> None:  # type: ignore[no-untyped-def]
    logging.getLogger("dz23.crash").error(
        "exceção não tratada", exc_info=(tipo, valor, traceback)
    )


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


def run_backend(host: str = "127.0.0.1", port: int = PORTA_PREFERIDA) -> None:
    """Start local FastAPI service bound exclusively to loopback."""
    os.environ["HOST"] = host
    os.environ["PORT"] = str(port)
    os.environ["DZ23_PORTA_EFETIVA"] = str(port)
    os.environ["MULTI_TENANT_ENABLED"] = "false"

    # Ensure data directory exists (a MESMA pasta que a migração de senha usa)
    user_data = owner_data_dir()
    user_data.mkdir(parents=True, exist_ok=True)
    os.environ["DATA_DIR"] = str(user_data)

    logger.info("backend: DATA_DIR=%s porta=%d — importando service.app", user_data, port)
    from service.app import app, set_shutdown_hook

    logger.info("backend: app importado; preparando uvicorn")
    # uvicorn.Server (não uvicorn.run) para dar ao painel um gatilho de
    # encerramento: o endpoint /v1/app/quit liga should_exit e o servidor
    # desliga graciosamente — o leigo não fica preso ao Gerenciador de Tarefas.
    # log_config=None: os logs do uvicorn caem no log persistente do launcher.
    config = uvicorn.Config(app, host=host, port=port, log_level="info", log_config=None)
    server = uvicorn.Server(config)
    set_shutdown_hook(lambda: setattr(server, "should_exit", True))
    logger.info("backend: uvicorn.run em http://%s:%d", host, port)
    try:
        server.run()
    except BaseException:
        logger.exception("backend: servidor morreu na porta %d", port)
        raise


def _port_in_use(host: str, port: int, timeout: float = 0.5) -> bool:
    """True se qualquer programa já aceita conexões na porta."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _rodar_sem_console(cmd: list[str]) -> bytes:
    flags = _CRIACOES_SEM_JANELA if _WINDOWS else 0
    return subprocess.run(cmd, capture_output=True, creationflags=flags).stdout


def _pid_da_porta(porta: int) -> int | None:
    """PID do processo ESCUTANDO a porta, via `netstat -ano`.

    Casa `local:PORTA` + peer `0.0.0.0:0`/`[::]:0` — o estado (LISTENING)
    fica em `\\S+` para não depender do idioma do Windows.
    """
    if not _WINDOWS:
        return None
    saida = _rodar_sem_console(["netstat", "-ano"]).decode("utf-8", "replace")
    padroes = (
        re.compile(rf"^\s*TCP\s+\S+:{porta}\s+0\.0\.0\.0:0\s+\S+\s+(\d+)\s*$", re.MULTILINE),
        re.compile(rf"^\s*TCP\s+\[::\]:{porta}\s+\[::\]:0\s+\S+\s+(\d+)\s*$", re.MULTILINE),
    )
    for padrao in padroes:
        achado = padrao.search(saida)
        if achado:
            return int(achado.group(1))
    return None


def _processo_por_pid(pid: int) -> str | None:
    """Nome do processo pelo PID (`tasklist` CSV) — só leitura, nunca encerra."""
    if pid <= 0 or not _WINDOWS:
        return None
    saida = _rodar_sem_console(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"]
    ).decode("utf-8", "replace")
    for linha in saida.splitlines():
        linha = linha.strip()
        if linha.startswith('"'):
            return linha.split(",")[0].strip('"')
    return None


@dataclass(frozen=True)
class PortaInfo:
    porta: int
    ocupada: bool
    dz23: bool
    pid: int | None
    processo: str | None


def _healthz_payload(base_url: str, timeout: float = 1.0) -> dict | None:
    """Consulta o /healthz; None quando não responde — sonda de estado, não
    esconderijo: falha de verdade vira aviso nativo + log persistente."""
    try:
        with urllib.request.urlopen(f"{base_url}/healthz", timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _e_dz23(payload: dict | None) -> bool:
    return bool(payload) and str(payload.get("app", "")).startswith(MARCADOR_APP)


def descreve_porta(info: PortaInfo) -> str:
    if not info.ocupada:
        return "livre"
    if info.dz23:
        return "instância DZ23 saudável (reutilizável)"
    if info.processo and info.pid:
        return f"ocupada por {info.processo} (PID {info.pid})"
    return "ocupada (processo não identificado)"


def investigar_porta(host: str, porta: int) -> PortaInfo:
    if not _port_in_use(host, porta):
        return PortaInfo(porta=porta, ocupada=False, dz23=False, pid=None, processo=None)
    payload = _healthz_payload(f"http://{host}:{porta}")
    pid = _pid_da_porta(porta)
    return PortaInfo(
        porta=porta,
        ocupada=True,
        dz23=_e_dz23(payload),
        pid=pid,
        processo=_processo_por_pid(pid) if pid is not None else None,
    )


def escolher_arranque(
    host: str,
    primeira: int = PORTA_PREFERIDA,
    ultima: int = PORTA_FINAL,
    carencia: float = CARENCIA_DZ23_SUBINDO_SEGUNDOS,
) -> tuple[str, int | None, list[PortaInfo]]:
    """Decide entre reutilizar uma instância DZ23 ou iniciar numa porta livre.

    Devolve ("reutilizar" | "iniciar" | "sem_porta", porta, diagnóstico).
    NUNCA encerra nem altera outros programas — portas de terceiros são
    apenas registradas no log e evitadas.
    """
    infos = [investigar_porta(host, p) for p in range(primeira, ultima + 1)]
    for info in infos:
        if info.dz23:
            return "reutilizar", info.porta, infos
    # Porta que aceita conexão mas ainda não se identifica pode ser um DZ23
    # terminando de subir (duplo clique): carência curta antes de decidir.
    if any(i.ocupada and not i.dz23 for i in infos):
        fim = time.time() + carencia
        while time.time() < fim:
            for indice, info in enumerate(infos):
                if info.ocupada and not info.dz23:
                    payload = _healthz_payload(f"http://{host}:{info.porta}")
                    if _e_dz23(payload):
                        infos[indice] = PortaInfo(info.porta, True, True, info.pid, info.processo)
                        return "reutilizar", info.porta, infos
            time.sleep(1.0)
    for info in infos:
        if not info.ocupada:
            return "iniciar", info.porta, infos
    return "sem_porta", None, infos


def esperar_healthz(
    host: str,
    porta: int,
    timeout: float = ESPERA_HEALTHZ_SEGUNDOS,
    thread: threading.Thread | None = None,
) -> bool:
    """True só quando o /healthz do PRÓPRIO DZ23 responde 200 (marcador `app`).

    Se o backend morrer no meio, devolve False na hora — o aviso sai em
    segundos, não depois do timeout inteiro.
    """
    base = f"http://{host}:{porta}"
    inicio = time.time()
    while time.time() - inicio < timeout:
        if thread is not None and not thread.is_alive():
            logger.error("backend: thread morreu durante a inicialização (ver traceback acima)")
            return False
        if _e_dz23(_healthz_payload(base)):
            return True
        time.sleep(0.5)
    return False


def _mensagem_box(titulo: str, texto: str) -> None:
    if not _WINDOWS:
        return
    try:
        ctypes.windll.user32.MessageBoxW(None, texto, titulo, 0x30)
    except Exception:
        logger.exception("não foi possível exibir o aviso nativo (%s)", titulo)


def show_port_busy_dialog(infos: list[PortaInfo]) -> None:
    linhas = [
        f"• porta {i.porta}: {descreve_porta(i)}" for i in infos if i.ocupada
    ]
    texto = (
        f"O aplicativo não conseguiu iniciar porque as portas "
        f"{PORTA_PREFERIDA}–{PORTA_FINAL} já estão em uso:\n\n"
        + "\n".join(linhas)
        + "\n\nFeche um desses programas (ou reinicie o computador) e abra o "
        "DZ23 Trading AI novamente.\n\nO aplicativo não encerra nem altera "
        "outros programas."
    )
    logger.error("portas ocupadas na faixa %d–%d; nada foi encerrado", PORTA_PREFERIDA, PORTA_FINAL)
    _mensagem_box("DZ23 Trading AI — portas ocupadas", texto)


def show_startup_failed_dialog(porta: int, caminho_log: Path | None = None) -> None:
    texto = (
        f"O aplicativo não conseguiu iniciar o servidor local na porta {porta}.\n\n"
        f"O motivo técnico foi gravado em:\n{caminho_log or log_path()}\n\n"
        "O aplicativo será encerrado. Guarde esse arquivo e envie junto com o "
        "relato do problema."
    )
    _mensagem_box("DZ23 Trading AI — falha ao iniciar", texto)


def main() -> int:
    host = "127.0.0.1"
    caminho_log = configurar_logs()
    logger.info("DZ23 Trading AI — launcher iniciando (log: %s)", caminho_log)
    logger.info(
        "execução: frozen=%s; python=%s; exe=%s",
        bool(getattr(sys, "frozen", False)), sys.version.split()[0], sys.executable,
    )

    try:
        decisao, porta, infos = escolher_arranque(host)
    except Exception:
        logger.exception("falha na pré-checagem de portas")
        show_startup_failed_dialog(PORTA_PREFERIDA, caminho_log)
        return 1
    for info in infos:
        logger.info("porta %d: %s", info.porta, descreve_porta(info))

    if decisao == "reutilizar":
        logger.info("instância DZ23 saudável na porta %s — abrindo o painel existente", porta)
        webbrowser.open(f"http://{host}:{porta}/app")
        return 0
    if decisao == "sem_porta":
        show_port_busy_dialog(infos)
        return 1

    logger.info("iniciando na porta efetiva %s (primeira livre da faixa)", porta)

    # Dono Único: senha legada em texto puro vira hash Argon2id (a MESMA senha
    # continua valendo) e o texto puro sai do disco. Primeira execução sem
    # senha nenhuma: o assistente no navegador cria — o launcher não deve
    # bloquear em diálogo (automação e encerramento limpo dependem disso).
    migrate_legacy_owner_password()

    backend_thread = threading.Thread(
        target=run_backend, args=(host, porta), daemon=True, name="backend"
    )
    backend_thread.start()

    # Só considera iniciado quando o healthcheck responde de verdade.
    if not esperar_healthz(host, porta, thread=backend_thread):
        logger.error("healthz não respondeu na porta %s — painel NÃO foi aberto", porta)
        show_startup_failed_dialog(porta, caminho_log)
        return 1
    logger.info("backend saudável em http://%s:%s/app — abrindo o painel", host, porta)
    webbrowser.open(f"http://{host}:{porta}/app")

    try:
        while backend_thread.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("encerrando o DZ23 Trading AI Desktop...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Matriz do instalador/atualizador Windows — DZ23 Trading AI (missao INST).

Prova, em ambiente Windows ISOLADO (runner de CI virgin ou VM descartavel):

  C0   guarda de ambiente virgem (NUNCA roda na maquina do dono com instalacao)
  C1   instalacao limpa da versao ANTIGA (0.4.0) + dados ficticios + terceiros
  C2   tentar atualizar com o app ANTIGO aberto -> RECUSADO, nada muda
       (nem dados, nem registro, nem outros programas)
  C3   fechar o app antigo pelo painel + atualizar -> dados byte-identicos,
       backup VERIFICADO criado, registro na versao nova
  C4   app NOVO: /healthz com versao, painel, --version, --quit gracioso
  C5   reparo com o app NOVO aberto -> encerra SOMENTE o DZ23 e conclui
  C6   /SIMULAR=1 com app aberto -> zero mudancas, app continua vivo
  C7   downgrade forjado -> recusado; /FORCEDOWNGRADE=1 -> permitido com backup
  C7b  rollback real com restaurar_backup.ps1 (recusa sem -Forcar; restaura;
       backup corrompido e detectado)
  C8   desinstalar NUNCA apaga os dados (%APPDATA%) nem os backups
  C9   reinstalar mantem todos os dados
  C9b  dois instaladores ao mesmo tempo -> um conclui, nada corrompe

Uso (runner CI ou VM descartavel):
    python scripts/matriz_instalador_win.py \
        --setup-antigo velho/dist/DZ23-Trading-AI-Setup-v0.4.0.exe \
        --setup-novo dist/DZ23-Trading-AI-Setup-v<versao>.exe

Evidencias (status KEY=VALUE de cada setup, logs do Inno, manifestos SHA256,
dumps do registro) sao gravadas em evidencias/matriz-instalador-<stamp>/.
Saida: linhas PASS/FALHA + HARNESS_EXIT=0 quando tudo passa.

PROTECAO DO DONO: se existir QUALQUER vestigio de instalacao (registro
DZ23 Trading AI_is1, %LOCALAPPDATA%\\DZ23-Trading-AI,
%APPDATA%\\DZ23-Trading-AI ou backups), o harness ABORTA sem tocar em nada.
A matriz destrutiva so roda em ambiente que ela mesma criou do zero.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

APP_NOME = "DZ23 Trading AI"
CHAVE_UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\DZ23 Trading AI_is1"
EXE_NOME = "DZ23-Trading-AI.exe"
VERSAO_ANTIGA_ESPERADA = "0.4.0"

PORTA_INICIAL, PORTA_FINAL = 8000, 8010
PORTA_TERCEIRO = 8010  # ouvinte de terceiro DENTRO da faixa: prova que o DZ23 nao o toca
SENHA = "senha-da-matriz-instalador-2026"

LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", "."))
APPDATA = Path(os.environ.get("APPDATA", "."))
DIR_APP = LOCALAPPDATA / "DZ23-Trading-AI"
EXE_APP = DIR_APP / EXE_NOME
DIR_DADOS = APPDATA / "DZ23-Trading-AI"
DIR_BACKUPS = APPDATA / "DZ23-Trading-AI-backups"

FALHAS: list[str] = []
RESUMO: list[str] = []
TERCEIRO_SERVIDOR: socket.socket | None = None
EVID: Path = RAIZ / "evidencias" / "matriz-instalador"


# ---------------------------------------------------------------------------
# utilidades de saida / evidencia
# ---------------------------------------------------------------------------

def checa(nome: str, ok: bool, detalhe: str = "") -> None:
    sufixo = f" — {detalhe}" if detalhe else ""
    linha = f"{'PASS' if ok else 'FALHA'} — {nome}{sufixo}"
    print(linha, flush=True)
    RESUMO.append(linha)
    if not ok:
        FALHAS.append(nome)


def grava_json(nome: str, objeto) -> None:
    EVID.mkdir(parents=True, exist_ok=True)
    (EVID / nome).write_text(
        json.dumps(objeto, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _sha256(caminho: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# HTTP / portas
# ---------------------------------------------------------------------------

def req(url: str, metodo: str = "GET", corpo: dict | None = None,
        bearer: str | None = None, timeout: int = 20):
    dados = json.dumps(corpo).encode("utf-8") if corpo is not None else None
    pedido = urllib.request.Request(url, data=dados, method=metodo)
    if dados is not None:
        pedido.add_header("Content-Type", "application/json")
    if bearer:
        pedido.add_header("Authorization", f"Bearer {bearer}")
    try:
        with urllib.request.urlopen(pedido, timeout=timeout) as resp:
            corpo_bytes = resp.read()
            try:
                return resp.status, json.loads(corpo_bytes.decode("utf-8"))
            except Exception:
                return resp.status, {}
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def porta_aceita(porta: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", porta), timeout=0.5):
            return True
    except OSError:
        return False


def faixa_dz23_livre() -> bool:
    return not any(porta_aceita(p) for p in range(PORTA_INICIAL, PORTA_FINAL))


def base_dz23(segundos: float):
    """Procura o marcador do DZ23 (/healthz.app) na faixa. Retorna (base, payload)."""
    limite = time.time() + segundos
    while time.time() < limite:
        for porta in range(PORTA_INICIAL, PORTA_FINAL):
            try:
                status, dados = req(f"http://127.0.0.1:{porta}/healthz", timeout=2)
            except Exception:
                continue
            if status == 200 and str(dados.get("app", "")).startswith("tradingagents-agency"):
                return f"http://127.0.0.1:{porta}", dados
        time.sleep(1.0)
    return None, {}


def _recusa_conexoes(servidor: socket.socket) -> None:
    while True:
        try:
            conexao, _ = servidor.accept()
        except OSError:
            return
        with conexao:
            pass


def abre_terceiros() -> None:
    global TERCEIRO_SERVIDOR
    subprocess.Popen(["notepad.exe"])
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", PORTA_TERCEIRO))
    s.listen(16)
    threading.Thread(target=_recusa_conexoes, args=(s,), daemon=True).start()
    TERCEIRO_SERVIDOR = s


def notepad_vivo() -> bool:
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq notepad.exe"],
                       capture_output=True)
    return b"notepad.exe" in r.stdout.lower()


def fecha_terceiros() -> None:
    global TERCEIRO_SERVIDOR
    subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"], capture_output=True)
    if TERCEIRO_SERVIDOR is not None:
        TERCEIRO_SERVIDOR.close()
        TERCEIRO_SERVIDOR = None


# ---------------------------------------------------------------------------
# aplicativo (Popen local) e contrato do launcher
# ---------------------------------------------------------------------------

def inicia_app() -> subprocess.Popen:
    env = dict(os.environ)
    env["ACCESS_PASSWORD"] = SENHA  # modo explicito: sem assistente
    return subprocess.Popen([str(EXE_APP)], env=env)


def aguarda_saida(p: subprocess.Popen, timeout: float):
    try:
        return p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def fecha_pelo_painel(base: str) -> int:
    status, _ = req(f"{base}/v1/app/quit", "POST", {}, bearer=SENHA)
    return status


def cli_quit() -> int | None:
    """Cli do app. Timeout = binario antigo sem contrato --quit (inicia a app
    inteira e nunca sai): limpa o DZ23 e retorna None — a checagem falha com
    honestidade e a matriz NAO trava 120s por cenario."""
    try:
        r = subprocess.run([str(EXE_APP), "--quit"], capture_output=True, timeout=120)
        return r.returncode
    except subprocess.TimeoutExpired:
        encerra_dz23_limpeza()
        return None


def cli_version() -> int | None:
    try:
        r = subprocess.run([str(EXE_APP), "--version"], capture_output=True, timeout=120)
        return r.returncode
    except subprocess.TimeoutExpired:
        encerra_dz23_limpeza()
        return None


def info_versao_exe(exe: Path) -> str:
    cmd = [
        "powershell", "-NoProfile", "-Command",
        f"$i=(Get-Item -LiteralPath '{exe}').VersionInfo; "
        "Write-Output ($i.FileVersion + '|' + $i.ProductVersion + '|' + $i.CompanyName)",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        return (r.stdout or "").strip()
    except subprocess.TimeoutExpired:
        return "TIMEOUT"


def encerra_dz23_limpeza(p: subprocess.Popen | None = None) -> None:
    """Limpeza de HARNESS (nao e o caminho gracioso). So o DZ23; nunca terceiros."""
    if p is not None and p.poll() is None:
        subprocess.run(["taskkill", "/PID", str(p.pid), "/F"], capture_output=True)
    subprocess.run(["taskkill", "/IM", EXE_NOME, "/F"], capture_output=True)
    limite = time.time() + 30
    while time.time() < limite and not faixa_dz23_livre():
        time.sleep(0.5)


def precondicao_sem_instancia_viva(rotulo: str) -> bool:
    """Antes de abrir o app do cenario, exige a faixa livre.

    Com uma instancia anterior ainda viva, um segundo lancamento reutilizaria
    a porta e sairia 0 na hora — as checagens de --quit e encerramento
    passariam SEM testar nada (falso-verde). Limpa o straggler e falha o
    cenario com motivo explicito.
    """
    if faixa_dz23_livre():
        checa(f"{rotulo}: pre-condicao — nenhuma instancia previa (faixa livre)", True)
        return True
    checa(f"{rotulo}: pre-condicao — nenhuma instancia previa (faixa livre)", False,
          "instancia de cenario anterior ainda viva; resultado nao seria confiavel")
    encerra_dz23_limpeza()
    return False


# ---------------------------------------------------------------------------
# registro do Windows (mesma chave que a instalacao 0.4.0 do dono usa)
# ---------------------------------------------------------------------------

def _abre_chave(acesso: int):
    import winreg

    for raiz in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for visao in (0, winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                return winreg.OpenKey(raiz, CHAVE_UNINSTALL, 0, acesso | visao)
            except OSError:
                continue
    return None


def le_registro() -> dict | None:
    import winreg

    chave = _abre_chave(winreg.KEY_READ)
    if chave is None:
        return None
    with chave:
        valores: dict = {}
        i = 0
        while True:
            try:
                nome, valor, _ = winreg.EnumValue(chave, i)
            except OSError:
                break
            valores[nome] = valor
            i += 1
    return {
        "display_name": valores.get("DisplayName", ""),
        "display_version": valores.get("DisplayVersion", ""),
        "install_location": valores.get("InstallLocation", ""),
        "uninstall_string": valores.get("UninstallString", ""),
        "publisher": valores.get("Publisher", ""),
    }


def forja_versao_no_registro(valor: str) -> bool:
    import winreg

    chave = _abre_chave(winreg.KEY_SET_VALUE)
    if chave is None:
        return False
    with chave:
        winreg.SetValueEx(chave, "DisplayVersion", 0, winreg.REG_SZ, valor)
    return True


# ---------------------------------------------------------------------------
# instaladores (Inno Setup /VERYSILENT) e telemetria KEY=VALUE
# ---------------------------------------------------------------------------

def le_status(caminho: Path) -> dict:
    """Parseia o arquivo KEY=VALUE do instalador (Ultimo RESULTADO = desfecho)."""
    chaves: dict[str, str] = {}
    resultados: list[str] = []
    if caminho.exists():
        for bruta in caminho.read_text(encoding="utf-8", errors="replace").splitlines():
            linha = bruta.strip().strip('"')
            if "=" not in linha:
                continue
            k, v = linha.split("=", 1)
            k, v = k.strip(), v.strip()
            if k == "RESULTADO":
                resultados.append(v)
            else:
                chaves[k] = v
    return {
        "chaves": chaves,
        "resultados": resultados,
        "resultado_final": resultados[-1] if resultados else None,
    }


def roda_setup(exe: Path, rotulo: str, extra: list[str] | None = None) -> tuple:
    EVID.mkdir(parents=True, exist_ok=True)
    status_path = EVID / f"{rotulo}.status.txt"
    log_path = EVID / f"{rotulo}.setup.log"
    if status_path.exists():
        status_path.unlink()
    args = [
        str(exe), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
        f"/LOG={log_path}", f"/DZ23STATUS={status_path}",
    ]
    args += extra or []
    try:
        r = subprocess.run(args, capture_output=True, timeout=1800)
        codigo = r.returncode
    except subprocess.TimeoutExpired:
        # Setup pendurado nao pode derrubar a matriz inteira: encerra o
        # instalador e segue — a checagem de exit reporta o timeout.
        subprocess.run(["taskkill", "/IM", exe.name, "/F"], capture_output=True)
        codigo = None
    st = le_status(status_path)
    grava_json(f"{rotulo}.telemetria.json", {"exit": codigo, **st})
    return codigo, st


def roda_uninstall() -> int:
    unins = DIR_APP / "unins000.exe"
    r = subprocess.run(
        [str(unins), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
         f"/LOG={EVID / 'c8-uninstall.log'}"],
        capture_output=True, timeout=900,
    )
    return r.returncode


def roda_ps(script: Path, argumentos: list[str], rotulo: str) -> dict:
    EVID.mkdir(parents=True, exist_ok=True)
    saida = EVID / f"{rotulo}.ps.txt"
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
           "-File", str(script)] + argumentos + ["-SaidaArquivo", str(saida)]
    with contextlib.suppress(subprocess.TimeoutExpired):
        subprocess.run(cmd, capture_output=True, timeout=900)
    return le_status(saida)  # timeout: a saida parcial decide pelo conteudo


# ---------------------------------------------------------------------------
# dados ficticios + manifestos SHA256
# ---------------------------------------------------------------------------

def semente_dados_ficticios() -> None:
    """Dados REPRESENTATIVOS e FICTICIOS (nunca reais): o upgrade tem de
    preserva-los byte a byte."""
    DIR_DADOS.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DIR_DADOS / "carteira-ficticia.db")
    try:
        con.execute("CREATE TABLE IF NOT EXISTS ordens_paper "
                    "(id INTEGER PRIMARY KEY, ativo TEXT, lado TEXT, qtd REAL, "
                    "preco REAL, status TEXT)")
        con.executemany(
            "INSERT INTO ordens_paper (ativo, lado, qtd, preco, status) VALUES (?,?,?,?,?)",
            [("FICT-BTCUSD", "compra", 0.01, 60000.0, "ABERTA"),
             ("FICT-ETHUSD", "venda", 0.2, 3000.0, "FECHADA")],
        )
        con.commit()
    finally:
        con.close()
    (DIR_DADOS / "config-ficticia.json").write_text(
        json.dumps({"modo": "PAPER", "ficticio": True, "carteira": "FICT-CARTEIRA-01"}),
        encoding="utf-8",
    )
    (DIR_DADOS / "historico-ficticio.csv").write_text(
        "data,ativo,lado,qtd,preco\n2026-01-02,FICT-BTCUSD,compra,0.01,59000\n",
        encoding="utf-8",
    )


def manifesto(raiz: Path, completo: bool = True) -> dict[str, str]:
    """SHA256 por arquivo. completo=False ignora logs/ e jobs.db* — usado
    SOMENTE com o aplicativo rodando (o agendador pode gravar jobs.db e o
    launcher escreve logs; nada disso e alteracao do instalador)."""
    resultado: dict[str, str] = {}
    if not raiz.exists():
        return resultado
    for p in sorted(raiz.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(raiz).as_posix()
        if not completo:
            if rel.startswith("logs/") or rel.endswith(".log"):
                continue
            if Path(rel).name.startswith("jobs.db"):
                continue
        resultado[rel] = _sha256(p)
    return resultado


def compara_manifestos(a: dict, b: dict) -> list[str]:
    return [rel for rel in sorted(set(a) | set(b)) if a.get(rel) != b.get(rel)]


def lista_backups() -> list[str]:
    if not DIR_BACKUPS.exists():
        return []
    return sorted(p.name for p in DIR_BACKUPS.iterdir()
                  if p.is_dir() and p.name.startswith("pre-upgrade-"))


def copia_logs_backup(rotulo: str) -> None:
    """Copia para a evidencia os logs que o instalador grava AO LADO dos
    backups — inclusive os de FALHA (a mensagem do instalador os promete; a
    evidencia precisa sobreviver ao runner efemero)."""
    if not DIR_BACKUPS.exists():
        return
    EVID.mkdir(parents=True, exist_ok=True)
    for log in sorted(DIR_BACKUPS.glob("instalador-backup-*.log*")):
        with contextlib.suppress(OSError):
            shutil.copy2(log, EVID / f"{rotulo}-{log.name}")


def backup_mais_recente() -> Path | None:
    nomes = lista_backups()
    return (DIR_BACKUPS / nomes[-1]) if nomes else None


# ---------------------------------------------------------------------------
# guard

def guarda_ambiente_virgem() -> bool:
    """PROTECAO DO DONO: recusa rodar em ambiente com instalacao existente."""
    reg = le_registro()
    ok = True
    if reg is not None:
        print(f"FALHA — guarda: registro ja existe ({reg}) — ambiente NAO virgem; "
              "nada sera instalado/desinstalado.", flush=True)
        ok = False
    for d in (DIR_APP, DIR_DADOS, DIR_BACKUPS):
        if d.exists():
            print(f"FALHA — guarda: {d} ja existe — ambiente NAO virgem; "
                  "nada sera instalado/desinstalado.", flush=True)
            ok = False
    if not faixa_dz23_livre():
        print("FALHA — guarda: portas 8000-8009 ocupadas — nada sera executado.",
              flush=True)
        ok = False
    return ok


# ---------------------------------------------------------------------------
# cenarios
# ---------------------------------------------------------------------------

# estado compartilhado entre cenarios (feito em C1/C3)
HASH_EXE_ANTIGO: str | None = None
PROC_ANTIGO: subprocess.Popen | None = None


def c1_instalacao_limpa_antiga(setup_antigo: Path, versao_nova: str) -> None:
    global HASH_EXE_ANTIGO, PROC_ANTIGO

    codigo, st = roda_setup(setup_antigo, "c1-instalacao-0.4.0")
    checa("C1: instalador 0.4.0 conclui (exit 0)", codigo == 0, f"exit={codigo}")
    checa("C1: exe instalado existe", EXE_APP.exists())
    checa("C1: desinstalador presente (unins000.exe)", (DIR_APP / "unins000.exe").exists())

    reg = le_registro()
    esperado_nome = f"{APP_NOME} version {VERSAO_ANTIGA_ESPERADA}"
    checa("C1: registro usa a identidade historica (mesma chave do dono)",
          reg is not None
          and reg.get("display_name") == esperado_nome
          and reg.get("display_version") == VERSAO_ANTIGA_ESPERADA,
          str(reg))

    HASH_EXE_ANTIGO = _sha256(EXE_APP)

    abre_terceiros()
    checa("C1: terceiro (notepad) aberto", notepad_vivo())
    checa(f"C1: ouvinte de terceiro na porta {PORTA_TERCEIRO}", porta_aceita(PORTA_TERCEIRO))

    PROC_ANTIGO = inicia_app()
    base, payload = base_dz23(120)
    checa("C1: app 0.4.0 inicia e responde no /healthz", base is not None, f"base={base}")
    if base:
        status, _ = req(f"{base}/app", timeout=15)
        checa("C1: painel /app responde 200 no app antigo", status == 200)
        status, _ = req(f"{base}/v1/auth/status", timeout=15)
        checa("C1: /v1/auth/status responde no app antigo", status == 200)
    checa("C1: healthz antigo NAO tem o campo versao (contrato novo ainda nao existe)",
          "versao" not in payload)

    semente_dados_ficticios()
    m = manifesto(DIR_DADOS)
    grava_json("manifesto-pos-instalacao-0.4.0.json", m)
    checa("C1: dados ficticios semeados (sqlite + json + csv)",
          (DIR_DADOS / "carteira-ficticia.db").exists()
          and (DIR_DADOS / "config-ficticia.json").exists()
          and (DIR_DADOS / "historico-ficticio.csv").exists())


def c2_recusa_com_app_antigo_aberto(setup_novo: Path) -> None:
    assert PROC_ANTIGO is not None
    base, _ = base_dz23(10)
    checa("C2: app 0.4.0 segue saudavel antes do teste", base is not None)

    snap = manifesto(DIR_DADOS, completo=False)
    backups_antes = lista_backups()

    codigo, st = roda_setup(setup_novo, "c2-upgrade-com-app-aberto")
    chaves = st["chaves"]
    checa("C2: atualizacao com app antigo aberto e RECUSADA (exit nao-zero)",
          codigo != 0, f"exit={codigo}")
    checa("C2: instalador detectou o app rodando",
          chaves.get("APP_RODANDO") == "1" and chaves.get("APP_MUTEX") == "0",
          str(chaves))
    checa("C2: resultado ABORTADO_APP_ABERTO_ANTIGO",
          st["resultado_final"] == "ABORTADO_APP_ABERTO_ANTIGO",
          str(st["resultado_final"]))
    checa("C2: cenario era ATUALIZACAO",
          chaves.get("CENARIO") == "ATUALIZACAO", str(chaves.get("CENARIO")))

    checa("C2: app 0.4.0 continua rodando (nada o encerrou)",
          base_dz23(15)[0] is not None)
    checa("C2: exe antigo intacto (nao foi substituido)",
          _sha256(EXE_APP) == HASH_EXE_ANTIGO)
    checa("C2: notepad (terceiro) intacto", notepad_vivo())
    checa("C2: ouvinte de terceiro na 8010 intacto", porta_aceita(PORTA_TERCEIRO))
    checa("C2: dados intactos", compara_manifestos(snap, manifesto(DIR_DADOS, completo=False)) == [])
    checa("C2: registro ainda na versao antiga",
          (le_registro() or {}).get("display_version") == VERSAO_ANTIGA_ESPERADA)
    checa("C2: nenhum backup criado (nada foi alterado)", lista_backups() == backups_antes)


def c3_upgrade_preserva_dados(setup_novo: Path, versao_nova: str) -> str | None:
    global PROC_ANTIGO
    assert PROC_ANTIGO is not None
    base, _ = base_dz23(10)
    if base is None:
        checa("C3: app 0.4.0 disponivel para fechamento gracioso", False)
        return None

    status = fecha_pelo_painel(base)
    checa("C3: app antigo aceita fechamento pelo painel (/v1/app/quit 200)", status == 200,
          f"HTTP {status}")
    codigo_saida = aguarda_saida(PROC_ANTIGO, 90)
    checa("C3: app 0.4.0 encerrou sozinho (exit 0, sem taskkill)", codigo_saida == 0,
          f"exit={codigo_saida}")
    checa("C3: porta liberada apos o fechamento", faixa_dz23_livre())
    PROC_ANTIGO = None

    snap = manifesto(DIR_DADOS)
    backups_antes = lista_backups()

    codigo, st = roda_setup(setup_novo, "c3-upgrade")
    chaves = st["chaves"]
    copia_logs_backup("c3-upgrade")
    checa("C3: atualizacao conclui (exit 0)", codigo == 0, f"exit={codigo}")
    checa("C3: cenario ATUALIZACAO", chaves.get("CENARIO") == "ATUALIZACAO",
          str(chaves.get("CENARIO")))
    checa("C3: versao instalada detectada = 0.4.0",
          chaves.get("VERSAO_INSTALADA") == VERSAO_ANTIGA_ESPERADA,
          str(chaves.get("VERSAO_INSTALADA")))
    checa("C3: resultado final OK", st["resultado_final"] == "OK",
          str(st["resultado_final"]))
    checa("C3: backup verificado criado (BACKUP=OK)", chaves.get("BACKUP") == "OK",
          str(chaves.get("BACKUP")))

    novos = [n for n in lista_backups() if n not in backups_antes]
    esperado_ini = f"pre-upgrade-{VERSAO_ANTIGA_ESPERADA}-para-{versao_nova}-"
    checa("C3: pasta de backup nomeada com as duas versoes",
          len(novos) == 1 and novos[0].startswith(esperado_ini), str(novos))
    if novos:
        pasta = DIR_BACKUPS / novos[0]
        manifest_bkp = pasta / "MANIFEST.sha256"
        bkp_json = pasta / "backup.json"
        ok_json = False
        if bkp_json.exists():
            try:
                ok_json = bool(json.loads(bkp_json.read_text(encoding="utf-8")).get("ok"))
            except Exception:
                ok_json = False
        checa("C3: backup tem MANIFEST.sha256 e backup.json(ok=true)",
              manifest_bkp.exists() and ok_json)
        checa("C3: backup cobre os dados ficticios (manifest com >0 linhas)",
              manifest_bkp.exists()
              and any(l2.strip() for l2 in manifest_bkp.read_text(encoding="utf-8").splitlines()))

    dif = compara_manifestos(snap, manifesto(DIR_DADOS))
    checa("C3: DADOS preservados byte a byte apos a atualizacao (fora logs)",
          dif == [], f"divergentes={dif[:5]}")

    reg = le_registro() or {}
    checa("C3: registro agora na versao nova",
          reg.get("display_version") == versao_nova, str(reg))
    novo_hash = _sha256(EXE_APP)
    checa("C3: exe substituido pela versao nova", novo_hash != HASH_EXE_ANTIGO)
    meta = info_versao_exe(EXE_APP)
    checa("C3: metadados do exe = versao nova", versao_nova in meta, meta)
    checa("C3: notepad (terceiro) intacto apos a atualizacao", notepad_vivo())
    checa("C3: ouvinte de terceiro na 8010 intacto", porta_aceita(PORTA_TERCEIRO))
    return novo_hash


def c4_app_novo_funcional(versao_nova: str) -> None:
    # Pre-condicao: sem a versao nova instalada (C3 abortou), o exe antigo
    # nem entende --quit — sem esta guarda a matriz inteira travava 120s
    # por cenario e morria em cascata.
    reg = le_registro() or {}
    if reg.get("display_version") != versao_nova:
        checa("C4: pre-condicao — versao nova instalada (C3 concluiu)", False,
              f"registro={reg.get('display_version')!r}")
        return
    if not precondicao_sem_instancia_viva("C4"):
        return
    checa("C4: --quit SEM instancia rodando retorna 1 (nada para fechar)",
          cli_quit() == 1)
    p = inicia_app()
    base, payload = base_dz23(120)
    checa("C4: app novo inicia", base is not None, f"base={base}")
    checa("C4: /healthz reporta a versao nova", payload.get("versao") == versao_nova,
          f"versao={payload.get('versao')}")
    checa("C4: /healthz traz armazenamento e banco",
          "armazenamento" in payload and "banco" in payload, str(sorted(payload)))
    if base:
        status, _ = req(f"{base}/app", timeout=15)
        checa("C4: painel /app responde 200", status == 200)
        status, _ = req(f"{base}/v1/auth/status", timeout=15)
        checa("C4: /v1/auth/status responde", status == 200)
    checa("C4: --version no exe empacotado retorna 0", cli_version() == 0)
    checa("C4: --quit sinaliza a instancia e retorna 0", cli_quit() == 0)
    codigo = aguarda_saida(p, 90)
    checa("C4: app encerrou graciosamente pelo --quit (exit 0)", codigo == 0,
          f"exit={codigo}")
    checa("C4: porta liberada apos o --quit", faixa_dz23_livre())


def c5_reparo_com_app_novo_aberto(setup_novo: Path, versao_nova: str) -> None:
    if not precondicao_sem_instancia_viva("C5"):
        return
    p = inicia_app()
    base, _ = base_dz23(120)
    checa("C5: app novo aberto antes do reparo", base is not None)
    snap = manifesto(DIR_DADOS, completo=False)
    backups_antes = lista_backups()

    codigo, st = roda_setup(setup_novo, "c5-reparo")
    chaves = st["chaves"]
    checa("C5: reparo conclui (exit 0)", codigo == 0, f"exit={codigo}")
    checa("C5: cenario REPARO", chaves.get("CENARIO") == "REPARO",
          str(chaves.get("CENARIO")))
    checa("C5: app estava rodando e foi detectado",
          chaves.get("APP_RODANDO") == "1", str(chaves))
    checa("C5: app novo fechado de forma GRACIOSA (somente o DZ23)",
          chaves.get("APP_FECHADO") == "GRACIOSO", str(chaves.get("APP_FECHADO")))
    checa("C5: resultado final OK", st["resultado_final"] == "OK")
    checa("C5: nao sobra instancia do app (porta liberada)", faixa_dz23_livre())
    checa("C5: sem backup novo (mesma versao nao gera backup)",
          lista_backups() == backups_antes)
    checa("C5: dados intactos", compara_manifestos(snap, manifesto(DIR_DADOS, completo=False)) == [])
    checa("C5: notepad (terceiro) intacto", notepad_vivo())
    checa("C5: registro continua na versao nova",
          (le_registro() or {}).get("display_version") == versao_nova)
    codigo_antigo = aguarda_saida(p, 5)
    checa("C5: processo original do app terminou (o instalador fechou so o DZ23)",
          codigo_antigo is not None, f"wait={codigo_antigo}")


def c6_simular_sem_mudancas(setup_novo: Path, versao_nova: str) -> None:
    if not precondicao_sem_instancia_viva("C6"):
        return
    p = inicia_app()
    base, _ = base_dz23(120)
    checa("C6: app aberto antes da simulacao", base is not None)
    snap = manifesto(DIR_DADOS, completo=False)
    backups_antes = lista_backups()
    exe_antes = _sha256(EXE_APP)
    reg_antes = le_registro()

    codigo, st = roda_setup(setup_novo, "c6-simular", extra=["/SIMULAR=1"])
    checa("C6: /SIMULAR=1 aborta de proposito (exit nao-zero)", codigo != 0,
          f"exit={codigo}")
    checa("C6: resultado SIMULADO", st["resultado_final"] == "SIMULADO",
          str(st["resultado_final"]))
    checa("C6: a simulacao viu o app rodando",
          st["chaves"].get("APP_RODANDO") == "1", str(st["chaves"]))
    checa("C6: app continua VIVO apos a simulacao (nada o fechou)",
          base_dz23(15)[0] is not None)
    checa("C6: exe intacto", _sha256(EXE_APP) == exe_antes)
    checa("C6: registro intacto", le_registro() == reg_antes)
    checa("C6: dados intactos", compara_manifestos(snap, manifesto(DIR_DADOS, completo=False)) == [])
    checa("C6: nenhum backup criado", lista_backups() == backups_antes)
    checa("C6: versao ainda a nova", versao_nova == (le_registro() or {}).get("display_version"))

    checa("C6: --quit encerra o app apos a simulacao", cli_quit() == 0)
    codigo_saida = aguarda_saida(p, 90)
    checa("C6: app saiu graciosamente (exit 0)", codigo_saida == 0, f"exit={codigo_saida}")


def c7_downgrade(setup_novo: Path, versao_nova: str) -> None:
    checa("C7: forjar versao 9.9.9 no registro (simula downgrade real)",
          forja_versao_no_registro("9.9.9"))
    snap = manifesto(DIR_DADOS)
    backups_antes = lista_backups()
    exe_antes = _sha256(EXE_APP)

    codigo, st = roda_setup(setup_novo, "c7-downgrade-recusado")
    checa("C7: downgrade RECUSADO sem confirmacao (exit nao-zero)", codigo != 0,
          f"exit={codigo}")
    checa("C7: resultado ABORTADO_DOWNGRADE",
          st["resultado_final"] == "ABORTADO_DOWNGRADE", str(st["resultado_final"]))
    checa("C7: cenario DOWNGRADE", st["chaves"].get("CENARIO") == "DOWNGRADE",
          str(st["chaves"].get("CENARIO")))
    checa("C7: exe intacto apos a recusa", _sha256(EXE_APP) == exe_antes)
    checa("C7: dados intactos apos a recusa",
          compara_manifestos(snap, manifesto(DIR_DADOS)) == [])
    checa("C7: registro ainda forjado (nada foi alterado)",
          (le_registro() or {}).get("display_version") == "9.9.9")
    checa("C7: nenhum backup criado na recusa", lista_backups() == backups_antes)

    codigo, st = roda_setup(setup_novo, "c7-downgrade-forcado",
                            extra=["/FORCEDOWNGRADE=1"])
    checa("C7: /FORCEDOWNGRADE=1 permite o downgrade (exit 0)", codigo == 0,
          f"exit={codigo}")
    checa("C7: resultado OK no downgrade forcado", st["resultado_final"] == "OK")
    checa("C7: backup criado ANTES do downgrade forcado",
          st["chaves"].get("BACKUP") == "OK", str(st["chaves"].get("BACKUP")))
    novos = [n for n in lista_backups() if n not in backups_antes]
    checa("C7: pasta de backup registra 9.9.9->" + versao_nova,
          len(novos) == 1 and novos[0].startswith(f"pre-upgrade-9.9.9-para-{versao_nova}-"),
          str(novos))
    checa("C7: dados intactos apos o downgrade forcado",
          compara_manifestos(snap, manifesto(DIR_DADOS)) == [])
    checa("C7: registro volta a versao do instalador",
          (le_registro() or {}).get("display_version") == versao_nova)
    checa("C7: metadados do exe = versao nova", versao_nova in info_versao_exe(EXE_APP))


def c7b_rollback() -> None:
    bkp = backup_mais_recente()
    checa("C7b: existe backup para testar o rollback", bkp is not None, str(bkp))
    if bkp is None:
        return
    script = RAIZ / "desktop" / "installer" / "restaurar_backup.ps1"
    scratch = EVID / "rollback-scratch"
    if scratch.exists():
        shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "arquivo-que-ja-existe.txt").write_text(
        "os dados serao restaurados por cima; nada aqui e apagado", encoding="utf-8")

    r = roda_ps(script, ["-Backup", str(bkp), "-Destino", str(scratch)], "c7b-recusa")
    checa("C7b: restauracao de destino existente e recusada sem -Forcar",
          r["chaves"].get("MOTIVO") == "DESTINO_EXISTENTE_SEM_FORCAR", str(r["chaves"]))

    r = roda_ps(script, ["-Backup", str(bkp), "-Destino", str(scratch), "-Forcar"],
                "c7b-restaura")
    checa("C7b: rollback restaura com -Forcar (RESULTADO=OK)",
          r["resultado_final"] == "OK", str(r))
    m_scratch = manifesto(scratch)
    m_backup = manifesto(bkp / "DZ23-Trading-AI")
    checa("C7b: conteudo restaurado byte-identico ao backup",
          compara_manifestos(m_backup, {k: v for k, v in m_scratch.items()
                                        if not k.startswith("arquivo-que-ja-existe")}) == [])

    # backup corrompido tem de ser detectado
    copia = EVID / "rollback-corrompido"
    if copia.exists():
        shutil.rmtree(copia, ignore_errors=True)
    shutil.copytree(bkp, copia)
    alvo = next((p for p in (copia / "DZ23-Trading-AI").rglob("*") if p.is_file()), None)
    if alvo is not None:
        alvo.write_text("corrompido de proposito", encoding="utf-8")
    destino2 = EVID / "rollback-destino2"
    if destino2.exists():
        shutil.rmtree(destino2, ignore_errors=True)
    r = roda_ps(script, ["-Backup", str(copia), "-Destino", str(destino2)], "c7b-corrompido")
    motivo = r["chaves"].get("MOTIVO", "")
    checa("C7b: backup corrompido e recusado (BACKUP_CORROMPIDO/FALTANDO)",
          r["resultado_final"] == "FALHOU"
          and (motivo.startswith("BACKUP_CORROMPIDO") or motivo.startswith("BACKUP_FALTANDO")),
          motivo)


def c8_desinstalar_preserva_dados(versao_nova: str) -> None:
    if not precondicao_sem_instancia_viva("C8"):
        return
    snap = manifesto(DIR_DADOS)
    backups_antes = lista_backups()

    codigo = roda_uninstall()
    checa("C8: desinstalador conclui (exit 0)", codigo == 0, f"exit={codigo}")
    limite = time.time() + 90
    while time.time() < limite and (EXE_APP.exists() or le_registro() is not None):
        time.sleep(1.0)
    checa("C8: programa removido do disco", not EXE_APP.exists(), str(EXE_APP))
    checa("C8: desinstalador removido", not (DIR_APP / "unins000.exe").exists())
    checa("C8: registro removido", le_registro() is None)
    checa("C8: DADOS FINANCEIROS preservados (%APPDATA% intacto)",
          DIR_DADOS.exists() and compara_manifestos(snap, manifesto(DIR_DADOS)) == [])
    checa("C8: backups preservados", lista_backups() == backups_antes)


def c9_reinstalar_mantem_dados(setup_novo: Path, versao_nova: str) -> None:
    if not precondicao_sem_instancia_viva("C9"):
        return
    snap = manifesto(DIR_DADOS)
    backups_antes = lista_backups()

    codigo, st = roda_setup(setup_novo, "c9-reinstalar")
    checa("C9: reinstalacao conclui (exit 0)", codigo == 0, f"exit={codigo}")
    checa("C9: cenario INSTALACAO_NOVA (registro estava vazio)",
          st["chaves"].get("CENARIO") == "INSTALACAO_NOVA",
          str(st["chaves"].get("CENARIO")))
    checa("C9: resultado OK", st["resultado_final"] == "OK")
    checa("C9: dados preservados apos reinstalar",
          compara_manifestos(snap, manifesto(DIR_DADOS)) == [])
    checa("C9: backups preservados", lista_backups() == backups_antes)
    checa("C9: registro de volta na versao nova",
          (le_registro() or {}).get("display_version") == versao_nova)

    p = inicia_app()
    base, payload = base_dz23(120)
    checa("C9: app volta a funcionar apos reinstalar", base is not None)
    checa("C9: healthz segue reportando a versao nova",
          payload.get("versao") == versao_nova)
    checa("C9: --quit encerra a instancia reinstalada", cli_quit() == 0)
    checa("C9: saida graciosa apos reinstalar", aguarda_saida(p, 90) == 0)


def c9b_instaladores_simultaneos(setup_novo: Path, versao_nova: str) -> None:
    st1 = EVID / "c9b-a.status.txt"
    st2 = EVID / "c9b-b.status.txt"
    for s in (st1, st2):
        if s.exists():
            s.unlink()
    snap = manifesto(DIR_DADOS)
    base_args = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]
    p1 = subprocess.Popen([str(setup_novo), *base_args,
                           f"/LOG={EVID / 'c9b-a.setup.log'}", f"/DZ23STATUS={st1}"])
    p2 = subprocess.Popen([str(setup_novo), *base_args,
                           f"/LOG={EVID / 'c9b-b.setup.log'}", f"/DZ23STATUS={st2}"])
    c1 = p1.wait(timeout=1500)
    c2 = p2.wait(timeout=1500)
    st_a = le_status(st1)
    st_b = le_status(st2)
    grava_json("c9b-simultaneos.json",
               {"exit_a": c1, "exit_b": c2, "a": st_a, "b": st_b})
    checa("C9b: exatamente um instalador conclui (mutex bloqueia o outro)",
          (c1 == 0) != (c2 == 0), f"exit_a={c1} exit_b={c2}")
    concluiu = st_a["resultado_final"] if c1 == 0 else st_b["resultado_final"]
    checa("C9b: quem concluiu terminou OK", concluiu == "OK", str(concluiu))
    checa("C9b: registro consistente na versao nova",
          (le_registro() or {}).get("display_version") == versao_nova)
    checa("C9b: dados intactos",
          compara_manifestos(snap, manifesto(DIR_DADOS)) == [])
    checa("C9b: instancia segue removida (sem app rodando)",
          faixa_dz23_livre())

    p = inicia_app()
    base, _ = base_dz23(120)
    checa("C9b: app volta a subir apos os instaladores simultaneos", base is not None)
    checa("C9b: --quit encerra", cli_quit() == 0)
    aguarda_saida(p, 90)


def cleanup_final() -> None:
    encerra_dz23_limpeza()
    fecha_terceiros()
    if le_registro() is not None and (DIR_APP / "unins000.exe").exists():
        roda_uninstall()
    limite = time.time() + 90
    while time.time() < limite and le_registro() is not None:
        time.sleep(1.0)
    checa("LIMPEZA: ambiente devolvido ao estado virgem (registro removido)",
          le_registro() is None)


# ---------------------------------------------------------------------------
# descoberta de binarios e main
# ---------------------------------------------------------------------------

def versao_nova_esperada() -> str:
    m = re.search(r'^version\s*=\s*"([^"]+)"',
                  (RAIZ / "pyproject.toml").read_text(encoding="utf-8"),
                  re.MULTILINE)
    if not m:
        raise SystemExit("pyproject.toml sem campo version")
    return m.group(1)


def acha_setup(explicito: str | None, env_nome: str, padroes: list[Path]) -> Path | None:
    if explicito:
        p = Path(explicito)
        return p if p.exists() else None
    do_env = os.environ.get(env_nome)
    if do_env:
        p = Path(do_env)
        return p if p.exists() else None
    for p in padroes:
        if p.exists():
            return p
    return None


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Matriz do instalador Windows DZ23")
    ap.add_argument("--setup-antigo", dest="setup_antigo", default=None)
    ap.add_argument("--setup-novo", dest="setup_novo", default=None)
    ap.add_argument("--evidencias", dest="evidencias", default=None)
    return ap.parse_args()


def main() -> int:
    global EVID
    args = parse_args()
    if args.evidencias:
        EVID = Path(args.evidencias)
    else:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        EVID = RAIZ / "evidencias" / f"matriz-instalador-{stamp}"
    EVID.mkdir(parents=True, exist_ok=True)

    if os.name != "nt":
        print("FALHA — a matriz do instalador exige Windows.", flush=True)
        return 2

    versao_nova = versao_nova_esperada()
    setup_antigo = acha_setup(
        args.setup_antigo, "DZ23_SETUP_ANTIGO",
        [RAIZ / "velho" / "dist" / f"DZ23-Trading-AI-Setup-v{VERSAO_ANTIGA_ESPERADA}.exe"]
        + sorted((RAIZ / "velho" / "dist").glob("DZ23-Trading-AI-Setup-v*.exe"))
        if (RAIZ / "velho" / "dist").exists() else
        [RAIZ / "velho" / "dist" / f"DZ23-Trading-AI-Setup-v{VERSAO_ANTIGA_ESPERADA}.exe"],
    )
    setup_novo = acha_setup(
        args.setup_novo, "DZ23_SETUP_NOVO",
        [RAIZ / "dist" / f"DZ23-Trading-AI-Setup-v{versao_nova}.exe"]
        + sorted((RAIZ / "dist").glob("DZ23-Trading-AI-Setup-v*.exe"))
        if (RAIZ / "dist").exists() else
        [RAIZ / "dist" / f"DZ23-Trading-AI-Setup-v{versao_nova}.exe"],
    )

    print(f"matriz instalador DZ23 — versao antiga {VERSAO_ANTIGA_ESPERADA}, "
          f"versao nova {versao_nova}", flush=True)
    print(f"antigo: {setup_antigo}", flush=True)
    print(f"novo:   {setup_novo}", flush=True)
    print(f"evidencias: {EVID}", flush=True)

    if setup_antigo is None or not setup_antigo.exists():
        print("FALHA — instalador ANTIGO (0.4.0) nao encontrado.", flush=True)
        return 2
    if setup_novo is None or not setup_novo.exists():
        print("FALHA — instalador NOVO nao encontrado.", flush=True)
        return 2

    if not guarda_ambiente_virgem():
        print("HARNESS_ABORTADO=AMBIENTE_NAO_VIRGEM", flush=True)
        return 2

    try:
        # C1/C2/C3 com o app antigo
        c1_instalacao_limpa_antiga(setup_antigo, versao_nova)
        c2_recusa_com_app_antigo_aberto(setup_novo)
        c3_upgrade_preserva_dados(setup_novo, versao_nova)
        c4_app_novo_funcional(versao_nova)
        c5_reparo_com_app_novo_aberto(setup_novo, versao_nova)
        c6_simular_sem_mudancas(setup_novo, versao_nova)
        c7_downgrade(setup_novo, versao_nova)
        c7b_rollback()
        c8_desinstalar_preserva_dados(versao_nova)
        c9_reinstalar_mantem_dados(setup_novo, versao_nova)
        c9b_instaladores_simultaneos(setup_novo, versao_nova)
    except Exception as exc:  # noqa: BLE001 — evidencia > elegância aqui
        import traceback

        traceback.print_exc()
        checa(f"MATRIZ: excecao inesperada ({exc.__class__.__name__})", False, str(exc))
    finally:
        try:
            cleanup_final()
        except Exception as exc:  # noqa: BLE001
            checa("LIMPEZA: falhou", False, str(exc))
        (EVID / "resumo.txt").write_text("\n".join(RESUMO) + "\n", encoding="utf-8")

    print(f"TOTAL: {len(FALHAS)} falha(s)", flush=True)
    if FALHAS:
        for f in FALHAS:
            print(f"  - {f}")
        return 1
    print("HARNESS_EXIT=0")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

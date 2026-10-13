"""Matriz de inicialização (item 10) no BUNDLE INSTALADO — DZ23 Trading AI.

Cenários: 1 inicialização limpa, 2 porta ocupada com fallback, 3 múltiplas
instâncias, 4 faixa inteira ocupada (diálogo visível), 5 reinício,
6 encerramento gracioso (POST /v1/app/quit → launcher sai sozinho), sem
taskkill.

Uso (na estação, com o app instalado e nenhuma instância rodando):
    uv run python scripts/matriz_inicializacao_win.py

Cenários 1–5 usam o DATA_DIR real (preservado; nada é apagado). O cenário 6
usa um APPDATA descartável para criar a senha do dono via assistente e provar
o encerramento gracioso sem tocar no segredo do dono real.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

EXE = Path(os.environ["LOCALAPPDATA"]) / "DZ23-Trading-AI" / "DZ23-Trading-AI.exe"
JANELAS = Path(__file__).resolve().parent / "diagnostico_janelas_win.py"
PORTA_INICIAL, PORTA_FINAL = 8000, 8010
SENHA_MATRIZ = "senha-da-matriz-2026-10-13"
FALHAS: list[str] = []


def checa(nome: str, ok: bool, detalhe: str = "") -> None:
    sufixo = f" — {detalhe}" if detalhe else ""
    print(f"{'PASS' if ok else 'FALHA'} — {nome}{sufixo}", flush=True)
    if not ok:
        FALHAS.append(nome)


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


def faixa_livre() -> bool:
    return not any(porta_aceita(p) for p in range(PORTA_INICIAL, PORTA_FINAL + 1))


def base_dz23(segundos: float):
    """Procura o marcador do DZ23 (campo app do /healthz) na faixa inteira."""
    limite = time.time() + segundos
    while time.time() < limite:
        for porta in range(PORTA_INICIAL, PORTA_FINAL + 1):
            try:
                status, dados = req(f"http://127.0.0.1:{porta}/healthz", timeout=2)
            except Exception:
                continue
            if status == 200 and str(dados.get("app", "")).startswith("tradingagents-agency"):
                return f"http://127.0.0.1:{porta}"
        time.sleep(1.0)
    return None


def encerra_limpeza(pid: int | None = None) -> None:
    """Limpeza de HARNESS (não é o caminho gracioso): mata só o DZ23 e espera
    a faixa esvaziar. Nunca toca em outros programas."""
    if pid:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    subprocess.run(["taskkill", "/IM", "DZ23-Trading-AI.exe", "/F"], capture_output=True)
    limite = time.time() + 20
    while time.time() < limite and not faixa_livre():
        time.sleep(0.5)


def inicia(env_extra: dict | None = None) -> subprocess.Popen:
    env = dict(os.environ)
    env.pop("ACCESS_PASSWORD", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.Popen([str(EXE)], env=env)


def ocupar(portas: list[int]) -> list[socket.socket]:
    """Abre ouvintes TCP reais (processo estranho simulado) nas portas dadas."""
    servidores = []
    for porta in portas:
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", porta))
        s.listen(4)
        servidores.append(s)
    return servidores


def titulos_janelas(pid: int) -> str:
    r = subprocess.run(
        [sys.executable, str(JANELAS), str(pid)],
        capture_output=True, text=True, errors="replace",
    )
    return (r.stdout or "") + (r.stderr or "")


def cenario_1_inicializacao() -> None:
    encerra_limpeza()
    checa("C1: estação limpa antes do teste (sem DZ23, faixa livre)", faixa_livre())
    p = inicia()
    t0 = time.time()
    base = base_dz23(90)
    checa(
        "C1: inicialização limpa — healthz do DZ23 responde na 1ª porta da faixa",
        base == "http://127.0.0.1:8000",
        f"base={base} em {round(time.time() - t0, 1)}s",
    )
    if base:
        status, _ = req(f"{base}/app", timeout=10)
        checa("C1: painel /app responde 200", status == 200)
        status, _ = req(f"{base}/manifest.webmanifest", timeout=10)
        checa("C1: manifesto PWA responde 200", status == 200)
    encerra_limpeza(p.pid)
    checa("C1: limpeza deixou a faixa livre", faixa_livre())


def cenario_2_porta_ocupada_fallback() -> None:
    encerra_limpeza()
    servidores = ocupar([8000])
    try:
        checa("C2: programa estranho ouvindo a 8000 antes do teste", porta_aceita(8000))
        p = inicia()
        base = base_dz23(90)
        checa(
            "C2: DZ23 sobe em porta alternativa (8001) com a 8000 ocupada",
            base == "http://127.0.0.1:8001",
            f"base={base}",
        )
        checa("C2: programa estranho segue vivo na 8000 (nada foi encerrado)", porta_aceita(8000))
        encerra_limpeza(p.pid)
    finally:
        for s in servidores:
            s.close()
    checa("C2: limpeza deixou a faixa livre", faixa_livre())


def cenario_3_multiplas_instancias() -> None:
    encerra_limpeza()
    p1 = inicia()
    base1 = base_dz23(90)
    checa("C3: primeira instância saudável", base1 is not None, f"base={base1}")
    p2 = inicia()
    try:
        codigo = p2.wait(timeout=60)
        checa(
            "C3: segunda instância sai sozinha (duplo clique não abre backend duplo)",
            codigo == 0,
            f"exit={codigo}",
        )
    except subprocess.TimeoutExpired:
        checa(
            "C3: segunda instância sai sozinha (duplo clique não abre backend duplo)",
            False,
            "seguia viva após 60s",
        )
        subprocess.run(["taskkill", "/PID", str(p2.pid), "/F"], capture_output=True)
    checa("C3: primeira instância continua saudável", base_dz23(10) is not None)
    encerra_limpeza(p1.pid)
    checa("C3: limpeza deixou a faixa livre", faixa_livre())


def cenario_4_faixa_ocupada_dialogo() -> None:
    encerra_limpeza()
    servidores = ocupar(list(range(PORTA_INICIAL, PORTA_FINAL + 1)))
    try:
        p = inicia()
        base = base_dz23(5)
        checa(
            "C4: DZ23 não finge iniciar quando a faixa inteira está ocupada",
            base is None,
            f"base={base}",
        )
        prazo = time.time() + 25
        titulos = ""
        while time.time() < prazo:
            titulos = titulos_janelas(p.pid)
            if "portas ocupadas" in titulos:
                break
            time.sleep(1.0)
        checa(
            'C4: diálogo nativo "portas ocupadas" visível ao dono',
            "DZ23 Trading AI — portas ocupadas" in titulos,
            titulos.strip().splitlines()[0] if titulos.strip() else "sem janelas",
        )
        checa(
            "C4: diálogo explica o que fazer e promete não encerrar outros programas",
            "em uso" in titulos and "outros programas" in titulos,
        )
        subprocess.run(["taskkill", "/PID", str(p.pid), "/F"], capture_output=True)
    finally:
        for s in servidores:
            s.close()
    time.sleep(1.0)
    checa("C4: limpeza deixou a faixa livre", faixa_livre())


def cenario_5_reinicio() -> None:
    encerra_limpeza()
    p1 = inicia()
    base1 = base_dz23(90)
    checa("C5: sobe (1ª execução)", base1 is not None, f"base={base1}")
    encerra_limpeza(p1.pid)
    p2 = inicia()
    base2 = base_dz23(90)
    checa("C5: reabre normalmente depois de fechado (reinício)", base2 is not None, f"base={base2}")
    encerra_limpeza(p2.pid)
    checa("C5: limpeza deixou a faixa livre", faixa_livre())


def cenario_6_encerramento_gracioso() -> None:
    """Prova o caminho do botão "Fechar aplicativo": quit pela API → o
    launcher desliga o uvicorn, sai sozinho com exit 0 e libera a porta —
    usando um DATA_DIR descartável (assistente cria a senha do dono nova)."""
    encerra_limpeza()
    temp = Path(tempfile.mkdtemp(prefix="dz23-matriz-"))
    try:
        p = inicia({"APPDATA": str(temp)})
        base = base_dz23(90)
        checa("C6: instância descartável sobe", base is not None, f"base={base}")
        if not base:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/F"], capture_output=True)
            return
        status, estado = req(f"{base}/v1/auth/status")
        checa(
            "C6: primeira execução pede o assistente (setup_required=true)",
            estado.get("setup_required") is True,
            f"HTTP {status}",
        )
        status, resposta = req(f"{base}/v1/auth/setup", "POST", {"senha": SENHA_MATRIZ})
        checa("C6: assistente cria a senha do dono", status == 200, f"HTTP {status} {resposta}")
        status, resposta = req(f"{base}/v1/app/quit", "POST", {}, bearer=SENHA_MATRIZ)
        checa("C6: POST /v1/app/quit com a senha do dono responde 200", status == 200, f"HTTP {status}")
        try:
            codigo = p.wait(timeout=40)
            checa(
                "C6: launcher encerra sozinho (exit 0) — nada de taskkill",
                codigo == 0,
                f"exit={codigo}",
            )
        except subprocess.TimeoutExpired:
            checa("C6: launcher encerra sozinho (exit 0) — nada de taskkill", False, "seguia vivo após 40s")
            subprocess.run(["taskkill", "/PID", str(p.pid), "/F"], capture_output=True)
        porta = int(base.rsplit(":", 1)[1])
        checa("C6: porta efetiva liberada após o encerramento", not porta_aceita(porta))
        logs = list((temp / "DZ23-Trading-AI" / "logs").glob("launcher.log"))
        checa("C6: log persistente do launcher gravado", bool(logs), str(logs[0]) if logs else "sem log")
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def main() -> int:
    if not EXE.exists():
        print(f"FALHA — executável instalado não encontrado: {EXE}")
        return 2
    if not JANELAS.exists():
        print(f"FALHA — auxiliar de janelas não encontrado: {JANELAS}")
        return 2
    print(f"EXE: {EXE}", flush=True)
    cenario_1_inicializacao()
    cenario_2_porta_ocupada_fallback()
    cenario_3_multiplas_instancias()
    cenario_4_faixa_ocupada_dialogo()
    cenario_5_reinicio()
    cenario_6_encerramento_gracioso()
    print(f"TOTAL: {len(FALHAS)} falha(s)", flush=True)
    if FALHAS:
        for f in FALHAS:
            print(f"  - {f}")
        return 1
    print("HARNESS_EXIT=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())

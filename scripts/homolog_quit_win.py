"""Prova funcional do encerramento gracioso (`--quit`) no Windows, com o
launcher de FONTE e APPDATA isolado (dados do dono intocados).

Cobre o defeito real do INST-5: o processo `--quit` criava um evento de saída
transitório que morria com ele, e o loop da instância (poll de 1s) nunca via o
pedido — o app ficava aberto e o instalador abortava com ABORTADO_APP_NAO_FECHOU.
Depois do fix, a INSTÂNCIA cria e segura o evento; o `--quit` apenas o
sinaliza e o pedido persiste até o loop lê-lo.

Passos (tudo com timeout e evidência):
  1. faixa 8000–8010 livre (não colide com instalação real do dono)
  2. sobe o launcher de fonte com APPDATA temporário
  3. espera healthz responder (backend REAL de pé)
  4. roda `--quit` -> espera exit 0
  5. instância encerra sozinha com exit 0 (gracioso)
  6. segundo `--quit` -> exit 1 (sem instância viva)
  7. porta liberada ao final
"""

from __future__ import annotations

import contextlib
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
LOG_EVID = RAIZ / "docs" / "mission" / "evidence" / "e2e" / "inst-quit-fix-local.log"
FAIXA = range(8000, 8011)

# Só o navegador é suprimido no subprocesso da instância (o dono não é
# incomodado com abas locais); TODO o resto — portas, mutex, evento, backend,
# healthz, --quit, shutdown — é o caminho real de produção.
_BOOTSTRAP = (
    "import runpy, sys, webbrowser; "
    "webbrowser.open = lambda *a, **k: True; "
    "sys.argv = ['desktop/launcher.py']; "
    "runpy.run_path('desktop/launcher.py', run_name='__main__')"
)

linhas: list[str] = []
falhas: list[str] = []


def checa(nome: str, ok: bool, detalhe: str = "") -> None:
    marca = "PASS" if ok else "FAIL"
    linhas.append(f"[{marca}] {nome}" + (f" — {detalhe}" if detalhe else ""))
    print(linhas[-1])
    if not ok:
        falhas.append(nome)


def porta_ocupada(porta: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", porta)) == 0


def espera_log(caminho: Path, agulha: str, timeout: float) -> str:
    limite = time.time() + timeout
    while time.time() < limite:
        if caminho.exists():
            texto = caminho.read_text(encoding="utf-8", errors="replace")
            if agulha in texto:
                return texto
        time.sleep(1.0)
    return caminho.read_text(encoding="utf-8", errors="replace") if caminho.exists() else ""


def espera_healthz(porta: int, timeout: float) -> dict | None:
    limite = time.time() + timeout
    url = f"http://127.0.0.1:{porta}/healthz"
    while time.time() < limite:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return {"status": r.status, "corpo": r.read(400).decode("utf-8", "replace")}
        except Exception:
            pass
        time.sleep(1.5)
    return None


def main() -> int:
    linhas.append("== DZ23 Trading AI — homologação do --quit (INST-5, launcher de fonte) ==")
    linhas.append(f"executável: {sys.executable}")
    linhas.append(f"raiz: {RAIZ}")

    ocupadas = [p for p in FAIXA if porta_ocupada(p)]
    checa("faixa 8000–8010 livre antes de começar", not ocupadas, f"ocupadas={ocupadas}")
    if falhas:
        return 1

    appdata = Path(tempfile.mkdtemp(prefix="dz23-quit-homolog-"))
    log = appdata / "DZ23-Trading-AI" / "logs" / "launcher.log"
    linhas.append(f"APPDATA temporário (dados do dono intocados): {appdata}")

    import os
    env = os.environ.copy()
    env["APPDATA"] = str(appdata)
    env["ACCESS_PASSWORD"] = "SENHA"
    env["PYTHONIOENCODING"] = "utf-8"

    saida_inst = appdata / "instancia.out.txt"
    with open(saida_inst, "wb") as saida:
        inst = subprocess.Popen(
            [sys.executable, "-c", _BOOTSTRAP],
            cwd=str(RAIZ), env=env, stdout=saida, stderr=subprocess.STDOUT,
        )
    linhas.append(f"instância iniciada (pid={inst.pid}); aguardando healthz…")

    try:
        espera_log(log, "iniciando na porta efetiva", 120)
        porta = None
        limite = time.time() + 60
        while time.time() < limite:
            porta = next((p for p in FAIXA if porta_ocupada(p)), None)
            if porta is not None:
                break
            time.sleep(1.0)
        checa("instância escolheu porta da faixa e está escutando", porta is not None, f"porta={porta}")
        if porta is None:
            return 1

        saude = espera_healthz(porta, 180)
        checa("healthz real respondeu 200", saude is not None,
              f"corpo={saude['corpo'][:120]}" if saude else "sem resposta")
        if saude is None:
            return 1

        t0 = time.time()
        quit1 = subprocess.run(
            [sys.executable, "desktop/launcher.py", "--quit"],
            cwd=str(RAIZ), env=env, capture_output=True, timeout=90,
        )
        checa("--quit COM instância viva retorna 0", quit1.returncode == 0,
              f"exit={quit1.returncode}; out={quit1.stdout.decode('utf-8', 'replace').strip()[:80]}")

        try:
            codigo_inst = inst.wait(timeout=60)
        except subprocess.TimeoutExpired:
            codigo_inst = None
        decorrido = time.time() - t0
        checa("instância encerrou sozinha e graciosamente (exit 0)",
              codigo_inst == 0, f"exit={codigo_inst}; em {decorrido:.1f}s")
        checa("encerramento rápido (pedido persistiu e o poll o viu)",
              codigo_inst == 0 and decorrido < 30.0, f"{decorrido:.1f}s")

        texto_log = log.read_text(encoding="utf-8", errors="replace")
        checa("log registra o pedido de encerramento gracioso",
              "pedido de encerramento gracioso enviado à instância viva" in texto_log)
        checa("log confirma backend encerrado graciosamente",
              "backend encerrado graciosamente" in texto_log)

        quit2 = subprocess.run(
            [sys.executable, "desktop/launcher.py", "--quit"],
            cwd=str(RAIZ), env=env, capture_output=True, timeout=90,
        )
        checa("segundo --quit (sem instância) retorna 1", quit2.returncode == 1,
              f"exit={quit2.returncode}")

        limite = time.time() + 15
        while time.time() < limite and any(porta_ocupada(p) for p in FAIXA):
            time.sleep(1.0)
        checa("porta liberada após o encerramento",
              not any(porta_ocupada(p) for p in FAIXA))
    finally:
        if inst.poll() is None:
            inst.kill()
            checa("instância ainda viva ao final (kill de limpeza)", False)

    return 1 if falhas else 0


if __name__ == "__main__":
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    codigo = main()
    linhas.append(f"RESULTADO={'OK' if codigo == 0 else 'FALHOU'}")
    LOG_EVID.parent.mkdir(parents=True, exist_ok=True)
    LOG_EVID.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    print(f"\nEvidência: {LOG_EVID}")
    raise SystemExit(codigo)

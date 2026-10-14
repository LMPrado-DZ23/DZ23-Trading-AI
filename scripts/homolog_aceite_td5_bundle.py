r"""Aceite E2E da matriz TD-5 no BUNDLE do artefato CI — ambiente ISOLADO.

Missão V8 §4 ("Não basta testar a API: executar E2E no bundle Windows
instalado") e §13 ("Utilizar ambientes isolados"): este harness extrai o
`DZ23-Trading-AI-windows-x64.zip` do artefato, sobe o exe com um APPDATA
DESCARTÁVEL (nunca toca em %APPDATA%\DZ23-Trading-AI do dono), cria a senha
do dono pelo assistente de primeira execução (mesmo caminho do proprietário)
e roda a MESMA matriz `tests/e2e/test_aceite_td5.py` com navegador real.

Segurança (não negociável):
- Se QUALQUER instância DZ23 já respondia na faixa 8000–8010 ANTES do boot,
  o harness ABORTA: o launcher reutilizaria a instância alheia e o "aceite"
  não provaria o bundle novo. Este guarda também protege a instalação
  pessoal do proprietário (porta 8000) de qualquer interferência.
- Encerramento pelo caminho de produção (`POST /v1/app/quit` → exit 0).
- Limpeza de emergência mata APENAS o PID que este harness criou.

Uso (no runner de CI ou em estação SEM instância DZ23 rodando):

    uv run --with playwright python scripts/homolog_aceite_td5_bundle.py \
        --bundle-dir bundle --evidencias docs/mission/evidence/v8-td5-exe
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# Console/pipe no Windows usa cp1252 e não codifica acentos de forma
# confiável — um UnicodeEncodeError mataria o harness no meio do log.
for _fluxo in (sys.stdout, sys.stderr):
    if _fluxo is not None and hasattr(_fluxo, "reconfigure"):
        _fluxo.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
MARCADOR_APP = "tradingagents-agency"  # campo "app" do /healthz do DZ23
PORTA_INICIAL, PORTA_FINAL = 8000, 8010
SENHA_PADRAO = "s3nh4-dz23-e2e"
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


def dz23_em(porta: int) -> bool:
    try:
        status, dados = req(f"http://127.0.0.1:{porta}/healthz", timeout=2)
    except Exception:
        return False
    return status == 200 and str(dados.get("app", "")).startswith(MARCADOR_APP)


def instancias_existentes() -> list[int]:
    return [p for p in range(PORTA_INICIAL, PORTA_FINAL + 1) if dz23_em(p)]


def porta_aceita(porta: int) -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", porta), timeout=0.5):
            return True
    except OSError:
        return False


def achar_exe(bundle_dir: Path) -> Path | None:
    candidatos = sorted(bundle_dir.rglob("DZ23-Trading-AI.exe"))
    return candidatos[0] if candidatos else None


def espera_healthz(proc: subprocess.Popen, segundos: float) -> str | None:
    """Base URL do DZ23 recém-nascido; None se não subir (ou morrer cedo)."""
    limite = time.time() + segundos
    while time.time() < limite:
        if proc.poll() is not None:
            print(f"processo do bundle saiu cedo (exit {proc.returncode})", flush=True)
            return None
        for porta in range(PORTA_INICIAL, PORTA_FINAL + 1):
            if dz23_em(porta):
                return f"http://127.0.0.1:{porta}"
        time.sleep(1.0)
    return None


def roda_pytest(base: str, senha: str, evidencias: Path) -> None:
    """Roda a matriz de aceite contra o bundle e cobra o placar EXATO.

    Skip silencioso é falha (§16: não ocultar falhas usando skip): o placar
    de coletados vem do próprio pytest e o de aprovados tem de bater.
    """
    env = dict(os.environ)
    env.update(
        {
            "DZ23_E2E_BASE_URL": base,
            "DZ23_E2E_OWNER_PASSWORD": senha,
            "DZ23_E2E_EVIDENCE_DIR": str(evidencias),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        }
    )
    alvo = "tests/e2e/test_aceite_td5.py"

    # Ferramentas do próprio ambiente do harness: falha aqui precisa dized
    # o QUE falta (um pytest ausente coletava 0 e parecia "nenhum teste").
    for ferramenta in ("pytest", "playwright.sync_api"):
        pronto = subprocess.run(
            [sys.executable, "-c", f"import {ferramenta}"],
            capture_output=True,
        )
        checa(f"ambiente do aceite tem {ferramenta}",
              pronto.returncode == 0,
              pronto.stderr.decode("utf-8", "replace").strip()[-200:])

    coleta = subprocess.run(
        [sys.executable, "-m", "pytest", alvo, "--collect-only", "-q",
         "-p", "no:cacheprovider"],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600,
    )
    coletados = len(re.findall(r"::", coleta.stdout))
    checa("matriz coletada com os testes de aceite (navegador disponível)",
          coletados > 0, f"coletados={coletados}")
    if coletados == 0:
        print(f"--- stdout (rc={coleta.returncode}) ---\n{coleta.stdout[-3000:]}",
              flush=True)
        print(f"--- stderr ---\n{coleta.stderr[-3000:]}", flush=True)
        return

    inicio = time.time()
    prova = subprocess.run(
        [sys.executable, "-m", "pytest", alvo, "-v", "-p", "no:cacheprovider"],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=1800,
    )
    duracao = round(time.time() - inicio, 1)
    print(prova.stdout, flush=True)
    if prova.stderr:
        print(prova.stderr[-3000:], flush=True)
    (evidencias / "pytest-aceite-bundle.txt").write_text(
        prova.stdout + "\n--- stderr ---\n" + prova.stderr, encoding="utf-8")

    placar = re.search(r"(\d+) passed", prova.stdout)
    aprovados = int(placar.group(1)) if placar else -1
    ocultos = bool(re.search(r"\d+ (failed|error|skipped)", prova.stdout))
    checa(
        f"matriz de aceite no bundle: {aprovados}/{coletados} aprovados em {duracao}s",
        aprovados == coletados and not ocultos and prova.returncode == 0,
        "" if aprovados == coletados else "placar divergente (falha/skip oculto?)",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", default="bundle",
                        help="pasta onde o zip do artefato foi extraído")
    parser.add_argument("--evidencias", default="docs/mission/evidence/v8-td5-exe",
                        help="pasta de evidências (screenshots + placar)")
    parser.add_argument("--senha", default=os.environ.get("DZ23_E2E_OWNER_PASSWORD")
                        or SENHA_PADRAO)
    args = parser.parse_args()

    bundle_dir = (REPO_ROOT / args.bundle_dir).resolve()
    evidencias = (REPO_ROOT / args.evidencias).resolve()
    evidencias.mkdir(parents=True, exist_ok=True)

    exe = achar_exe(bundle_dir)
    checa("exe do bundle do artefato encontrado", exe is not None,
          str(exe) if exe else f"nenhum DZ23-Trading-AI.exe sob {bundle_dir}")
    if exe is None:
        return 1
    print(f"EXE: {exe}", flush=True)

    alheias = instancias_existentes()
    checa("ambiente isolado (nenhuma instância DZ23 antes do boot)", not alheias,
          f"instância DZ23 já respondia em {alheias} — abortado por segurança"
          if alheias else "")
    if alheias:
        return 1

    temp = Path(tempfile.mkdtemp(prefix="dz23-aceite-bundle-"))
    proc: subprocess.Popen | None = None
    try:
        env = dict(os.environ)
        env.pop("ACCESS_PASSWORD", None)
        env["APPDATA"] = str(temp)  # dados descartáveis; nunca o APPDATA do dono
        proc = subprocess.Popen(
            [str(exe)], cwd=str(exe.parent), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        t0 = time.time()
        base = espera_healthz(proc, 120)
        checa("bundle sobe em ambiente isolado e responde /healthz",
              base is not None, f"base={base} em {round(time.time() - t0, 1)}s")
        if not base:
            return 1
        print(f"BASE: {base} (APPDATA isolado: {temp})", flush=True)

        status, estado = req(f"{base}/v1/auth/status")
        checa("primeira execução pede o assistente (setup_required=true)",
              status == 200 and estado.get("setup_required") is True,
              f"HTTP {status}")
        status, resposta = req(f"{base}/v1/auth/setup", "POST", {"senha": args.senha})
        checa("assistente cria a senha do dono (mesmo caminho do proprietário)",
              status == 200, f"HTTP {status}")
        if status != 200:
            return 1

        roda_pytest(base, args.senha, evidencias)

        status, _ = req(f"{base}/v1/app/quit", "POST", {}, bearer=args.senha)
        checa("POST /v1/app/quit responde 200 (caminho de encerramento real)",
              status == 200, f"HTTP {status}")
        try:
            codigo = proc.wait(timeout=40)
            checa("launcher encerra sozinho (exit 0)", codigo == 0, f"exit={codigo}")
        except subprocess.TimeoutExpired:
            checa("launcher encerra sozinho (exit 0)", False, "seguia vivo após 40s")
        porta_efetiva = int(base.rsplit(":", 1)[1])
        checa("porta efetiva liberada após o encerramento",
              not porta_aceita(porta_efetiva))
    finally:
        if proc is not None and proc.poll() is None:
            # Emergência: mata SÓ o PID desta instância isolada.
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/F"],
                           capture_output=True, check=False)
        # Log do launcher (se houve): parte da evidência forense do aceite.
        log = temp / "DZ23-Trading-AI" / "logs" / "launcher.log"
        if log.exists():
            shutil.copy2(log, evidencias / "launcher.log")
        shutil.rmtree(temp, ignore_errors=True)

    print(f"TOTAL: {len(FALHAS)} falha(s)", flush=True)
    if FALHAS:
        for f in FALHAS:
            print(f"  - {f}")
        return 1
    print("HARNESS_EXIT=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())

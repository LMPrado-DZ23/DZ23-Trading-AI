"""Homologa a RECUPERAÇÃO DE CRASH (item 22) no BUNDLE INSTALADO.

Simula um crash do sistema operacional matando à força o PRÓPRIO processo
que este harness subiu (`taskkill /F` — nunca toca em programa de terceiros)
depois de um ciclo PAPER real, e prova que o estado durável volta do disco
byte a byte na reabertura: ordens do OMS (id/estado/qtd/preço), contador e
`ultimo_ciclo` do agendador, posições abertas, saldos/patrimônio/disponível
e `pendencias_reconciliacao=[]` — e que a senha do dono (Argon2id no DATA_DIR
descartável) continua valendo após o crash.

Usa um APPDATA descartável (não toca no DATA_DIR real do dono). Se qualquer
DZ23 estiver rodando, o harness aborta SEM matar nada — pré-condição: a faixa
8000–8010 livre. Uso (na estação, com o app instalado):

    uv run python scripts/homolog_crash_bundle.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path

# Console/pipe no Windows usa cp1252 e não codifica acentos de forma confiável.
for _fluxo in (sys.stdout, sys.stderr):
    if _fluxo is not None and hasattr(_fluxo, "reconfigure"):
        _fluxo.reconfigure(encoding="utf-8", errors="replace")

EXE = Path(os.environ["LOCALAPPDATA"]) / "DZ23-Trading-AI" / "DZ23-Trading-AI.exe"
PORTA_INICIAL, PORTA_FINAL = 8000, 8010
SENHA = "senha-do-item22-2026-10-14"
FALHAS: list[str] = []


def checa(nome: str, ok: bool, detalhe: str = "") -> None:
    sufixo = f" — {detalhe}" if detalhe else ""
    print(f"{'PASS' if ok else 'FALHA'} — {nome}{sufixo}", flush=True)
    if not ok:
        FALHAS.append(nome)


def espera_ok(predicado, segundos: float) -> bool:
    limite = time.time() + segundos
    while time.time() < limite:
        try:
            if predicado():
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


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
    import socket
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


def inicia(appdata: str) -> subprocess.Popen:
    env = dict(os.environ)
    env.pop("ACCESS_PASSWORD", None)
    env["APPDATA"] = appdata
    return subprocess.Popen([str(EXE)], env=env)


def captura(base: str) -> dict | None:
    """Estado durável que o crash não pode perder."""
    status, st = req(f"{base}/v1/desk/status", bearer=SENHA)
    if status != 200:
        return None
    ordens = sorted(
        (o.get("id"), o.get("estado"), o.get("qtd"), o.get("preco"))
        for o in st.get("orders", []))
    contab = st.get("contabilidade", {})
    agend = st.get("agendador", {})
    return {
        "ordens": ordens,
        "saldos": contab.get("saldos", {}),
        "patrimonio": contab.get("patrimonio"),
        "balanceado": contab.get("balanceado"),
        "disponivel": (st.get("risk") or {}).get("disponivel"),
        "abertos": (st.get("risk") or {}).get("instrumentos_abertos"),
        "ciclos": agend.get("ciclos"),
        "ultimo_ciclo": agend.get("ultimo_ciclo"),
        "posicoes": agend.get("posicoes"),
        "pend_agend": agend.get("pendencias"),
        "pend_rec": st.get("pendencias_reconciliacao"),
        "durabilidade": st.get("durabilidade"),
        "modo": (st.get("modo") or {}).get("estado"),
    }


def decimal_ok(valor) -> Decimal | None:
    try:
        return Decimal(str(valor))
    except (InvalidOperation, TypeError):
        return None


def main() -> int:
    if not EXE.exists():
        print(f"FALHA — executável instalado não encontrado: {EXE}")
        return 2
    print(f"EXE: {EXE}", flush=True)

    checa(
        "estação limpa antes do teste (nenhuma instância DZ23 na faixa 8000–8010)",
        faixa_livre(),
        "se houver DZ23 aberto, feche-o — este harness NÃO mata o que não subiu",
    )
    if not faixa_livre():
        return 2

    temp = Path(tempfile.mkdtemp(prefix="dz23-item22-"))
    p: subprocess.Popen | None = None
    try:
        # ---------- sessão 1: ciclo real com estado no disco ----------
        p = inicia(str(temp))
        t0 = time.time()
        base = base_dz23(90)
        checa(
            "instância descartável sobe e responde healthz",
            base is not None,
            f"base={base} em {round(time.time() - t0, 1)}s",
        )
        if not base:
            return 1
        porta = int(base.rsplit(":", 1)[1])

        status, estado = req(f"{base}/v1/auth/status")
        checa("primeira execução pede o assistente (setup_required=true)",
              estado.get("setup_required") is True, f"HTTP {status}")
        status, _ = req(f"{base}/v1/auth/setup", "POST", {"senha": SENHA})
        checa("assistente cria a senha do dono", status == 200, f"HTTP {status}")
        if status != 200:
            return 1

        status, ciclo = req(f"{base}/v1/desk/cycle", "POST", {}, bearer=SENHA,
                            timeout=120)
        resumo = str(ciclo.get("resumo", ""))
        checa(
            "ciclo PAPER real executa e admite entradas",
            status == 200 and ciclo.get("ok") is True
            and len(ciclo.get("ordens", [])) >= 1,
            f'HTTP {status} · resumo: "{resumo[:90]}"',
        )
        checa("sem erros no ciclo", not ciclo.get("erros"),
              "; ".join(str(e) for e in ciclo.get("erros", [])[:2]))

        a = captura(base)
        checa("estado durável capturado antes do crash", a is not None)
        if a is None:
            return 1
        checa("ordens do OMS registradas no livro", len(a["ordens"]) >= 1,
              f'{len(a["ordens"])} ordens: ' + ", ".join(
                  f"{o[0]}:{o[1]}" for o in a["ordens"][:4]))
        checa("ledger balanceado antes do crash", a["balanceado"] is True)
        patrim = decimal_ok(a["patrimonio"])
        checa(
            "patrimônio coerente com o mandato (0 < patrimônio ≤ 1000)",
            patrim is not None and Decimal(0) < patrim <= Decimal(1000),
            f'patrimônio={a["patrimonio"]} disponível={a["disponivel"]}',
        )
        checa("nenhuma pendência de reconciliação antes do crash",
              a["pend_rec"] == [] and a["pend_agend"] == [],
              f'agendador={a["pend_agend"]} mesa={a["pend_rec"]}')
        checa("contador do agendador registrou o ciclo",
              a["ciclos"] >= 1 and a["ultimo_ciclo"] is not None,
              f'ciclos={a["ciclos"]} ultimo_ciclo={a["ultimo_ciclo"]}')

        # ---------- CRASH: matar À FORÇA somente o processo DESTE harness ----------
        print(f"crash simulado: taskkill /F /PID {p.pid} (processo do harness)",
              flush=True)
        subprocess.run(["taskkill", "/PID", str(p.pid), "/F"], capture_output=True)
        morreu = espera_ok(lambda: p.poll() is not None, 15)
        checa("processo morre à força (sem saída graciosa)", morreu,
              f"exit={p.poll()}" if morreu else "segue vivo após 15s")
        liberou = espera_ok(lambda: not porta_aceita(porta), 15)
        checa("porta efetiva é liberada pelo SO após o crash", liberou,
              f"porta {porta}")

        # ---------- sessão 2: reabrir e conferir o disco ----------
        p = inicia(str(temp))
        t1 = time.time()
        base2 = base_dz23(90)
        checa(
            "reabertura após o crash sobe e responde healthz",
            base2 is not None,
            f"base={base2} em {round(time.time() - t1, 1)}s",
        )
        if not base2:
            return 1
        porta2 = int(base2.rsplit(":", 1)[1])
        checa("reabre sem tocar em outra porta (a mesma ficou livre)", porta2 == porta,
              f"{porta} -> {porta2}")

        status, me = req(f"{base2}/v1/auth/me", bearer=SENHA)
        checa("senha do dono continua valendo após o crash (Argon2id no disco)",
              status == 200 and "admin" in json.dumps(me).lower(), f"HTTP {status}")

        b = captura(base2)
        checa("estado durável lido do disco após o crash", b is not None)
        if b is None:
            return 1
        checa(
            "ordens do OMS idênticas (id/estado/qtd/preço)",
            b["ordens"] == a["ordens"],
            f'{len(b["ordens"])} ordens antes e depois',
        )
        checa(
            "contador e ultimo_ciclo do agendador preservados",
            b["ciclos"] == a["ciclos"] and b["ultimo_ciclo"] == a["ultimo_ciclo"],
            f'ciclos {a["ciclos"]} -> {b["ciclos"]} · '
            f'ultimo_ciclo {a["ultimo_ciclo"]} -> {b["ultimo_ciclo"]}',
        )
        checa(
            "posições abertas e instrumentos preservados",
            b["posicoes"] == a["posicoes"] and b["abertos"] == a["abertos"],
            f'posições={b["posicoes"]} abertos={b["abertos"]}',
        )
        checa(
            "saldos/patrimônio/disponível idênticos após o crash",
            b["saldos"] == a["saldos"] and b["patrimonio"] == a["patrimonio"]
            and b["disponivel"] == a["disponivel"],
            f'patrimônio {a["patrimonio"]} -> {b["patrimonio"]} · '
            f'disponível {a["disponivel"]} -> {b["disponivel"]}',
        )
        checa("ledger segue balanceado após o crash", b["balanceado"] is True)
        checa(
            "ledger volta LIMPO (sem pendências de reconciliação)",
            b["pend_rec"] == [] and b["pend_agend"] == [],
            f'agendador={b["pend_agend"]} mesa={b["pend_rec"]}',
        )
        checa(
            "store de durabilidade reporta estado íntegro na reabertura",
            isinstance(b["durabilidade"], dict) and bool(b["durabilidade"]),
            json.dumps(b["durabilidade"])[:120],
        )
        checa("modo da mesa preservado", b["modo"] == a["modo"], str(b["modo"]))

        # ---------- encerramento gracioso desta sessão ----------
        status, _ = req(f"{base2}/v1/app/quit", "POST", {}, bearer=SENHA)
        checa("POST /v1/app/quit responde 200 após a recuperação",
              status == 200, f"HTTP {status}")
        try:
            codigo = p.wait(timeout=40)
            checa("launcher encerra sozinho (exit 0) após a recuperação",
                  codigo == 0, f"exit={codigo}")
        except subprocess.TimeoutExpired:
            checa("launcher encerra sozinho (exit 0) após a recuperação",
                  False, "seguia vivo após 40s")
        checa("porta liberada após o encerramento gracioso",
              espera_ok(lambda: not porta_aceita(porta2), 15))
    finally:
        if p is not None and p.poll() is None:
            # limpeza de harness: só o processo que ESTE harness subiu
            subprocess.run(["taskkill", "/PID", str(p.pid), "/F"], capture_output=True)
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

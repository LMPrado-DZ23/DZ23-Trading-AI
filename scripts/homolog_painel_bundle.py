"""Homologa o PAINEL AUTENTICADO da escola/mesa (item 21) no BUNDLE INSTALADO.

Prova, no exe `--windowed` instalado (artefato do CI), o fluxo do dono pelo
painel real: assistente cria a senha do dono → login pelo portão → as 6
páginas → mesa com os 10 traders por tier (3 homologado / 7 experimental) e um
ciclo real disparado pelo botão → Escola Central (contextos de certificação,
salas por corretora, pilotos com autorização exclusiva do proprietário) →
zero respostas 5xx → encerramento gracioso (`POST /v1/app/quit` → launcher sai
sozinho, exit 0, porta liberada).

Usa um APPDATA descartável (não toca no DATA_DIR real do dono). O launcher
abre o navegador padrão na inicialização — comportamento de produção; a janela
externa não interfere no harness.

Uso (na estação, com o app instalado e nenhuma instância rodando):

    uv run --with playwright python scripts/homolog_painel_bundle.py
"""

from __future__ import annotations

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

# Console/pipe no Windows usa cp1252 e não codifica acentos/setas de forma
# confiável — um UnicodeEncodeError mataria o harness no meio do log.
for _fluxo in (sys.stdout, sys.stderr):
    if _fluxo is not None and hasattr(_fluxo, "reconfigure"):
        _fluxo.reconfigure(encoding="utf-8", errors="replace")

EXE = Path(os.environ["LOCALAPPDATA"]) / "DZ23-Trading-AI" / "DZ23-Trading-AI.exe"
EVID = Path(__file__).resolve().parents[1] / "docs" / "mission" / "evidence" / "e2e"
PORTA_INICIAL, PORTA_FINAL = 8000, 8010
SENHA = "senha-do-item21-2026-10-14"
PAGINAS = [
    ("overview", "Visão geral"),
    ("config", "Configuração"),
    ("analysis", "Análises"),
    ("mesa", "Mesa de Agentes"),
    ("floor", "Trading Floor"),
    ("risk", "Risco"),
]
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


def fluxo_do_dono(base: str) -> None:
    from playwright.sync_api import sync_playwright

    EVID.mkdir(parents=True, exist_ok=True)
    erros_5xx: list[str] = []

    def registra(resp) -> None:
        if resp.status >= 500:
            erros_5xx.append(f"{resp.status} {resp.url}")

    with sync_playwright() as pw:
        navegador = pw.chromium.launch()
        pagina = navegador.new_page(viewport={"width": 1366, "height": 768})
        pagina.on("response", registra)

        # --- login do dono pelo portão (senha criada pelo assistente) ---
        pagina.goto(f"{base}/app", wait_until="domcontentloaded")
        pagina.wait_for_selector("#loginPassword", state="visible", timeout=20000)
        pagina.fill("#loginPassword", SENHA)
        pagina.locator("#btnEntrar").click()
        if not espera_ok(lambda: pagina.locator("#app").is_visible(), 30):
            pagina.screenshot(path=str(EVID / "c021-item21-login-falhou.png"))
            checa("dono entra no painel do bundle pelo portão", False, "painel não abriu após o login")
            navegador.close()
            return
        checa("dono entra no painel do bundle pelo portão (senha do assistente)", True)

        papel = espera_ok(
            lambda: pagina.locator("#roleBadge").inner_text().strip() == "ADMIN", 10)
        checa("papel do dono exibido como ADMIN", papel)
        pronto = espera_ok(
            lambda: pagina.locator("#appStatus").inner_text().strip() == "READY", 30)
        checa("estado do app READY no painel autenticado", pronto)
        checa(
            "identidade do dono preenchida no cabeçalho",
            pagina.locator("#userEmail").inner_text().strip() != "—",
            pagina.locator("#userEmail").inner_text().strip()[:60],
        )

        # --- 6 páginas do painel ---
        for ident, titulo in PAGINAS:
            pagina.locator(f'[data-page="{ident}"]').first.click()

            def pagina_ok(ident=ident, titulo=titulo):
                classe = pagina.locator(f"#page-{ident}").get_attribute("class") or ""
                return ("active" in classe
                        and pagina.locator("#pageTitle").inner_text().strip() == titulo)

            checa(f'página "{titulo}" ativa com título correto', espera_ok(pagina_ok, 10))

        # --- mesa: 10 traders por tier ---
        # Os h3 do painel usam text-transform: uppercase — inner_text() devolve
        # MAIÚSCULAS; comparar em caixa baixa (a 1ª execução comparou literal e
        # deu falso-negativo: o app renderizou "TRADERS (10 PERFIS)").
        pagina.locator('[data-page="mesa"]').first.click()
        carregou = espera_ok(
            lambda: "traders (10 perfis)" in pagina.locator("#deskOut").inner_text().lower(), 45)
        checa('mesa carrega o livro com "Traders (10 perfis)"', carregou)
        if carregou:
            linhas = pagina.locator("#deskOut table").first.locator("tbody tr")
            n_linhas = linhas.count()
            tiers = linhas.evaluate_all(
                "(trs) => trs.map(tr => { const s = tr.querySelector('td small');"
                " return s ? s.textContent.trim() : ''; })")
            n_h = sum(1 for t in tiers if t == "homologado")
            n_e = sum(1 for t in tiers if t == "experimental")
            checa(
                "10 traders com tiers 3 homologado / 7 experimental",
                n_linhas == 10 and n_h == 3 and n_e == 7,
                f"linhas={n_linhas} homologado={n_h} experimental={n_e}",
            )
            pills = linhas.evaluate_all(
                "(trs) => trs.map(tr => { const p = tr.querySelector('.pill');"
                " return p ? p.textContent.trim() : ''; })")
            n_ap = sum(1 for x in pills if x == "aprovado (paper)")
            n_ex = sum(1 for x in pills if x == "experimental (paper)")
            checa(
                'estágios por trader: 3 "aprovado (paper)" / 7 "experimental (paper)"',
                n_ap == 3 and n_ex == 7,
                f"aprovado={n_ap} experimental={n_ex}",
            )

            # --- ciclo real pelo botão (deskCycle → POST /v1/desk/cycle) ---
            def n_ciclos() -> int:
                m = re.search(r"(\d+)\s+ciclos",
                              pagina.locator("#deskOut").inner_text())
                return int(m.group(1)) if m else -1

            n0 = n_ciclos()
            pagina.get_by_role("button", name="Rodar um ciclo", exact=True).click()
            pagina.wait_for_timeout(400)
            try:
                toast = (pagina.locator("#toast").inner_text().strip()
                         if pagina.locator("#toast").is_visible() else "")
            except Exception:
                toast = ""
            rodou = espera_ok(
                lambda: n_ciclos() >= n0 + 1
                and pagina.locator("#deskOut .event").count() >= 1, 90)
            checa(
                'botão "Rodar um ciclo" executa um ciclo real na mesa do bundle',
                rodou,
                f"ciclos {n0} -> {n_ciclos()}"
                + (f' · aviso: "{toast[:80]}"' if toast else ""),
            )
            pagina.screenshot(path=str(EVID / "c021-item21-mesa-bundle.png"))

        # --- Escola Central (escola autenticada no painel) ---
        pagina.get_by_role("button", name="Escola Central", exact=True).click()
        escola_ok = espera_ok(
            lambda: "escola central" in pagina.locator("#deskMsg").inner_text().lower(), 30)
        checa("botão \"Escola Central\" renderiza a escola no painel autenticado", escola_ok)
        if escola_ok:
            texto = pagina.locator("#deskMsg").inner_text()
            texto_min = texto.lower()
            checa("contextos de certificação expostos",
                  "contextos de certificação" in texto_min)
            checa("salas por corretora (isolamento financeiro) expostas",
                  "salas por corretora (isolamento financeiro)" in texto_min)
            checa("pilotos expostos com autorização exclusiva do proprietário",
                  "pilotos (modo" in texto_min
                  and "ato exclusivo do proprietário" in texto_min)
            if "Nenhuma sala aberta" in texto_min:
                checa("salas: instância recém-criada — estado vazio honesto", True)
            else:
                # A chave de isolamento é corretora|conta|mercado|modo; validar
                # por partes (o 1º regex usava \w+, que não cobre "paper-legacy").
                chaves = pagina.locator("#deskMsg table").first.locator(
                    "tbody tr td:first-child"
                ).evaluate_all("(tds) => tds.map(td => td.textContent.trim())")
                partes = chaves[0].split("|") if chaves else []
                checa(
                    "salas usam chave de isolamento por corretora|conta|mercado|modo",
                    len(partes) == 4 and all(partes),
                    chaves[0] if chaves else "sem salas",
                )
            pagina.screenshot(path=str(EVID / "c021-item21-escola-bundle.png"))

        checa(
            "zero respostas 5xx durante o fluxo autenticado do dono",
            not erros_5xx,
            "; ".join(erros_5xx[:4]),
        )
        navegador.close()


def main() -> int:
    if not EXE.exists():
        print(f"FALHA — executável instalado não encontrado: {EXE}")
        return 2
    print(f"EXE: {EXE}", flush=True)

    encerra_limpeza()
    checa("estação limpa antes do teste (sem DZ23, faixa livre)", faixa_livre())

    temp = Path(tempfile.mkdtemp(prefix="dz23-item21-"))
    p: subprocess.Popen | None = None
    try:
        p = inicia({"APPDATA": str(temp)})
        t0 = time.time()
        base = base_dz23(90)
        checa(
            "instância descartável sobe e responde healthz",
            base is not None,
            f"base={base} em {round(time.time() - t0, 1)}s",
        )
        if not base:
            return 1

        status, estado = req(f"{base}/v1/auth/status")
        checa(
            "primeira execução pede o assistente (setup_required=true)",
            estado.get("setup_required") is True,
            f"HTTP {status}",
        )
        status, resposta = req(f"{base}/v1/auth/setup", "POST", {"senha": SENHA})
        checa("assistente cria a senha do dono", status == 200, f"HTTP {status}")
        if status != 200:
            return 1

        fluxo_do_dono(base)

        status, _ = req(f"{base}/v1/app/quit", "POST", {}, bearer=SENHA)
        checa("POST /v1/app/quit com a senha do dono responde 200",
              status == 200, f"HTTP {status}")
        try:
            codigo = p.wait(timeout=40)
            checa("launcher encerra sozinho (exit 0) após o painel autenticado",
                  codigo == 0, f"exit={codigo}")
        except subprocess.TimeoutExpired:
            checa("launcher encerra sozinho (exit 0) após o painel autenticado",
                  False, "seguia vivo após 40s")
        porta = int(base.rsplit(":", 1)[1])
        checa("porta efetiva liberada após o encerramento", not porta_aceita(porta))
    finally:
        if p is not None and p.poll() is None:
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

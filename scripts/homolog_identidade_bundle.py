"""Homologa a IDENTIDADE VISUAL (C020) no bundle Windows INSTALADO — item 18.

Pré-condições: app instalado pelo Setup do artefato de CI e NENHUM processo
`DZ23-Trading-AI.exe` em execução (o script inicia e encerra o app).

Roda com:

    uv run --with playwright python scripts/homolog_identidade_bundle.py

Checagens: healthz (com 1 retentativa registrada, precedente do item 17) →
contrato HTTP das rotas de marca/manifesto/estado do dono → portão de login
(símbolo separado carregado, wordmark "Trading AI" sem repetir "DZ23", selos
dinâmicos vindos do backend, senha não pré-preenchida, toggle acessível,
mensagem de erro correta, aviso de servidor ausente só quando falha) →
responsivo 1920×1080 / 390×844 / 360×640 → encerramento.

O encerramento aqui é `taskkill` de harness (limpeza); o encerramento
gracioso pelo botão já está homologado nos itens 12/13/15.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"
EXE = Path(os.environ["LOCALAPPDATA"]) / "DZ23-Trading-AI" / "DZ23-Trading-AI.exe"
EVID = Path(__file__).resolve().parents[1] / "docs" / "mission" / "evidence" / "e2e"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

FALHAS: list[str] = []


def checa(nome: str, ok: bool, detalhe: str = "") -> None:
    sufixo = f" — {detalhe}" if detalhe else ""
    print(f"{'PASS' if ok else 'FALHA'} — {nome}{sufixo}", flush=True)
    if not ok:
        FALHAS.append(nome)


def http(url: str):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, r.read()


def encerra_app() -> None:
    # A saída do taskkill vem em bytes da página de código local (GBK):
    # decodificar como UTF-8 quebrava o harness com UnicodeDecodeError.
    subprocess.run(
        ["taskkill", "/IM", "DZ23-Trading-AI.exe", "/F"],
        capture_output=True,
    )


def porta_livre() -> bool:
    try:
        http(f"{BASE}/healthz")
        return False
    except Exception:
        return True


def base_saudavel():
    """Descobre a porta efetiva do DZ23 (8000–8010) pelo marcador do /healthz."""
    for porta in range(8000, 8011):
        base = f"http://127.0.0.1:{porta}"
        try:
            status, corpo = http(f"{base}/healthz")
        except Exception:
            continue
        if status != 200:
            continue
        try:
            dados = json.loads(corpo.decode("utf-8"))
        except Exception:
            continue
        if str(dados.get("app", "")).startswith("tradingagents-agency"):
            return base
    return None


def espera_healthz(segundos: int = 90):
    """Espera o DZ23 saudável e devolve a base efetiva — o launcher escolhe a
    primeira porta livre da faixa; fixar 8000 quebrava o harness quando havia
    conflito legítimo com outro programa."""
    global BASE
    limite = time.time() + segundos
    while time.time() < limite:
        base = base_saudavel()
        if base:
            BASE = base
            return base
        time.sleep(1.0)
    return None


def sobe_com_retentativa() -> dict:
    """Sobe o app; 1 retentativa registrada (transitório de 1ª execução)."""
    tentativas = 0
    encerra_app()
    time.sleep(1.0)
    for tentativas in (1, 2):
        t0 = time.time()
        subprocess.Popen([str(EXE)])
        base = espera_healthz()
        if base:
            return {
                "ok": True,
                "tentativas": tentativas,
                "segundos": round(time.time() - t0, 1),
                "base": base,
            }
        encerra_app()
        time.sleep(2.0)
    return {"ok": False, "tentativas": tentativas, "segundos": None, "base": None}


def contrato_http() -> None:
    status, corpo = http(f"{BASE}/favicon.ico")
    checa("favicon.ico 200 e é ICO", status == 200 and corpo[:4] == b"\x00\x00\x01\x00")

    status, corpo = http(f"{BASE}/static/marca/dz23-simbolo-192.png")
    checa("símbolo 192 servido (PNG)", status == 200 and corpo[:8] == PNG_MAGIC)

    status, corpo = http(f"{BASE}/static/marca/dz23-logo-192.png")
    checa("logomarca 192 servida (PNG)", status == 200 and corpo[:8] == PNG_MAGIC)

    status, corpo = http(f"{BASE}/manifest.webmanifest")
    try:
        man = json.loads(corpo.decode("utf-8"))
    except Exception:
        man = {}
    checa(
        "manifesto PWA com nome/marca/start_url/theme_color",
        status == 200
        and man.get("name") == "DZ23 Trading AI"
        and man.get("short_name") == "Trading AI"
        and man.get("start_url") == "/app"
        and man.get("theme_color") == "#061526",
    )

    try:
        http(f"{BASE}/static/marca/dz23-logo-original.png")
        checa("original NÃO é servido pelo painel (404)", False, "veio 200")
    except urllib.error.HTTPError as e:
        checa("original NÃO é servido pelo painel (404)", e.code == 404, f"HTTP {e.code}")

    status, corpo = http(f"{BASE}/v1/auth/status")
    estado = json.loads(corpo.decode("utf-8")) if status == 200 else {}
    checa(
        "estado do dono preservado após reinstalação (setup_required=false)",
        estado.get("setup_required") is False,
    )

    status, corpo = http(f"{BASE}/v1/status/operacao")
    op = json.loads(corpo.decode("utf-8")) if status == 200 else {}
    checa(
        "backend expõe estado operacional real (modos + operacao_real)",
        status == 200 and "modos" in op and "operacao_real" in op,
        f"modo_atual={op.get('modo_atual')}",
    )


def portao_e_responsivo() -> None:
    from playwright.sync_api import sync_playwright

    EVID.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        navegador = p.chromium.launch()
        pagina = navegador.new_page(viewport={"width": 1366, "height": 768})
        pagina.goto(BASE, wait_until="domcontentloaded")
        pagina.wait_for_selector("#authGate", state="visible")

        # --- identidade do portão ---
        simbolo = pagina.locator("#authGate .logo-mark")
        carregou = simbolo.evaluate("e => e.complete && e.naturalWidth >= 64")
        checa(
            "símbolo DZ23 carregado no portão (≥64 px reais)",
            carregou,
            pagina.locator("#authGate .logo-mark").get_attribute("src") or "",
        )
        checa(
            'wordmark é "Trading AI"',
            pagina.locator(".auth-wordmark").inner_text().strip() == "Trading AI",
        )
        checa(
            'subtítulo "Inteligência coordenada. Risco governado."',
            "Inteligência coordenada. Risco governado."
            in pagina.locator(".auth-sub").inner_text(),
        )
        texto_portao = pagina.locator("#authGate").inner_text()
        checa('"DZ23 Trading AI" NÃO repetido no portão', "DZ23 Trading AI" not in texto_portao)
        checa('"TRADING PLATFORM" ausente', "TRADING PLATFORM" not in texto_portao.upper())

        # --- selos dinâmicos do backend ---
        selos = pagina.locator("#gateBadges")
        pagina.wait_for_function(
            "() => document.querySelector('#gateBadges').innerText.includes('PAPER')",
            timeout=15000,
        )
        texto_selos = selos.inner_text()
        checa("selo PAPER MODE presente", "PAPER MODE" in texto_selos)
        checa("selo EXECUÇÃO GOVERNADA presente", "EXECUÇÃO GOVERNADA" in texto_selos)
        checa("selo DINHEIRO REAL BLOQUEADO presente", "DINHEIRO REAL BLOQUEADO" in texto_selos)
        checa(
            "selo neutro de indisponibilidade NÃO apareceu com o backend vivo",
            "indisponível" not in texto_selos,
        )

        # --- senha: máscara, sem pré-preenchimento, toggle acessível ---
        senha = pagina.locator("#loginPassword")
        checa("campo de senha mascarado e vazio", senha.get_attribute("type") == "password"
              and (senha.input_value() == ""))
        olho = pagina.locator(".pwd-eye")
        checa("toggle começa com aria-pressed=false", olho.get_attribute("aria-pressed") == "false")
        olho.click()
        checa("toggle revela (type=text, aria-pressed=true)",
              senha.get_attribute("type") == "text" and olho.get_attribute("aria-pressed") == "true")
        olho.click()
        checa("toggle oculta de novo", senha.get_attribute("type") == "password")

        checa(
            "aviso de servidor ausente NÃO aparece com o servidor vivo",
            "Não foi possível falar com o servidor local" not in pagina.locator("#authMsg").inner_text(),
        )
        pagina.locator("#btnEntrar").click()
        pagina.wait_for_function(
            "() => document.querySelector('#authMsg').innerText.includes('senha')", timeout=10000)
        checa("senha vazia → mensagem clara",
              "Informe a senha do dono" in pagina.locator("#authMsg").inner_text())

        pagina.screenshot(path=str(EVID / "c020-portao-bundle.png"))

        # --- responsivo ---
        for largura, altura in ((1920, 1080), (390, 844), (360, 640)):
            pagina.set_viewport_size({"width": largura, "height": altura})
            pagina.wait_for_timeout(250)
            sem_hscroll = pagina.evaluate(
                "() => document.documentElement.scrollWidth <= window.innerWidth + 1")
            botao = pagina.locator("#btnEntrar").bounding_box() or {}
            dentro = botao.get("x", -1) >= 0 and botao.get("x", 0) + botao.get("width", 0) <= largura + 1
            visivel = pagina.locator("#btnEntrar").is_visible()
            campo = pagina.locator("#loginPassword").bounding_box() or {}
            campo_ok = campo.get("x", -1) >= 0 and campo.get("x", 0) + campo.get("width", 0) <= largura + 1
            checa(
                f"{largura}×{altura}: sem rolagem horizontal, botão e senha dentro da tela",
                sem_hscroll and dentro and visivel and campo_ok,
            )
            pagina.screenshot(path=str(EVID / f"c020-portao-{largura}x{altura}.png"))

        navegador.close()


def main() -> int:
    if not EXE.exists():
        print(f"FALHA — executável instalado não encontrado: {EXE}")
        return 2
    print(f"EXE: {EXE}", flush=True)

    subida = sobe_com_retentativa()
    checa(
        "app instalado sobe e responde healthz",
        subida["ok"],
        f"tentativas={subida['tentativas']} segundos={subida['segundos']} porta={subida.get('base')}",
    )
    if not subida["ok"]:
        encerra_app()
        return 1
    try:
        contrato_http()
        portao_e_responsivo()
    finally:
        encerra_app()
        time.sleep(1.5)
    checa("porta efetiva liberada após encerrar", porta_livre())

    print(f"TOTAL: {len(FALHAS)} falha(s)", flush=True)
    if FALHAS:
        for f in FALHAS:
            print(f"  - {f}")
        return 1
    print("HARNESS_EXIT=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())

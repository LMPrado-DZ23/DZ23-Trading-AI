"""Gera o GIF de demonstração do DZ23 Trading AI (docs/demo/dz23-demo.gif).

Receita da instância isolada (tools/fm4_prova_viva.py): DATA_DIR temporário,
porta livre própria, senha descartável e chaves de IA do ambiente REMOVIDAS —
a instalação do dono (porta 8000) nunca é tocada e nada sai à rede de IA.
O navegador é dirigido com os seletores dos testes e2e; os frames viram GIF
com Pillow (paleta única, 960px, loop infinito).

Uso:
    uv run --with playwright --with pillow python tools/gerar_gif_demo.py
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SAIDA = REPO_ROOT / "docs" / "demo" / "dz23-demo.gif"
SENHA = "s3nh4-dz23-demo"
LARGURA_GIF, ALTURA_GIF = 960, 600


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _ambiente_do_filho() -> dict:
    env = os.environ.copy()
    env.pop("ACCESS_PASSWORD", None)
    from service.api.ia_provedores import CATALOGO

    for p in CATALOGO:
        if p.get("chave_env"):
            env.pop(p["chave_env"], None)
    for extra in (
        "TRADINGAGENTS_LLM_PROVIDER",
        "TRADINGAGENTS_LLM_MODEL",
        "TRADINGAGENTS_BROKER_MODE",
        "EXCHANGE_API_KEY",
        "EXCHANGE_API_SECRET",
        "MESA_LIVE_LIBERADA",
    ):
        env.pop(extra, None)
    env.update(
        {
            "DATA_DIR": tempfile.mkdtemp(prefix="dz23-gif-"),
            "MULTI_TENANT_ENABLED": "false",
            "ACCESS_PASSWORD": SENHA,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return env


def _req(base: str, metodo: str, rota: str, corpo: dict | None = None,
         timeout: float = 240.0):
    url = base + rota
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(url, data=dados, method=metodo)
    req.add_header("Authorization", f"Bearer {SENHA}")
    if dados is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            corpo_erro = json.loads(e.read().decode())
        except Exception:
            corpo_erro = {}
        return e.code, corpo_erro


def _esperar_healthz(base: str, proc: subprocess.Popen) -> None:
    limite = time.monotonic() + 60
    while time.monotonic() < limite:
        if proc.poll() is not None:
            raise RuntimeError(f"uvicorn morreu cedo (exit {proc.returncode})")
        try:
            with urllib.request.urlopen(base + "/healthz", timeout=2) as r:
                if r.status == 200:
                    return
        except OSError:
            time.sleep(0.4)
    raise RuntimeError("servidor de demo não respondeu /healthz em 60s")


def _aquecimento(base: str) -> dict:
    """Ciclos PAPER antes da gravação: cache de dados de mercado e histórico
    nos traders, para a mesa aparecer viva na primeira tela."""
    registro: dict = {}
    for i in range(3):
        st, ciclo = _req(base, "POST", "/v1/desk/cycle", {})
        registro[f"ciclo_{i + 1}"] = {"http": st, "ok": ciclo.get("ok"),
                                      "resumo": ciclo.get("resumo")}
        if st != 200 or not ciclo.get("ok"):
            raise RuntimeError(f"ciclo de aquecimento {i + 1} falhou: {ciclo}")
        _, s = _req(base, "GET", "/v1/desk/status")
        if s.get("posicoes"):
            break
    return registro


def _gravar(base: str, dir_frames: Path) -> list[tuple[Path, int]]:
    from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

    captura: list[tuple[Path, int]] = []
    contador = [0]

    def shot(page, dur_ms: int) -> None:
        contador[0] += 1
        caminho = dir_frames / f"frame-{contador[0]:04d}.png"
        page.screenshot(path=str(caminho))
        captura.append((caminho, dur_ms))

    # CSP do app nega 'unsafe-eval': nada de wait_for_function/evaluate com
    # string — só seletores CSS nativos do Playwright e eventos reais de mouse.
    def rolar(page, dy: int) -> None:
        page.mouse.move(720, 400)
        page.mouse.wheel(0, dy)

    with sync_playwright() as pw:
        browser = None
        erros: list[str] = []
        for kwargs in ({"channel": "chrome"}, {"channel": "msedge"}, {}):
            try:
                browser = pw.chromium.launch(headless=True, **kwargs)
                break
            except Exception as exc:  # noqa: BLE001 - canal ausente é esperado
                erros.append(f"{kwargs.get('channel', 'chromium padrão')}: {exc}")
        if browser is None:
            raise RuntimeError("nenhum navegador: " + " | ".join(erros))
        try:
            context = browser.new_context(
                viewport={"width": 1440, "height": 900}, device_scale_factor=2
            )
            page = context.new_page()

            # ---- portão premium do dono ----
            page.goto(f"{base}/app")
            page.wait_for_selector("#authTitle")
            page.wait_for_timeout(800)
            shot(page, 2200)

            # ---- digitação da senha (campo mascarado) ----
            page.click("#loginPassword")
            for ch in SENHA:
                page.keyboard.type(ch)
                page.wait_for_timeout(50)
                shot(page, 90)

            page.get_by_role("button", name="Entrar", exact=True).click()
            # appStatus só é preenchido na página "Visão geral"; no Início o
            # marcador de carregado é o cartão de conexão do inicioLoad().
            page.wait_for_selector("#app:not(.hide)", timeout=45000)
            page.wait_for_selector("#page-inicio.active", timeout=15000)
            page.wait_for_selector(
                '#iniConectarEstado:has-text("Corretora")', timeout=30000
            )
            page.wait_for_timeout(400)
            shot(page, 700)
            shot(page, 2100)  # Início

            # ---- Modo Avançado: revela a Mesa de Agentes no menu ----
            page.select_option("#uiModo", "avancado")
            page.wait_for_timeout(500)
            shot(page, 900)
            page.wait_for_selector('[data-page="mesa"]', timeout=15000)

            # ---- Mesa de Agentes ----
            page.locator('[data-page="mesa"]').first.click()
            page.wait_for_selector("#page-mesa.active", timeout=15000)
            page.wait_for_selector('#deskOut:has-text("Traders")', timeout=45000)
            page.wait_for_timeout(600)
            shot(page, 700)

            # rolar até a mesa dos 10 traders e voltar ao topo
            for _ in range(3):
                rolar(page, 200)
                page.wait_for_timeout(70)
                shot(page, 80)
            shot(page, 1700)
            for _ in range(3):
                rolar(page, -200)
                page.wait_for_timeout(70)
                shot(page, 80)

            # ---- ciclo PAPER real, pelo botão da mesa ----
            page.wait_for_timeout(4300)  # deixa qualquer toast velho expirar
            page.get_by_role("button", name="Rodar um ciclo").click()
            shot(page, 500)
            limite = time.monotonic() + 240
            concluiu = False
            while time.monotonic() < limite:
                try:
                    page.wait_for_selector("#toast:not(.hide)", timeout=2000)
                    concluiu = True
                    break
                except PWTimeout:
                    shot(page, 260)
            if not concluiu:
                raise RuntimeError("ciclo PAPER não concluiu em 240s (toast ausente)")
            toast_txt = page.locator("#toast").inner_text().strip()
            print("toast do ciclo:", toast_txt)
            if not toast_txt or "falhou" in toast_txt.lower():
                raise RuntimeError(f"ciclo PAPER falhou na UI: {toast_txt!r}")
            page.wait_for_timeout(700)  # deskLoad() atualiza as tabelas
            shot(page, 700)
            shot(page, 2300)

            # ---- resultados: ordens / posições / ciclos ----
            for _ in range(10):
                rolar(page, 300)
                page.wait_for_timeout(70)
                shot(page, 80)
            shot(page, 2700)
            context.close()
        finally:
            browser.close()
    return captura


def _montar(captura: list[tuple[Path, int]], saida: Path) -> dict:
    from PIL import Image

    quadros = []
    for caminho, _ in captura:
        with Image.open(caminho) as im:
            quadros.append(
                im.convert("RGB").resize((LARGURA_GIF, ALTURA_GIF), Image.LANCZOS)
            )

    # paleta única para todos os quadros (sem tremulação de cores)
    passo = max(1, len(quadros) // 8)
    amostras = quadros[::passo][:8]
    mosaico = Image.new("RGB", (LARGURA_GIF, ALTURA_GIF * len(amostras)))
    for i, q in enumerate(amostras):
        mosaico.paste(q, (0, ALTURA_GIF * i))
    paleta = mosaico.quantize(colors=255, method=Image.MEDIANCUT)

    finais = [q.quantize(palette=paleta, dither=Image.Dither.NONE) for q in quadros]
    duracoes = [d for _, d in captura]
    saida.parent.mkdir(parents=True, exist_ok=True)
    finais[0].save(
        str(saida),
        save_all=True,
        append_images=finais[1:],
        duration=duracoes,
        loop=0,
        optimize=True,
    )
    return {
        "quadros": len(finais),
        "duracao_s": round(sum(duracoes) / 1000, 1),
        "bytes": saida.stat().st_size,
    }


def main() -> int:
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        print("uvicorn ausente — instale o extra 'desk'")
        return 2

    porta = _free_port()
    base = f"http://127.0.0.1:{porta}"
    env = _ambiente_do_filho()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "service.app:app",
         "--host", "127.0.0.1", "--port", str(porta), "--log-level", "warning"],
        cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _esperar_healthz(base, proc)

        aquecimento = _aquecimento(base)
        print("aquecimento:", json.dumps(aquecimento, ensure_ascii=False))

        dir_frames = Path(tempfile.mkdtemp(prefix="dz23-gif-frames-"))
        captura = _gravar(base, dir_frames)
        info = _montar(captura, SAIDA)
        print(f"OK — {SAIDA}")
        print(json.dumps(info, ensure_ascii=False))

        for caminho, _ in captura:
            caminho.unlink(missing_ok=True)
        dir_frames.rmdir()
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())

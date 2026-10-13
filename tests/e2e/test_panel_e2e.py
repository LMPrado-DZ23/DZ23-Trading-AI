"""E2E do painel real em navegador real (Playwright + servidor uvicorn).

Cobre o fluxo do dono único ponta a ponta: login, menu completo, formulários,
Trading Floor embutido, validação de formulário e contratos 4xx/401 das rotas
bloqueadas — tudo contra o app rodando de verdade, não mocks.
"""

from __future__ import annotations

import re

import pytest

sync_api = pytest.importorskip(
    "playwright.sync_api", reason="playwright não instalado; rode: uv run --with playwright pytest tests/e2e -v"
)
expect = sync_api.expect

PAGINAS = [
    ("config", "Configuração"),
    ("analysis", "Análises"),
    ("mesa", "Mesa de Agentes"),
    ("floor", "Trading Floor"),
    ("risk", "Risco"),
    ("plano", "Plano e pagamento"),
    ("admin", "Administração"),
    ("overview", "Visão geral"),
]


def entrar_como_dono(page, base_url: str, senha: str) -> None:
    page.goto(f"{base_url}/app")
    expect(page.locator("#authTitle")).to_have_text("Entrar na sua mesa")
    expect(page.locator("#loginTenant")).to_be_hidden()
    page.fill("#loginPassword", senha)
    page.get_by_role("button", name="Entrar", exact=True).click()
    expect(page.locator("#app")).to_be_visible(timeout=15000)


def test_dono_entra_e_visao_geral_paper(page, live_server, owner_password, evidence_dir):
    entrar_como_dono(page, live_server, owner_password)

    expect(page.locator("#roleBadge")).to_have_text("ADMIN")
    expect(page.locator("#userEmail")).not_to_have_text("—")
    expect(page.locator("#appStatus")).to_have_text("READY", timeout=15000)
    expect(page.locator("#agentCount")).to_have_text(re.compile(r"\d+"))
    assert int(page.locator("#agentCount").inner_text()) > 0
    expect(page.locator("#healthList")).to_contain_text("Live trading")
    expect(page.locator("#healthList")).to_contain_text("BLOCKED")
    expect(page.locator("#page-overview")).to_contain_text("Live bloqueado")

    page.screenshot(path=str(evidence_dir / "01-visao-geral-dono.png"))


def test_dono_entra_mesmo_demorando_no_portao(page, live_server, owner_password):
    # Regressão do mundo real: o painel embutido (iframe /floor) polla o
    # servidor enquanto o dono ainda está no portão de login. Antes da
    # correção, esses 401 anônimos armavam a trava de força-bruta
    # (5 tentativas => 15 min) e o login do dono respondia 429 só por ele
    # demorar um pouco para digitar a senha.
    page.goto(f"{live_server}/app")
    expect(page.locator("#authTitle")).to_have_text("Entrar na sua mesa")
    page.wait_for_timeout(16000)  # > 5 ciclos de polling anônimo do painel

    entrar_como_dono(page, live_server, owner_password)
    expect(page.locator("#appStatus")).to_have_text("READY", timeout=15000)


def test_menu_completo_formularios_e_sem_erro_5xx(page, live_server, owner_password):
    erros_5xx: list[str] = []

    def registrar(resp) -> None:
        if resp.status >= 500:
            erros_5xx.append(f"{resp.status} {resp.url}")

    page.on("response", registrar)
    entrar_como_dono(page, live_server, owner_password)

    for pagina, titulo in PAGINAS:
        page.locator(f'[data-page="{pagina}"]').first.click()
        expect(page.locator(f"#page-{pagina}")).to_have_class(re.compile(r"\bactive\b"))
        expect(page.locator("#pageTitle")).to_have_text(titulo)

    page.locator('[data-page="config"]').first.click()
    expect(page.locator("#TRADINGAGENTS_LLM_PROVIDER")).to_be_visible()
    assert page.locator("#TRADINGAGENTS_LLM_PROVIDER option").count() >= 5
    expect(page.locator("#OPENROUTER_API_KEY")).to_be_visible()
    expect(page.locator("#TRADINGAGENTS_EXCHANGE")).to_be_visible()

    page.locator('[data-page="analysis"]').first.click()
    page.fill("#ticker", "")
    page.get_by_role("button", name="Iniciar análise").click()
    expect(page.locator("#toast")).to_contain_text("Informe ativo e data")

    assert erros_5xx == [], f"respostas 5xx durante o uso real do painel: {erros_5xx}"


def test_mesa_camada_quant_mostra_governanca(page, live_server, owner_password, evidence_dir):
    entrar_como_dono(page, live_server, owner_password)

    page.locator('[data-page="mesa"]').first.click()
    page.get_by_role("button", name="Camada quant/IA").click()
    expect(page.locator("#deskMsg")).to_contain_text("nenhum modelo de IA envia ordens")
    expect(page.locator("#deskMsg")).to_contain_text("Modelo")

    page.screenshot(path=str(evidence_dir / "03-mesa-quant.png"))


def test_floor_embutido_same_origin(page, live_server, owner_password, evidence_dir):
    entrar_como_dono(page, live_server, owner_password)

    page.locator('[data-page="floor"]').first.click()
    iframe = page.locator("iframe.floor-frame")
    expect(iframe).to_be_visible()

    frame = page.frame_locator("iframe.floor-frame")
    expect(frame.locator("h1")).to_contain_text("DZ23", timeout=15000)
    expect(frame.locator("h1")).to_contain_text("TRADING FLOOR")
    expect(frame.locator(".mode")).to_contain_text("PAPER MODE")

    page.screenshot(path=str(evidence_dir / "02-floor-embutido.png"))


def test_rotas_saas_bloqueadas_e_login_dono_por_bearer(page, live_server, owner_password):
    req = page.request
    assert req.post(f"{live_server}/v1/auth/login", data={}).status == 404
    assert req.post(f"{live_server}/v1/auth/signup", data={}).status == 404
    assert req.get(f"{live_server}/v1/auth/oauth/providers").status in (403, 404)

    assert req.get(f"{live_server}/v1/auth/me").status == 401
    ruim = req.get(f"{live_server}/v1/auth/me", headers={"Authorization": "Bearer senha-errada"})
    assert ruim.status == 401

    bom = req.get(f"{live_server}/v1/auth/me", headers={"Authorization": f"Bearer {owner_password}"})
    assert bom.status == 200
    assert bom.json()["role"] == "admin"

    quant = req.get(f"{live_server}/v1/quant/status", headers={"Authorization": f"Bearer {owner_password}"})
    assert quant.status == 200
    assert "governanca" in quant.json()

    assert req.get(f"{live_server}/rota-que-nao-existe-dz23").status == 404

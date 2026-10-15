"""Matriz FM-6 (§11 da missão de auditoria funcional) — app real, navegador real.

Cobre os itens do §11 que a matriz de aceite TD-5 não exercitava explicitamente
no BUNDLE: Modo Avançado (3 ↔ 8 páginas), TODAS as páginas (incl. Visão geral,
Análises, Trading Floor embutido e Risco) com zero respostas 5xx, Corretoras
(catálogo + capacidades honestas + reconciliação), Escola Central e o Trading
Dashboard (Lightweight Charts vendorizado: mercado desenhado pelo LC; portfólio
pelo LC com operações fechadas ou pela queda honesta "sem operações fechadas").
"Reinício e recuperação" roda no harness (`scripts/homolog_aceite_td5_bundle.py`)
depois desta matriz.

O MESMO arquivo roda em dois alvos (como o aceite):

1. Instância local (padrão): `live_server` do conftest (uvicorn + DATA_DIR
   temporário) — `uv run --with playwright pytest tests/e2e/test_fm6_app_isolado.py -q`.
2. Instância EXTERNA (bundle Windows extraído em ambiente isolado no CI):
   defina `DZ23_E2E_BASE_URL` + `DZ23_E2E_OWNER_PASSWORD` — nenhum processo
   é spawned e os mesmos checks rodam contra o alvo.

Helpers locais de propósito: este arquivo precisa ser auto-suficiente para
executar contra um alvo externo sem depender de outros módulos de teste.
"""

from __future__ import annotations

import os
import re

import pytest

sync_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright não instalado; rode: uv run --with playwright pytest tests/e2e -q",
)
expect = sync_api.expect

PAGINAS = [
    ("inicio", "Início"),
    ("overview", "Visão geral"),
    ("config", "Configuração"),
    ("analysis", "Análises"),
    ("mesa", "Mesa de Agentes"),
    ("manual", "Minhas Operações"),
    ("floor", "Trading Floor"),
    ("risk", "Risco"),
]


def entrar_como_dono(page, base_url: str, senha: str) -> None:
    page.goto(f"{base_url}/app")
    expect(page.locator("#authTitle")).to_have_text("Entrar na sua mesa")
    page.fill("#loginPassword", senha)
    page.get_by_role("button", name="Entrar", exact=True).click()
    expect(page.locator("#app")).to_be_visible(timeout=15000)


def modo_avancado(page) -> None:
    page.select_option("#uiModo", "avancado")


def ir_para(page, pagina: str) -> None:
    # O menu existe duas vezes (sidebar e barra inferior do celular); em
    # tela estreita a sidebar fica display:none — clicamos a cópia VISÍVEL.
    page.locator(f'[data-page="{pagina}"]:visible').first.click()
    expect(page.locator(f"#page-{pagina}")).to_have_class(re.compile(r"\bactive\b"))


@pytest.fixture()
def mesa_url(request):
    """Alvo dos testes: instância externa via env OU servidor local do conftest."""
    externo = (os.environ.get("DZ23_E2E_BASE_URL") or "").strip()
    if externo:
        return externo.rstrip("/")
    return request.getfixturevalue("live_server")


@pytest.fixture()
def senha(request):
    return os.environ.get("DZ23_E2E_OWNER_PASSWORD") or request.getfixturevalue("owner_password")


# ------------------------------------------------------------------- item 01
def test_fm6_01_modo_avancado_todas_as_paginas(page, mesa_url, senha, evidence_dir):
    # §11 "Modo Avançado" + "Todas as páginas": o Avançado abre as OITO
    # páginas do produto (Simples volta a três — nada é removido), cada uma
    # carrega seu conteúdo real e NENHUMA resposta 5xx acontece no caminho.
    erros_5xx: list[str] = []

    def registrar(resp):
        if 500 <= resp.status < 600:
            erros_5xx.append(f"{resp.status} {resp.url}")

    page.on("response", registrar)

    entrar_como_dono(page, mesa_url, senha)
    expect(page.locator("#sideNav button")).to_have_count(3)  # Simples é o padrão

    modo_avancado(page)
    expect(page.locator("#sideNav button")).to_have_count(len(PAGINAS))

    # Varredura com âncora REAL por página (não basta "clicou e não quebrou").
    ir_para(page, "inicio")
    expect(page.locator("#pageTitle")).to_have_text("Início")
    expect(page.locator("#iniConectarEstado")).not_to_contain_text("Carregando...", timeout=15000)

    ir_para(page, "overview")
    expect(page.locator("#pageTitle")).to_have_text("Visão geral")
    expect(page.locator("#appStatus")).not_to_have_text("")
    expect(page.locator("#checklist")).to_contain_text("concluídos", timeout=15000)

    ir_para(page, "config")
    expect(page.locator("#pageTitle")).to_have_text("Configuração")
    expect(page.locator("#iaProvedores")).not_to_contain_text("Carregando...", timeout=15000)

    ir_para(page, "analysis")
    expect(page.locator("#pageTitle")).to_have_text("Análises")
    page.wait_for_function("() => document.getElementById('jobs').innerHTML.length > 0")

    ir_para(page, "mesa")
    expect(page.locator("#pageTitle")).to_have_text("Mesa de Agentes")
    expect(page.locator("#deskOut")).to_contain_text("(10 perfis)", timeout=20000)

    ir_para(page, "manual")
    expect(page.locator("#pageTitle")).to_have_text("Minhas Operações")
    expect(page.locator("#sinaisCopiloto")).to_be_visible(timeout=15000)

    ir_para(page, "floor")
    expect(page.locator("#pageTitle")).to_have_text("Trading Floor")
    frame = page.frame_locator("iframe.floor-frame")
    expect(frame.locator("h1")).to_contain_text("TRADING FLOOR", timeout=15000)
    expect(frame.locator(".mode")).to_contain_text("PAPER MODE")

    ir_para(page, "risk")
    expect(page.locator("#pageTitle")).to_have_text("Risco")
    expect(page.locator("#shadowHealth")).to_contain_text("Ordens enviadas", timeout=15000)
    page.screenshot(path=str(evidence_dir / "fm6-01-avancado-todas-paginas.png"))

    assert erros_5xx == [], f"respostas 5xx durante a varredura do Avançado: {erros_5xx}"

    # "Simplificar ≠ remover": o Simples volta a três páginas, sem perder nada.
    page.select_option("#uiModo", "simples")
    expect(page.locator("#pageTitle")).to_have_text("Início")
    expect(page.locator("#sideNav button")).to_have_count(3)


# ------------------------------------------------------------------- item 02
def test_fm6_02_corretoras_catalogo_capacidades_reconciliacao(page, mesa_url, senha, evidence_dir):
    # §11 "Corretoras" + §3: catálogo local sem credenciais/rede, capacidades
    # anunciadas como NÃO verificadas, execução pelo painel desligada e
    # reconciliação pelo portão real — nada aqui ativa dinheiro real.
    entrar_como_dono(page, mesa_url, senha)
    modo_avancado(page)
    ir_para(page, "config")

    expect(page.locator("#page-config")).to_contain_text("Live não é aceito por esta versão")

    page.get_by_role("button", name=re.compile("Catálogo e capacidades")).click()
    cat = page.locator("#brokerCatalogo")
    expect(cat).to_contain_text("Salas fixas", timeout=15000)
    expect(cat).to_contain_text("Binance")
    expect(cat).to_contain_text("planejada, não validada")
    expect(cat).to_contain_text("Adaptador CCXT instalado nesta máquina")
    expect(cat).to_contain_text("Execução de ordens pelo painel: desligada")

    cat.locator("button", has_text="Capacidades").first.click()
    det = page.locator("#brokerDetalhe")
    expect(det).to_contain_text("sem credenciais e sem rede", timeout=15000)
    expect(det).to_contain_text("UNVERIFIED")
    expect(det).to_contain_text("verificado: não")
    expect(det).to_contain_text("operação real: não")

    # Validar configuração prepara e testa o sandbox SEM chaves. Em ambiente
    # isolado o testnet é honestamente operacional ("chaves: ausentes",
    # "ordens de mentira, risco zero"); se o servidor recusar, o fail-closed
    # ("não pronta") também é resposta honesta. O que nunca é aceito é
    # fingir credencial ou fingir verificação.
    page.get_by_role("button", name="Validar configuração").click()
    expect(det).to_contain_text(re.compile(r"chaves: ausentes|não pronta"), timeout=15000)
    expect(det).to_contain_text(re.compile(r"testnet: ordens de mentira, risco zero|não pronta"))

    page.get_by_role("button", name="Reconciliar com a corretora").click()
    expect(det).to_contain_text("Reconciliação com a corretora", timeout=15000)
    expect(det).to_contain_text(re.compile(r"portão: (LIBERADO|FECHADO)"))
    expect(det).to_contain_text("ordens conferidas")
    page.screenshot(path=str(evidence_dir / "fm6-02-corretoras.png"))


# ------------------------------------------------------------------- item 03
def test_fm6_03_escola_central_no_painel(page, mesa_url, senha, evidence_dir):
    # §11 "Escola": a Escola Central abre no painel autenticado com contextos
    # de certificação, salas isoladas por corretora/conta/mercado, pausa e
    # exposição global do Maestro e pilotos — com a autorização declarada
    # como ato exclusivo do proprietário.
    entrar_como_dono(page, mesa_url, senha)
    modo_avancado(page)
    ir_para(page, "mesa")
    expect(page.locator("#deskOut")).to_contain_text("(10 perfis)", timeout=20000)

    page.get_by_role("button", name="Escola Central").click()
    escola = page.locator("#deskMsg")
    expect(escola).to_contain_text("Escola Central", timeout=15000)
    expect(escola).to_contain_text("Contextos de certificação")
    expect(escola).to_contain_text("Salas por corretora (isolamento financeiro)")
    expect(escola).to_contain_text("Maestro: pausa global")
    expect(escola).to_contain_text("Maestro: exposição global")
    expect(escola).to_contain_text(re.compile(r"Pilotos \(modo"))
    expect(escola).not_to_contain_text("falhou")
    page.screenshot(path=str(evidence_dir / "fm6-03-escola.png"))


def test_fm6_03b_escola_falha_visivel_e_tentativa_de_novo(page, mesa_url, senha, evidence_dir):
    # V9-UI-01: o run F0 deixou #deskMsg em branco por 15 s quando uma das
    # três consultas da Escola falhou (toast efêmero sumia e sobrava painel
    # vazio — indistinguível de "nunca cliquei"). A falha injetada aqui é
    # determinística: uma rota responde 500. O painel PRECISA mostrar o que
    # falhou, sem alterar nada, e "Tentar de novo" deve recuperar o painel
    # completo quando a consulta volta a funcionar.
    entrar_como_dono(page, mesa_url, senha)
    modo_avancado(page)
    ir_para(page, "mesa")
    expect(page.locator("#deskOut")).to_contain_text("(10 perfis)", timeout=20000)

    def falhar_pilotos(route):
        route.fulfill(
            status=500,
            content_type="application/json",
            body='{"detail":"falha injetada no teste (pilotos)"}',
        )

    page.route("**/v1/escola/pilotos", falhar_pilotos)
    page.get_by_role("button", name="Escola Central").click()
    escola = page.locator("#deskMsg")
    expect(escola).to_contain_text("não respondeu", timeout=15000)
    expect(escola).to_contain_text("pilotos")
    expect(escola).to_contain_text("falha injetada no teste")
    expect(escola).to_contain_text("Nada foi alterado")
    expect(escola.get_by_role("button", name="Tentar de novo")).to_be_visible()
    page.screenshot(path=str(evidence_dir / "fm6-03b-escola-erro-visivel.png"))

    page.unroute("**/v1/escola/pilotos", falhar_pilotos)
    escola.get_by_role("button", name="Tentar de novo").click()
    expect(escola).to_contain_text("Contextos de certificação", timeout=15000)
    expect(escola).to_contain_text("Maestro: pausa global")
    expect(escola).not_to_contain_text("não respondeu")
    page.screenshot(path=str(evidence_dir / "fm6-03b-escola-retomada.png"))


# ------------------------------------------------------------------- item 04
def test_fm6_04_dashboard_lc_mercado_e_portfolio(page, mesa_url, senha, evidence_dir):
    # §11 "Trading Dashboard" + §6: a biblioteca profissional carrega NO APP
    # INSTALADO (vendorizada, sem rede) e a sala do trader desenha DOIS
    # gráficos reais — mercado (candles/volume/S-R) e portfólio do ledger.
    entrar_como_dono(page, mesa_url, senha)
    modo_avancado(page)
    ir_para(page, "mesa")
    expect(page.locator("#deskOut")).to_contain_text("(10 perfis)", timeout=20000)

    page.get_by_role("button", name="Sala", exact=True).first.click()
    expect(page.locator("#salaTrader")).to_be_visible()
    saida = page.locator("#salaTraderOut")
    expect(saida).not_to_contain_text("Carregando a sala", timeout=20000)
    expect(saida).not_to_contain_text("sala indisponível")

    lc_carregada = page.evaluate(
        "() => typeof window.LightweightCharts === 'object' "
        "&& typeof window.LightweightCharts.createChart === 'function'"
    )
    assert lc_carregada, "Lightweight Charts não carregou no app (script vendorizado)"

    # Mercado: a fonte entrega candles/linha sempre — o canvas do LC tem de
    # aparecer (o SVG é só a queda para navegador sem a biblioteca).
    mercado = page.locator("#lcMercado")
    expect(mercado).to_be_visible(timeout=15000)
    assert mercado.locator("canvas").count() >= 1, "gráfico de mercado sem canvas (LC não desenhou)"

    # Portfólio: com operações fechadas o LC desenha (canvas); SEM operações
    # fechadas o app cai na queda HONESTA ("sem operações fechadas — nada a
    # desenhar") — uma posição aberta é expectativa, não resultado. Os dois
    # desfechos são legítimos; caixa vazia não é.
    page.wait_for_function(
        """() => {
            const out = document.querySelector('#salaTraderOut');
            if (!out) return false;
            const lc = out.querySelector('#lcPortfolio');
            if (lc && lc.querySelector('canvas')) return true;
            return /sem operações fechadas|uma operação fechada/.test(out.textContent || '');
        }""",
        timeout=15000,
    )
    # A leitura por IA lê o gráfico por séries estruturadas e nunca some.
    expect(page.locator("#salaLeituraIAOut")).to_be_visible()
    page.screenshot(path=str(evidence_dir / "fm6-04-dashboard-lc.png"))

    page.get_by_role("button", name="Fechar", exact=True).click()
    expect(page.locator("#salaTrader")).to_be_hidden()

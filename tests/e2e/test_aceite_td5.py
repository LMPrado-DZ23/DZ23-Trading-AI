"""Matriz de ACEITE do TD-5 (missão V8 §16/§18 + V8.1 §13) — navegador real.

Doze itens de dashboard/UX + o fluxo do iniciante em dez passos, cobrados no
app de verdade. O MESMO arquivo roda em dois alvos:

1. Instância local (padrão): o `live_server` do conftest sobe o uvicorn com
   DATA_DIR temporário — `uv run --with playwright pytest tests/e2e -q`.
2. Instância EXTERNA já em execução (bundle Windows extraído em ambiente
   isolado, servidor na Oracle): defina `DZ23_E2E_BASE_URL` — nenhum
   processo é spawned e os mesmos checks rodam contra o alvo:

       DZ23_E2E_BASE_URL=http://127.0.0.1:8012 \
       DZ23_E2E_OWNER_PASSWORD=... \
       DZ23_E2E_EVIDENCE_DIR=docs/mission/evidence/v8-td5-exe \
       pytest tests/e2e/test_aceite_td5.py -q

Nota de ORDEM declarada: o item 08 (Centro de Alertas) verifica na caixa de
entrada o alerta de stop gerado no item 07 — a fila durável é o mesmo
sistema, e a prova encadeada é intencional. Rodas `-k` isolado, gere antes
o alerta ou aceite que o item 08 exija o 07.

Os helpers de login/navegação são locais de propósito: este arquivo precisa
ser auto-suficiente para executar contra um alvo externo sem depender de
outros módulos de teste.
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


_JS_CULPADOS = """
() => {
  const w = document.documentElement.clientWidth;
  const fixo = (el) => {
    let p = el;
    for (let i = 0; i < 4 && p; i++) { if (getComputedStyle(p).position === 'fixed') return true; p = p.parentElement; }
    return false;
  };
  const out = [];
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (r.right > w + 1 && r.width > 0 && !fixo(el)) {
      let s = el.tagName.toLowerCase();
      if (el.id) s += '#' + el.id;
      if (el.className && typeof el.className === 'string') s += '.' + el.className.split(' ').slice(0,2).join('.');
      out.push(s + ' right=' + Math.round(r.right) + ' txt=' + (el.textContent || '').trim().slice(0, 40));
    }
  }
  return out.slice(0, 12);
}
"""


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


# ---------------------------------------------------------------- item 01
def test_aceite_td5_01_inicio_assistente_conectar_modo_play(page, mesa_url, senha, evidence_dir):
    # §11/§18: o fluxo principal é CONECTAR → ESCOLHER DEMO/REAL/COPILOTO →
    # PLAY; REAL aparece, mas BLOQUEADO até autorização explícita.
    entrar_como_dono(page, mesa_url, senha)

    expect(page.locator("#page-inicio")).to_have_class(re.compile(r"\bactive\b"))
    inicio = page.locator("#page-inicio")
    expect(inicio).to_contain_text("1 · CONECTAR")
    expect(inicio).to_contain_text("2 · MODO")
    expect(inicio).to_contain_text("3 · PLAY")
    assert page.locator("#iniModoReal").is_disabled(), "REAL não pode estar selecionável"
    expect(inicio).to_contain_text("BLOQUEADO")
    expect(page.locator("#btnInicioPlay")).to_be_visible()
    expect(page.locator("#iniConectarEstado")).not_to_contain_text("Carregando...", timeout=15000)
    expect(page.locator("#iniMesaEstado")).not_to_have_text("Carregando...", timeout=15000)

    page.screenshot(path=str(evidence_dir / "td5-01-inicio.png"))


# ---------------------------------------------------------------- item 02
def test_aceite_td5_02_painel_global_maestro(page, mesa_url, senha, evidence_dir):
    # §10: patrimônio virtual por modo/moeda com taxas e livros; PAPER,
    # TESTNET e LIVE NUNCA somados.
    entrar_como_dono(page, mesa_url, senha)
    modo_avancado(page)
    ir_para(page, "mesa")

    painel = page.locator("#painelGlobal")
    expect(painel).to_be_visible()
    out = page.locator("#painelGlobalOut")
    expect(out).not_to_contain_text("Carregando...", timeout=20000)
    expect(out).to_contain_text(re.compile(r"PAPER|nenhuma"))
    expect(painel).to_contain_text("nunca são somados")
    expect(painel).to_contain_text("o proprietário")

    page.screenshot(path=str(evidence_dir / "td5-02-painel-global.png"))


# ---------------------------------------------------------------- item 03
def test_aceite_td5_03_sala_do_trader_com_grafico(page, mesa_url, senha, evidence_dir):
    # Mesa individual (§9): gráfico de mercado + portfólio do ledger; nada
    # de números inventados; LIVE permanece bloqueado.
    entrar_como_dono(page, mesa_url, senha)
    modo_avancado(page)
    ir_para(page, "mesa")

    desk = page.locator("#deskOut")
    expect(desk).to_contain_text("(10 perfis)", timeout=20000)

    botoes_sala = page.get_by_role("button", name="Sala", exact=True)
    assert botoes_sala.count() == 10, f"esperadas 10 salas, achadas {botoes_sala.count()}"
    botoes_sala.first.click()

    sala = page.locator("#salaTrader")
    expect(sala).to_be_visible()
    saida = page.locator("#salaTraderOut")
    expect(saida).not_to_contain_text("Carregando a sala", timeout=20000)
    expect(saida).not_to_contain_text("sala indisponível")
    expect(page.locator("#salaTraderTitulo")).to_contain_text("v")
    # Gráfico profissional (canvas do Lightweight Charts vendored).
    assert page.locator("#salaTraderOut canvas").count() >= 1, (
        "sala sem gráfico renderizado (canvas do Lightweight Charts)"
    )
    expect(sala).to_contain_text("LIVE permanece bloqueado")

    page.screenshot(path=str(evidence_dir / "td5-03-sala-trader.png"))
    page.get_by_role("button", name="Fechar", exact=True).click()
    expect(sala).to_be_hidden()


# ---------------------------------------------------------------- item 04
def test_aceite_td5_04_analise_do_copiloto(page, mesa_url, senha, evidence_dir):
    # §3 passo 2-3: análise com dados IDENTIFICADOS — oportunidade, riscos,
    # custos (NUNCA estimados), horizonte, invalidação e fonte rotulada.
    entrar_como_dono(page, mesa_url, senha)
    ir_para(page, "manual")

    expect(page.locator("#sinaisCopiloto")).to_be_visible()
    page.fill("#sigInstr", "BTC/USDT")
    page.select_option("#sigTf", "1h")
    page.get_by_role("button", name="Analisar mercado").click()

    out = page.locator("#sinaisOut")
    expect(out).to_contain_text("Preço de referência", timeout=15000)
    expect(out).to_contain_text("158.5")
    expect(out).to_contain_text("Custos")
    expect(out).to_contain_text(re.compile(r"NUNCA estima custos", re.I))
    expect(out).to_contain_text("Riscos")
    expect(out).to_contain_text("fixture")
    # Gate determinístico do RSI: preço esticado não vira "oportunidade".
    expect(out).to_contain_text(re.compile(r"não é oportunidade"))
    expect(page.get_by_role("button", name="Usar esta análise no registro")).to_be_visible()

    page.screenshot(path=str(evidence_dir / "td5-04-analise-copiloto.png"))


# ---------------------------------------------------------------- item 05
def test_aceite_td5_05_botao_acompanhar_visivel_com_prefill(page, mesa_url, senha, evidence_dir):
    # §4: o botão "Acompanhar esta operação" precisa estar visível e
    # funcional; a análise alimenta o registro (prefill) — e o copiloto
    # nunca cria/cancela ordens.
    entrar_como_dono(page, mesa_url, senha)
    ir_para(page, "manual")

    page.fill("#sigInstr", "BTC/USDT")
    page.get_by_role("button", name="Analisar mercado").click()
    expect(page.locator("#sinaisOut")).to_contain_text("Preço de referência", timeout=15000)

    page.get_by_role("button", name="Usar esta análise no registro").click()
    expect(page.locator("#toast")).to_contain_text("Análise levada ao registro", timeout=10000)
    assert page.locator("#manInstr").input_value() == "BTC/USDT"
    assert "análise do copiloto" in page.locator("#manTese").input_value()

    botao = page.get_by_role("button", name="Acompanhar esta operação")
    expect(botao).to_be_visible()
    caixa = botao.bounding_box()
    assert caixa and caixa["width"] > 0 and caixa["height"] > 0
    expect(page.locator("#page-manual")).to_contain_text("nunca cria ou cancela ordens")

    page.screenshot(path=str(evidence_dir / "td5-05-acompanhar-prefill.png"))


# ---------------------------------------------------------------- item 06
def test_aceite_td5_06_registro_posicao_e_diario(page, mesa_url, senha, evidence_dir):
    # §3 passos 4-8 e 11: registra o que JÁ foi executado (preço real,
    # quantidade, taxas, horário), acompanha, registra saída parcial e o
    # resultado alimenta o diário.
    entrar_como_dono(page, mesa_url, senha)
    ir_para(page, "manual")

    page.fill("#manInstr", "ACEITE6")
    page.select_option("#manDir", "compra")
    page.fill("#manQtd", "5")
    page.fill("#manPreco", "10.50")
    page.fill("#manStop", "9.90")
    page.fill("#manCustos", "1.25")
    page.fill("#manTese", "aceite TD-5: registrar o que foi executado de verdade")
    page.get_by_role("button", name="Acompanhar esta operação").click()
    expect(page.locator("#toast")).to_contain_text(re.compile(r"registrada|já existia"), timeout=10000)

    card = page.locator("#manualLista .job", has_text="ACEITE6").first
    expect(card).to_contain_text(re.compile(r"COMPRA|LONG"), timeout=10000)
    card.get_by_role("button", name="Detalhes").click()

    detalhe = page.locator("#manualDetalhe")
    expect(detalhe).to_contain_text("Política de monitoramento", timeout=10000)
    expect(detalhe).to_contain_text("9.90")
    expect(detalhe).to_contain_text("1.25")
    expect(detalhe).to_contain_text("Registrar o que você executou")

    # Sem preço executado a execução NÃO é registrada (cotação de tela não substitui).
    page.select_option("#manEvTipo", "reducao")
    page.fill("#manEvQtd", "2")
    page.get_by_role("button", name="Registrar execução").click()
    expect(page.locator("#toast")).to_contain_text("obrigatório", timeout=5000)

    page.fill("#manEvPreco", "11.20")
    page.get_by_role("button", name="Registrar execução").click()
    expect(page.locator("#toast")).to_contain_text(re.compile(r"Execução registrada|já existia"), timeout=10000)
    expect(page.locator("#manualDetalhe")).to_contain_text("reducao", timeout=10000)
    expect(page.locator("#manualResumo")).to_contain_text("aberta(s)")

    page.screenshot(path=str(evidence_dir / "td5-06-registro-diario.png"))


# ---------------------------------------------------------------- item 07
def test_aceite_td5_07_monitor_alerta_de_stop_objetivo(page, mesa_url, senha, evidence_dir):
    # §4/§5: monitor ativo emite ALERTA quando regra objetiva é acionada.
    # Com a fonte rotulada 'fixture', BTC/USDT vale 158.5: um stop declarado
    # em 159 para uma compra registrada a 160 JÁ foi cruzado — o alerta é
    # determinístico e auditável, sem depender de mercado vivo.
    entrar_como_dono(page, mesa_url, senha)
    modo_avancado(page)
    ir_para(page, "mesa")  # boota a mesa: a MESMA fonte alimenta o monitor
    ir_para(page, "manual")

    ligado_no_inicio = page.locator("#manualModo b").inner_text() == "LIGADO"
    if not ligado_no_inicio:
        page.get_by_role("button", name="Ligar copiloto").click()
        expect(page.locator("#manualModo")).to_contain_text("LIGADO", timeout=10000)

    page.fill("#manInstr", "BTC/USDT")
    page.select_option("#manDir", "compra")
    page.fill("#manQtd", "0.5")
    page.fill("#manPreco", "160")
    page.fill("#manStop", "159")
    page.fill("#manCustos", "0.20")
    page.fill("#manTese", "aceite TD-5: provar o alerta objetivo de stop")
    page.get_by_role("button", name="Acompanhar esta operação").click()
    expect(page.locator("#toast")).to_contain_text(re.compile(r"registrada|já existia"), timeout=10000)

    page.get_by_role("button", name="Verificar agora").click()
    # Dreno determinístico: a resposta do health chega depois e, se tardia,
    # sobrescreve o toast seguinte ("Marcar lido") — flake real de CI em 2026-10-14.
    expect(page.locator("#toast")).to_contain_text("Monitor verificado", timeout=15000)

    card = page.locator("#manualLista .job", has_text="BTC/USDT").first
    card.get_by_role("button", name="Detalhes").click()
    alertas = page.locator("#manualAlertas")
    expect(alertas).to_contain_text("Stop atingido", timeout=20000)
    expect(alertas).to_contain_text("cruzou seu stop 159")
    expect(page.locator("#manualMonitor")).not_to_contain_text("sem dados")

    page.screenshot(path=str(evidence_dir / "td5-07-alerta-stop.png"))

    page.get_by_role("button", name="Marcar lido").click()
    expect(page.locator("#toast")).to_contain_text("Alerta marcado como lido", timeout=10000)
    expect(alertas).to_contain_text("lido")

    if not ligado_no_inicio:
        page.get_by_role("button", name="Desligar", exact=True).click()
        expect(page.locator("#manualModo")).to_contain_text("DESLIGADO", timeout=10000)


# ---------------------------------------------------------------- item 08
def test_aceite_td5_08_centro_de_alertas(page, mesa_url, senha, evidence_dir):
    # §5: canais com estado honesto + Caixa de entrada (fila durável — o
    # alerta do item 07 vive aqui, mesmo sistema) + matriz de roteamento.
    entrar_como_dono(page, mesa_url, senha)
    modo_avancado(page)
    ir_para(page, "config")

    canais = page.locator("#notifCanais")
    expect(canais).not_to_contain_text("Carregando...", timeout=15000)
    expect(canais).to_contain_text(re.compile(r"telegram", re.I))
    expect(canais).to_contain_text(re.compile(r"configurado"))
    assert page.get_by_role("button", name="Testar", exact=True).count() >= 4, (
        "canais sem botão de teste real"
    )

    inbox = page.locator("#notifInbox")
    expect(inbox).not_to_contain_text("Carregando...", timeout=15000)
    # Ordem declarada no docstring: o stop do item 07 tem de estar na caixa.
    expect(inbox).to_contain_text("Stop atingido", timeout=10000)

    expect(page.locator("#notifMatriz")).to_be_visible()
    expect(page.get_by_role("button", name="Salvar matriz")).to_be_visible()
    expect(page.locator("#page-config")).to_contain_text("fila durável")

    page.screenshot(path=str(evidence_dir / "td5-08-centro-alertas.png"))


# ---------------------------------------------------------------- item 09
def test_aceite_td5_09_config_ia_provedores_modelos_consumo(page, mesa_url, senha, evidence_dir):
    # §6: catálogo de provedores, teste de autenticação, fallback, orçamento
    # e consumo com custo estimado — tudo honesto (sem chave NÃO finge).
    entrar_como_dono(page, mesa_url, senha)
    modo_avancado(page)
    ir_para(page, "config")

    provedores = page.locator("#iaProvedores")
    expect(provedores).not_to_contain_text("Carregando...", timeout=15000)
    for nome in ("OpenAI", "Anthropic", "Google Gemini", "DeepSeek", "Groq", "Mistral", "OpenRouter"):
        expect(provedores).to_contain_text(nome)
    expect(provedores).to_contain_text("Testar")

    assert page.locator("#TRADINGAGENTS_LLM_PROVIDER option").count() >= 8
    expect(page.locator("#TRADINGAGENTS_LLM_FALLBACK")).to_be_visible()
    expect(page.locator("#TRADINGAGENTS_IA_ORCAMENTO_USD")).to_be_visible()

    uso = page.locator("#iaUso")
    expect(uso).not_to_contain_text("Carregando...", timeout=15000)
    expect(page.locator("#page-config")).to_contain_text("Consumo e custo estimado")

    page.screenshot(path=str(evidence_dir / "td5-09-config-ia.png"))


# ---------------------------------------------------------------- item 10
def test_aceite_td5_10_mesa_dez_traders(page, mesa_url, senha, evidence_dir):
    # §9: dez perfis visíveis com estratégia/estágio; a fonte é declarada.
    entrar_como_dono(page, mesa_url, senha)
    modo_avancado(page)
    ir_para(page, "mesa")

    desk = page.locator("#deskOut")
    expect(desk).to_contain_text("(10 perfis)", timeout=20000)
    tabela = desk.locator("table").first
    assert tabela.locator("tbody tr").count() == 10
    assert page.get_by_role("button", name="Sala", exact=True).count() == 10
    expect(desk).to_contain_text(re.compile(r"fixture", re.I))
    expect(desk).to_contain_text(re.compile(r"RODANDO|PARADA"))

    page.screenshot(path=str(evidence_dir / "td5-10-mesa-dez-traders.png"))


# ---------------------------------------------------------------- item 11
def test_aceite_td5_11_pwa_instalacao_e_push_ui(page, mesa_url, senha, evidence_dir):
    # PWA §2/§4/§5: SW + manifest servidos, estado de push honesto, botão
    # Instalar NÃO finge capacidade, instruções de iPhone presentes.
    entrar_como_dono(page, mesa_url, senha)
    modo_avancado(page)
    ir_para(page, "config")

    expect(page.locator("#page-config")).to_contain_text("Aplicativo e Notificações Push")
    estado = page.locator("#pwaEstado")
    expect(estado).to_contain_text("aparelho(s) inscrito(s)", timeout=15000)
    expect(estado).to_contain_text(re.compile(r"servidor (com Web Push instalado|sem o extra \[push\])"))
    expect(page.locator("#btnPwaAtivar")).to_be_enabled()
    expect(page.locator("#page-config")).to_contain_text("Adicionar à Tela de Início")

    resposta_sw = page.request.get(f"{mesa_url}/sw.js")
    assert resposta_sw.status == 200
    assert resposta_sw.headers.get("service-worker-allowed") == "/"
    resposta_manifest = page.request.get(f"{mesa_url}/manifest.webmanifest")
    assert resposta_manifest.status == 200
    assert "manifest" in (resposta_manifest.headers.get("content-type") or "")

    # Sem oferta do navegador, o botão Instalar fica escondido (não finge).
    expect(page.locator("#btnPwaInstalar")).to_be_hidden()

    page.screenshot(path=str(evidence_dir / "td5-11-pwa-push.png"))


# ---------------------------------------------------------------- item 12
def test_aceite_td5_12_responsivo_mobile_sem_corte(page, mesa_url, senha, evidence_dir):
    # PWA §3: páginas principais usáveis no celular (390 e 320 px) sem
    # rolagem horizontal no documento — tabelas rolam DENTRO do card.
    entrar_como_dono(page, mesa_url, senha)
    # O seletor Simples/Avançado mora na sidebar, que some no celular:
    # ativa o Avançado em tela larga para o menu mobile listar todas as páginas.
    modo_avancado(page)

    def sem_rolagem(rotulo: str) -> None:
        estouro = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        if estouro > 1:
            culpados = page.evaluate(_JS_CULPADOS)
            raise AssertionError(f"rolagem horizontal em {rotulo}: +{estouro}px — culpados: {culpados}")

    page.set_viewport_size({"width": 390, "height": 844})
    ir_para(page, "inicio")
    sem_rolagem("inicio@390")
    ir_para(page, "manual")
    expect(page.locator("#sinaisCopiloto")).to_be_visible()
    sem_rolagem("manual@390")
    page.screenshot(path=str(evidence_dir / "td5-12-manual-390.png"))

    ir_para(page, "mesa")
    sem_rolagem("mesa@390")
    ir_para(page, "config")
    sem_rolagem("config@390")

    page.set_viewport_size({"width": 320, "height": 700})
    ir_para(page, "inicio")
    sem_rolagem("inicio@320")
    ir_para(page, "manual")
    expect(page.locator("#sigInstr")).to_be_visible()
    sem_rolagem("manual@320")
    expect(page.locator("#page-manual")).to_contain_text("Acompanhar esta operação")
    page.screenshot(path=str(evidence_dir / "td5-12-manual-320.png"))


# ---------------------------------------------------------------- fluxo iniciante
def test_aceite_td5_13_fluxo_iniciante_dez_passos(page, mesa_url, senha, evidence_dir):
    # Critério de aceite do proprietário, ponta a ponta, sem atalhos.
    # 1) Abrir o aplicativo e entrar.
    entrar_como_dono(page, mesa_url, senha)
    expect(page.locator("#page-inicio")).to_have_class(re.compile(r"\bactive\b"))
    page.screenshot(path=str(evidence_dir / "td5-13-01-abrir.png"))

    # 2) Modo DEMO selecionado; REAL bloqueado.
    expect(page.locator("#iniModoDemo")).to_be_checked()
    assert page.locator("#iniModoReal").is_disabled()

    # 3) Análise com fonte e preço de referência verificáveis.
    ir_para(page, "manual")
    page.fill("#sigInstr", "BTC/USDT")
    page.get_by_role("button", name="Analisar mercado").click()
    expect(page.locator("#sinaisOut")).to_contain_text("Preço de referência", timeout=15000)
    expect(page.locator("#sinaisOut")).to_contain_text("fixture")
    page.screenshot(path=str(evidence_dir / "td5-13-03-analise.png"))

    # 4) Registrar a posição JÁ executada (preço REAL que o dono executou).
    page.get_by_role("button", name="Usar esta análise no registro").click()
    expect(page.locator("#toast")).to_contain_text("Análise levada ao registro", timeout=10000)
    assert page.locator("#manInstr").input_value() == "BTC/USDT"
    page.fill("#manQtd", "0.5")
    page.fill("#manPreco", "160.25")
    page.fill("#manStop", "159")
    page.fill("#manCustos", "0.30")
    page.get_by_role("button", name="Acompanhar esta operação").click()
    expect(page.locator("#toast")).to_contain_text(re.compile(r"registrada|já existia"), timeout=10000)

    # 5) O acompanhamento é PERSISTENTE: recarrega a página e continua lá.
    page.reload()
    expect(page.locator("#app")).to_be_visible(timeout=15000)
    ir_para(page, "manual")
    expect(page.locator("#manualLista .job", has_text="160.25").first).to_be_visible(timeout=15000)
    page.screenshot(path=str(evidence_dir / "td5-13-05-persistencia.png"))

    # 6) Os dez traders na mesa (e a mesa boota a fonte rotulada do monitor).
    modo_avancado(page)
    ir_para(page, "mesa")
    expect(page.locator("#deskOut")).to_contain_text("(10 perfis)", timeout=20000)

    # 7) Ligar o monitor e receber alerta quando regra objetiva for acionada.
    ir_para(page, "manual")
    ligado_no_inicio = page.locator("#manualModo b").inner_text() == "LIGADO"
    if not ligado_no_inicio:
        page.get_by_role("button", name="Ligar copiloto").click()
        expect(page.locator("#manualModo")).to_contain_text("LIGADO", timeout=10000)
    page.get_by_role("button", name="Verificar agora").click()
    # Mesmo dreno do item 07: o health tardio sobrescrevia "Execução
    # registrada." no passo 8 (flake real, run CI 38604619895).
    expect(page.locator("#toast")).to_contain_text("Monitor verificado", timeout=15000)
    card = page.locator("#manualLista .job", has_text="160.25").first
    card.get_by_role("button", name="Detalhes").click()
    expect(page.locator("#manualAlertas")).to_contain_text("Stop atingido", timeout=20000)
    expect(page.locator("#manualAlertas")).to_contain_text("cruzou seu stop 159")
    page.screenshot(path=str(evidence_dir / "td5-13-07-alerta.png"))

    # 8) Registrar a saída parcial com o preço real executado.
    page.select_option("#manEvTipo", "reducao")
    page.fill("#manEvQtd", "0.25")
    page.fill("#manEvPreco", "161.10")
    page.get_by_role("button", name="Registrar execução").click()
    expect(page.locator("#toast")).to_contain_text(re.compile(r"Execução registrada|já existia"), timeout=10000)

    # 9) Resultado, custos e histórico visíveis e persistidos no servidor.
    detalhe = page.locator("#manualDetalhe")
    expect(detalhe).to_contain_text("reducao", timeout=10000)
    expect(detalhe).to_contain_text("custos total")
    expect(detalhe).to_contain_text("lucro")
    # Autenticação do painel é Bearer em sessionStorage (não cookie): a
    # conferência de persistência roda DENTRO da página, com o token real.
    estado_api = page.evaluate(
        """async () => {
            const r = await fetch('/v1/manual/positions', {
                headers: {'Authorization': 'Bearer ' + sessionStorage.getItem('dz23_token')},
            });
            return {status: r.status, corpo: r.ok ? await r.json() : null};
        }"""
    )
    assert estado_api["status"] == 200, f"API recusou: {estado_api['status']}"
    assert any(p["instrumento"] == "BTC/USDT" for p in (estado_api["corpo"] or {}).get("posicoes", [])), (
        "posição não persistida no servidor"
    )
    page.screenshot(path=str(evidence_dir / "td5-13-09-resultado.png"))

    # 10) Devolve o estado: o copiloto volta a desligado se não estava ligado.
    if not ligado_no_inicio:
        page.get_by_role("button", name="Desligar", exact=True).click()
        expect(page.locator("#manualModo")).to_contain_text("DESLIGADO", timeout=10000)

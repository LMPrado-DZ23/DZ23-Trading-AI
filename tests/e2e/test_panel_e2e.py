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
    ("inicio", "Início"),
    ("config", "Configuração"),
    ("analysis", "Análises"),
    ("mesa", "Mesa de Agentes"),
    ("manual", "Minhas Operações"),
    ("floor", "Trading Floor"),
    ("risk", "Risco"),
    ("overview", "Visão geral"),
]


def entrar_como_dono(page, base_url: str, senha: str) -> None:
    page.goto(f"{base_url}/app")
    expect(page.locator("#authTitle")).to_have_text("Entrar na sua mesa")
    expect(page.locator("#loginTenant")).to_have_count(0)
    page.fill("#loginPassword", senha)
    page.get_by_role("button", name="Entrar", exact=True).click()
    expect(page.locator("#app")).to_be_visible(timeout=15000)


def modo_avancado(page) -> None:
    page.select_option("#uiModo", "avancado")


def ver_overview(page) -> None:
    page.locator('[data-page="overview"]').first.click()
    expect(page.locator("#page-overview")).to_have_class(re.compile(r"\bactive\b"))


def test_dono_entra_e_visao_geral_paper(page, live_server, owner_password, evidence_dir):
    entrar_como_dono(page, live_server, owner_password)
    ver_overview(page)

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
    ver_overview(page)
    expect(page.locator("#appStatus")).to_have_text("READY", timeout=15000)


def test_menu_completo_formularios_e_sem_erro_5xx(page, live_server, owner_password):
    erros_5xx: list[str] = []

    def registrar(resp) -> None:
        if resp.status >= 500:
            erros_5xx.append(f"{resp.status} {resp.url}")

    page.on("response", registrar)
    entrar_como_dono(page, live_server, owner_password)
    modo_avancado(page)

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
    modo_avancado(page)

    page.locator('[data-page="mesa"]').first.click()
    page.get_by_role("button", name="Camada quant/IA").click()
    expect(page.locator("#deskMsg")).to_contain_text("nenhum modelo de IA envia ordens")
    expect(page.locator("#deskMsg")).to_contain_text("Modelo")

    page.screenshot(path=str(evidence_dir / "03-mesa-quant.png"))


def test_floor_embutido_same_origin(page, live_server, owner_password, evidence_dir):
    entrar_como_dono(page, live_server, owner_password)
    modo_avancado(page)

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


def test_login_premium_desktop_identidade_e_estado(page, live_server, evidence_dir):
    # O portão precisa parecer aplicativo pessoal, não página de SaaS: o
    # símbolo DZ23 com "Trading AI" ao lado, badges que espelham o estado
    # real do backend e nenhuma menção a arquivo de senha. O aviso de erro
    # precisa ser anunciado (aria-live).
    page.goto(f"{live_server}/app")
    expect(page.locator("#loginForm")).to_be_visible(timeout=15000)
    expect(page.locator(".auth-wordmark")).to_have_text("Trading AI")
    expect(page.locator("#authMsg")).to_have_attribute("role", "status")
    expect(page.locator("#authMsg")).to_have_attribute("aria-live", "polite")

    badges = page.locator("#gateBadges")
    expect(badges).to_contain_text("PAPER MODE", timeout=10000)
    expect(badges).to_contain_text("EXECUÇÃO GOVERNADA")
    expect(badges).to_contain_text("DINHEIRO REAL BLOQUEADO")

    texto = page.locator("#authGate").inner_text()
    assert "MULTI-TENANT" not in texto
    assert "AGENCY" not in texto
    assert "owner_password" not in texto
    assert "DZ23 Trading AI" not in texto, "o símbolo já contém DZ23; não repetir a marca abaixo"

    page.screenshot(path=str(evidence_dir / "00-login-premium-desktop.png"))


def test_primeira_execucao_assistente_cria_senha_e_codigo(page, setup_server, evidence_dir):
    # Instalação nova: nada de arquivo para editar — o assistente visual cria
    # a senha, mostra o código de recuperação UMA vez e só então abre o painel.
    nova_senha = "s3nh4-nova-do-dono"
    page.goto(f"{setup_server}/app")
    expect(page.locator("#setupForm")).to_be_visible(timeout=15000)
    expect(page.locator("#setupForm")).to_contain_text("Primeira configuração")
    expect(page.locator("#loginForm")).to_be_hidden()

    page.fill("#setupPassword", nova_senha)
    page.fill("#setupPassword2", nova_senha)
    page.get_by_role("button", name="Criar senha e entrar").click()

    expect(page.locator("#codigoForm")).to_be_visible(timeout=10000)
    codigo = page.locator("#codigoValor").inner_text().strip()
    assert re.match(r"^[A-Z2-9]{5}(-[A-Z2-9]{5}){5}$", codigo), codigo
    page.screenshot(path=str(evidence_dir / "06-codigo-recuperacao.png"))

    page.check("#codigoConfirmado")
    page.get_by_role("button", name="Abrir o painel").click()
    expect(page.locator("#app")).to_be_visible(timeout=15000)
    ver_overview(page)
    expect(page.locator("#appStatus")).to_have_text("READY", timeout=15000)

    # Recuperação: sem sessão, "Esqueci minha senha" + código troca a senha.
    page.evaluate("sessionStorage.removeItem('dz23_token')")
    page.reload()
    expect(page.locator("#loginForm")).to_be_visible(timeout=15000)
    page.get_by_role("button", name="Esqueci minha senha").click()
    expect(page.locator("#recoverForm")).to_be_visible()

    page.fill("#recoverCodigo", codigo)
    page.fill("#recoverSenha", "s3nh4-trocada-2026")
    page.get_by_role("button", name="Trocar senha").click()

    expect(page.locator("#codigoForm")).to_be_visible(timeout=10000)
    codigo_novo = page.locator("#codigoValor").inner_text().strip()
    assert codigo_novo != codigo, "o código usado precisa morrer"
    page.check("#codigoConfirmado")
    page.get_by_role("button", name="Abrir o painel").click()
    expect(page.locator("#app")).to_be_visible(timeout=15000)

    velha = page.request.get(
        f"{setup_server}/v1/auth/me", headers={"Authorization": f"Bearer {nova_senha}"}
    )
    assert velha.status == 401, "a senha antiga não pode continuar valendo"
    nova = page.request.get(
        f"{setup_server}/v1/auth/me", headers={"Authorization": "Bearer s3nh4-trocada-2026"}
    )
    assert nova.status == 200


def test_login_mobile_390x844(mobile_page, live_server, owner_password, evidence_dir):
    # O mesmo painel precisa funcionar no celular (acesso remoto à Oracle).
    page = mobile_page
    page.goto(f"{live_server}/app")
    expect(page.locator("#loginForm")).to_be_visible(timeout=15000)
    expect(page.locator("#authTitle")).to_have_text("Entrar na sua mesa")
    page.screenshot(path=str(evidence_dir / "04-login-mobile.png"))

    page.fill("#loginPassword", owner_password)
    page.get_by_role("button", name="Entrar", exact=True).click()
    expect(page.locator("#app")).to_be_visible(timeout=15000)
    expect(page.locator("#pageTitle")).to_have_text("Início")
    expect(page.locator("#page-inicio")).to_contain_text("3 · PLAY", timeout=15000)
    page.screenshot(path=str(evidence_dir / "05-painel-mobile.png"))


def test_login_responsivo_sem_corte_nem_rolagem_horizontal(page, live_server, evidence_dir):
    # O portão precisa caber inteiro nas larguras reais de uso (desktop,
    # notebook, tablet e celular estreito): nada de campo/botão/erro cortado
    # e nada de rolagem horizontal acidental.
    tamanhos = [(1920, 1080), (1366, 768), (768, 1024), (390, 844), (360, 640), (320, 568)]
    for largura, altura in tamanhos:
        page.set_viewport_size({"width": largura, "height": altura})
        page.goto(f"{live_server}/app")
        expect(page.locator("#loginForm")).to_be_visible(timeout=15000)

        estouro = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        assert estouro <= 1, f"rolagem horizontal em {largura}x{altura}: +{estouro}px"

        campo = page.locator("#loginPassword").bounding_box()
        assert campo, f"campo de senha invisível em {largura}x{altura}"
        assert campo["x"] >= 0 and campo["x"] + campo["width"] <= largura + 1, (
            f"campo de senha cortado em {largura}x{altura}"
        )

        botao = page.get_by_role("button", name="Entrar", exact=True).bounding_box()
        assert botao and botao["x"] + botao["width"] <= largura + 1, (
            f"botão Entrar cortado em {largura}x{altura}"
        )

        page.get_by_role("button", name="Entrar", exact=True).click()
        expect(page.locator("#authMsg")).to_contain_text("Informe a senha do dono", timeout=5000)
        aviso = page.locator("#authMsg").bounding_box()
        assert aviso and aviso["x"] + aviso["width"] <= largura + 1, (
            f"mensagem de erro cortada em {largura}x{altura}"
        )

        page.screenshot(path=str(evidence_dir / f"07-login-{largura}x{altura}.png"))


def test_pwa_service_worker_ativo_e_estado_honesto(page, live_server, owner_password, evidence_dir):
    # O PWA é o painel EXISTENTE instalável: em localhost (contexto seguro)
    # o Service Worker se registra, o card mostra o estado REAL do push e o
    # botão Instalar jamais aparece sem o navegador oferecer a capacidade.
    entrar_como_dono(page, live_server, owner_password)
    modo_avancado(page)
    page.locator('[data-page="config"]').first.click()
    expect(page.locator("#page-config")).to_have_class(re.compile(r"\bactive\b"))

    resposta = page.request.get(f"{live_server}/sw.js")
    assert resposta.status == 200
    assert resposta.headers.get("service-worker-allowed") == "/"

    # O painel registra o SW sozinho ao entrar (updateViaCache none).
    page.wait_for_function(
        "navigator.serviceWorker.getRegistration('/').then(r => !!r)",
        timeout=15000)

    expect(page.locator("#page-config")).to_contain_text("Aplicativo e Notificações Push")
    estado = page.locator("#pwaEstado")
    expect(estado).to_contain_text("VAPID", timeout=15000)
    expect(estado).to_contain_text("aparelho(s) inscrito(s)")
    expect(estado).to_contain_text("permissão de notificação:")
    # Honestidade do servidor: ou tem o extra [push], ou diz que não tem.
    expect(estado).to_contain_text(re.compile(r"servidor (com Web Push instalado|sem o extra \[push\])"))
    # Em localhost a capacidade existe: o botão de ativar fica habilitado.
    expect(page.locator("#btnPwaAtivar")).to_be_enabled()

    page.screenshot(path=str(evidence_dir / "08-pwa-config.png"))


def test_minhas_operacoes_copiloto_manual_ponta_a_ponta(page, live_server, owner_password, evidence_dir):
    # E4: o dono registra o que JÁ executou na corretora dele; o copiloto
    # acompanha, calcula e alerta. Nada nesta página executa ordem — e o
    # preço executado é obrigatório (cotação de tela nunca substitui).
    entrar_como_dono(page, live_server, owner_password)

    page.locator('[data-page="manual"]').first.click()
    expect(page.locator("#page-manual")).to_have_class(re.compile(r"\bactive\b"))
    # Regressão pinada: a página precisa viver na coluna de conteúdo
    # (main.content), nunca na faixa da sidebar — um </div> sobrando na
    # Configuração já jogou páginas inteiras para trás da sidebar.
    caixa = page.locator("#page-manual").bounding_box()
    assert caixa and caixa["x"] >= 245, f"#page-manual fora da coluna de conteúdo: {caixa}"
    assert page.evaluate("!!document.querySelector('#page-manual').closest('main.content')")
    expect(page.locator("#pageTitle")).to_have_text("Minhas Operações")
    expect(page.locator("#page-manual")).to_contain_text("ele nunca opera por você")
    expect(page.locator("#manualModo")).to_contain_text(re.compile(r"LIGADO|DESLIGADO"))

    # Regressão: "LIGADO" é substring de "DESLIGADO" — a detecção precisa
    # ler o <b> exato, senão o desligar de limpeza nunca acontece e o
    # copiloto contamina os testes seguintes (409 no PLAY da mesa).
    expect(page.locator("#manualModo b")).to_have_text(re.compile(r"^(LIGADO|DESLIGADO)$"))
    ligado_no_inicio = page.locator("#manualModo b").inner_text() == "LIGADO"

    # 1) Registra a posição que o dono JÁ executou na corretora dele
    page.fill("#manInstr", "PETR4")
    page.select_option("#manDir", "compra")
    page.fill("#manQtd", "100")
    page.fill("#manPreco", "38.50")
    page.fill("#manStop", "37.00")
    page.fill("#manAlvos", "40.00, 41.50")
    page.fill("#manTese", "rompimento com volume; sai se perder o suporte")
    # TD-4 (§4): o botão de registro agora se chama "Acompanhar esta operação"
    page.get_by_role("button", name="Acompanhar esta operação").click()
    expect(page.locator("#toast")).to_contain_text(re.compile(r"registrada|já existia"), timeout=10000)

    card = page.locator("#manualLista .job", has_text="PETR4").first
    # O domínio normaliza o lado para a convenção de trader (LONG/SHORT).
    expect(card).to_contain_text(re.compile(r"COMPRA|LONG"), timeout=10000)

    # 2) Liga o copiloto: a mesa autônoma para; o copiloto só acompanha
    page.get_by_role("button", name="Ligar copiloto").click()
    expect(page.locator("#manualModo")).to_contain_text("LIGADO", timeout=10000)

    # 3) Detalhes: política com o stop declarado, formulário de execução,
    #    diário e alertas do copiloto
    card.get_by_role("button", name="Detalhes").click()
    detalhe = page.locator("#manualDetalhe")
    expect(detalhe).to_contain_text("Política de monitoramento", timeout=10000)
    expect(detalhe).to_contain_text("37.00")
    expect(detalhe).to_contain_text("Registrar o que você executou")
    expect(page.locator("#manualAlertas")).to_be_visible()
    page.screenshot(path=str(evidence_dir / "08-minhas-operacoes.png"))

    # 4) Sem preço executado a execução NÃO é registrada
    page.select_option("#manEvTipo", "reducao")
    page.fill("#manEvQtd", "50")
    page.get_by_role("button", name="Registrar execução").click()
    expect(page.locator("#toast")).to_contain_text("obrigatório", timeout=5000)

    # 5) Com o preço real, a execução entra no diário da posição
    page.fill("#manEvPreco", "39.25")
    page.get_by_role("button", name="Registrar execução").click()
    expect(page.locator("#toast")).to_contain_text(re.compile(r"Execução registrada|já existia"), timeout=10000)
    expect(page.locator("#manualDetalhe")).to_contain_text("reducao", timeout=10000)

    # 6) Devolve o modo ao estado inicial (não contamina os outros testes)
    if not ligado_no_inicio:
        page.get_by_role("button", name="Desligar", exact=True).click()
        expect(page.locator("#manualModo")).to_contain_text("DESLIGADO", timeout=10000)


def test_modo_simples_assistente_conectar_modo_play(page, live_server, owner_password, evidence_dir):
    # SS-5 (diretriz definitiva de UX): o dono entra direto no assistente
    # Início; PLAY/PAUSAR usam a MESMA rota da mesa; o Avançado mostra todas
    # as páginas e o Simples volta a mostrar três — nada é removido.
    entrar_como_dono(page, live_server, owner_password)

    expect(page.locator("#pageTitle")).to_have_text("Início")
    pagina = page.locator("#page-inicio")
    expect(pagina).to_contain_text("1 · CONECTAR")
    expect(pagina).to_contain_text("2 · MODO")
    expect(pagina).to_contain_text("3 · PLAY")
    expect(pagina).to_contain_text("COPILOTO DE SINAIS")
    expect(pagina).to_contain_text("nunca envia ordens")
    expect(page.locator("#iniModoReal")).to_be_disabled()
    expect(page.locator("#sideNav button")).to_have_count(3)
    page.screenshot(path=str(evidence_dir / "09-inicio-assistente.png"))

    # PLAY liga a mesa de verdade (mesma rota do painel avançado)
    page.locator("#btnInicioPlay").click()
    expect(page.locator("#toast")).to_contain_text("LIGADA", timeout=15000)
    expect(page.locator("#iniMesaEstado")).to_contain_text("RODANDO", timeout=15000)

    # PAUSAR devolve o estado real — e o teste deixa a mesa parada
    page.locator("#btnInicioParar").click()
    expect(page.locator("#iniMesaEstado")).to_contain_text("PARADA", timeout=15000)

    # §1 da missão de auditoria funcional: o Simples mostra as capacidades
    # com estado honesto (4 estados) e acesso de UM CLIQUE a todas as telas —
    # telas ocultas no Simples abrem ligando o Avançado, nada escondido.
    caps = page.locator("#capacidadesLista")
    expect(caps).to_contain_text("disponível", timeout=15000)
    expect(caps).to_contain_text("configuração necessária")
    expect(caps).to_contain_text("bloqueado por segurança")
    expect(caps).to_contain_text("ainda não implementado")
    page.locator("#capacidadesLista").scroll_into_view_if_needed()
    page.screenshot(path=str(evidence_dir / "09b-inicio-capacidades.png"))
    page.locator('[data-abrir="mesa"]').first.click()
    expect(page.locator("#page-mesa")).to_have_class(re.compile(r"\bactive\b"))
    assert page.evaluate("localStorage.getItem('dz23_ui_modo')") == "avancado"
    page.select_option("#uiModo", "simples")
    expect(page.locator("#pageTitle")).to_have_text("Início")
    expect(page.locator("#sideNav button")).to_have_count(3)

    # Avançado mostra TODAS as páginas; o Simples continua acessível
    modo_avancado(page)
    expect(page.locator("#sideNav button")).to_have_count(len(PAGINAS))
    page.locator('[data-page="risk"]').first.click()
    expect(page.locator("#pageTitle")).to_have_text("Risco")
    page.select_option("#uiModo", "simples")
    expect(page.locator("#pageTitle")).to_have_text("Início")
    expect(page.locator("#sideNav button")).to_have_count(3)

    # A escolha é preferência do aparelho (localStorage), nunca do servidor
    assert page.evaluate("localStorage.getItem('dz23_ui_modo')") == "simples"


def test_fm3_corretora_catalogo_capacidades_e_preparo_real(page, live_server, owner_password, evidence_dir):
    # FM-3 (§3+§4): a tela mostra o catálogo real, as capacidades anunciadas
    # SEM credenciais e SEM rede, a reconciliação e a preparação para REAL —
    # visível, verificável e BLOQUEADA (nada aqui ativa dinheiro real).
    entrar_como_dono(page, live_server, owner_password)

    # §4 — preparação: checklist do servidor, com o bloqueio dito de frente
    page.locator("#preparoRealCard").scroll_into_view_if_needed()
    page.get_by_role("button", name=re.compile("Ver preparação")).click()
    prep = page.locator("#preparoReal")
    expect(prep).to_contain_text("BLOQUEADO", timeout=15000)
    expect(prep).to_contain_text("Frase de autorização digitada")
    expect(prep).to_contain_text("Liberação de código para operar dinheiro real")
    expect(prep).to_contain_text("Indisponível neste lançamento")
    expect(prep).to_contain_text("NÃO ativa nada")
    page.screenshot(path=str(evidence_dir / "09c-fm3-preparo-real.png"))

    # §3 — catálogo e capacidades na tela de corretora (Modo Avançado)
    modo_avancado(page)
    page.locator('[data-page="config"]').first.click()
    expect(page.locator("#pageTitle")).to_have_text("Configuração")
    page.get_by_role("button", name=re.compile("Catálogo e capacidades")).click()
    cat = page.locator("#brokerCatalogo")
    expect(cat).to_contain_text("Salas fixas", timeout=15000)
    expect(cat).to_contain_text("Binance")
    expect(cat).to_contain_text("planejada, não validada")
    expect(cat).to_contain_text("desligada")
    page.screenshot(path=str(evidence_dir / "09d-fm3-catalogo.png"))

    # Capacidades do primeiro adaptador instalado: anúncio local, não teste
    cat.locator("button", has_text="Capacidades").first.click()
    det = page.locator("#brokerDetalhe")
    expect(det).to_contain_text("sem credenciais e sem rede", timeout=15000)
    expect(det).to_contain_text("UNVERIFIED")
    expect(det).to_contain_text("verificado: não")

    # Reconciliação: relatório real do portão (FECHADO sem acesso à corretora)
    page.get_by_role("button", name="Reconciliar com a corretora").click()
    expect(det).to_contain_text("Reconciliação com a corretora", timeout=15000)
    expect(det).to_contain_text(re.compile(r"portão: (LIBERADO|FECHADO)"))
    expect(det).to_contain_text("ordens conferidas")
    page.screenshot(path=str(evidence_dir / "09e-fm3-reconciliacao.png"))

    # A escolha do modo é do aparelho — devolve ao Simples ao final
    page.select_option("#uiModo", "simples")
    expect(page.locator("#pageTitle")).to_have_text("Início")

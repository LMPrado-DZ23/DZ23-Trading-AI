import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _element(html: str, div_id: str) -> str:
    start = html.index(f'<div id="{div_id}"')
    depth = 0
    for m in re.finditer(r"<div\b|</div>", html[start:]):
        depth += 1 if m.group(0).startswith("<div") else -1
        if depth == 0:
            return html[start : start + m.end()]
    raise AssertionError(f"<div id={div_id}> sem fechamento")


def test_dashboard_contains_only_the_personal_app_surfaces():
    html = (ROOT / "service/static/index.html").read_text(encoding="utf-8")
    for contract in (
        "/v1/auth/status",
        "/v1/auth/me",
        "/v1/auth/logout",
        "/v1/config",
        "/v1/jobs",
        "/v1/jobs/health",
        "/v1/agency/health",
        "/v1/execution/health",
        "dz23_token",
        # O estado operacional deixou de ser texto fixo: a tela PERGUNTA ao
        # servidor. Travar a string aqui obrigaria a interface a mentir
        # quando o estado mudasse.
        "/v1/status/operacao",
    ):
        assert contract in html


def test_the_saas_dom_is_gone_not_just_hidden():
    # A fronteira HTTP já responde 404 para todo o SaaS (C011). Estes
    # vestígios de DOM foram removidos de vez: o dono não vê tela de
    # cadastro, planos/cobrança PIX, login social nem administração de
    # inquilinos — nada que clique e não funcione.
    html = (ROOT / "service/static/index.html").read_text(encoding="utf-8")
    for remnant in (
        'id="bootstrapForm"', 'id="signupForm"', 'id="socialBox"',
        'id="page-plano"', 'id="page-admin"', 'id="pixBox"',
        "loadPlano", "pagar(", "loadUsers", "criarConta", "carregarProvedores",
        "/v1/auth/bootstrap", "/v1/auth/signup", "/v1/auth/oauth",
        "/v1/admin/users", "/v1/billing",
    ):
        assert remnant not in html, f"vestígio SaaS no painel: {remnant}"


def test_owner_mode_login_contract():
    # No modo Dono Único (padrão Windows) /v1/auth/login responde 404;
    # o painel precisa entrar com a senha do dono via Bearer em /v1/auth/me.
    html = (ROOT / "service/static/index.html").read_text(encoding="utf-8")
    assert "if(status.multi_tenant===false)" in html
    assert "ownerMode" in html
    assert "Senha do dono" in html
    assert "Entrar na sua mesa" in html


def test_identidade_premium_do_login_do_dono():
    # O login deixou de parecer página de SaaS: identidade DZ23 Trading AI
    # (sem AGENCY), sem instrução para ler owner_password.txt, assistente
    # de primeira execução, código de recuperação exibido uma vez e
    # feedback acessível (aria-live).
    html = (ROOT / "service/static/index.html").read_text(encoding="utf-8")
    assert "DZ23 AGENCY" not in html
    assert "DZ23 Trading AI" in html
    assert "owner_password.txt" not in html
    assert 'id="setupForm"' in html
    assert "Primeira configuração" in html
    assert 'id="recoverForm"' in html
    assert "Esqueci minha senha" in html
    assert 'id="codigoForm"' in html
    assert "código de recuperação" in html
    assert 'aria-live="polite"' in html


def test_trading_floor_reuses_multitenant_session():
    html = (ROOT / "service/static/floor.html").read_text(encoding="utf-8")
    assert "sessionStorage.getItem('dz23_token')" in html


def test_trading_floor_nao_polla_sem_sessao():
    # Sem token o floor não dispara chamadas protegidas: séries de 401
    # anônimos armariam a trava de força-bruta antes do login do dono.
    html = (ROOT / "service/static/floor.html").read_text(encoding="utf-8")
    assert "AGUARDANDO LOGIN" in html
    assert "prompt('Senha do painel')" not in html
    assert "WORKER RUNNING" in html
    assert "/v1/agency/missions" in html


def test_config_page_exposes_the_desk_feed_toggle():
    # A rota /v1/desk/feed manda o dono "em Configuração, Fonte de dados =
    # Mercado real" — o controle precisa existir de verdade, com as duas
    # opções, e a chave precisa entrar no CFG_KEYS para o Salvar levar o
    # valor ao servidor.
    html = (ROOT / "service/static/index.html").read_text(encoding="utf-8")
    assert 'id="TRADINGAGENTS_DESK_FEED"' in html
    assert "'TRADINGAGENTS_DESK_FEED'" in html, "fora do CFG_KEYS: Salvar não enviaria"
    assert "<option value=\"fixture\">" in html
    assert "<option value=\"real\">" in html
    assert "Fonte de dados" in html


def test_iq_option_exposes_explicit_payout_only_for_its_school_room():
    html = (ROOT / "service/static/index.html").read_text(encoding="utf-8")
    assert 'id="TRADINGAGENTS_DESK_PAYOUT"' in html
    assert "'TRADINGAGENTS_DESK_PAYOUT'" in html
    assert 'min="0.01"' in html and 'max="1"' in html
    assert "grupoDeskPayout" in html
    assert "classList.toggle('hide',!iq)" in html


def test_iq_option_read_only_flow_confirms_unique_regular_account_without_id():
    html = (ROOT / "service/static/index.html").read_text(encoding="utf-8")
    assert "confirmo_conta_regular_unica" in html
    assert "uma única conta REAL regular" in html
    assert "balance_id da conta regular exibido pela IQ Option" not in html


def test_config_exposes_required_live_risk_limits_without_suggested_values():
    html = (ROOT / "service/static/index.html").read_text(encoding="utf-8")
    for key in (
        "TRADINGAGENTS_DESK_TETO_EXPOSICAO",
        "LIVE_MAX_ORDER_VALUE",
        "LIVE_MAX_DAILY_LOSS",
    ):
        assert f'id="{key}"' in html
        assert f"'{key}'" in html, f"{key} fora de CFG_KEYS"
    assert 'id="LIVE_MAX_ORDER_VALUE" value=' not in html
    assert 'id="LIVE_MAX_DAILY_LOSS" value=' not in html
    assert 'id="TRADINGAGENTS_DESK_TETO_EXPOSICAO" value=' not in html


def test_pilot_limits_require_owner_values_without_financial_defaults():
    html = (ROOT / "service/static/index.html").read_text(encoding="utf-8")
    for default in (
        "Capital máximo do piloto (não libera LIVE):','100'",
        "Perda máxima diária:','10'",
        "Perda máxima total:','30'",
        "Prazo em dias (1 a 365):','30'",
    ):
        assert default not in html
    assert "Informe todos os limites do piloto" in html


def test_iq_option_possui_token_live_separado_e_preparo_sem_ordem():
    html = (ROOT / "service/static/index.html").read_text(encoding="utf-8")
    assert 'id="IQ_OPTION_MCP_LIVE_BEARER_TOKEN"' in html
    assert "IQ_OPTION_MCP_LIVE_BEARER_TOKEN" in html
    assert "confirmo_conta_regular_unica" in html
    trecho = html.split("async function brokerPrepararExecucao()", 1)[1]
    trecho = trecho.split("async function catalogoCarregar()", 1)[0]
    assert "ainda não possui executor LIVE" not in trecho
    assert "/v1/live/preparacao-execucao" in trecho
    assert "confirmo_trade:true" in trecho


def test_painel_tem_saida_do_aplicativo():
    # C009: sem console, o usuário só fecharia o app pelo Gerenciador de
    # Tarefas. O painel do dono precisa de um botão que encerra o servidor,
    # visível apenas no modo Dono Único (multi-tenant não pode derrubar o
    # servidor compartilhado de um inquilino).
    html = (ROOT / "service/static/index.html").read_text(encoding="utf-8")
    assert 'id="quitAppBtn"' in html
    assert "Fechar aplicativo" in html
    assert "function quitApp" in html
    assert "/v1/app/quit" in html
    assert "quitAppBtn').classList.remove('hide')" in html

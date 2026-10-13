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


def test_dashboard_contains_multitenant_and_operational_surfaces():
    html = (ROOT / "service/static/index.html").read_text(encoding="utf-8")
    for contract in (
        "/v1/auth/status",
        "/v1/auth/bootstrap",
        "/v1/auth/login",
        "/v1/auth/me",
        "/v1/auth/logout",
        "/v1/admin/users",
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


def test_bootstrap_form_contains_token_and_button():
    html = (ROOT / "service/static/index.html").read_text(encoding="utf-8")
    form = _element(html, "bootstrapForm")
    assert 'class="hide"' in form.split(">", 1)[0]
    assert 'id="bootToken"' in form
    assert 'onclick="bootstrap()"' in form


def test_owner_mode_login_contract():
    # No modo Dono Único (padrão Windows) /v1/auth/login responde 404;
    # o painel precisa entrar com a senha do dono via Bearer em /v1/auth/me.
    html = (ROOT / "service/static/index.html").read_text(encoding="utf-8")
    assert "if(status.multi_tenant===false)" in html
    assert "ownerMode" in html
    assert "Senha do dono" in html
    assert "Entrar na sua mesa" in html


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

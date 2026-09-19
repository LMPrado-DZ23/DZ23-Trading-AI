from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_contains_multitenant_and_operational_surfaces():
    html = (ROOT / "service/static/index.html").read_text()
    for contract in (
        "/v1/auth/status",
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
        assert contract in html, contract


def test_first_run_setup_page_wires_bootstrap():
    """O bootstrap inicial saiu do dashboard para uma página dedicada (/setup),
    que decide sozinha se ainda precisa configurar. É onde o token e a senha do
    primeiro admin são enviados — nunca no painel do dia a dia."""
    html = (ROOT / "service/static/setup.html").read_text()
    assert "/v1/auth/bootstrap" in html
    assert "/v1/auth/status" in html      # some depois da primeira área criada
    assert "X-Tenant-Bootstrap" in html   # o token vai no header, não na URL


def test_trading_floor_reuses_multitenant_session():
    html = (ROOT / "service/static/floor.html").read_text()
    assert "sessionStorage.getItem('dz23_token')" in html
    assert "prompt('Senha do painel')" not in html

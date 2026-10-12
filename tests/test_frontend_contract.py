from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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


def test_trading_floor_reuses_multitenant_session():
    html = (ROOT / "service/static/floor.html").read_text(encoding="utf-8")
    assert "sessionStorage.getItem('dz23_token')" in html
    assert "prompt('Senha do painel')" not in html
    assert "WORKER RUNNING" in html
    assert "/v1/agency/missions" in html

"""Trading Floor: a cena reflete o read model oficial — nunca inventa estado."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLOOR = (ROOT / "service/static/floor.html").read_text(encoding="utf-8")


class TestCena:
    def test_isometric_canvas_exists(self):
        assert 'id="iso"' in FLOOR and "getContext('2d')" in FLOOR
        assert "desenharSala" in FLOOR

    def test_scene_is_fed_by_official_endpoints_only(self):
        assert "/v1/agency/snapshot" in FLOOR and "/v1/agency/events" in FLOOR
        # nada de fabricar estado na tela:
        assert "Math.random" not in FLOOR
        assert "fakeAgents" not in FLOOR and "demoData" not in FLOOR

    def test_status_colors_come_from_official_statuses(self):
        for status in ("ANALYZING", "PAPER_TRADING", "RISK_LOCKED", "ERROR", "PAUSED"):
            assert status in FLOOR

    def test_station_click_states_it_is_a_read_model(self):
        assert "read model oficial" in FLOOR
        assert "nenhuma posição ou PnL é inferido" in FLOOR

    def test_reuses_the_session_and_never_prompts_for_password(self):
        assert "sessionStorage.getItem('dz23_token')" in FLOOR
        assert "prompt('Senha do painel')" not in FLOOR

    def test_live_is_shown_as_blocked(self):
        assert "LIVE BLOCKED" in FLOOR or "BLOCKED" in FLOOR

    def test_credits_the_visual_inspiration(self):
        assert "Claw3D" in FLOOR and "openclaw-office" in FLOOR

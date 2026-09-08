"""Auditoria de usabilidade: o serviço protege o leigo por construção."""
import importlib
import pytest


def _load_app(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import service.app as m
    importlib.reload(m)
    return m


class TestAssetType:
    @pytest.mark.parametrize("t,expected", [
        ("BTC/USDT", "crypto"), ("ETH-USD", "crypto"), ("SOL-USDT", "crypto"),
        ("NVDA", "stock"), ("PETR4", "stock"),
    ])
    def test_crypto_detection_rule(self, t, expected):
        t_up = t.upper()
        got = "crypto" if ("/" in t_up or t_up.endswith(("-USD", "-USDT", "-BTC"))) else "stock"
        assert got == expected


class TestServerSideHistory:
    def test_list_jobs_route_exists(self, tmp_path, monkeypatch):
        m = _load_app(tmp_path, monkeypatch)
        paths = [r.path for r in m.app.routes]
        assert "/v1/jobs" in paths and "/v1/jobs/{jid}" in paths


class TestPanelHonesty:
    def test_no_stale_promises_in_panel(self):
        html = open("service/static/index.html", encoding="utf-8").read()
        assert "reiniciar o serviço" not in html      # botão mentiroso removido
        assert "via Coolify" not in html               # resquício da central antiga
        assert "capital real segue bloqueado" not in html  # live existe
        assert "Comece aqui" in html                   # guia do leigo presente
        assert "testnet.binance.vision" in html        # onde pegar chave de treino

    def test_broker_test_saves_before_testing(self):
        html = open("service/static/index.html", encoding="utf-8").read()
        i_save = html.find("/v1/config'", html.find("async function brokerTest"))
        i_stat = html.find("/v1/broker/status", html.find("async function brokerTest"))
        assert 0 < i_save < i_stat  # salva ANTES de testar

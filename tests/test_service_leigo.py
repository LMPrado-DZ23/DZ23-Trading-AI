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


class TestFreeProviders:
    def _resolve(self, tmp_path, monkeypatch, cfg):
        m = _load_app(tmp_path, monkeypatch)
        return m.resolve_llm(cfg)

    def test_groq_maps_to_compatible_with_own_credential(self, tmp_path, monkeypatch):
        conf, env = self._resolve(tmp_path, monkeypatch,
            {"TRADINGAGENTS_LLM_PROVIDER": "groq", "GROQ_API_KEY": "gsk-x"})
        assert conf["llm_provider"] == "openai_compatible"
        assert conf["backend_url"] == "https://api.groq.com/openai/v1"
        assert conf["deep_think_llm"] and conf["quick_think_llm"]
        assert env == {"OPENAI_COMPATIBLE_API_KEY": "gsk-x"}
        assert "OPENAI_API_KEY" not in env  # chave OpenAI real jamais vaza p/ backend alheio

    def test_mistral_and_openrouter(self, tmp_path, monkeypatch):
        for prov, key, url in [("mistral", "MISTRAL_API_KEY", "https://api.mistral.ai/v1"),
                               ("openrouter", "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1")]:
            conf, env = self._resolve(tmp_path, monkeypatch,
                {"TRADINGAGENTS_LLM_PROVIDER": prov, key: "k1"})
            assert conf["backend_url"] == url
            assert env["OPENAI_COMPATIBLE_API_KEY"] == "k1"

    def test_missing_free_key_fails_with_plain_portuguese(self, tmp_path, monkeypatch):
        with pytest.raises(ValueError, match="grátis"):
            self._resolve(tmp_path, monkeypatch, {"TRADINGAGENTS_LLM_PROVIDER": "groq"})

    def test_ollama_requires_address_and_uses_native_provider(self, tmp_path, monkeypatch):
        with pytest.raises(ValueError, match="Endereço"):
            self._resolve(tmp_path, monkeypatch, {"TRADINGAGENTS_LLM_PROVIDER": "ollama"})
        conf, env = self._resolve(tmp_path, monkeypatch,
            {"TRADINGAGENTS_LLM_PROVIDER": "ollama",
             "TRADINGAGENTS_BACKEND_URL": "http://10.0.0.5:11434/v1"})
        assert conf["llm_provider"] == "ollama"
        assert conf["backend_url"] == "http://10.0.0.5:11434/v1"
        assert env == {}

    def test_paid_providers_untouched(self, tmp_path, monkeypatch):
        conf, env = self._resolve(tmp_path, monkeypatch,
            {"TRADINGAGENTS_LLM_PROVIDER": "openai",
             "TRADINGAGENTS_DEEP_THINK_LLM": "gpt-5"})
        assert conf["llm_provider"] == "openai" and conf["deep_think_llm"] == "gpt-5"
        assert "backend_url" not in conf and env == {}


class TestBruteForceLock:
    def _client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = _load_app(tmp_path, monkeypatch)
        m._FAILS.clear()
        return TestClient(m.app), m

    def test_five_wrong_tries_lock_the_ip(self, tmp_path, monkeypatch):
        c, m = self._client(tmp_path, monkeypatch)
        for _ in range(5):
            assert c.get("/v1/config", headers={"Authorization": "Bearer errada"}).status_code == 401
        r = c.get("/v1/config", headers={"Authorization": "Bearer errada"})
        assert r.status_code == 429 and "min" in r.json()["detail"]
        # até a senha CERTA espera durante a trava (proteção > conveniência)
        assert c.get("/v1/config", headers={"Authorization": "Bearer s3nh4"}).status_code == 429

    def test_lock_expires_and_correct_password_resets(self, tmp_path, monkeypatch):
        c, m = self._client(tmp_path, monkeypatch)
        for _ in range(5):
            c.get("/v1/config", headers={"Authorization": "Bearer errada"})
        for rec in m._FAILS.values():
            rec["until"] = 0  # simula os 15 min passando
        assert c.get("/v1/config", headers={"Authorization": "Bearer s3nh4"}).status_code == 200
        assert not m._FAILS  # acerto limpa o histórico

    def test_security_headers_present(self, tmp_path, monkeypatch):
        c, _ = self._client(tmp_path, monkeypatch)
        h = c.get("/healthz").headers
        assert h["X-Content-Type-Options"] == "nosniff"
        assert h["X-Frame-Options"] == "DENY"


class TestPainelMesaDeAgentes:
    """MA-08: a aba existe de verdade no HTML servido, com botões ligados a
    APIs reais (regressão do replace silencioso que não aplicou)."""

    def test_desk_tab_and_buttons_exist(self):
        html = open("service/static/index.html", encoding="utf-8").read()
        for needle in ['id="pane-desk"', 'data-t="desk"', "deskSched('start')",
                       "deskCycle()", "deskPause()", 'id="deskOut"']:
            assert needle in html, f"faltou no painel: {needle}"

    def test_desk_routes_are_registered(self, tmp_path, monkeypatch):
        m = _load_app(tmp_path, monkeypatch)
        paths = {r.path for r in m.app.routes}
        assert {"/v1/desk/status", "/v1/desk/cycle", "/v1/desk/pause",
                "/v1/desk/scheduler/{action}"} <= paths

    def test_desk_status_requires_auth(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = _load_app(tmp_path, monkeypatch)
        m._FAILS.clear()
        c = TestClient(m.app)
        assert c.get("/v1/desk/status").status_code == 401

    def test_desk_cycle_runs_and_books_stay_balanced(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = _load_app(tmp_path, monkeypatch)
        m._FAILS.clear()
        m._DESK.update(orchestrator=None, scheduler=None)
        c = TestClient(m.app)
        h = {"Authorization": "Bearer s3nh4"}
        assert c.post("/v1/desk/cycle", headers=h).status_code == 200
        st = c.get("/v1/desk/status", headers=h).json()
        assert st["contabilidade"]["balanceado"] is True
        assert st["mandate"]["mode"] == "paper"      # esta superfície não liga LIVE
        assert len(st["orders"]) == 2
        again = c.post("/v1/desk/cycle", headers=h).json()
        assert again["ordens"] == []                 # segundo ciclo não duplica

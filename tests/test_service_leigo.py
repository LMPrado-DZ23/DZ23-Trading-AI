"""Auditoria de usabilidade: o serviço protege o leigo por construção."""
import importlib
from pathlib import Path

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
        html = Path("service/static/index.html").read_text(encoding="utf-8")
        assert "reiniciar o serviço" not in html      # botão mentiroso removido
        assert "via Coolify" not in html               # resquício da central antiga
        assert "testnet.binance.vision" in html        # onde pegar chave de treino

    def test_broker_test_saves_before_testing(self):
        html = Path("service/static/index.html").read_text(encoding="utf-8")
        i = html.find("async function brokerTest")
        i_save = html.find("/v1/config", i)
        i_stat = html.find("/v1/broker/status", i)
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
        html = Path("service/static/index.html").read_text(encoding="utf-8")
        for needle in ['id="page-mesa"', "deskSched('start')", "deskCycle()",
                       'id="deskOut"', 'id="checklist"']:
            assert needle in html, f"faltou no painel: {needle}"

    def test_desk_routes_are_registered(self, tmp_path, monkeypatch):
        m = _load_app(tmp_path, monkeypatch)
        paths = {r.path for r in m.app.routes}
        assert {"/v1/desk/status", "/v1/desk/cycle", "/v1/desk/feed",
                "/v1/desk/avaliacao", "/v1/desk/scheduler/{action}",
                "/v1/backup", "/v1/primeiros-passos"} <= paths

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
        m._DESKS.clear()
        c = TestClient(m.app)
        h = {"Authorization": "Bearer s3nh4"}
        assert c.post("/v1/desk/cycle", headers=h).status_code == 200
        st = c.get("/v1/desk/status", headers=h).json()
        assert st["contabilidade"]["balanceado"] is True
        assert st["mandate"]["mode"] == "paper"      # esta superfície não liga LIVE
        assert len(st["orders"]) == 2
        again = c.post("/v1/desk/cycle", headers=h).json()
        assert again["ordens"] == []                 # segundo ciclo não duplica


class TestDiagnosticoDoFeed:
    """Quando o mercado não chega, o painel diz o motivo e a próxima ação —
    não fica mudo (o leigo precisa saber se é firewall ou exchange fora)."""

    def test_feed_route_exists_and_reports_fixture_mode(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = _load_app(tmp_path, monkeypatch)
        m._FAILS.clear()
        m._DESKS.clear()
        c = TestClient(m.app)
        r = c.get("/v1/desk/feed", headers={"Authorization": "Bearer s3nh4"})
        assert r.status_code == 200
        body = r.json()
        assert body["fonte"] == "fixture" and "Mercado real" in body["proxima_acao"]

    def test_feed_button_exists_in_panel(self):
        html = Path("service/static/index.html").read_text(encoding="utf-8")
        assert "feedTest" in html and "Testar dados de mercado" in html


class TestArranqueResiliente:
    """O serviço prefere DEGRADAR a morrer: um banco fora do ar não pode
    derrubar a página de configuração (causa provável do 502 em produção)."""

    def test_broken_database_url_falls_back_instead_of_crashing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/nada")
        m = _load_app(tmp_path, monkeypatch)      # importar NÃO pode explodir
        assert m.JOB_STORE is not None
        assert "fallback" in m.HEALTH["armazenamento"]
        assert m.HEALTH["avisos"] and "PostgreSQL" in m.HEALTH["avisos"][0]

    def test_healthz_reports_degraded_without_auth(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/nada")
        m = _load_app(tmp_path, monkeypatch)
        body = TestClient(m.app).get("/healthz").json()
        assert body["status"] == "degraded" and body["avisos"]

    def test_panel_still_serves_when_degraded(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/nada")
        m = _load_app(tmp_path, monkeypatch)
        assert TestClient(m.app).get("/").status_code == 200


class TestOnboarding:
    def test_checklist_starts_empty_and_advances(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = _load_app(tmp_path, monkeypatch)
        m._FAILS.clear()
        c = TestClient(m.app)
        h = {"Authorization": "Bearer s3nh4"}
        first = c.get("/v1/primeiros-passos", headers=h).json()
        assert first["total"] == 6
        assert all(i["acao"] for i in first["itens"])       # toda pendência tem ação
        passo_ia = next(i for i in first["itens"] if "IA" in i["passo"])
        assert passo_ia["pronto"] is False
        c.post("/v1/config", headers=h,
               json={"vars": {"TRADINGAGENTS_LLM_PROVIDER": "groq",
                              "GROQ_API_KEY": "gsk-x"}})
        depois = c.get("/v1/primeiros-passos", headers=h).json()
        assert next(i for i in depois["itens"] if "IA" in i["passo"])["pronto"] is True
        assert depois["concluidos"] > first["concluidos"]

    def test_panel_shows_checklist_and_health_banner(self):
        html = Path("service/static/index.html").read_text(encoding="utf-8")
        assert 'id="checklist"' in html and 'id="page-mesa"' in html

    def test_compose_restarts_and_has_healthcheck(self):
        compose = Path("docker-compose.yml").read_text()
        assert "restart: unless-stopped" in compose and "healthcheck" in compose
        assert "/data" in compose


class TestEntradasInvalidas:
    """Auditoria: nenhuma entrada malformada pode virar 500 (isso é o servidor
    confessando um bug que é do cliente)."""

    def _client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = _load_app(tmp_path, monkeypatch)
        m._FAILS.clear()
        return TestClient(m.app), {"Authorization": "Bearer s3nh4"}

    def test_broken_json_is_422_not_500(self, tmp_path, monkeypatch):
        c, h = self._client(tmp_path, monkeypatch)
        for rota in ("/v1/config", "/v1/jobs"):   # broker/order é 410 por política
            r = c.post(rota, headers={**h, "Content-Type": "application/json"},
                       content="isto não é json")
            assert r.status_code == 422, f"{rota} devolveu {r.status_code}"

    def test_non_object_body_is_422(self, tmp_path, monkeypatch):
        c, h = self._client(tmp_path, monkeypatch)
        r = c.post("/v1/jobs", headers=h, json=["lista", "não", "objeto"])
        assert r.status_code == 422

    def test_missing_fields_are_422(self, tmp_path, monkeypatch):
        c, h = self._client(tmp_path, monkeypatch)
        assert c.post("/v1/jobs", headers=h, json={}).status_code == 422
        assert c.post("/v1/jobs", headers=h,
                      json={"ticker": "X", "trade_date": "2026"}).status_code == 422

    def test_unknown_config_key_is_rejected(self, tmp_path, monkeypatch):
        c, h = self._client(tmp_path, monkeypatch)
        r = c.post("/v1/config", headers=h, json={"vars": {"NAO_EXISTE": "x"}})
        assert r.status_code in (400, 422)

    def test_invalid_scheduler_action_is_422(self, tmp_path, monkeypatch):
        c, h = self._client(tmp_path, monkeypatch)
        assert c.post("/v1/desk/scheduler/explodir", headers=h).status_code == 422


class TestPainelSemBotaoQuebrado:
    """Auditoria: botão que chama função inexistente não faz nada no
    navegador e passa despercebido em teste de API. Quatro botões estavam
    assim (ligar mesa, testar dados, backup, avaliar)."""

    def test_every_handler_exists(self):
        import re
        html = Path("service/static/index.html").read_text(encoding="utf-8")
        chamadas = set(re.findall(r'on(?:click|change)="(\w+)\(', html))
        definidas = set(re.findall(r'(?:async )?function (\w+)\(', html))
        assert chamadas, "nenhum handler encontrado — painel vazio?"
        assert not (chamadas - definidas), f"botões quebrados: {sorted(chamadas - definidas)}"

    def test_every_fetched_route_is_registered(self, tmp_path, monkeypatch):
        import re
        html = Path("service/static/index.html").read_text(encoding="utf-8")
        m = _load_app(tmp_path, monkeypatch)
        registradas = {r.path for r in m.app.routes}
        for rota in set(re.findall(r"fetch\('(/[\w/-]+)", html)):
            base = rota.rstrip("/")
            assert any(base == p or p.startswith(base.rsplit("/", 1)[0])
                       for p in registradas), f"painel chama rota inexistente: {rota}"


class TestValidadorNaoRecursivo:
    """Regressão de um erro real: o validador de corpo passou a chamar a si
    mesmo e TODA rota POST devolvia 422 — inclusive as válidas."""

    def test_valid_post_is_accepted(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = _load_app(tmp_path, monkeypatch)
        m._FAILS.clear()
        c = TestClient(m.app)
        h = {"Authorization": "Bearer s3nh4"}
        assert c.post("/v1/config", headers=h,
                      json={"vars": {"GROQ_API_KEY": "gsk-x"}}).status_code == 200
        assert c.post("/v1/jobs", headers=h,
                      json={"ticker": "NVDA", "trade_date": "2026-09-04"}
                      ).status_code in (200, 201)

    def test_validator_does_not_call_itself(self):
        import inspect

        from service import app as m
        fonte = inspect.getsource(m._json_body)
        assert fonte.count("_json_body") == 1
        assert "await req.json()" in fonte


class TestDominioOficial:
    """A documentação precisa apontar para o domínio real — endereço errado
    em guia de instalação custa uma hora de suporte por cliente."""

    def test_install_guide_uses_the_official_domain(self):
        guia = Path("INSTALAR.md").read_text(encoding="utf-8")
        assert "dz23trading.com.br" in guia
        assert "SEU-DOMINIO" not in guia
        assert "https://dz23trading.com.br/v1/webhooks/woovi" in guia

    def test_domain_checklist_exists_with_every_registration(self):
        doc = Path("DOMINIO-E-CADASTROS.md").read_text(encoding="utf-8")
        for item in ("<IP-DO-SERVIDOR>", "/v1/webhooks/woovi",
                     "/v1/auth/oauth/google/callback",
                     "/v1/auth/oauth/microsoft/callback", "SUPABASE_PROJECT_REF"):
            assert item in doc, f"faltou no checklist: {item}"

    def test_no_secret_value_in_any_doc(self):
        import re
        for nome in ("INSTALAR.md", "DOMINIO-E-CADASTROS.md", ".env.example"):
            texto = Path(nome).read_text(encoding="utf-8")
            assert not re.search(r"GOCSPX-[A-Za-z0-9_-]{10,}", texto)
            assert "sb_publishable_" not in texto
            assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in texto

"""Auditoria de usabilidade: o serviço protege o leigo por construção."""
import importlib
import threading
from pathlib import Path

import pytest


def _load_app(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import service.app as m
    importlib.reload(m)
    return m


def _get_all_paths(app):
    paths = set()
    for r in app.routes:
        if hasattr(r, "path") and r.path is not None:
            paths.add(r.path)
        elif "IncludedRouter" in type(r).__name__ and hasattr(r, "original_router") and hasattr(r.original_router, "routes"):
            for sub_r in r.original_router.routes:
                if hasattr(sub_r, "path") and sub_r.path is not None:
                    paths.add(sub_r.path)
    return paths


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
        paths = _get_all_paths(m.app)
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

    def test_anonymous_requests_never_lock_the_owner(self, tmp_path, monkeypatch):
        # O painel embutido (iframe /floor) chama endpoints protegidos antes
        # do login; esses 401 SEM credencial nenhuma (ou com "Bearer " vazio)
        # não podem contar como tentativa de senha — senão o dono que só
        # demora a digitar fica 15 min trancado fora do próprio painel.
        c, m = self._client(tmp_path, monkeypatch)
        for _ in range(10):
            assert c.get("/v1/config").status_code == 401
            assert c.get("/v1/config",
                         headers={"Authorization": "Bearer "}).status_code == 401
        assert m._FAILS == {}
        assert c.get(
            "/v1/config", headers={"Authorization": "Bearer s3nh4"}).status_code == 200

    def test_security_headers_present(self, tmp_path, monkeypatch):
        c, _ = self._client(tmp_path, monkeypatch)
        h = c.get("/healthz").headers
        assert h["X-Content-Type-Options"] == "nosniff"
        assert h["X-Frame-Options"] == "DENY"

    def test_floor_allows_same_origin_iframe_embedding(self, tmp_path, monkeypatch):
        # O painel embute /floor em iframe same-origin; DENY quebraria a tela.
        c, _ = self._client(tmp_path, monkeypatch)
        assert c.get("/floor").headers["X-Frame-Options"] == "SAMEORIGIN"
        assert c.get("/app").headers["X-Frame-Options"] == "DENY"

    def test_html_documents_never_cached_stale(self, tmp_path, monkeypatch):
        # Depois de atualizar o app, o navegador não pode continuar rodando o
        # HTML ANTIGO em cache: o floor antigo pollava sem sessão e, no
        # servidor antigo, armava a trava de força-bruta trancando o dono.
        c, _ = self._client(tmp_path, monkeypatch)
        for rota in ("/app", "/floor"):
            assert c.get(rota).headers.get("Cache-Control") == "no-store"

    def test_forwarded_for_is_ignored_without_trusted_proxy(self, tmp_path, monkeypatch):
        c, m = self._client(tmp_path, monkeypatch)
        request = type("Request", (), {
            "client": type("Client", (), {"host": "127.0.0.1"})(),
            "headers": {"x-forwarded-for": "203.0.113.10"},
        })()
        assert m._client_ip(request) == "127.0.0.1"
        monkeypatch.setenv("TRUSTED_PROXY_NETWORKS", "127.0.0.1/32")
        assert m._client_ip(request) == "203.0.113.10"


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
        paths = _get_all_paths(m.app)
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


class TestAgenciaGeralTarefas:
    def test_task_control_plane_is_tenant_scoped_and_approval_gated(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = _load_app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        h = {"Authorization": "Bearer s3nh4"}
        created = c.post("/v1/agency/tasks", headers=h, json={
            "mission_id": "mission-1", "agent_id": "market-scanner",
            "kind": "research", "payload": {"topic": "BTC"},
            "idempotency_key": "task-once", "requires_approval": True})
        assert created.status_code == 200
        assert created.json()["task"]["status"] == "REVIEW_REQUIRED"
        assert c.post("/v1/agency/tasks/run", headers=h).json()["tasks"][0]["status"] == "REVIEW_REQUIRED"
        approved = c.post(f"/v1/agency/tasks/{created.json()['task']['task_id']}/approve", headers=h)
        assert approved.status_code == 200 and approved.json()["status"] == "QUEUED"
        run = c.post("/v1/agency/tasks/run", headers=h).json()
        assert run["tasks"][0]["status"] == "FAILED"  # sem handler real, nunca finge sucesso

    def test_worker_lifecycle_is_explicit_and_idempotent(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = _load_app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        h = {"Authorization": "Bearer s3nh4"}
        started = c.post("/v1/agency/worker/start", headers=h, json={"poll_interval": 0.1, "cycle_timeout": 1})
        assert started.status_code == 200 and started.json()["running"] is True
        status = c.get("/v1/agency/worker/status", headers=h)
        assert status.status_code == 200 and status.json()["tenant_id"] == "legacy"
        stopped = c.post("/v1/agency/worker/stop", headers=h)
        assert stopped.status_code == 200 and stopped.json()["running"] is False

    def test_health_reports_missing_llm_handlers(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = _load_app(tmp_path, monkeypatch)
        body = TestClient(m.app).get("/v1/agency/health", headers={"Authorization": "Bearer s3nh4"}).json()
        assert body["runtime"] == "DEGRADED"
        assert body["llm_handlers"]["configured"] < body["llm_handlers"]["required"]
        assert "/v1/config" in body["next_action"]
        assert len(body["agent_details"]) >= 15


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

    def test_requested_multitenant_never_falls_back_to_legacy_auth(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        monkeypatch.setenv("MULTI_TENANT_ENABLED", "true")
        monkeypatch.setenv("TENANT_TOKEN_SIGNING_KEY", "curta")
        m = _load_app(tmp_path, monkeypatch)
        response = TestClient(m.app).get(
            "/v1/config", headers={"Authorization": "Bearer s3nh4"})
        assert response.status_code == 503


class TestEmergencyStopPersistence:
    def test_live_halt_survives_memory_reload(self, tmp_path, monkeypatch):
        m = _load_app(tmp_path, monkeypatch)
        parada = m._parada("tenant-a")
        parada.acionar("parada de emergência de teste", por="tenant-a")
        p2 = m._parada("tenant-a")
        assert p2.bloqueia is True


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
        registradas = _get_all_paths(m.app)
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


def _reconciliar_com_corretora_que_confirma(coord):
    """Corretora que CONFIRMA o estado local: nenhuma ordem viva e as mesmas
    posições que o livro tem. É o cenário 'tudo bate' — e mesmo ele precisa
    passar pela reconciliação para gerar evidência."""
    from decimal import Decimal as _D
    posicoes = {i: _D(str(d["quantidade"]))
                for i, d in coord.ledger.open_positions_detail().items()}
    return coord.reconciliar(lambda coid: None, consultar_posicoes=lambda: posicoes, consultar_ordens_abertas=lambda: [])


class TestDinheiroRealNoServico:
    """O caminho de dinheiro real existe e é honesto sobre o que falta."""

    def _c(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = _load_app(tmp_path, monkeypatch)
        m._FAILS.clear()
        m._DESKS.clear()
        return m, TestClient(m.app), {"Authorization": "Bearer s3nh4"}

    def test_checklist_starts_blocked_and_lists_what_is_missing(self, tmp_path, monkeypatch):
        m, c, h = self._c(tmp_path, monkeypatch)
        d = c.get("/v1/live/status", headers=h).json()
        assert d["pode_operar_real"] is False and d["pendencias"] >= 5
        assert all(i["acao"] for i in d["checklist"] if not i["pronto"])
        assert "perdas são suas" in d["aviso"]

    def test_checklist_advances_with_the_owner_phrase(self, tmp_path, monkeypatch):
        m, c, h = self._c(tmp_path, monkeypatch)
        antes = c.get("/v1/live/status", headers=h).json()["pendencias"]
        c.post("/v1/config", headers=h, json={"vars": {
            "LIVE_TRADING_CONFIRM": "EU AUTORIZO OPERAR COM DINHEIRO REAL"}})
        depois = c.get("/v1/live/status", headers=h).json()
        assert depois["pendencias"] == antes - 1
        assert depois["checklist"][0]["pronto"] is True

    def test_wrong_phrase_does_not_unlock(self, tmp_path, monkeypatch):
        m, c, h = self._c(tmp_path, monkeypatch)
        c.post("/v1/config", headers=h,
               json={"vars": {"LIVE_TRADING_CONFIRM": "eu autorizo"}})
        assert c.get("/v1/live/status", headers=h).json()["checklist"][0]["pronto"] is False

    def test_emergency_stop_halts_everything(self, tmp_path, monkeypatch):
        m, c, h = self._c(tmp_path, monkeypatch)
        c.post("/v1/desk/scheduler/start", headers=h)
        r = c.post("/v1/live/parada-emergencia", headers=h)
        assert r.status_code == 200 and r.json()["parado"] is True
        # com a parada acionada, a mesa fica trancada (423) até liberar
        assert c.get("/v1/desk/status", headers=h).status_code == 423
        assert c.post("/v1/live/liberar", headers=h,
                      json={"frase": "qualquer"}).status_code == 422
        # Liberar exige DUAS condições: a frase E a reconciliação. Só a frase
        # não basta — pode haver ordem ou posição que o sistema desconhece.
        # Mesa LIMPA ainda assim exige reconciliação: sem relatório do
        # servidor não há evidência, e sem evidência não se libera.
        sem_relatorio = c.post(
            "/v1/live/liberar", headers=h,
            json={"frase": "EU AUTORIZO OPERAR COM DINHEIRO REAL"})
        assert sem_relatorio.status_code == 409
        mesa = m._DESKS.get("legacy") or m._desk_boot("legacy")
        m._DESKS["legacy"] = mesa
        _reconciliar_com_corretora_que_confirma(mesa["coordinator"])
        limpa = c.post("/v1/live/liberar", headers=h,
                       json={"frase": "EU AUTORIZO OPERAR COM DINHEIRO REAL"})
        assert limpa.status_code == 200, limpa.text

        # Com ordem INCERTA, a liberação é recusada — e nenhuma afirmação do
        # cliente ("reconciliado": true) pode destravar isso.
        c.get("/v1/desk/status", headers=h)          # carrega a mesa
        coord = m._DESKS["legacy"]["coordinator"]
        coord.exigir_reconciliacao("ordem incerta de teste")
        c.post("/v1/live/parada-emergencia", headers=h)
        recusado = c.post(
            "/v1/live/liberar", headers=h,
            json={"frase": "EU AUTORIZO OPERAR COM DINHEIRO REAL",
                  "reconciliado": True})
        assert recusado.status_code == 409
        sem_reconciliar = recusado
        detalhe = sem_reconciliar.json()["detail"]
        assert "reconcilie" in detalhe["acao"]
        assert detalhe["pendencias"], "a recusa precisa DIZER o que está pendente"
        # Repetir o pedido com a mesma afirmação do cliente continua sendo
        # recusado: só o SERVIDOR pode atestar a reconciliação.
        de_novo = c.post("/v1/live/liberar", headers=h,
                         json={"frase": "EU AUTORIZO OPERAR COM DINHEIRO REAL",
                               "reconciliado": True})
        assert de_novo.status_code == 409

        # Só após a reconciliação concluída no servidor a liberação passa.
        atual = m._DESKS.get("legacy") or m._desk_boot("legacy")
        m._DESKS["legacy"] = atual
        # Reconciliação REAL do servidor: corretora sem ordens e sem posições.
        rel = _reconciliar_com_corretora_que_confirma(atual["coordinator"])
        assert rel["seguro"] is True, rel["divergencias"]
        ok = c.post("/v1/live/liberar", headers=h,
                    json={"frase": "EU AUTORIZO OPERAR COM DINHEIRO REAL"})
        assert ok.status_code == 200, ok.text
        assert c.get("/v1/desk/status", headers=h).status_code == 200

    def test_panel_has_the_panic_button(self):
        html = Path("service/static/index.html").read_text(encoding="utf-8")
        assert "PARADA DE EMERGÊNCIA" in html and "paradaEmergencia()" in html
        assert "liveStatus()" in html


class TestRestauracaoDeAmbienteNoJob:
    """Bug real pego pelo ruff (F821): o finally restaurava uma variável que
    havia deixado de ser definida — toda falha de análise viraria NameError,
    escondendo o erro verdadeiro e deixando a chave do inquilino no processo."""

    def test_captura_e_restauracao_estao_no_mesmo_escopo(self):
        import inspect

        import service.app as m
        fonte = inspect.getsource(m.run_job)
        assert "_env_anterior = {" in fonte, "a captura do ambiente sumiu"
        assert fonte.index("_env_anterior = {") < fonte.index("finally:")
        assert "os.environ.pop(_k, None)" in fonte

    def test_job_com_falha_nao_deixa_chave_no_processo(self, tmp_path, monkeypatch):
        import os

        from fastapi.testclient import TestClient
        m = _load_app(tmp_path, monkeypatch)
        m._FAILS.clear()
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        c = TestClient(m.app)
        h = {"Authorization": "Bearer s3nh4"}
        c.post("/v1/config", headers=h, json={"vars": {
            "TRADINGAGENTS_LLM_PROVIDER": "groq", "GROQ_API_KEY": "gsk-do-tenant"}})
        r = c.post("/v1/jobs", headers=h,
                   json={"ticker": "NVDA", "trade_date": "2026-09-04"})
        assert r.status_code in (200, 201)
        import time as _t
        for _ in range(60):                      # espera o job terminar
            if os.environ.get("GROQ_API_KEY") is None:
                break
            _t.sleep(0.1)
        assert os.environ.get("GROQ_API_KEY") is None, (
            "a chave do inquilino ficou no ambiente do processo")


class TestMarcaDoProduto:
    """Os ícones precisam ser servidos — sem rota, o favicon e o ícone de
    atalho simplesmente não aparecem."""

    @staticmethod
    def _cliente():
        # sem `with`: o ciclo de vida reinicia o agendador, que só pode
        # subir uma vez por processo
        from fastapi.testclient import TestClient

        import service.app as m
        return TestClient(m.app)

    def test_the_favicon_is_served(self):
        r = self._cliente().get("/favicon.ico")
        assert r.status_code == 200 and len(r.content) > 0

    def test_the_logo_is_served(self):
        c = self._cliente()
        for arquivo in ("dz23-logo-192.png", "dz23-logo-512.png",
                        "favicon-32.png"):
            r = c.get(f"/static/marca/{arquivo}")
            assert r.status_code == 200, arquivo
            assert r.content[:4] == b"\x89PNG", f"{arquivo} não é PNG"

    def test_arbitrary_paths_are_refused(self):
        """Nome arbitrário leria qualquer arquivo do disco."""
        c = self._cliente()
        for tentativa in ("..%2F..%2Fapp.py", "segredo.env", "app.py"):
            r = c.get(f"/static/marca/{tentativa}")
            assert r.status_code in (404, 400), tentativa

    def test_the_pages_reference_the_brand(self):
        html = self._cliente().get("/").text
        assert "/static/marca/favicon" in html
        assert "/static/marca/dz23-logo-192.png" in html


class TestEncerrarAplicativo:
    """C009: o leigo precisa de um caminho de saída.

    O executável é windowed (sem console); fechar o navegador não para o
    servidor. Sem este contrato, o dono só consegue encerrar o app pelo
    Gerenciador de Tarefas.
    """

    def _client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = _load_app(tmp_path, monkeypatch)
        m._FAILS.clear()
        return TestClient(m.app), m

    def test_quit_exige_senha_do_dono(self, tmp_path, monkeypatch):
        c, _ = self._client(tmp_path, monkeypatch)
        assert c.post("/v1/app/quit").status_code == 401
        assert c.post(
            "/v1/app/quit", headers={"Authorization": "Bearer errada"}
        ).status_code == 401

    def test_quit_sem_gancho_nao_mente(self, tmp_path, monkeypatch):
        # Servidor iniciado fora do launcher (testes, uvicorn solto) não pode
        # fingir que encerrou: sem gancho registrado, responde 409 e segue vivo.
        c, _ = self._client(tmp_path, monkeypatch)
        r = c.post("/v1/app/quit", headers={"Authorization": "Bearer s3nh4"})
        assert r.status_code == 409

    def test_quit_dispara_o_desligamento_com_senha_certa(self, tmp_path, monkeypatch):
        c, m = self._client(tmp_path, monkeypatch)
        parado = threading.Event()
        m.set_shutdown_hook(parado.set)
        r = c.post("/v1/app/quit", headers={"Authorization": "Bearer s3nh4"})
        assert r.status_code == 200 and r.json()["ok"] is True
        assert parado.wait(5), "gancho de desligamento não foi chamado"

    def test_quit_indisponivel_fora_do_modo_dono(self, tmp_path, monkeypatch):
        # Em modo multi-tenant o encerramento por HTTP é vetado ANTES da
        # autenticação: nenhum inquilino pode derrubar o servidor compartilhado.
        c, m = self._client(tmp_path, monkeypatch)
        monkeypatch.setattr(m, "MULTI_TENANT_ENABLED", True)
        assert c.post("/v1/app/quit").status_code == 404

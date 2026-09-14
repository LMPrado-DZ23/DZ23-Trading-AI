"""Supabase como banco gerenciado: DSN correto e chave de serviço protegida."""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from tradingagents.infra.supabase import (
    SupabaseConfig,
    SupabaseConfigError,
    check_rest,
    config_from_env,
    resolve_database_url,
)

REF = "camhdvnrddwxwfmpvsdf"


class TestDsn:
    def test_pooler_is_the_default_port(self):
        cfg = SupabaseConfig(REF, "senha-forte")
        assert f"postgres.{REF}" in cfg.database_url() and ":6543/postgres" in cfg.database_url()

    def test_direct_connection_when_pooler_disabled(self):
        cfg = SupabaseConfig(REF, "senha", pooler=False)
        assert f"db.{REF}.supabase.co:5432" in cfg.database_url()

    def test_special_characters_in_password_are_escaped(self):
        dsn = SupabaseConfig(REF, "a b&c@d/e").database_url()
        assert "%20" in dsn and "%26" in dsn and "%40" in dsn
        assert " " not in dsn                      # DSN quebrado = 502 no deploy

    def test_missing_password_explains_where_to_get_it(self):
        with pytest.raises(SupabaseConfigError, match="Database password"):
            SupabaseConfig(REF, "").database_url()

    def test_invalid_ref_is_refused(self):
        with pytest.raises(SupabaseConfigError, match="identificador"):
            config_from_env({"SUPABASE_PROJECT_REF": "curto-demais"})


class TestResolucao:
    def test_explicit_database_url_wins(self):
        dsn, motivo = resolve_database_url(
            {"DATABASE_URL": "postgresql://x/y", "SUPABASE_PROJECT_REF": REF,
             "SUPABASE_DB_PASSWORD": "p"})
        assert dsn == "postgresql://x/y" and "explícita" in motivo

    def test_supabase_is_used_when_configured(self):
        dsn, motivo = resolve_database_url(
            {"SUPABASE_PROJECT_REF": REF, "SUPABASE_DB_PASSWORD": "p"})
        assert REF in dsn and REF in motivo

    def test_nothing_configured_falls_back_to_local(self):
        dsn, motivo = resolve_database_url({})
        assert dsn is None and "banco local" in motivo

    def test_bad_config_never_raises_at_boot(self):
        dsn, motivo = resolve_database_url({"SUPABASE_PROJECT_REF": "ruim"})
        assert dsn is None and "mal configurado" in motivo


class TestChaveDeServico:
    def test_rest_check_without_key_says_so(self):
        assert check_rest(SupabaseConfig(REF), "")["ok"] is False

    def test_service_key_never_returns_in_the_payload(self):
        def falso(cfg, key):
            return {"ok": True, "status_http": 200}
        out = check_rest(SupabaseConfig(REF), "chave-super-secreta", transport=falso)
        assert "chave-super-secreta" not in str(out)

    def test_panel_never_contains_a_service_role_key(self):
        from pathlib import Path
        for arquivo in ("service/static/index.html", "service/static/floor.html"):
            html = Path(arquivo).read_text(encoding="utf-8")
            assert "service_role" not in html
            assert "SUPABASE_SERVICE_KEY" not in html


class TestDiagnosticoNoServico:
    def _app(self, tmp_path, monkeypatch, **env):
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        import service.app as m
        importlib.reload(m)
        m._FAILS.clear()
        return m

    def test_route_reports_local_when_not_configured(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_PROJECT_REF", raising=False)
        m = self._app(tmp_path, monkeypatch)
        d = TestClient(m.app).get("/v1/admin/database",
                                  headers={"Authorization": "Bearer s3nh4"}).json()
        assert "banco local" in d["origem"]
        assert d["supabase"]["configurado"] is False
        assert "SUPABASE_PROJECT_REF" in d["supabase"]["proxima_acao"]

    def test_healthz_shows_database_origin(self, tmp_path, monkeypatch):
        m = self._app(tmp_path, monkeypatch)
        assert "banco" in TestClient(m.app).get("/healthz").json()

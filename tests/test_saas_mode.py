"""SAAS_MODE fail-closed (§7): ligado, exige multi-tenant + signing key + banco
transacional + billing store. Config ausente NUNCA cai no modo gratuito legado.
"""
from __future__ import annotations

import importlib

import pytest

from tradingagents.saas_config import is_truthy, missing_saas_requirements

_OK = {"multi_tenant": True, "tenant_store_ok": True, "signing_key": "k" * 40,
       "database_url": "postgres://x", "billing_store_ok": True}


class TestRequisitosPuros:
    def test_tudo_presente_nao_falta_nada(self):
        assert missing_saas_requirements(**_OK) == []

    def test_sem_multitenant(self):
        faltas = missing_saas_requirements(**{**_OK, "multi_tenant": False})
        assert any("MULTI_TENANT" in f for f in faltas)

    def test_store_nao_inicializado(self):
        faltas = missing_saas_requirements(**{**_OK, "tenant_store_ok": False})
        assert any("MULTI_TENANT" in f for f in faltas)

    def test_sem_signing_key(self):
        faltas = missing_saas_requirements(**{**_OK, "signing_key": None})
        assert any("SIGNING_KEY" in f for f in faltas)

    def test_sqlite_nao_serve_para_saas(self):
        faltas = missing_saas_requirements(**{**_OK, "database_url": None})
        assert any("DATABASE_URL" in f for f in faltas)

    def test_billing_store_indisponivel(self):
        faltas = missing_saas_requirements(**{**_OK, "billing_store_ok": False})
        assert any("billing" in f for f in faltas)

    def test_is_truthy_estrito(self):
        assert is_truthy("true") and is_truthy("1") and is_truthy("SIM")
        assert not is_truthy(None) and not is_truthy("false") and not is_truthy("talvez")


@pytest.fixture()
def _restaura_app(monkeypatch):
    """Depois de reloads com SAAS_MODE, devolve service.app a um estado legado
    limpo para não contaminar outros testes da suíte."""
    yield
    monkeypatch.delenv("SAAS_MODE", raising=False)
    monkeypatch.delenv("MULTI_TENANT_ENABLED", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
    import service.app as m
    importlib.reload(m)


class TestBootFailClosed:
    def test_saas_sem_signing_key_aborta_o_boot(self, tmp_path, monkeypatch, _restaura_app):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("SAAS_MODE", "true")
        monkeypatch.setenv("MULTI_TENANT_ENABLED", "true")
        monkeypatch.delenv("TENANT_TOKEN_SIGNING_KEY", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        import service.app as m
        with pytest.raises(RuntimeError, match="SAAS_MODE"):
            importlib.reload(m)

    def test_saas_sem_database_url_aborta_o_boot(self, tmp_path, monkeypatch, _restaura_app):
        # multi-tenant sobe em SQLite, mas SaaS exige DATABASE_URL -> aborta.
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("SAAS_MODE", "true")
        monkeypatch.setenv("MULTI_TENANT_ENABLED", "true")
        monkeypatch.setenv("TENANT_TOKEN_SIGNING_KEY", "k" * 40)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        import service.app as m
        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            importlib.reload(m)

    def test_sem_saas_mode_permite_legado(self, tmp_path, monkeypatch, _restaura_app):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.delenv("SAAS_MODE", raising=False)
        monkeypatch.setenv("MULTI_TENANT_ENABLED", "false")
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        import service.app as m
        importlib.reload(m)
        assert m.SAAS_MODE is False and m.MULTI_TENANT_ENABLED is False

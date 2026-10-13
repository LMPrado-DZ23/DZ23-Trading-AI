"""Dono Único é o padrão do aplicativo Windows (exigência do produto).

Sem pedido explícito de multi-tenant, a superfície SaaS (contas, planos,
webhooks, oauth) responde 404 e o funil de planos não se aplica ao dono
local. Quem pedir multi-tenant via ambiente reabre a superfície íntegra.
"""
import importlib

from fastapi.testclient import TestClient


def _load(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    for chave, valor in env.items():
        monkeypatch.setenv(chave, valor)
    import service.app as m
    importlib.reload(m)
    return m


def test_default_mode_blocks_the_saas_surface(tmp_path, monkeypatch):
    monkeypatch.delenv("MULTI_TENANT_ENABLED", raising=False)
    m = _load(tmp_path, monkeypatch)
    c = TestClient(m.app)
    assert m.SINGLE_OWNER_ENFORCED is True
    assert c.get("/v1/auth/signup-status").status_code == 404
    assert c.post("/v1/webhooks/woovi", content=b"{}").status_code == 404
    assert c.post("/v1/auth/login", json={}).status_code == 404
    # Login social inteiro: não basta bloquear o provedor que existe hoje —
    # um prefixo cobre microsoft e qualquer provedor futuro.
    assert c.get("/v1/auth/oauth/providers").status_code == 404
    assert c.get("/v1/auth/oauth/microsoft/start").status_code == 404
    assert c.get("/v1/auth/oauth/google/start").status_code == 404


def test_multitenant_flag_does_not_reopen_the_surface(tmp_path, monkeypatch):
    """DECISÃO ARQUITETURAL (dono único permanente): diferentemente de uma
    versão anterior deste teste, pedir multi-tenant por ambiente NÃO reabre o
    SaaS. A trava é fixa no código (fail-closed) e nenhuma flag a desarma."""
    m = _load(tmp_path, monkeypatch, MULTI_TENANT_ENABLED="true",
              TENANT_TOKEN_SIGNING_KEY="k" * 40)
    c = TestClient(m.app)
    assert m.SINGLE_OWNER_ENFORCED is True
    assert m.MULTI_TENANT_ENABLED is False      # o pedido foi ignorado
    assert c.get("/v1/auth/signup-status").status_code == 404
    assert c.post("/v1/auth/login", json={}).status_code == 404
    assert c.get("/v1/auth/oauth/providers").status_code == 404


def test_dono_local_nunca_e_trancado_fora_por_plano(tmp_path, monkeypatch):
    """A mesa do dono local não tem assinatura: um registro de plano vencido
    (resquício de qualquer fase anterior) não pode bloquear o próprio dono."""
    from datetime import datetime, timedelta, timezone

    monkeypatch.delenv("MULTI_TENANT_ENABLED", raising=False)
    m = _load(tmp_path, monkeypatch)
    sub = m.SUBS.ensure("legacy")
    sub.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    sub.status = "expired"
    m.SUBS.save(sub)
    c = TestClient(m.app)
    r = c.get("/v1/desk/status", headers={"Authorization": "Bearer s3nh4"})
    assert r.status_code == 200

"""Bootstrap da 1ª área: sem uma env dedicada (TENANT_BOOTSTRAP_TOKEN), o
ACCESS_PASSWORD já existente autoriza o bootstrap único — assim o operador não
precisa criar mais um segredo só para a configuração inicial. Quando a env
dedicada existe, ela tem precedência (o ACCESS_PASSWORD não vale como token)."""
from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


def _app(tmp_path, monkeypatch, *, access="s3nh4-access-longo", dedicado=None):
    monkeypatch.setenv("ACCESS_PASSWORD", access)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MULTI_TENANT_ENABLED", "true")
    monkeypatch.setenv("TENANT_TOKEN_SIGNING_KEY", "chave-de-assinatura-de-teste-0123456789")
    if dedicado is None:
        monkeypatch.delenv("TENANT_BOOTSTRAP_TOKEN", raising=False)
    else:
        monkeypatch.setenv("TENANT_BOOTSTRAP_TOKEN", dedicado)
    import service.app as m
    importlib.reload(m)
    m._FAILS.clear()
    return m, TestClient(m.app)


_CORPO = {"tenant_name": "DZ23", "email": "dono@exemplo.com",
          "password": "uma-senha-bem-longa-2026"}


def test_access_password_autoriza_quando_sem_token_dedicado(tmp_path, monkeypatch):
    m, c = _app(tmp_path, monkeypatch, access="s3nh4-access-longo")
    assert c.get("/v1/auth/status").json()["bootstrap_required"] is True
    r = c.post("/v1/auth/bootstrap",
               headers={"X-Tenant-Bootstrap": "s3nh4-access-longo"}, json=_CORPO)
    assert r.status_code == 200 and r.json()["role"] == "admin"
    # consumido: a primeira área existe
    assert c.get("/v1/auth/status").json()["bootstrap_required"] is False


def test_token_errado_recusa(tmp_path, monkeypatch):
    _, c = _app(tmp_path, monkeypatch, access="s3nh4-access-longo")
    r = c.post("/v1/auth/bootstrap",
               headers={"X-Tenant-Bootstrap": "errado"}, json=_CORPO)
    assert r.status_code == 401


def test_token_dedicado_tem_precedencia(tmp_path, monkeypatch):
    _, c = _app(tmp_path, monkeypatch, access="s3nh4-access-longo", dedicado="token-dedicado-forte")
    # com env dedicada, o ACCESS_PASSWORD NÃO vale mais como token de bootstrap
    assert c.post("/v1/auth/bootstrap",
                  headers={"X-Tenant-Bootstrap": "s3nh4-access-longo"},
                  json=_CORPO).status_code == 401
    assert c.post("/v1/auth/bootstrap",
                  headers={"X-Tenant-Bootstrap": "token-dedicado-forte"},
                  json=_CORPO).status_code == 200

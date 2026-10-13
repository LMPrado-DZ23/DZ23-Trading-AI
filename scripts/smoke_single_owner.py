"""Smoke de DONO ÚNICO (single-owner) — substitui o antigo smoke multi-tenant.

Carrega o serviço no pior cenário (todas as flags de SaaS ligadas) e prova, no
nível de integração, que:
  - o multi-tenant é forçado a desligado e não há tenant store;
  - toda a superfície SaaS responde 404;
  - o acesso do dono por senha funciona e nunca fica aberto;
  - a mesa opera em modo não-LIVE.
Complementa a prova unitária em tests/test_single_owner_g2.py.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="dz23-single-owner-")
# Liga de propósito tudo que, num SaaS, abriria a superfície:
os.environ["MULTI_TENANT_ENABLED"] = "true"
os.environ["SIGNUP_ENABLED"] = "true"
os.environ["TENANT_TOKEN_SIGNING_KEY"] = "z" * 64
os.environ["TENANT_BOOTSTRAP_TOKEN"] = "bootstrap-secret"
os.environ["ACCESS_PASSWORD"] = "senha-do-dono"

from fastapi.testclient import TestClient

import service.app as app_module
from service.app import app

# 1) Multi-tenant forçado a desligado, apesar do ambiente pedir.
assert app_module.SINGLE_OWNER_ENFORCED is True
assert app_module.MULTI_TENANT_ENABLED is False
assert app_module.TENANT_STORE is None

OWNER = {"Authorization": "Bearer senha-do-dono"}

with TestClient(app) as client:
    # 2) Toda a superfície SaaS está inalcançável (404), com e sem auth.
    saas = [
        ("GET", "/v1/auth/signup-status"),
        ("POST", "/v1/auth/signup"),
        ("POST", "/v1/auth/login"),
        ("POST", "/v1/auth/bootstrap"),
        ("GET", "/v1/auth/oauth/providers"),
        ("GET", "/v1/auth/oauth/google/start"),
        ("POST", "/v1/admin/tenants"),
        ("GET", "/v1/admin/users"),
        ("POST", "/v1/admin/users"),
        ("GET", "/v1/billing/planos"),
        ("GET", "/v1/billing/status"),
        ("POST", "/v1/billing/checkout"),
        ("POST", "/v1/webhooks/woovi"),
    ]
    for metodo, caminho in saas:
        for headers in ({}, OWNER):
            r = client.request(metodo, caminho, headers=headers)
            assert r.status_code == 404, f"{metodo} {caminho} -> {r.status_code}"

    # 3) O acesso do dono por senha funciona — e nunca fica aberto.
    assert client.get("/v1/config").status_code == 401
    assert client.get("/v1/config", headers=OWNER).status_code == 200
    assert client.get("/v1/jobs", headers=OWNER).json()["jobs"] == []

    # 4) Status público honesto e mesa em modo não-LIVE.
    status = client.get("/v1/auth/status").json()
    assert status["multi_tenant"] is False
    assert status["live_trading"] is False
    office = client.get("/v1/office/snapshot", headers=OWNER)
    assert office.status_code == 200, office.text
    assert office.json()["live_trading"] is False

print("single-owner smoke passed")

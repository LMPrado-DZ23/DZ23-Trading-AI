import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="dz23-multitenant-")
os.environ["MULTI_TENANT_ENABLED"] = "true"
os.environ["TENANT_TOKEN_SIGNING_KEY"] = "z" * 64
os.environ["TENANT_BOOTSTRAP_TOKEN"] = "bootstrap-secret"
os.environ["ACCESS_PASSWORD"] = "unused-legacy-password"

from fastapi.testclient import TestClient

from service.app import TENANT_STORE, app

with TestClient(app) as client:
    response = client.post("/v1/auth/bootstrap", headers={"X-Tenant-Bootstrap": "bootstrap-secret"}, json={"tenant_name": "Acme", "email": "admin@acme.test", "password": "correct horse battery staple"})
    assert response.status_code == 200, response.text
    tenant_id = response.json()["tenant_id"]
    login = client.post("/v1/auth/login", json={"tenant_id": tenant_id, "email": "admin@acme.test", "password": "correct horse battery staple"})
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/v1/config", headers=headers).status_code == 200
    assert client.get("/v1/jobs", headers=headers).json()["jobs"] == []
    assert client.post("/v1/execution/paper", headers=headers, json={}).status_code in {400, 422, 423}
    beta = TENANT_STORE.create_tenant("Beta")
    beta_user = TENANT_STORE.create_user("admin@beta.test", "correct horse battery staple")
    TENANT_STORE.add_member(beta, beta_user, "admin")
    beta_token = client.post("/v1/auth/login", json={"tenant_id": beta, "email": "admin@beta.test", "password": "correct horse battery staple"}).json()["access_token"]
    beta_headers = {"Authorization": f"Bearer {beta_token}"}
    assert client.get("/v1/jobs", headers=beta_headers).json()["jobs"] == []
    assert client.get("/v1/agency/events", headers=headers).json()["events"] == []
    assert client.get("/v1/agency/events", headers=beta_headers).json()["events"] == []
print("multi-tenant smoke passed")

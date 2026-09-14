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
    assert client.get("/v1/auth/status").json()["bootstrap_required"] is True
    response = client.post("/v1/auth/bootstrap", headers={"X-Tenant-Bootstrap": "bootstrap-secret"}, json={"tenant_name": "Acme", "email": "admin@acme.test", "password": "correct horse battery staple"})
    assert response.status_code == 200, response.text
    tenant_id = response.json()["tenant_id"]
    login = client.post("/v1/auth/login", json={"tenant_id": tenant_id, "email": "admin@acme.test", "password": "correct horse battery staple"})
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/v1/auth/me", headers=headers)
    assert me.status_code == 200 and me.json()["role"] == "admin"
    assert client.get("/v1/auth/status").json()["bootstrap_required"] is False
    created = client.post("/v1/admin/users", headers=headers, json={"email": "analyst@acme.test", "password": "another correct password", "role": "analyst"})
    assert created.status_code == 200, created.text
    members = client.get("/v1/admin/users", headers=headers)
    assert members.status_code == 200 and len(members.json()["users"]) == 2
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
    office = client.get("/v1/office/snapshot", headers=headers)
    assert office.status_code == 200, office.text
    assert office.json()["mode"] == "PAPER_SHADOW"
    assert office.json()["live_trading"] is False
    assert office.json()["job_store"]["status"] == "ok"
    office_events = client.get("/v1/office/events", headers=headers)
    assert office_events.status_code == 200 and office_events.json()["schema_version"] == "1.0"
    assert client.post("/v1/auth/logout", headers=beta_headers).status_code == 200
    assert client.get("/v1/auth/me", headers=beta_headers).status_code == 401
print("multi-tenant smoke passed")

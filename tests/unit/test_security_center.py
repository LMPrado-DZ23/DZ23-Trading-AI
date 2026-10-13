from fastapi.testclient import TestClient

from service.app import app

client = TestClient(app)

def test_security_incident_center_endpoint():
    resp = client.get("/v1/security/incident-center")
    assert resp.status_code == 200
    data = resp.json()
    assert data["kill_switch_active"] is False
    assert data["real_money_execution"] is False
    assert "audit_logs" in data

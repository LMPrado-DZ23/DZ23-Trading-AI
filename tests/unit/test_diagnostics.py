from fastapi.testclient import TestClient
from service.app import app

client = TestClient(app)

def test_run_diagnostics_endpoint():
    resp = client.post("/v1/diagnostics/run")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "checks" in data
    assert data["checks"]["live_trading_locked"] is True

from fastapi.testclient import TestClient


def test_personal_routes_disabled_by_default(monkeypatch):
    monkeypatch.delenv("PERSONAL_TRADING_LAB_ENABLED", raising=False)
    import importlib
    import service.app as app_module

    importlib.reload(app_module)
    client = TestClient(app_module.app)

    resp = client.get(
        "/v1/personal/exchanges",
        headers={"Authorization": "Bearer test-password"},
    )
    assert resp.status_code == 404


def test_personal_routes_read_only_when_enabled(monkeypatch):
    monkeypatch.setenv("PERSONAL_TRADING_LAB_ENABLED", "true")
    import importlib
    import service.app as app_module

    importlib.reload(app_module)
    client = TestClient(app_module.app)

    resp = client.get(
        "/v1/personal/exchanges",
        headers={"Authorization": "Bearer test-password"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert "exchanges" in payload or "fixed_rooms" in payload
    if "exchanges" in payload:
        assert all(item["live_enabled"] is False for item in payload["exchanges"])
        assert all(item["execution_enabled"] is False for item in payload["exchanges"])

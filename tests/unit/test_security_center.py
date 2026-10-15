from fastapi.testclient import TestClient

from service.app import app

client = TestClient(app)


def test_security_incident_center_removido_retorna_404():
    # V9-SEC-06/F7.2: stub de resposta fixa e sem auth eliminado. Estado real
    # de kill-switch já é exposto por /readyz e /v1/execution/kill-switch
    # (autenticado). Trava a remoção contra remontagem acidental.
    resp = client.get("/v1/security/incident-center")
    assert resp.status_code == 404

from fastapi.testclient import TestClient

from service.app import app

client = TestClient(app)


def test_diagnostics_run_removido_retorna_404():
    # V9-SEC-06/F7.2: stub de resposta fixa e sem auth eliminado. Estado real
    # de saúde já é exposto por /healthz e /readyz. Trava a remoção contra
    # remontagem acidental da superfície.
    resp = client.post("/v1/diagnostics/run")
    assert resp.status_code == 404

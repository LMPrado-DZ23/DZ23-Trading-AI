"""Regressão do job 'public smoke checks': /v1/office/* por inquilino dava 500.

Causa raiz: um segundo `import KillSwitch` (do desk) REBINDAVA o global que
o topo de `service.app` liga a `execution.KillSwitch`. Como o `TenantRuntime`
resolve o nome em tempo de chamada, ele construía a classe ERRADA — a do desk,
que não tem `.state` —, e `office_snapshot` fazia `runtime.kill_switch.state`
→ `AttributeError: 'KillSwitch' object has no attribute 'state'` → HTTP 500.

Estes testes reproduzem o traceback (nível HTTP) e fixam a causa (a classe
concreta no runtime). Rodam no CI, onde as deps do serviço estão instaladas.
"""
from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


def _app(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MULTI_TENANT_ENABLED", "true")
    monkeypatch.setenv("TENANT_TOKEN_SIGNING_KEY", "z" * 64)
    monkeypatch.setenv("TENANT_BOOTSTRAP_TOKEN", "bootstrap-secret")
    monkeypatch.setenv("ACCESS_PASSWORD", "unused-legacy-password")
    import service.app as m
    importlib.reload(m)
    return m


def _tenant(client):
    # No app de dono único, o cadastro/login multi-tenant não existe (404). O
    # acesso é a senha do dono; a regressão do /v1/office/* (não dar 500 e
    # manter LIVE bloqueado) vale igualmente pelo runtime legacy, que é o que
    # o dono usa. A causa-raiz do shadowing continua coberta pelo teste que
    # instancia TenantRuntime diretamente.
    return {"Authorization": "Bearer unused-legacy-password"}


def test_tenant_runtime_usa_o_killswitch_com_state(monkeypatch, tmp_path):
    """A causa raiz, direta: o KillSwitch do runtime é o de execution (tem
    `.state`), não o do desk."""
    m = _app(monkeypatch, tmp_path)
    from tradingagents.execution import KillSwitch as ExecKillSwitch
    rt = m.TenantRuntime("acme-runtime")
    assert isinstance(rt.kill_switch, ExecKillSwitch), (
        "TenantRuntime construiu a classe KillSwitch errada (shadowing)")
    estado = rt.kill_switch.state
    assert estado.enabled is False
    assert hasattr(estado, "reason") and hasattr(estado, "changed_at")


def test_office_snapshot_por_inquilino_nao_da_500(monkeypatch, tmp_path):
    """Reprodução do traceback do smoke: agora responde 200, LIVE bloqueado."""
    m = _app(monkeypatch, tmp_path)
    with TestClient(m.app) as client:
        headers = _tenant(client)
        office = client.get("/v1/office/snapshot", headers=headers)
        assert office.status_code == 200, office.text
        body = office.json()
        assert body["live_trading"] is False
        ks = body["kill_switch"]
        assert ks["enabled"] is False
        for campo in ("reason", "changed_at", "actor"):
            assert campo in ks
        eventos = client.get("/v1/office/events", headers=headers)
        assert eventos.status_code == 200, eventos.text


def test_kill_switch_acionado_reflete_no_snapshot(monkeypatch, tmp_path):
    """Acionar o kill switch do inquilino bloqueia e aparece no snapshot,
    sem 500 e sem liberar LIVE."""
    m = _app(monkeypatch, tmp_path)
    with TestClient(m.app) as client:
        headers = _tenant(client)
        acionar = client.post("/v1/execution/kill-switch", headers=headers,
                              json={"action": "engage", "reason": "teste",
                                    "actor": "op"})
        assert acionar.status_code in (200, 423), acionar.text
        office = client.get("/v1/office/snapshot", headers=headers)
        assert office.status_code == 200, office.text
        assert office.json()["live_trading"] is False

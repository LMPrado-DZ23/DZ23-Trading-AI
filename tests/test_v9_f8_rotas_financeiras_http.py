"""F8/§6: HTTP regression for the 5 financially sensitive routes without a
direct test reference at the F7 recount, plus /readyz and /metrics auth.

Routes covered here (f7-rotas-classificacao.md §6, priority #10-#14):
- POST /v1/broker/order              -> must stay 410 (legacy, never sends)
- POST /v1/execution/reconciliation  -> runs the guard the router requires
- POST /v1/execution/paper           -> the governed paper path end-to-end
- POST /v1/execution/shadow          -> records intention, orders_sent == 0
- GET  /v1/execution/shadow          -> summary reflects recorded trades

And the §7 residual: /readyz behavioral shape, /metrics auth surface.
Every route is also asserted 401 without credentials, so a future auth
regression on these financially sensitive paths is caught by HTTP, not by
textual reference.
"""
from __future__ import annotations

import importlib
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import service.app as appmod
from tradingagents.reconciliation_worker import ReconciliationMonitor

_OWNER_PASSWORD = "f8-synthetic-owner-password"


@pytest.fixture()
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ACCESS_PASSWORD", _OWNER_PASSWORD)
    monkeypatch.delenv("MULTI_TENANT_ENABLED", raising=False)
    importlib.reload(appmod)
    with TestClient(appmod.app) as client:
        yield client


def _auth():
    return {"Authorization": f"Bearer {_OWNER_PASSWORD}"}


def _decision_body(*, decision_id="dec-f8-1", client_order_id=None,
                   side="BUY", quantity="0.1", price="100"):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "decision_id": decision_id,
        "run_id": "run-f8-1",
        "strategy_id": "strat-f8-1",
        "desk_id": "strategy",
        "instrument_id": "BTCUSDT",
        "side": side,
        "order_type": "MARKET",
        "entry_price": price,
        "stop_loss": "98",
        "take_profit": "105",
        "risk_amount": "2",
        "risk_pct_equity": "0.002",
        "quantity": quantity,
        "time_in_force": "GTC",
        "signal_ttl_seconds": 60,
        "confidence": "0.8",
        "regime": "TREND",
        "evidence_ids": ["ev-f8-1"],
        "agent_votes": [],
        "observed_at": now,
        "bid": "99.9",
        "ask": "100.1",
        "last": price,
        "volume": "10",
        "sequence": 1,
        "client_order_id": client_order_id or f"paper-{decision_id}",
    }


def _shadow_body(*, client_order_id="shadow-f8-1", side="BUY", quantity="0.5"):
    return {
        "client_order_id": client_order_id,
        "instrument_id": "BTCUSDT",
        "side": side,
        "order_type": "MARKET",
        "quantity": quantity,
        "price": "100",
        "bid": "99.9",
        "ask": "100.1",
        "last": "100",
        "stop_loss": "98",
        "take_profit": "105",
    }


# ---------------------------------------------------------------------------
# §6 #10 — POST /v1/broker/order: rota legada permanentemente removida (410).
# Nenhum caminho de ordem pode reabri-la; o contrato é a RECUSA.
# ---------------------------------------------------------------------------

def test_broker_order_legacy_route_is_permanently_gone(api):
    r = api.post("/v1/broker/order", headers=_auth(), json={"whatever": True})
    assert r.status_code == 410, r.text
    detalhe = r.json()["detail"]
    assert "removido" in detalhe and "/v1/execution/paper" in detalhe


def test_broker_order_requires_auth_before_anything(api):
    r = api.post("/v1/broker/order", json={"whatever": True})
    assert r.status_code == 401, r.text


# ---------------------------------------------------------------------------
# §6 #12 — POST /v1/execution/reconciliation: roda o portão que o router exige.
# Sem provider remoto configurado o relatório tem de ser HONESTO: UNKNOWN +
# unsafe (o monitor captura o RuntimeError e não mente "MATCHED"). O contrato
# é fail-closed: sem um MATCHED real a execução em papel permanece 423.
# ---------------------------------------------------------------------------

def test_reconciliation_without_remote_is_honestly_unsafe(api):
    r = api.post("/v1/execution/reconciliation", headers=_auth())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "UNKNOWN"
    assert body["safe"] is False
    assert body["missing_local"] == []
    assert body["missing_remote"] == []
    assert any("RuntimeError" in item for item in body["mismatches"])


def test_reconciliation_requires_auth(api):
    r = api.post("/v1/execution/reconciliation")
    assert r.status_code == 401, r.text


# ---------------------------------------------------------------------------
# §6 #11 — POST /v1/execution/paper: caminho governado Decision->Risk->Adapter.
# (1) Sem reconciliação segura o portão bloqueia e NADA muda no adapter —
#     mesmo depois de rodar a reconciliação honesta (UNKNOWN) o 423 persiste.
# (2) Com um remoto espelhado (MATCHED provado), a MESMA ordem é aceita e vira
#     ordem + posição no snapshot oficial do adapter (nada sai por rota lateral).
# ---------------------------------------------------------------------------

def _mirror_reconciliation(monkeypatch):
    snapshot = appmod.PAPER_ADAPTER.reconciliation_snapshot
    monitor = ReconciliationMonitor(snapshot, snapshot)
    monkeypatch.setattr(appmod, "PAPER_RECONCILIATION", monitor)
    return monitor


def test_paper_execution_stays_blocked_while_reconciliation_unsafe(api):
    body = _decision_body()
    blocked = api.post("/v1/execution/paper", headers=_auth(), json=body)
    assert blocked.status_code == 423, blocked.text
    assert "reconcilia" in blocked.json()["detail"].lower()

    unsafe = api.post("/v1/execution/reconciliation", headers=_auth())
    assert unsafe.status_code == 200, unsafe.text
    assert unsafe.json()["safe"] is False

    still = api.post("/v1/execution/paper", headers=_auth(), json=body)
    assert still.status_code == 423, still.text
    assert "reconcilia" in still.json()["detail"].lower()
    snap = appmod.PAPER_ADAPTER.reconciliation_snapshot()
    assert snap["orders"] == [] and snap["positions"] == []


def test_paper_execution_accepted_after_mirrored_reconciliation(api, monkeypatch):
    _mirror_reconciliation(monkeypatch)
    rec = api.post("/v1/execution/reconciliation", headers=_auth())
    assert rec.status_code == 200, rec.text
    assert rec.json()["status"] == "MATCHED"
    assert rec.json()["safe"] is True

    r = api.post("/v1/execution/paper", headers=_auth(), json=_decision_body())
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["risk"]["status"] == "APPROVE"
    assert payload["order"]["client_order_id"] == "paper-dec-f8-1"
    assert payload["order"]["status"] == "FILLED"

    # O estado oficial (adapter do runtime) registrou a ordem e a posição.
    snap = appmod.PAPER_ADAPTER.reconciliation_snapshot()
    assert [o["client_order_id"] for o in snap["orders"]] == ["paper-dec-f8-1"]
    assert [p["instrument_id"] for p in snap["positions"]] == ["BTCUSDT"]


def test_paper_execution_rejects_missing_fields(api):
    api.post("/v1/execution/reconciliation", headers=_auth())
    r = api.post("/v1/execution/paper", headers=_auth(), json={"side": "BUY"})
    assert r.status_code == 422, r.text


def test_paper_execution_requires_auth(api):
    r = api.post("/v1/execution/paper", json=_decision_body())
    assert r.status_code == 401, r.text


def test_paper_execution_blocked_by_kill_switch(api, monkeypatch):
    # Com a reconciliação espelhada (MATCHED) o único bloqueio restante é o
    # kill-switch — o 423 é atribuível a ele, não ao portão de reconciliação.
    _mirror_reconciliation(monkeypatch)
    api.post("/v1/execution/reconciliation", headers=_auth())
    engage = api.post("/v1/execution/kill-switch", headers=_auth(),
                      json={"action": "engage", "reason": "f8"})
    assert engage.status_code in (200, 423), engage.text
    r = api.post("/v1/execution/paper", headers=_auth(), json=_decision_body())
    assert r.status_code == 423, r.text


# ---------------------------------------------------------------------------
# §6 #13/#14 — GET/POST /v1/execution/shadow: registra intenção, jamais
# envia ordem. A promessa de "shadow" tem de aparecer no corpo: orders_sent=0.
# ---------------------------------------------------------------------------

def test_shadow_starts_empty_and_never_counts_orders(api):
    s = api.get("/v1/execution/shadow", headers=_auth())
    assert s.status_code == 200, s.text
    assert s.json() == {"trades": 0, "closed": 0, "hypothetical_pnl": "0", "orders_sent": 0}

    r = api.post("/v1/execution/shadow", headers=_auth(), json=_shadow_body())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["orders_sent"] == 0
    assert body["status"] == "OPEN"
    assert body["reference_price"] == "100"

    after = api.get("/v1/execution/shadow", headers=_auth())
    assert after.status_code == 200, after.text
    resumo = after.json()
    assert resumo["trades"] == 1 and resumo["orders_sent"] == 0
    assert resumo["closed"] == 0 and resumo["hypothetical_pnl"] == "0"


def test_shadow_execution_rejects_invalid_side(api):
    r = api.post("/v1/execution/shadow", headers=_auth(),
                 json=_shadow_body(side="SIDEWAYS"))
    assert r.status_code == 422, r.text


def test_shadow_routes_require_auth(api):
    assert api.get("/v1/execution/shadow").status_code == 401
    assert api.post("/v1/execution/shadow",
                    json=_shadow_body()).status_code == 401


# ---------------------------------------------------------------------------
# §7 — /readyz: contrato comportamental mínimo (status 200 + campos reais).
# Já é impossível /readyz mentir: ele deriva do kill-switch, do adapter e do
# barramento de eventos do processo.
# ---------------------------------------------------------------------------

def test_readyz_reports_real_state_and_never_dies(api):
    r = api.get("/readyz")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ready"
    assert body["kill_switch"] is False
    assert body["paper"]["status"] == "READY"
    assert body["paper"]["environment"] == "PAPER"
    assert body["paper"]["account_id"] == "paper-account"
    assert isinstance(body["event_sequence"], int)

    engage = api.post("/v1/execution/kill-switch", headers=_auth(),
                      json={"action": "engage", "reason": "f8-readyz"})
    assert engage.status_code in (200, 423), engage.text
    blocked = api.get("/readyz")
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["status"] == "blocked"
    assert blocked.json()["kill_switch"] is True


# ---------------------------------------------------------------------------
# §6 #1 — /metrics: autenticada nos dois sentidos (401 sem credencial;
# snapshot real com). O consumidor externo continua sendo pergunta aberta,
# mas a superfície autenticada fica travada por teste HTTP.
# ---------------------------------------------------------------------------

def test_metrics_requires_auth_and_returns_snapshot(api):
    assert api.get("/metrics").status_code == 401
    r = api.get("/metrics", headers=_auth())
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), dict)

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="dz23-agency-")
os.environ["ACCESS_PASSWORD"] = "test-password"

from fastapi.testclient import TestClient

from service.app import app

client = TestClient(app)
headers = {"Authorization": "Bearer test-password"}
assert client.get("/healthz").status_code == 200
assert client.get("/floor").status_code == 200
snapshot = client.get("/v1/agency/snapshot", headers=headers)
assert snapshot.status_code == 200
assert len(snapshot.json()["agents"]) >= 8
meeting = client.post("/v1/agency/meetings", headers=headers, json={"meeting_id": "m-smoke", "meeting_type": "StrategyCommittee", "desk_id": "strategy", "participants": ["strategy-agent", "risk-advisor"], "quorum": 2})
assert meeting.status_code == 200
vote = client.post("/v1/agency/meetings/m-smoke/votes", headers=headers, json={"agent_id": "strategy-agent", "action": "BUY", "confidence": 0.7, "evidence_ids": ["ev-1"]})
assert vote.status_code == 200
status = client.post("/v1/agency/agents/strategy-agent/status", headers=headers, json={"status": "ANALYZING"})
assert status.status_code == 200
assert client.get("/v1/agency/events", headers=headers).json()["events"]
paper = client.post("/v1/execution/paper", headers=headers, json={"decision_id": "smoke-decision", "strategy_id": "smoke-strategy", "instrument_id": "BTCUSDT", "side": "BUY", "entry_price": "100", "stop_loss": "98", "take_profit": "105", "risk_amount": "10", "risk_pct_equity": "0.001", "quantity": "1", "confidence": "0.8", "evidence_ids": ["smoke-evidence"], "bid": "99.9", "ask": "100.1", "last": "100"})
assert paper.status_code == 200, paper.text
assert paper.json()["risk"]["status"] == "APPROVE"
assert paper.json()["order"]["status"] == "FILLED"
assert client.get("/v1/execution/health", headers=headers).json()["kill_switch"]["enabled"] is False
engage = client.post("/v1/execution/kill-switch", headers=headers, json={"action": "engage", "reason": "smoke"})
assert engage.status_code == 200 and engage.json()["enabled"] is True
blocked = client.post("/v1/execution/paper", headers=headers, json={"decision_id": "blocked", "strategy_id": "smoke-strategy", "instrument_id": "BTCUSDT", "side": "BUY", "entry_price": "100", "stop_loss": "98", "take_profit": "105", "risk_amount": "10", "risk_pct_equity": "0.001", "quantity": "1", "confidence": "0.8", "evidence_ids": ["smoke-evidence"], "bid": "99.9", "ask": "100.1", "last": "100"})
assert blocked.status_code == 423
reset = client.post("/v1/execution/kill-switch", headers=headers, json={"action": "reset", "actor": "smoke", "reconciliation_ok": True})
assert reset.status_code == 423
shadow = client.post("/v1/execution/shadow", headers=headers, json={"client_order_id": "smoke-shadow", "instrument_id": "BTCUSDT", "side": "BUY", "quantity": "1", "price": "100", "bid": "99.9", "ask": "100.1", "last": "100"})
assert shadow.status_code == 200 and shadow.json()["orders_sent"] == 0
assert client.get("/v1/execution/shadow", headers=headers).json()["trades"] >= 1
legacy = client.post("/v1/broker/order", headers=headers, json={"job_id": "smoke", "symbol": "BTC/USDT", "side": "buy", "amount": 1})
assert legacy.status_code == 410
print("agency API smoke passed")

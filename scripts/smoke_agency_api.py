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
print("agency API smoke passed")

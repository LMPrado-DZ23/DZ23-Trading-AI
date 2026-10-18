"""A prontidão REAL aceita somente homologação IQ training reconciliada."""
from __future__ import annotations

from datetime import datetime, timezone

from service import app as module
from service.iq_option_training_homologation import (
    IQ_OPTION_TRAINING_PROOF_KEY,
    IQ_OPTION_TRAINING_PROOF_SCHEMA,
)
from tradingagents.brokers.ccxt_bridge import (
    live_execution_credential_fingerprint,
)


class Store:
    def __init__(self):
        self.documents = {}

    def carregar(self, tenant_id, key):
        return self.documents.get((tenant_id, key))


def _cfg(token="training-token"):
    return {
        "TRADINGAGENTS_EXCHANGE": "iq_option",
        "TRADINGAGENTS_BROKER_MODE": "testnet",
        "LIVE_EXECUTION_EXCHANGE": "iq_option",
        "IQ_OPTION_MCP_BEARER_TOKEN": "read-token",
        "IQ_OPTION_MCP_LIVE_BEARER_TOKEN": token,
    }


def test_prova_training_reconciliada_satisfaz_gate_sem_expor_fingerprints(
        monkeypatch):
    store = Store()
    cfg = _cfg()
    store.documents[("tenant-1", IQ_OPTION_TRAINING_PROOF_KEY)] = {
        "schema": IQ_OPTION_TRAINING_PROOF_SCHEMA,
        "exchange": "iq_option",
        "mode": "training",
        "filled_external_order": True,
        "reconciled": True,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "credential_fingerprint": live_execution_credential_fingerprint(
            module._env_live_execution(cfg)),
        "external_order_fingerprint": "b" * 64,
    }
    monkeypatch.setattr(module, "_escola_of", lambda _request: {
        "armazem": store,
        "tenant_id": "tenant-1",
    })

    public, valid = module._prova_testnet(object(), cfg)

    assert valid is True
    assert public["valida"] is True
    assert public["mode"] == "training"
    assert "credential_fingerprint" not in public
    assert "external_order_fingerprint" not in public


def test_rotacao_do_token_invalida_prova_training(monkeypatch):
    store = Store()
    original = _cfg()
    store.documents[("tenant-1", IQ_OPTION_TRAINING_PROOF_KEY)] = {
        "schema": IQ_OPTION_TRAINING_PROOF_SCHEMA,
        "exchange": "iq_option",
        "mode": "training",
        "filled_external_order": True,
        "reconciled": True,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "credential_fingerprint": live_execution_credential_fingerprint(
            module._env_live_execution(original)),
        "external_order_fingerprint": "b" * 64,
    }
    monkeypatch.setattr(module, "_escola_of", lambda _request: {
        "armazem": store,
        "tenant_id": "tenant-1",
    })

    public, valid = module._prova_testnet(
        object(), _cfg(token="rotated-token"))

    assert valid is False
    assert "alteradas" in public["motivo"]

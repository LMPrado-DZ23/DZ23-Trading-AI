"""Rotas IQ training exigem preparo e consentimento a cada mutação."""
from __future__ import annotations

import hashlib
import importlib
from copy import deepcopy
from types import SimpleNamespace

from tradingagents.brokers.ccxt_bridge import (
    live_execution_credential_fingerprint,
)
from tradingagents.brokers.iq_option_binary import TRAINING_CONFIRM_PHRASE

H = {"Authorization": "Bearer s3nh4"}


class Store:
    def __init__(self):
        self.documents = {}

    def carregar(self, tenant, key):
        return deepcopy(self.documents.get((tenant, key)))

    def salvar(self, tenant, key, value):
        self.documents[(tenant, key)] = deepcopy(value)


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import service.app as module
    importlib.reload(module)
    module._FAILS.clear()
    from fastapi.testclient import TestClient
    return module, TestClient(module.app)


def _cfg():
    return {
        "TRADINGAGENTS_EXCHANGE": "iq_option",
        "TRADINGAGENTS_BROKER_MODE": "testnet",
        "LIVE_EXECUTION_EXCHANGE": "iq_option",
        "IQ_OPTION_MCP_BEARER_TOKEN": "read-token",
        "IQ_OPTION_MCP_LIVE_BEARER_TOKEN": "training-token",
    }


def _prepared(module, store, cfg):
    store.salvar("legacy", module._CHAVE_PREPARACAO_IQ_TREINO, {
        "schema": module._SCHEMA_PREPARACAO_IQ_TREINO,
        "exchange": "iq_option",
        "account_kind": "training",
        "authenticated_read": True,
        "credential_fingerprint": live_execution_credential_fingerprint(
            module._env_live_execution(cfg)),
        "verificada_em": "2026-10-17T12:00:00+00:00",
        "ordem_enviada": False,
        "homologacao_concluida": False,
    })


def test_execucao_exige_frase_exata_antes_de_montar_runtime(
        tmp_path, monkeypatch):
    module, client = _app(tmp_path, monkeypatch)
    cfg = _cfg()
    store = Store()
    _prepared(module, store, cfg)
    monkeypatch.setattr(module, "load_cfg", lambda _tenant: cfg)
    monkeypatch.setattr(module, "_escola_of", lambda _request: {
        "armazem": store, "tenant_id": "legacy"})
    calls = []

    async def build(**_kwargs):
        calls.append("build")
        raise AssertionError("não deveria montar")

    monkeypatch.setattr(
        module, "build_iq_option_training_homologation_runtime", build)

    response = client.post(
        "/v1/live/homologacao-iq-option/executar-conta-treino",
        headers=H,
        json={
            "confirmo_uma_ordem_treinamento": True,
            "frase_confirmacao": "sim",
        },
    )

    assert response.status_code == 422
    assert calls == []


def test_execucao_exige_preparo_vinculado_a_credencial(
        tmp_path, monkeypatch):
    module, client = _app(tmp_path, monkeypatch)
    cfg = _cfg()
    store = Store()
    monkeypatch.setattr(module, "load_cfg", lambda _tenant: cfg)
    monkeypatch.setattr(module, "_escola_of", lambda _request: {
        "armazem": store, "tenant_id": "legacy"})
    calls = []
    monkeypatch.setattr(
        module, "build_iq_option_training_homologation_runtime",
        lambda **_kwargs: calls.append("build"))

    response = client.post(
        "/v1/live/homologacao-iq-option/executar-conta-treino",
        headers=H,
        json={
            "confirmo_uma_ordem_treinamento": True,
            "frase_confirmacao": TRAINING_CONFIRM_PHRASE,
            "client_order_id": "training-1",
            "instrument": "EUR/USD",
            "direction": "call",
            "amount": "1",
            "expiration": 1_800_000_300,
            "min_profit_percent": 80,
        },
    )

    assert response.status_code == 409
    assert calls == []


def test_execucao_controlada_nao_expoe_position_id(
        tmp_path, monkeypatch):
    module, client = _app(tmp_path, monkeypatch)
    cfg = _cfg()
    store = Store()
    _prepared(module, store, cfg)
    monkeypatch.setattr(module, "load_cfg", lambda _tenant: cfg)
    monkeypatch.setattr(module, "_escola_of", lambda _request: {
        "armazem": store, "tenant_id": "legacy"})
    calls = []

    class Runtime:
        def submit(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                state=SimpleNamespace(value="open"),
                position_id="private-training-position",
            )

    async def build(**kwargs):
        assert kwargs["confirmation_phrase"] == TRAINING_CONFIRM_PHRASE
        assert kwargs["tenant_id"] == "legacy"
        return Runtime()

    monkeypatch.setattr(
        module, "build_iq_option_training_homologation_runtime", build)

    response = client.post(
        "/v1/live/homologacao-iq-option/executar-conta-treino",
        headers=H,
        json={
            "confirmo_uma_ordem_treinamento": True,
            "frase_confirmacao": TRAINING_CONFIRM_PHRASE,
            "client_order_id": "training-1",
            "instrument": "EUR/USD",
            "direction": "call",
            "amount": "1",
            "expiration": 1_800_000_300,
            "min_profit_percent": 80,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "open"
    assert body["amount"] == "1"
    assert body["position_fingerprint"] == hashlib.sha256(
        b"iq_option\0training\0private-training-position").hexdigest()
    assert "private-training-position" not in response.text
    assert calls[0]["amount"] == "1"


def test_reconciliacao_certifica_apenas_pelo_runtime(
        tmp_path, monkeypatch):
    module, client = _app(tmp_path, monkeypatch)
    cfg = _cfg()
    store = Store()
    _prepared(module, store, cfg)
    monkeypatch.setattr(module, "load_cfg", lambda _tenant: cfg)
    monkeypatch.setattr(module, "_escola_of", lambda _request: {
        "armazem": store, "tenant_id": "legacy"})
    calls = []
    expected = {
        "schema": "dz23.homologacao-iq-training/1",
        "exchange": "iq_option",
        "mode": "training",
        "filled_external_order": True,
        "reconciled": True,
        "verified_at": "2026-10-17T15:00:00+00:00",
        "credential_fingerprint": "a" * 64,
        "external_order_fingerprint": "b" * 64,
    }

    class Runtime:
        def reconcile_and_certify(self, client_order_id):
            calls.append(client_order_id)
            return expected

    async def build(**_kwargs):
        return Runtime()

    monkeypatch.setattr(
        module, "build_iq_option_training_homologation_runtime", build)

    response = client.post(
        "/v1/live/homologacao-iq-option/reconciliar-conta-treino",
        headers=H,
        json={
            "confirmo_reconciliacao_treinamento": True,
            "frase_confirmacao": TRAINING_CONFIRM_PHRASE,
            "client_order_id": "training-1",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["homologacao"]["reconciled"] is True
    assert "credential_fingerprint" not in response.text
    assert "external_order_fingerprint" not in response.text
    assert calls == ["training-1"]

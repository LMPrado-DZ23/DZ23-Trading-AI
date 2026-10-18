"""Preparacao passiva da conta de treino nao pode virar homologacao de fill."""
from __future__ import annotations

import importlib
from types import SimpleNamespace

H = {"Authorization": "Bearer s3nh4"}


class Store:
    def __init__(self):
        self.documents = {}

    def salvar(self, tenant, key, value):
        self.documents[(tenant, key)] = value


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import service.app as module
    importlib.reload(module)
    module._FAILS.clear()
    from fastapi.testclient import TestClient
    return module, TestClient(module.app)


def test_preparacao_descobre_treino_sem_ordem_e_persiste_so_prova_publica(
        tmp_path, monkeypatch):
    module, client = _app(tmp_path, monkeypatch)
    store = Store()
    monkeypatch.setattr(module, "load_cfg", lambda _tenant: {
        "TRADINGAGENTS_EXCHANGE": "iq_option",
        "TRADINGAGENTS_BROKER_MODE": "testnet",
        "LIVE_EXECUTION_EXCHANGE": "iq_option",
        "IQ_OPTION_MCP_BEARER_TOKEN": "read-token",
        "IQ_OPTION_MCP_LIVE_BEARER_TOKEN": "live-token",
    })
    monkeypatch.setattr(module, "_escola_of", lambda _request: {
        "armazem": store,
        "tenant_id": "legacy",
    })
    calls = []

    async def resolve(env):
        calls.append(env)
        return SimpleNamespace(
            public_proof=lambda: {
                "exchange": "iq_option",
                "account_kind": "training",
                "authenticated_read": True,
                "read_methods": [
                    "binary-options:list_balances[TRAINING]"],
                "credential_fingerprint": "a" * 64,
                "balance_id": 812345,
                "currency": "USD",
                "unexpected_private_field": "never-persist",
            },
            balance_id=812345,
            currency="USD",
        )

    monkeypatch.setattr(
        module, "resolve_iq_option_training_account_async", resolve)

    response = client.post(
        "/v1/live/homologacao-iq-option/preparar-conta-treino",
        headers=H,
        json={"confirmo_conta_treinamento_unica": True},
    )

    assert response.status_code == 200, response.text
    assert response.json()["ordem_enviada"] is False
    assert response.json()["homologacao_concluida"] is False
    assert len(calls) == 1
    proof = store.documents[(
        "legacy", module._CHAVE_PREPARACAO_IQ_TREINO)]
    assert proof["schema"] == module._SCHEMA_PREPARACAO_IQ_TREINO
    assert "balance_id" not in proof
    assert "currency" not in proof
    assert "unexpected_private_field" not in proof
    assert "812345" not in str(proof)


def test_preparacao_exige_confirmacao_e_nao_chama_resolvedor(
        tmp_path, monkeypatch):
    module, client = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "load_cfg", lambda _tenant: {
        "TRADINGAGENTS_EXCHANGE": "iq_option",
        "TRADINGAGENTS_BROKER_MODE": "testnet",
    })
    calls = []
    monkeypatch.setattr(
        module, "resolve_iq_option_training_account_async",
        lambda *_args, **_kwargs: calls.append("network"),
    )

    response = client.post(
        "/v1/live/homologacao-iq-option/preparar-conta-treino",
        headers=H,
        json={},
    )

    assert response.status_code == 422
    assert calls == []


def test_preparacao_recusa_outro_modo_ou_corretora_sem_rede(
        tmp_path, monkeypatch):
    module, client = _app(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(
        module, "resolve_iq_option_training_account_async",
        lambda *_args, **_kwargs: calls.append("network"),
    )
    for cfg in (
        {"TRADINGAGENTS_EXCHANGE": "binance",
         "TRADINGAGENTS_BROKER_MODE": "testnet"},
        {"TRADINGAGENTS_EXCHANGE": "iq_option",
         "TRADINGAGENTS_BROKER_MODE": "live"},
    ):
        monkeypatch.setattr(
            module, "load_cfg", lambda _tenant, value=cfg: dict(value))
        response = client.post(
            "/v1/live/homologacao-iq-option/preparar-conta-treino",
            headers=H,
            json={"confirmo_conta_treinamento_unica": True},
        )
        assert response.status_code == 409

    assert calls == []

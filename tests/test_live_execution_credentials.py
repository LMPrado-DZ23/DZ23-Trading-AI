"""Credencial LIVE separada, comprovada e vinculada à conta autorizada."""
from __future__ import annotations

import importlib
import json
import sys
from types import SimpleNamespace

import pytest

from tradingagents.brokers.ccxt_bridge import (
    BrokerSecurityError,
    live_execution_credential_fingerprint,
    probe_live_execution_account,
)

H = {"Authorization": "Bearer s3nh4"}
ACCOUNT_ID = "broker-account-private"


class _ReadOnlyProbeExchange:
    calls: list[str] = []
    last_config: dict = {}
    requiredCredentials = {"apiKey": True, "secret": True}
    has = {"fetchOpenOrders": True, "fetchAccounts": True}

    def __init__(self, config):
        type(self).last_config = dict(config)

    def fetch_balance(self):
        self.calls.append("fetch_balance")
        return {"total": {"USDT": "10"}}

    def fetch_open_orders(self):
        self.calls.append("fetch_open_orders")
        return []

    def fetch_accounts(self):
        self.calls.append("fetch_accounts")
        return [{"id": ACCOUNT_ID}]

    def create_order(self, *_args, **_kwargs):  # pragma: no cover - sentinela
        raise AssertionError("a prova da credencial LIVE tentou criar ordem")

    def withdraw(self, *_args, **_kwargs):  # pragma: no cover - sentinela
        raise AssertionError("a prova da credencial LIVE tentou sacar")


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import service.app as m
    importlib.reload(m)
    m._FAILS.clear()
    m._DESKS.clear()
    from fastapi.testclient import TestClient
    return m, TestClient(m.app)


def _cfg():
    return {
        "TRADINGAGENTS_EXCHANGE": "binance",
        "EXCHANGE_API_KEY": "testnet-key",
        "EXCHANGE_API_SECRET": "testnet-secret",
        "EXCHANGE_CREDENTIAL_EXCHANGE": "binance",
        "LIVE_EXCHANGE_API_KEY": "live-key",
        "LIVE_EXCHANGE_API_SECRET": "live-secret",
        "LIVE_EXECUTION_EXCHANGE": "binance",
        "REAL_READ_ONLY_API_KEY": "read-key",
        "REAL_READ_ONLY_API_SECRET": "read-secret",
        "REAL_READ_ONLY_EXCHANGE": "binance",
    }


def test_fingerprint_live_usa_apenas_credencial_live():
    cfg = _cfg()
    original = live_execution_credential_fingerprint(cfg)
    assert len(original) == 64
    assert live_execution_credential_fingerprint({
        **cfg, "EXCHANGE_API_KEY": "rotated-testnet"}) == original
    assert live_execution_credential_fingerprint({
        **cfg, "REAL_READ_ONLY_API_KEY": "rotated-read"}) == original
    assert live_execution_credential_fingerprint({
        **cfg, "LIVE_EXCHANGE_API_KEY": "rotated-live"}) != original


def test_probe_live_somente_le_e_nao_expoe_segredo(monkeypatch):
    _ReadOnlyProbeExchange.calls = []
    monkeypatch.setitem(
        sys.modules, "ccxt",
        SimpleNamespace(binance=_ReadOnlyProbeExchange))
    env = {**_cfg(), "LIVE_EXECUTION_ACCOUNT_ID": ACCOUNT_ID}

    result = probe_live_execution_account(env)

    assert result["authenticated_read"] is True
    assert result["broker_identity_verified"] is True
    assert result["read_methods"] == [
        "fetch_balance", "fetch_open_orders", "fetch_accounts"]
    assert _ReadOnlyProbeExchange.calls == result["read_methods"]
    assert _ReadOnlyProbeExchange.last_config["apiKey"] == "live-key"
    assert _ReadOnlyProbeExchange.last_config["secret"] == "live-secret"
    assert result["credential_fingerprint"] == (
        live_execution_credential_fingerprint(env))
    serialized = json.dumps(result)
    assert "live-key" not in serialized
    assert "live-secret" not in serialized
    assert ACCOUNT_ID not in serialized


def test_probe_live_recusa_identidade_divergente(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "ccxt",
        SimpleNamespace(binance=_ReadOnlyProbeExchange))
    with pytest.raises(BrokerSecurityError, match="não corresponde"):
        probe_live_execution_account({
            **_cfg(), "LIVE_EXECUTION_ACCOUNT_ID": "outra-conta"})


def test_config_vincula_e_mascara_credencial_live(tmp_path, monkeypatch):
    m, c = _app(tmp_path, monkeypatch)
    response = c.post("/v1/config", headers=H, json={"vars": {
        "TRADINGAGENTS_EXCHANGE": "binance",
        "LIVE_EXCHANGE_API_KEY": "live-key",
        "LIVE_EXCHANGE_API_SECRET": "live-secret",
    }})
    assert response.status_code == 200, response.text
    stored = m.load_cfg("legacy")
    assert stored["LIVE_EXECUTION_EXCHANGE"] == "binance"
    assert stored["LIVE_EXECUTION_CREDENTIAL_BOUND_AT"]
    public = c.get("/v1/config", headers=H).json()["config"]
    assert public["LIVE_EXCHANGE_API_KEY"]["set"] is True
    assert "value" not in public["LIVE_EXCHANGE_API_KEY"]
    assert public["LIVE_EXCHANGE_API_SECRET"]["set"] is True


def test_preparo_live_recusa_reuso_da_credencial_testnet_sem_rede(
        tmp_path, monkeypatch):
    m, c = _app(tmp_path, monkeypatch)
    cfg = _cfg()
    cfg["LIVE_EXCHANGE_API_KEY"] = cfg["EXCHANGE_API_KEY"]
    cfg["LIVE_EXCHANGE_API_SECRET"] = cfg["EXCHANGE_API_SECRET"]
    c.post("/v1/config", headers=H, json={"vars": cfg})
    monkeypatch.setattr(m, "_prova_leitura_real", lambda *_: ({
        "exchange": "binance", "conta": "principal",
        "credential_fingerprint": "a" * 64,
        "broker_identity_fingerprint": "b" * 64,
        "authenticated_read": True, "broker_identity_verified": True,
        "sem_trade_declarado": True, "sem_saque_declarado": True,
    }, True))
    called = False

    def _must_not_probe(_env):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(m, "probe_live_execution_account", _must_not_probe)
    response = c.post("/v1/live/preparacao-execucao", headers=H, json={
        "conta": "principal", "broker_account_id": ACCOUNT_ID,
        "confirmo_trade": True, "confirmo_sem_movimentacao": True,
    })
    assert response.status_code == 409
    assert "TESTNET" in str(response.json()["detail"])
    assert called is False


def test_preparo_live_recusa_reuso_da_credencial_de_leitura_sem_rede(
        tmp_path, monkeypatch):
    m, c = _app(tmp_path, monkeypatch)
    cfg = _cfg()
    cfg["LIVE_EXCHANGE_API_KEY"] = cfg["REAL_READ_ONLY_API_KEY"]
    cfg["LIVE_EXCHANGE_API_SECRET"] = cfg["REAL_READ_ONLY_API_SECRET"]
    c.post("/v1/config", headers=H, json={"vars": cfg})
    monkeypatch.setattr(m, "_prova_leitura_real", lambda *_: ({
        "exchange": "binance", "conta": "principal",
        "credential_fingerprint": "a" * 64,
        "broker_identity_fingerprint": "b" * 64,
        "authenticated_read": True, "broker_identity_verified": True,
        "sem_trade_declarado": True, "sem_saque_declarado": True,
        "provider_mutating_tools_exposed": False,
    }, True))
    called = False

    def _must_not_probe(_env):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(m, "probe_live_execution_account", _must_not_probe)
    response = c.post("/v1/live/preparacao-execucao", headers=H, json={
        "conta": "principal", "broker_account_id": ACCOUNT_ID,
        "confirmo_trade": True, "confirmo_sem_movimentacao": True,
    })
    assert response.status_code == 409
    assert "somente leitura" in str(response.json()["detail"])
    assert called is False


def test_projecao_do_broker_live_nao_reutiliza_credencial_testnet(
        tmp_path, monkeypatch):
    m, _ = _app(tmp_path, monkeypatch)
    cfg = _cfg()

    projetada = m._cfg_para_broker_live(cfg)

    assert projetada["EXCHANGE_API_KEY"] == "live-key"
    assert projetada["EXCHANGE_API_SECRET"] == "live-secret"
    assert projetada["EXCHANGE_CREDENTIAL_EXCHANGE"] == "binance"
    assert cfg["EXCHANGE_API_KEY"] == "testnet-key"


def test_preparo_live_persiste_prova_sem_segredo_ou_id(
        tmp_path, monkeypatch):
    m, c = _app(tmp_path, monkeypatch)
    assert c.post("/v1/config", headers=H,
                  json={"vars": _cfg()}).status_code == 200
    read_env = m._env_leitura_real(m.load_cfg("legacy"))
    read_identity = m.broker_identity_fingerprint(read_env, ACCOUNT_ID)
    read_proof = {
        "exchange": "binance", "conta": "principal",
        "credential_fingerprint": m.read_only_credential_fingerprint(read_env),
        "broker_identity_fingerprint": read_identity,
        "authenticated_read": True, "broker_identity_verified": True,
        "sem_trade_declarado": True, "sem_saque_declarado": True,
        "provider_mutating_tools_exposed": False,
    }
    monkeypatch.setattr(
        m, "_prova_leitura_real", lambda *_: (read_proof, True))

    def _probe(env):
        assert env["LIVE_EXECUTION_ACCOUNT_ID"] == ACCOUNT_ID
        return {
            "exchange": "binance", "authenticated_read": True,
            "read_methods": ["fetch_balance", "fetch_accounts"],
            "broker_identity_verified": True,
            "broker_identity_fingerprint": m.broker_identity_fingerprint(
                m._env_live_como_leitura(env), ACCOUNT_ID),
            "credential_fingerprint": (
                live_execution_credential_fingerprint(env)),
        }

    monkeypatch.setattr(m, "probe_live_execution_account", _probe)
    response = c.post("/v1/live/preparacao-execucao", headers=H, json={
        "conta": "principal", "broker_account_id": ACCOUNT_ID,
        "confirmo_trade": True, "confirmo_sem_movimentacao": True,
    })
    assert response.status_code == 200, response.text
    assert response.json()["preparacao"]["valida"] is True
    proof_path = next((tmp_path / "desks").rglob(
        "preparacao_real_execucao.json"))
    contents = proof_path.read_text(encoding="utf-8")
    for forbidden in (
            "live-key", "live-secret", "read-key", "read-secret", ACCOUNT_ID):
        assert forbidden not in contents

    status = c.get("/v1/live/status", headers=H).json()
    item = next(i for i in status["checklist"]
                if "execução REAL separada" in i["item"])
    assert item["pronto"] is True
    assert status["preparacao_execucao"]["valida"] is True


def test_reinicio_invalida_ativacao_apos_rotacao_da_chave_live(
        tmp_path, monkeypatch):
    m, _ = _app(tmp_path, monkeypatch)
    cfg = {**_cfg(), "LIVE_MAX_ORDER_VALUE": "10",
           "LIVE_MAX_DAILY_LOSS": "5"}
    registro = {
        "schema": m._SCHEMA_ATIVACAO_LIVE,
        "tenant_id": "legacy",
        "exchange": "binance",
        "conta": "principal",
        "agent_id": "trader-momentum",
        "authorization_id": "AUT-1",
        "execution_credential_fingerprint": (
            live_execution_credential_fingerprint(cfg)),
    }

    class Armazem:
        def carregar(self, _tenant, chave):
            return registro if chave == m._CHAVE_ATIVACAO_LIVE else None

    rotacionada = {**cfg, "LIVE_EXCHANGE_API_KEY": "live-key-rotated"}
    resultado = m._validar_ativacao_live_salva(
        Armazem(), "legacy", rotacionada)

    assert resultado["valida"] is False

"""Preparação REAL: credencial isolada e prova autenticada sem escrita."""
from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest

from tradingagents.brokers.ccxt_bridge import (
    probe_read_only_account,
    read_only_credential_fingerprint,
)

H = {"Authorization": "Bearer s3nh4"}
READ_CFG = {
    "TRADINGAGENTS_EXCHANGE": "binance",
    "REAL_READ_ONLY_API_KEY": "read-key",
    "REAL_READ_ONLY_API_SECRET": "read-secret",
}


class _ExchangeSomenteLeitura:
    chamadas: list[str] = []
    requiredCredentials = {"apiKey": True, "secret": True}
    has = {"fetchOpenOrders": True, "fetchAccounts": True}

    def __init__(self, config):
        self.config = config

    def fetch_balance(self):
        self.chamadas.append("fetch_balance")
        return {"total": {"USDT": "10"}}

    def fetch_open_orders(self):
        self.chamadas.append("fetch_open_orders")
        return []

    def fetch_accounts(self):
        self.chamadas.append("fetch_accounts")
        return [{"id": "privado-e-nao-retornado"}]

    def create_order(self, *args, **kwargs):  # pragma: no cover - sentinela
        raise AssertionError("a prova de leitura tentou criar uma ordem")

    def withdraw(self, *args, **kwargs):  # pragma: no cover - sentinela
        raise AssertionError("a prova de leitura tentou sacar")


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


def _item(status, trecho):
    return next(i for i in status["checklist"]
                if trecho.lower() in i["item"].lower())


def test_probe_chama_exclusivamente_metodos_de_leitura(monkeypatch):
    _ExchangeSomenteLeitura.chamadas = []
    monkeypatch.setitem(
        sys.modules, "ccxt",
        SimpleNamespace(binance=_ExchangeSomenteLeitura))
    resultado = probe_read_only_account(READ_CFG)
    assert resultado["authenticated_read"] is True
    assert resultado["read_methods"] == [
        "fetch_balance", "fetch_open_orders", "fetch_accounts"]
    assert _ExchangeSomenteLeitura.chamadas == resultado["read_methods"]
    assert "read-key" not in str(resultado)
    assert "read-secret" not in str(resultado)


@pytest.mark.parametrize(
    "campo", ["confirmo_sem_trade", "confirmo_sem_movimentacao"])
def test_endpoint_exige_as_duas_declaracoes(tmp_path, monkeypatch, campo):
    m, c = _app(tmp_path, monkeypatch)
    c.post("/v1/config", headers=H, json={"vars": READ_CFG})
    chamado = False

    def _nao_deve_chamar(_env):
        nonlocal chamado
        chamado = True
        return {}

    monkeypatch.setattr(m, "probe_read_only_account", _nao_deve_chamar)
    body = {"conta": "real-principal", "confirmo_sem_trade": True,
            "confirmo_sem_movimentacao": True}
    body[campo] = False
    assert c.post("/v1/live/preparacao-leitura", headers=H,
                  json=body).status_code == 422
    assert chamado is False


def test_prova_persistida_libera_itens_mas_nunca_o_live(tmp_path, monkeypatch):
    m, c = _app(tmp_path, monkeypatch)
    c.post("/v1/config", headers=H, json={"vars": READ_CFG})
    capturado = {}

    def _provar(env):
        capturado.update(env)
        return {
            "exchange": "binance", "ccxt_id": "binance",
            "authenticated_read": True,
            "read_methods": ["fetch_balance", "fetch_open_orders"],
            "credential_fingerprint": read_only_credential_fingerprint(env),
        }

    monkeypatch.setattr(m, "probe_read_only_account", _provar)
    r = c.post("/v1/live/preparacao-leitura", headers=H, json={
        "conta": "real-principal", "confirmo_sem_trade": True,
        "confirmo_sem_movimentacao": True})
    assert r.status_code == 200, r.text
    assert capturado["REAL_READ_ONLY_API_KEY"] == "read-key"
    assert "read-key" not in r.text and "read-secret" not in r.text

    status = c.get("/v1/live/status", headers=H).json()
    assert _item(status, "permissões mínimas")["pronto"] is True
    assert _item(status, "identidade")["pronto"] is True
    assert status["preparacao_leitura"]["valida"] is True
    assert status["preparacao_leitura"]["conta"] == "real-principal"
    assert _item(status, "Piloto autorizado")["pronto"] is False
    assert status["pode_operar_real"] is False

    prova = next((tmp_path / "desks").rglob("preparacao_real_leitura.json"))
    conteudo = prova.read_text(encoding="utf-8")
    assert "read-key" not in conteudo and "read-secret" not in conteudo

    class _Supervisor:
        @staticmethod
        def vigiar(**_kwargs):
            return []

        @staticmethod
        def relatorio():
            return [{"agent_id": "trader-momentum",
                     "estrategia": "momentum", "versao": "1",
                     "corretora": "binance", "conta": "real-principal",
                     "estado": "piloto_autorizado"}]

    monkeypatch.setattr(m, "_supervisor_of", lambda _request: _Supervisor())
    com_piloto = c.get("/v1/live/status", headers=H).json()
    assert _item(com_piloto, "Piloto autorizado")["pronto"] is True
    assert com_piloto["pilotos_autorizados_no_contexto"] == [{
        "agent_id": "trader-momentum", "estrategia": "momentum",
        "versao": "1", "conta": "real-principal"}]
    assert com_piloto["pode_operar_real"] is False


def test_rotacao_da_chave_invalida_a_prova(tmp_path, monkeypatch):
    m, c = _app(tmp_path, monkeypatch)
    c.post("/v1/config", headers=H, json={"vars": READ_CFG})
    monkeypatch.setattr(m, "probe_read_only_account", lambda env: {
        "exchange": "binance", "authenticated_read": True,
        "read_methods": ["fetch_balance"],
        "credential_fingerprint": read_only_credential_fingerprint(env),
    })
    assert c.post("/v1/live/preparacao-leitura", headers=H, json={
        "conta": "real-principal", "confirmo_sem_trade": True,
        "confirmo_sem_movimentacao": True}).status_code == 200

    c.post("/v1/config", headers=H, json={"vars": {
        "REAL_READ_ONLY_API_KEY": "rotated-key"}})
    status = c.get("/v1/live/status", headers=H).json()
    assert status["preparacao_leitura"]["valida"] is False
    assert _item(status, "permissões mínimas")["pronto"] is False
    assert _item(status, "identidade")["pronto"] is False


def test_certificacao_amarra_estrategia_conta_risco_e_reconciliacao(
        tmp_path, monkeypatch):
    m, c = _app(tmp_path, monkeypatch)
    c.post("/v1/config", headers=H, json={"vars": {
        **READ_CFG, "LIVE_MAX_ORDER_VALUE": "40",
        "LIVE_MAX_DAILY_LOSS": "8"}})
    monkeypatch.setattr(m, "probe_read_only_account", lambda env: {
        "exchange": "binance", "authenticated_read": True,
        "read_methods": ["fetch_balance"],
        "credential_fingerprint": read_only_credential_fingerprint(env),
    })
    assert c.post("/v1/live/preparacao-leitura", headers=H, json={
        "conta": "real-principal", "confirmo_sem_trade": True,
        "confirmo_sem_movimentacao": True}).status_code == 200

    class _Supervisor:
        @staticmethod
        def vigiar(**_kwargs):
            return []

        @staticmethod
        def relatorio():
            return [{
                "agent_id": "trader-momentum", "authorization_id": "AUT-1",
                "estrategia": "momentum", "versao": "1",
                "corretora": "binance", "conta": "real-principal",
                "capital_maximo": "100", "perda_diaria_maxima": "10",
                "perda_total_maxima": "30", "valido_ate": "2027-01-01",
                "estado": "piloto_autorizado",
            }]

    monkeypatch.setattr(m, "_supervisor_of", lambda _request: _Supervisor())
    monkeypatch.setattr(m, "_relatorio_de_reconciliacao", lambda _tid: {
        "seguro": True, "pendencias": []})
    status = c.get("/v1/live/status", headers=H).json()
    assert _item(status, "piloto autorizado vigente")["pronto"] is True
    assert _item(status, "Limites globais compatíveis")["pronto"] is True
    assert _item(status, "Reconciliação financeira")["pronto"] is True
    cert = status["certificacoes_estrategia_conta"]
    assert cert == [{
        "agent_id": "trader-momentum", "authorization_id": "AUT-1",
        "estrategia": "momentum", "versao": "1",
        "corretora": "binance", "conta": "real-principal",
        "contexto_certificacao": {},
        "valido_ate": "2027-01-01", "risco_compativel": True,
        "pendencias_risco": [],
    }]
    assert status["pode_operar_real"] is False


def test_status_revalida_escola_e_recusa_autorizacao_obsoleta(
        tmp_path, monkeypatch):
    m, c = _app(tmp_path, monkeypatch)
    c.post("/v1/config", headers=H, json={"vars": READ_CFG})
    monkeypatch.setattr(m, "probe_read_only_account", lambda env: {
        "exchange": "binance", "authenticated_read": True,
        "read_methods": ["fetch_balance"],
        "credential_fingerprint": read_only_credential_fingerprint(env),
    })
    assert c.post("/v1/live/preparacao-leitura", headers=H, json={
        "conta": "real-principal", "confirmo_sem_trade": True,
        "confirmo_sem_movimentacao": True}).status_code == 200

    class _Supervisor:
        estado = "piloto_autorizado"

        @classmethod
        def vigiar(cls, **_kwargs):
            cls.estado = "suspenso"
            return ["trader-momentum: piloto SUSPENSO"]

        @classmethod
        def relatorio(cls):
            return [{"agent_id": "trader-momentum",
                     "estrategia": "momentum", "versao": "1",
                     "corretora": "binance", "conta": "real-principal",
                     "estado": cls.estado}]

    monkeypatch.setattr(m, "_supervisor_of", lambda _request: _Supervisor())
    status = c.get("/v1/live/status", headers=H).json()
    assert _item(status, "piloto autorizado vigente")["pronto"] is False
    assert status["certificacoes_estrategia_conta"] == []


def test_limite_global_acima_do_piloto_e_reconciliacao_pendente_bloqueiam(
        tmp_path, monkeypatch):
    m, c = _app(tmp_path, monkeypatch)
    prova = {"exchange": "binance", "conta": "real-principal",
             "authenticated_read": True, "sem_trade_declarado": True,
             "sem_saque_declarado": True}
    monkeypatch.setattr(m, "_prova_leitura_real", lambda _request, _cfg: (
        prova, True))
    c.post("/v1/config", headers=H, json={"vars": {
        "TRADINGAGENTS_EXCHANGE": "binance",
        "LIVE_MAX_ORDER_VALUE": "101", "LIVE_MAX_DAILY_LOSS": "11"}})

    class _Supervisor:
        @staticmethod
        def vigiar(**_kwargs):
            return []

        @staticmethod
        def relatorio():
            return [{
                "agent_id": "trader-momentum", "authorization_id": "AUT-1",
                "estrategia": "momentum", "versao": "1",
                "corretora": "binance", "conta": "real-principal",
                "capital_maximo": "100", "perda_diaria_maxima": "10",
                "perda_total_maxima": "30", "estado": "piloto_autorizado",
            }]

    monkeypatch.setattr(m, "_supervisor_of", lambda _request: _Supervisor())
    monkeypatch.setattr(m, "_relatorio_de_reconciliacao", lambda _tid: {
        "seguro": False, "pendencias": ["ordem incerta"]})
    status = c.get("/v1/live/status", headers=H).json()
    assert _item(status, "Limites globais compatíveis")["pronto"] is False
    assert _item(status, "Reconciliação financeira")["pronto"] is False
    assert status["reconciliacao"]["pendencias"] == ["ordem incerta"]
    assert status["certificacoes_estrategia_conta"][0][
        "risco_compativel"] is False

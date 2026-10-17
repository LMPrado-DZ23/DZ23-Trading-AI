"""Prova TESTNET durável e vinculada à corretora para o checklist REAL."""
from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from tradingagents.brokers.ccxt_bridge import (
    testnet_credential_fingerprint as _testnet_credential_fingerprint,
)
from tradingagents.desk.execution import OrderState

H = {"Authorization": "Bearer s3nh4"}
TESTNET_ENV = {
    "TRADINGAGENTS_EXCHANGE": "binance",
    "EXCHANGE_API_KEY": "test-key",
    "EXCHANGE_API_SECRET": "test-secret",
    "EXCHANGE_API_PASSWORD": "",
    "EXCHANGE_API_UID": "",
}


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


class _BrokerTestnet:
    def __init__(self, exchange="binance"):
        self.bridge = NS(mode="testnet", exchange_name=exchange)

    def fetch_order_by_client_id(self, _client_order_id):
        return None

    def fetch_positions(self):
        return {}

    def fetch_order_fills(self, _client_order_id, _exchange_order_id=""):
        return []

    def fetch_open_orders(self):
        return []

    def fetch_cash(self):
        return {"USDT": "1000"}


class _Coordenador:
    def __init__(self, *, exchange="binance", seguro=True,
                 external_id="4380219706", estado=OrderState.FILLED,
                 origem_exchange=None, origem_fingerprint=None,
                 terminal_em=None):
        self.broker = _BrokerTestnet(exchange)
        self.orders = {"ord-1": NS(
            state=estado, external_id=external_id,
            testnet_exchange=(exchange if origem_exchange is None
                              else origem_exchange),
            testnet_credential_fingerprint=(
                _testnet_credential_fingerprint({
                    **TESTNET_ENV, "TRADINGAGENTS_EXCHANGE": exchange})
                if origem_fingerprint is None else origem_fingerprint),
            terminal_em=(terminal_em or datetime.now(timezone.utc).isoformat()))}
        self._seguro = seguro

    def reconciliar(self, *_args, **_kwargs):
        return {"seguro": self._seguro,
                "pendencias": [] if self._seguro else ["divergência"]}


def _configurar_testnet(c, exchange="binance"):
    r = c.post("/v1/config", headers=H, json={"vars": {
        "TRADINGAGENTS_DESK_BROKER": "ccxt",
        "TRADINGAGENTS_EXCHANGE": exchange,
        "TRADINGAGENTS_BROKER_MODE": "testnet",
        "EXCHANGE_API_KEY": "test-key",
        "EXCHANGE_API_SECRET": "test-secret",
    }})
    assert r.status_code == 200, r.text


def test_fill_reconciliado_vira_prova_duravel_apos_trocar_para_live(
        tmp_path, monkeypatch):
    m, c = _app(tmp_path, monkeypatch)
    _configurar_testnet(c)
    m._DESKS["legacy"] = {
        "coordinator": _Coordenador(), "broker_mode": "testnet"}

    r = c.post("/v1/desk/reconciliar", headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["homologacao_testnet"]["exchange"] == "binance"
    assert "4380219706" not in r.text
    assert "test-secret" not in r.text
    arquivo = next((tmp_path / "desks").rglob("homologacao_testnet.json"))
    persistido = arquivo.read_text(encoding="utf-8")
    assert "4380219706" not in persistido
    assert "test-key" not in persistido and "test-secret" not in persistido

    # Salvar LIVE reconstrói a mesa; a prova não pode desaparecer junto com
    # o objeto em memória que continha o fill TESTNET.
    r = c.post("/v1/config", headers=H, json={"vars": {
        "TRADINGAGENTS_BROKER_MODE": "live"}})
    assert r.status_code == 200, r.text
    assert "legacy" not in m._DESKS
    status = c.get("/v1/live/status", headers=H).json()
    assert _item(status, "testnet")["pronto"] is True
    assert status["homologacao_testnet"]["valida"] is True
    assert status["homologacao_testnet"]["exchange"] == "binance"


def test_prova_de_uma_corretora_nao_homologa_outra(tmp_path, monkeypatch):
    m, c = _app(tmp_path, monkeypatch)
    _configurar_testnet(c, "binance")
    m._DESKS["legacy"] = {
        "coordinator": _Coordenador(exchange="binance"),
        "broker_mode": "testnet"}
    assert c.post("/v1/desk/reconciliar", headers=H).status_code == 200

    c.post("/v1/config", headers=H, json={"vars": {
        "TRADINGAGENTS_EXCHANGE": "okx",
        "TRADINGAGENTS_BROKER_MODE": "live"}})
    status = c.get("/v1/live/status", headers=H).json()
    assert _item(status, "testnet")["pronto"] is False
    assert status["homologacao_testnet"]["valida"] is False
    assert status["homologacao_testnet"]["motivo"] == (
        "sem prova TESTNET reconciliada para okx")


@pytest.mark.parametrize("campo,novo", [
    ("EXCHANGE_API_KEY", "rotated-test-key"),
    ("EXCHANGE_API_SECRET", "rotated-test-secret"),
    ("EXCHANGE_API_PASSWORD", "rotated-passphrase"),
    ("EXCHANGE_API_UID", "rotated-uid"),
])
def test_rotacao_de_qualquer_credencial_invalida_prova_testnet(
        tmp_path, monkeypatch, campo, novo):
    m, c = _app(tmp_path, monkeypatch)
    _configurar_testnet(c)
    m._DESKS["legacy"] = {
        "coordinator": _Coordenador(), "broker_mode": "testnet"}
    assert c.post("/v1/desk/reconciliar", headers=H).status_code == 200
    antes = c.get("/v1/live/status", headers=H).json()
    assert _item(antes, "testnet")["pronto"] is True, (
        antes["homologacao_testnet"])

    assert c.post("/v1/config", headers=H,
                  json={"vars": {campo: novo}}).status_code == 200
    status = c.get("/v1/live/status", headers=H).json()
    assert _item(status, "testnet")["pronto"] is False
    assert status["homologacao_testnet"]["valida"] is False
    assert "credenciais TESTNET foram alteradas" in (
        status["homologacao_testnet"]["motivo"])
    assert "credential_fingerprint" not in status["homologacao_testnet"]


def test_prova_testnet_recusa_horario_e_identidade_de_ordem_invalidos(
        tmp_path, monkeypatch):
    m, c = _app(tmp_path, monkeypatch)
    _configurar_testnet(c)
    m._DESKS["legacy"] = {
        "coordinator": _Coordenador(), "broker_mode": "testnet"}
    assert c.post("/v1/desk/reconciliar", headers=H).status_code == 200
    arquivo = next((tmp_path / "desks").rglob("homologacao_testnet.json"))

    conteudo = json.loads(arquivo.read_text(encoding="utf-8"))
    conteudo["binance"]["verified_at"] = (
        datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    arquivo.write_text(json.dumps(conteudo), encoding="utf-8")
    status = c.get("/v1/live/status", headers=H).json()
    assert status["homologacao_testnet"]["valida"] is False
    assert "horário inválido" in status["homologacao_testnet"]["motivo"]

    conteudo["binance"]["verified_at"] = datetime.now(timezone.utc).isoformat()
    conteudo["binance"]["external_order_fingerprint"] = "incompleto"
    arquivo.write_text(json.dumps(conteudo), encoding="utf-8")
    status = c.get("/v1/live/status", headers=H).json()
    assert status["homologacao_testnet"]["valida"] is False
    assert "identidade de ordem" in status["homologacao_testnet"]["motivo"]


def test_reconciliacao_insegura_nao_grava_prova(tmp_path, monkeypatch):
    m, c = _app(tmp_path, monkeypatch)
    _configurar_testnet(c)
    m._DESKS["legacy"] = {
        "coordinator": _Coordenador(seguro=False),
        "broker_mode": "testnet"}
    r = c.post("/v1/desk/reconciliar", headers=H)
    assert r.status_code == 200
    assert "homologacao_testnet" not in r.json()
    assert _item(c.get("/v1/live/status", headers=H).json(),
                 "testnet")["pronto"] is False


def test_fill_paper_ou_parcial_nao_grava_prova(tmp_path, monkeypatch):
    m, c = _app(tmp_path, monkeypatch)
    _configurar_testnet(c)
    for external_id, estado in (
            ("paper-1", OrderState.FILLED),
            ("4380219706", OrderState.PARTIALLY_FILLED)):
        m._DESKS["legacy"] = {
            "coordinator": _Coordenador(
                external_id=external_id, estado=estado),
            "broker_mode": "testnet"}
        r = c.post("/v1/desk/reconciliar", headers=H)
        assert r.status_code == 200
        assert "homologacao_testnet" not in r.json()


@pytest.mark.parametrize("origem_exchange,origem_fingerprint,terminal_em", [
    ("okx", _testnet_credential_fingerprint(TESTNET_ENV), None),
    ("binance", "f" * 64, None),
    ("binance", _testnet_credential_fingerprint(TESTNET_ENV),
     "2000-01-01T00:00:00+00:00"),
])
def test_fill_antigo_ou_de_outro_vinculo_nao_homologa_credencial_atual(
        tmp_path, monkeypatch, origem_exchange, origem_fingerprint,
        terminal_em):
    m, c = _app(tmp_path, monkeypatch)
    _configurar_testnet(c)
    m._DESKS["legacy"] = {
        "coordinator": _Coordenador(
            origem_exchange=origem_exchange,
            origem_fingerprint=origem_fingerprint,
            terminal_em=terminal_em),
        "broker_mode": "testnet"}

    r = c.post("/v1/desk/reconciliar", headers=H)

    assert r.status_code == 200
    assert "homologacao_testnet" not in r.json()
    assert _item(c.get("/v1/live/status", headers=H).json(),
                 "testnet")["pronto"] is False


def test_fill_restaurado_sem_origem_nao_homologa_vinculo_atual(
        tmp_path, monkeypatch):
    m, c = _app(tmp_path, monkeypatch)
    _configurar_testnet(c)
    coordenador = _Coordenador()
    del coordenador.orders["ord-1"].testnet_exchange
    del coordenador.orders["ord-1"].testnet_credential_fingerprint
    m._DESKS["legacy"] = {
        "coordinator": coordenador, "broker_mode": "testnet"}

    r = c.post("/v1/desk/reconciliar", headers=H)

    assert r.status_code == 200
    assert "homologacao_testnet" not in r.json()


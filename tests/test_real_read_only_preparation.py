"""Preparação REAL: credencial isolada e prova autenticada sem escrita."""
from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tradingagents.brokers.ccxt_bridge import (
    BrokerSecurityError,
    _broker_identity_fingerprint,
    probe_read_only_account,
    read_only_credential_fingerprint,
)

H = {"Authorization": "Bearer s3nh4"}
READ_CFG = {
    "TRADINGAGENTS_EXCHANGE": "binance",
    "REAL_READ_ONLY_EXCHANGE": "binance",
    "REAL_READ_ONLY_API_KEY": "read-key",
    "REAL_READ_ONLY_API_SECRET": "read-secret",
}
BROKER_ACCOUNT_ID = "privado-e-nao-retornado"


def _preparacao_body(conta="real-principal"):
    return {
        "conta": conta,
        "broker_account_id": BROKER_ACCOUNT_ID,
        "confirmo_sem_trade": True,
        "confirmo_sem_movimentacao": True,
    }


def _resultado_prova(env, *, methods=None):
    return {
        "exchange": "binance",
        "authenticated_read": True,
        "read_methods": list(methods or ["fetch_balance"]),
        "broker_identity_verified": True,
        "broker_identity_fingerprint": _broker_identity_fingerprint(
            env, BROKER_ACCOUNT_ID),
        "credential_fingerprint": read_only_credential_fingerprint(env),
    }


class _ExchangeSomenteLeitura:
    chamadas: list[str] = []
    ultima_config: dict = {}
    requiredCredentials = {"apiKey": True, "secret": True}
    has = {"fetchOpenOrders": True, "fetchAccounts": True}

    def __init__(self, config):
        self.config = config
        type(self).ultima_config = config

    def fetch_balance(self):
        self.chamadas.append("fetch_balance")
        return {"total": {"USDT": "10"}}

    def fetch_open_orders(self):
        self.chamadas.append("fetch_open_orders")
        return []

    def fetch_accounts(self):
        self.chamadas.append("fetch_accounts")
        return [{"id": BROKER_ACCOUNT_ID}]

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


@pytest.mark.parametrize("exchange", ["okx", "coinbase", "kucoin", "bitget"])
def test_checklist_exige_passphrase_nas_corretoras_que_precisam(
        tmp_path, monkeypatch, exchange):
    _, c = _app(tmp_path, monkeypatch)
    r = c.post("/v1/config", headers=H, json={"vars": {
        "TRADINGAGENTS_EXCHANGE": exchange,
        "EXCHANGE_API_KEY": "execution-key",
        "EXCHANGE_API_SECRET": "execution-secret",
    }})
    assert r.status_code == 200, r.text

    sem = _item(c.get("/v1/live/status", headers=H).json(),
                "Chaves da corretora")
    assert sem["pronto"] is False
    assert "passphrase" in sem["acao"]

    assert c.post("/v1/config", headers=H, json={"vars": {
        "EXCHANGE_API_PASSWORD": "execution-passphrase",
    }}).status_code == 200
    com = _item(c.get("/v1/live/status", headers=H).json(),
                "Chaves da corretora")
    assert com["pronto"] is True


@pytest.mark.parametrize(
    "exchange", ["binance", "bybit", "gateio", "kraken", "mercadobitcoin"])
def test_checklist_nao_inventa_passphrase_nas_demais_corretoras(
        tmp_path, monkeypatch, exchange):
    _, c = _app(tmp_path, monkeypatch)
    r = c.post("/v1/config", headers=H, json={"vars": {
        "TRADINGAGENTS_EXCHANGE": exchange,
        "EXCHANGE_API_KEY": "execution-key",
        "EXCHANGE_API_SECRET": "execution-secret",
    }})
    assert r.status_code == 200, r.text
    item = _item(c.get("/v1/live/status", headers=H).json(),
                 "Chaves da corretora")
    assert item["pronto"] is True
    assert "passphrase" not in item["acao"]


def test_trocar_corretora_nao_reusa_credencial_de_execucao(
        tmp_path, monkeypatch):
    _, c = _app(tmp_path, monkeypatch)
    assert c.post("/v1/config", headers=H, json={"vars": {
        "TRADINGAGENTS_EXCHANGE": "binance",
        "TRADINGAGENTS_BROKER_MODE": "testnet",
        "EXCHANGE_API_KEY": "binance-key",
        "EXCHANGE_API_SECRET": "binance-secret",
    }}).status_code == 200
    assert _item(c.get("/v1/live/status", headers=H).json(),
                 "Chaves da corretora")["pronto"] is True

    assert c.post("/v1/config", headers=H, json={"vars": {
        "TRADINGAGENTS_EXCHANGE": "bybit",
    }}).status_code == 200
    trocada = _item(c.get("/v1/live/status", headers=H).json(),
                    "Chaves da corretora")
    assert trocada["pronto"] is False
    assert "nova chave e novo segredo" in trocada["acao"]
    primeiros = c.get("/v1/primeiros-passos", headers=H).json()
    treino = next(x for x in primeiros["itens"]
                  if x["passo"] == "Corretora de treino")
    assert treino["pronto"] is False
    broker = c.get("/v1/broker/status", headers=H).json()
    assert broker["ready"] is False
    assert "incompatível" in broker["erro"]

    resposta = c.post("/v1/config", headers=H, json={"vars": {
        "EXCHANGE_API_KEY": "bybit-key",
        "EXCHANGE_API_SECRET": "bybit-secret",
    }})
    assert resposta.status_code == 200
    config_publica = c.get("/v1/config", headers=H).json()["config"]
    assert config_publica["EXCHANGE_CREDENTIAL_EXCHANGE"]["value"] == "bybit"
    assert _item(c.get("/v1/live/status", headers=H).json(),
                 "Chaves da corretora")["pronto"] is True


def test_api_nao_aceita_forjar_vinculo_sem_novas_credenciais(
        tmp_path, monkeypatch):
    _, c = _app(tmp_path, monkeypatch)
    assert c.post("/v1/config", headers=H, json={"vars": READ_CFG}).status_code == 200
    assert c.post("/v1/config", headers=H, json={"vars": {
        "EXCHANGE_API_KEY": "binance-key",
        "EXCHANGE_API_SECRET": "binance-secret",
    }}).status_code == 200

    assert c.post("/v1/config", headers=H, json={"vars": {
        "TRADINGAGENTS_EXCHANGE": "bybit",
        "EXCHANGE_CREDENTIAL_EXCHANGE": "bybit",
        "REAL_READ_ONLY_EXCHANGE": "bybit",
    }}).status_code == 200
    publica = c.get("/v1/config", headers=H).json()["config"]
    assert publica["EXCHANGE_CREDENTIAL_EXCHANGE"]["value"] == "binance"
    assert publica["REAL_READ_ONLY_EXCHANGE"]["value"] == "binance"
    assert _item(c.get("/v1/live/status", headers=H).json(),
                 "Chaves da corretora")["pronto"] is False

    chamado = False

    def _nao_deve_chamar(_env):
        nonlocal chamado
        chamado = True
        return {}

    import service.app as m
    monkeypatch.setattr(m, "probe_read_only_account", _nao_deve_chamar)
    r = c.post("/v1/live/preparacao-leitura", headers=H,
               json=_preparacao_body("real-bybit"))
    assert r.status_code == 409
    assert chamado is False


def test_probe_chama_exclusivamente_metodos_de_leitura(monkeypatch):
    _ExchangeSomenteLeitura.chamadas = []
    monkeypatch.setitem(
        sys.modules, "ccxt",
        SimpleNamespace(binance=_ExchangeSomenteLeitura))
    resultado = probe_read_only_account({
        **READ_CFG, "REAL_READ_ONLY_ACCOUNT_ID": BROKER_ACCOUNT_ID})
    assert resultado["authenticated_read"] is True
    assert resultado["read_methods"] == [
        "fetch_balance", "fetch_open_orders", "fetch_accounts"]
    assert _ExchangeSomenteLeitura.chamadas == resultado["read_methods"]
    assert "read-key" not in str(resultado)
    assert "read-secret" not in str(resultado)
    assert BROKER_ACCOUNT_ID not in str(resultado)
    assert resultado["broker_identity_verified"] is True
    assert _ExchangeSomenteLeitura.ultima_config.get("options") == {
        "warnOnFetchOpenOrdersWithoutSymbol": False,
        "fetchOpenOrders": {"warnWithoutSymbol": False},
    }


def test_probe_recusa_identidade_de_outra_conta(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "ccxt",
        SimpleNamespace(binance=_ExchangeSomenteLeitura))
    with pytest.raises(BrokerSecurityError, match="não corresponde"):
        probe_read_only_account({
            **READ_CFG, "REAL_READ_ONLY_ACCOUNT_ID": "conta-incorreta"})


def test_probe_recusa_corretora_sem_identidade_verificavel(monkeypatch):
    class _ExchangeSemContas(_ExchangeSomenteLeitura):
        has = {"fetchOpenOrders": True, "fetchAccounts": False}

    monkeypatch.setitem(
        sys.modules, "ccxt", SimpleNamespace(binance=_ExchangeSemContas))
    with pytest.raises(BrokerSecurityError, match="identidade de conta"):
        probe_read_only_account({
            **READ_CFG, "REAL_READ_ONLY_ACCOUNT_ID": BROKER_ACCOUNT_ID})


def test_iq_option_usa_token_isolado_e_prova_mcp_sem_ccxt(
        tmp_path, monkeypatch):
    m, c = _app(tmp_path, monkeypatch)
    token = "token-iq-opaco"
    resposta = c.post("/v1/config", headers=H, json={"vars": {
        "TRADINGAGENTS_EXCHANGE": "iq_option",
        "IQ_OPTION_MCP_BEARER_TOKEN": token,
    }})
    assert resposta.status_code == 200, resposta.text
    publica = c.get("/v1/config", headers=H).json()["config"]
    assert publica["IQ_OPTION_MCP_BEARER_TOKEN"]["set"] is True
    assert "value" not in publica["IQ_OPTION_MCP_BEARER_TOKEN"]
    assert publica["REAL_READ_ONLY_EXCHANGE"]["value"] == "iq_option"

    capturado = {}

    async def provar(env):
        capturado.update(env)
        return {
            "exchange": "iq_option",
            "authenticated_read": True,
            "read_methods": ["binary-options:list_balances[NORMAL]"],
            "provider_mutating_tools_exposed": True,
            "broker_identity_verified": True,
            "broker_identity_fingerprint": _broker_identity_fingerprint(
                env, BROKER_ACCOUNT_ID),
            "credential_fingerprint": read_only_credential_fingerprint(env),
        }

    monkeypatch.setattr(m, "probe_iq_option_read_only_account_async", provar)
    r = c.post("/v1/live/preparacao-leitura", headers=H,
               json=_preparacao_body("iq-real-principal"))

    assert r.status_code == 200, r.text
    assert capturado["IQ_OPTION_MCP_BEARER_TOKEN"] == token
    assert capturado["REAL_READ_ONLY_ACCOUNT_ID"] == BROKER_ACCOUNT_ID
    assert token not in r.text and BROKER_ACCOUNT_ID not in r.text
    assert r.json()["preparacao"]["exchange"] == "iq_option"
    assert r.json()["preparacao"]["broker_identity_verified"] is True
    assert r.json()["preparacao"]["provider_mutating_tools_exposed"] is True
    status = c.get("/v1/live/status", headers=H).json()
    assert _item(status, "permissões mínimas")["pronto"] is False
    assert "ferramentas mutáveis" in _item(
        status, "permissões mínimas")["acao"]


def test_token_iq_nao_pode_ser_vinculado_a_outra_corretora(
        tmp_path, monkeypatch):
    _, c = _app(tmp_path, monkeypatch)

    r = c.post("/v1/config", headers=H, json={"vars": {
        "TRADINGAGENTS_EXCHANGE": "binance",
        "IQ_OPTION_MCP_BEARER_TOKEN": "token-na-corretora-errada",
    }})

    assert r.status_code == 409
    publica = c.get("/v1/config", headers=H).json()["config"]
    assert publica["IQ_OPTION_MCP_BEARER_TOKEN"]["set"] is False


def test_health_mcp_usa_token_cifrado_sem_devolve_lo(tmp_path, monkeypatch):
    _, c = _app(tmp_path, monkeypatch)
    token = "token-iq-para-health"
    assert c.post("/v1/config", headers=H, json={"vars": {
        "TRADINGAGENTS_EXCHANGE": "iq_option",
        "IQ_OPTION_MCP_BEARER_TOKEN": token,
    }}).status_code == 200
    capturado = {}

    def provar(*, token=None):
        capturado["token"] = token
        return {"id": "iq_option", "status": "MCP_METADATA_CONNECTED",
                "ok": True, "connected": 7, "expected": 7, "probes": []}

    import service.iq_option_mcp as iq_mcp
    monkeypatch.setattr(iq_mcp, "probe_iq_option_mcp", provar)

    r = c.get("/v1/personal/exchanges/iq_option/mcp-health", headers=H)

    assert r.status_code == 200, r.text
    assert capturado["token"] == token
    assert token not in r.text


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
    body = _preparacao_body()
    body[campo] = False
    assert c.post("/v1/live/preparacao-leitura", headers=H,
                  json=body).status_code == 422
    assert chamado is False


def test_endpoint_recusa_credencial_vinculada_a_outra_corretora_sem_rede(
        tmp_path, monkeypatch):
    m, c = _app(tmp_path, monkeypatch)
    assert c.post("/v1/config", headers=H,
                  json={"vars": READ_CFG}).status_code == 200
    assert c.post("/v1/config", headers=H, json={"vars": {
        "TRADINGAGENTS_EXCHANGE": "bybit"}}).status_code == 200
    chamado = False

    def _nao_deve_chamar(_env):
        nonlocal chamado
        chamado = True
        return {}

    monkeypatch.setattr(m, "probe_read_only_account", _nao_deve_chamar)
    r = c.post("/v1/live/preparacao-leitura", headers=H,
               json=_preparacao_body("real-bybit"))
    assert r.status_code == 409
    assert "não está vinculada" in r.json()["detail"]
    assert chamado is False


def test_prova_persistida_libera_itens_mas_nunca_o_live(tmp_path, monkeypatch):
    m, c = _app(tmp_path, monkeypatch)
    c.post("/v1/config", headers=H, json={"vars": READ_CFG})
    capturado = {}

    def _provar(env):
        capturado.update(env)
        return _resultado_prova(
            env, methods=["fetch_balance", "fetch_open_orders"])

    monkeypatch.setattr(m, "probe_read_only_account", _provar)
    r = c.post("/v1/live/preparacao-leitura", headers=H,
               json=_preparacao_body())
    assert r.status_code == 200, r.text
    assert capturado["REAL_READ_ONLY_API_KEY"] == "read-key"
    assert capturado["REAL_READ_ONLY_ACCOUNT_ID"] == BROKER_ACCOUNT_ID
    assert "read-key" not in r.text and "read-secret" not in r.text
    assert BROKER_ACCOUNT_ID not in r.text

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
    assert BROKER_ACCOUNT_ID not in conteudo

    class _Supervisor:
        @staticmethod
        def vigiar(**_kwargs):
            return []

        @staticmethod
        def relatorio():
            return [{"agent_id": "trader-momentum",
                     "estrategia": "momentum", "versao": "1",
                     "corretora": "binance", "conta": "real-principal",
                     "conta_real": True,
                     "broker_identity_fingerprint":
                         _broker_identity_fingerprint(
                             {**READ_CFG, "REAL_READ_ONLY_ACCOUNT_ID":
                              BROKER_ACCOUNT_ID}, BROKER_ACCOUNT_ID),
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
    monkeypatch.setattr(m, "probe_read_only_account", _resultado_prova)
    assert c.post("/v1/live/preparacao-leitura", headers=H,
                  json=_preparacao_body()).status_code == 200

    c.post("/v1/config", headers=H, json={"vars": {
        "REAL_READ_ONLY_API_KEY": "rotated-key"}})
    status = c.get("/v1/live/status", headers=H).json()
    assert status["preparacao_leitura"]["valida"] is False
    assert _item(status, "permissões mínimas")["pronto"] is False
    assert _item(status, "identidade")["pronto"] is False


def test_prova_de_leitura_expira_e_bloqueia_o_piloto(tmp_path, monkeypatch):
    m, c = _app(tmp_path, monkeypatch)
    c.post("/v1/config", headers=H, json={"vars": READ_CFG})
    monkeypatch.setattr(m, "probe_read_only_account", _resultado_prova)
    assert c.post("/v1/live/preparacao-leitura", headers=H,
                  json=_preparacao_body()).status_code == 200

    caminho = next((tmp_path / "desks").rglob("preparacao_real_leitura.json"))
    prova = json.loads(caminho.read_text(encoding="utf-8"))
    prova["verificada_em"] = (
        datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    caminho.write_text(json.dumps(prova), encoding="utf-8")

    status = c.get("/v1/live/status", headers=H).json()
    assert status["preparacao_leitura"]["valida"] is False
    assert "expirada" in status["preparacao_leitura"]["aviso"]
    assert status["preparacao_leitura"]["expira_em"]
    assert _item(status, "permissões mínimas")["pronto"] is False
    assert _item(status, "identidade")["pronto"] is False


@pytest.mark.parametrize("horario", [
    "", "sem-data", "2026-10-16T00:00:00", "2999-01-01T00:00:00+00:00"])
def test_prova_com_horario_ausente_invalido_ou_sem_fuso_falha_fechado(
        tmp_path, monkeypatch, horario):
    m, c = _app(tmp_path, monkeypatch)
    c.post("/v1/config", headers=H, json={"vars": READ_CFG})
    monkeypatch.setattr(m, "probe_read_only_account", _resultado_prova)
    assert c.post("/v1/live/preparacao-leitura", headers=H,
                  json=_preparacao_body()).status_code == 200
    caminho = next((tmp_path / "desks").rglob("preparacao_real_leitura.json"))
    prova = json.loads(caminho.read_text(encoding="utf-8"))
    prova["verificada_em"] = horario
    caminho.write_text(json.dumps(prova), encoding="utf-8")

    status = c.get("/v1/live/status", headers=H).json()
    assert status["preparacao_leitura"]["valida"] is False
    assert _item(status, "permissões mínimas")["pronto"] is False


def test_prova_legada_schema_1_falha_fechado(tmp_path, monkeypatch):
    m, c = _app(tmp_path, monkeypatch)
    c.post("/v1/config", headers=H, json={"vars": READ_CFG})
    monkeypatch.setattr(m, "probe_read_only_account", _resultado_prova)
    assert c.post("/v1/live/preparacao-leitura", headers=H,
                  json=_preparacao_body()).status_code == 200
    caminho = next((tmp_path / "desks").rglob("preparacao_real_leitura.json"))
    prova = json.loads(caminho.read_text(encoding="utf-8"))
    prova["schema"] = "dz23.preparacao-real-leitura/1"
    caminho.write_text(json.dumps(prova), encoding="utf-8")

    status = c.get("/v1/live/status", headers=H).json()
    assert status["preparacao_leitura"]["valida"] is False
    assert _item(status, "permissões mínimas")["pronto"] is False


def test_vigia_live_bloqueia_piloto_sem_vinculo_ou_com_prova_invalida(
        tmp_path, monkeypatch):
    m, _ = _app(tmp_path, monkeypatch)
    estado = SimpleNamespace(value="piloto_autorizado")
    real = SimpleNamespace(
        agent_id="trader-real", estado=estado, conta_real=True,
        corretora="binance", conta="real-principal",
        broker_identity_fingerprint="b" * 64)
    legado = SimpleNamespace(
        agent_id="trader-legado", estado=estado, conta_real=False,
        corretora="binance", conta="paper-legacy")
    supervisor = SimpleNamespace(autorizacoes=lambda: [real, legado])
    prova = {
        "exchange": "binance", "conta": "real-principal",
        "authenticated_read": True, "sem_trade_declarado": True,
        "sem_saque_declarado": True, "broker_identity_verified": True,
        "broker_identity_fingerprint": "b" * 64,
    }

    bloqueios = m._bloqueios_de_conta_real(supervisor, prova, True)
    assert "trader-real" not in bloqueios
    assert "vínculo explícito" in bloqueios["trader-legado"]

    bloqueios = m._bloqueios_de_conta_real(supervisor, prova, False)
    assert "prova somente leitura" in bloqueios["trader-real"]


def test_certificacao_amarra_estrategia_conta_risco_e_reconciliacao(
        tmp_path, monkeypatch):
    m, c = _app(tmp_path, monkeypatch)
    c.post("/v1/config", headers=H, json={"vars": {
        **READ_CFG, "LIVE_MAX_ORDER_VALUE": "40",
        "LIVE_MAX_DAILY_LOSS": "8"}})
    monkeypatch.setattr(m, "probe_read_only_account", _resultado_prova)
    assert c.post("/v1/live/preparacao-leitura", headers=H,
                  json=_preparacao_body()).status_code == 200

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
                "conta_real": True,
                "broker_identity_fingerprint": _broker_identity_fingerprint(
                    {**READ_CFG, "REAL_READ_ONLY_ACCOUNT_ID":
                     BROKER_ACCOUNT_ID}, BROKER_ACCOUNT_ID),
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
    monkeypatch.setattr(m, "probe_read_only_account", _resultado_prova)
    assert c.post("/v1/live/preparacao-leitura", headers=H,
                  json=_preparacao_body()).status_code == 200

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
                     "broker_identity_fingerprint":
                         _broker_identity_fingerprint(
                             {**READ_CFG, "REAL_READ_ONLY_ACCOUNT_ID":
                              BROKER_ACCOUNT_ID}, BROKER_ACCOUNT_ID),
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
             "sem_saque_declarado": True,
             "broker_identity_verified": True,
             "broker_identity_fingerprint": "b" * 64}
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
                "conta_real": True,
                "broker_identity_fingerprint": "b" * 64,
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

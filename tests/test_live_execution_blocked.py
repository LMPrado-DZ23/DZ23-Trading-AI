"""MISSÃO CLAUDE 01 — item 5: a execução com dinheiro real permanece
indisponível sem autorização específica.

Prova, sem qualquer acesso de rede, que o `CCXTBridge` recusa por construção
(fail-closed) todo caminho `mode=live` que não traga, simultaneamente: a frase
EXATA de autorização do dono, as chaves reais e os tetos por ordem e por dia.
E que, em QUALQUER modo, nenhum envio passa sem a marca de transporte seguro
(gate de capital) — a recusa acontece antes de tocar a rede.

Nota de auditoria: com TODAS essas variáveis presentes o código PERMITE live.
Isto é documentado em docs/mission/CLAUDE-01-FINANCIAL-AUDIT.md como achado; a
política (G9) mantém LIVE bloqueado até nova decisão humana explícita. Este
teste NÃO habilita nada: exercita apenas os caminhos de recusa.
"""
from __future__ import annotations

import pytest

from tradingagents.brokers.ccxt_bridge import (
    LIVE_CONFIRM_PHRASE,
    BrokerSecurityError,
    CCXTBridge,
)

BASE = {"TRADINGAGENTS_EXCHANGE": "binance"}


def _env(**extra):
    return {**BASE, **extra}


def test_invalid_mode_is_refused(tmp_path):
    with pytest.raises(BrokerSecurityError, match="BROKER_MODE"):
        CCXTBridge(env=_env(TRADINGAGENTS_BROKER_MODE="paper"),
                   journal_path=str(tmp_path / "j.jsonl"))


def test_live_without_confirmation_phrase_is_refused(tmp_path):
    with pytest.raises(BrokerSecurityError, match="frase exata"):
        CCXTBridge(env=_env(TRADINGAGENTS_BROKER_MODE="live"),
                   journal_path=str(tmp_path / "j.jsonl"))


def test_live_with_wrong_phrase_is_refused(tmp_path):
    with pytest.raises(BrokerSecurityError, match="frase exata"):
        CCXTBridge(env=_env(TRADINGAGENTS_BROKER_MODE="live",
                            LIVE_TRADING_CONFIRM="eu autorizo (quase)"),
                   journal_path=str(tmp_path / "j.jsonl"))


def test_live_with_phrase_but_no_keys_is_refused(tmp_path):
    with pytest.raises(BrokerSecurityError, match="API_KEY"):
        CCXTBridge(env=_env(TRADINGAGENTS_BROKER_MODE="live",
                            LIVE_TRADING_CONFIRM=LIVE_CONFIRM_PHRASE),
                   journal_path=str(tmp_path / "j.jsonl"))


def test_live_without_order_cap_is_refused(tmp_path):
    with pytest.raises(BrokerSecurityError, match="MAX_ORDER_VALUE"):
        CCXTBridge(env=_env(TRADINGAGENTS_BROKER_MODE="live",
                            LIVE_TRADING_CONFIRM=LIVE_CONFIRM_PHRASE,
                            EXCHANGE_API_KEY="k", EXCHANGE_API_SECRET="s"),
                   journal_path=str(tmp_path / "j.jsonl"))


def test_live_without_daily_loss_cap_is_refused(tmp_path):
    with pytest.raises(BrokerSecurityError, match="MAX_DAILY_LOSS"):
        CCXTBridge(env=_env(TRADINGAGENTS_BROKER_MODE="live",
                            LIVE_TRADING_CONFIRM=LIVE_CONFIRM_PHRASE,
                            EXCHANGE_API_KEY="k", EXCHANGE_API_SECRET="s",
                            LIVE_MAX_ORDER_VALUE="100"),
                   journal_path=str(tmp_path / "j.jsonl"))


def test_all_live_guards_raise_before_any_network_client_is_built(tmp_path):
    """As quatro travas de live são verificadas ANTES de `import ccxt` e da
    criação de qualquer cliente de rede — por isso nenhum teste acima precisa
    de rede nem de credencial real."""
    import inspect

    fonte = inspect.getsource(CCXTBridge.__init__)
    pos_guardas = fonte.index('mode == "live"')
    pos_cliente = fonte.index("import ccxt")
    assert pos_guardas < pos_cliente, (
        "as travas de live precisam acontecer antes de criar o cliente de rede")


class TestEstadoAtualDoProdutoNaoEnviaOrdemReal:
    """FIN-1: no estado atual do produto, nenhuma rota HTTP chega ao envio de
    ordem real — o endpoint legado foi removido (410) e a execução é PAPER
    pelo ExecutionRouter governado."""

    def _app(self, tmp_path, monkeypatch):
        import importlib
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        import service.app as m
        importlib.reload(m)
        m._FAILS.clear()
        return m

    def test_legacy_broker_order_route_is_gone(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        r = c.post("/v1/broker/order", headers={"Authorization": "Bearer s3nh4"},
                   json={"job_id": "x", "symbol": "BTC/USDT", "side": "buy",
                         "amount": 1})
        assert r.status_code == 410, "a rota legada de ordem real deve estar removida"

    def test_docstring_no_longer_claims_live_was_removed(self):
        """A documentação do bridge foi alinhada à implementação: não pode
        afirmar que LIVE foi 'removido' enquanto o ramo existe."""
        import inspect

        from tradingagents.brokers import ccxt_bridge
        doc = inspect.getdoc(ccxt_bridge) or ""
        assert "removido nesta branch" not in doc
        assert "BLOCKED_BY_POLICY" in doc


class TestGateDeCapitalGuardaTodoEnvio:
    """Em QUALQUER modo, um estado sem marca de transporte seguro é recusado
    antes de qualquer chamada de rede — o gate é incontornável."""

    def _bridge_testnet(self, tmp_path):
        # binance expõe testnet no ccxt; set_sandbox_mode é local (sem rede).
        return CCXTBridge(env=_env(TRADINGAGENTS_BROKER_MODE="testnet"),
                          journal_path=str(tmp_path / "j.jsonl"))

    def test_empty_state_is_refused_before_the_network(self, tmp_path):
        from tradingagents.gates import InsecureTransportError
        bridge = self._bridge_testnet(tmp_path)
        with pytest.raises(InsecureTransportError):
            bridge.submit_order({}, job_id="j", symbol="BTC/USDT",
                                side="buy", amount=1.0)

    def test_tampered_transport_mark_is_refused(self, tmp_path):
        from tradingagents.gates import InsecureTransportError
        bridge = self._bridge_testnet(tmp_path)
        with pytest.raises(InsecureTransportError):
            bridge.submit_order({"transport_security": {"ok": True}},
                                job_id="j", symbol="BTC/USDT",
                                side="buy", amount=1.0)

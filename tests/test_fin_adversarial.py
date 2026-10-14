"""FIN-2 / FIN-3 — auditoria adversarial do envio de ordem no CCXTBridge,
com corretora SIMULADA (sem rede, sem ordem real).

FIN-3: a corretora aceita (ou pode ter aceitado) a ordem mas a resposta se
perde. Prova: clientOrderId determinístico (dedup pela corretora), tentativa
registrada no journal ANTES da rede (recuperação por clientOrderId) e, em erro
de rede, o bridge RE-LEVANTA — nunca reporta sucesso inventado.

FIN-2: o gate de transporte seguro (assert_secure_for_capital) é camada de
autorização INDEPENDENTE e incontornável, verificada antes de qualquer rede,
inclusive em testnet. (A cadeia de autorização do desk — dono, conta,
estratégia, kill-switch, limites, reconciliação — é coberta por
test_auditoria_blockers.py, test_kill_switch*.py e test_reconciliation_state.py.)

Nenhuma credencial real, nenhuma rede, nenhuma ordem real.
"""
from __future__ import annotations

import json

import pytest

from tradingagents.brokers.ccxt_bridge import BrokerSecurityError, CCXTBridge
from tradingagents.gates import InsecureTransportError, transport_security_mark

SYMBOL = "BTC/USDT"
DATE = "2026-10-14"


def _estado_seguro() -> dict:
    """final_state que PASSA no gate de capital (transporte seguro, ligado à
    identidade da corrida)."""
    mark = transport_security_mark({}, run_ticker=SYMBOL, run_trade_date=DATE)
    return {"company_of_interest": SYMBOL, "trade_date": DATE,
            "transport_security": mark}


class CorretoraSimulada:
    """Dublê de exchange ccxt: registra chamadas e pode perder a resposta."""

    id = "binance"

    def __init__(self, erro: Exception | None = None):
        self.erro = erro
        self.chamadas: list[dict] = []

    def create_order(self, symbol, order_type, side, amount, price, params):
        self.chamadas.append({"symbol": symbol, "side": side,
                              "amount": amount, "params": dict(params)})
        if self.erro is not None:
            raise self.erro
        return {"id": "ex-123", "status": "closed", "filled": amount,
                "average": 100, "clientOrderId": params.get("clientOrderId")}


def _bridge(tmp_path, erro=None):
    b = CCXTBridge(env={"TRADINGAGENTS_EXCHANGE": "binance",
                        "TRADINGAGENTS_BROKER_MODE": "testnet"},
                   journal_path=str(tmp_path / "journal.jsonl"))
    b._ex = CorretoraSimulada(erro)       # injeta o dublê (sem rede)
    return b


class TestClientOrderIdDeterministico:
    def test_same_inputs_same_id(self):
        a = CCXTBridge.client_order_id("job-1", SYMBOL, "buy")
        b = CCXTBridge.client_order_id("job-1", SYMBOL, "buy")
        assert a == b and a.startswith("dz23-")

    def test_different_side_different_id(self):
        assert (CCXTBridge.client_order_id("job-1", SYMBOL, "buy") !=
                CCXTBridge.client_order_id("job-1", SYMBOL, "sell"))

    def test_retry_reuses_the_same_id_so_exchange_can_dedup(self, tmp_path):
        b = _bridge(tmp_path)
        st = _estado_seguro()
        r1 = b.submit_order(st, job_id="job-9", symbol=SYMBOL, side="buy", amount=1.0)
        r2 = b.submit_order(st, job_id="job-9", symbol=SYMBOL, side="buy", amount=1.0)
        assert r1["client_order_id"] == r2["client_order_id"]
        # A corretora recebeu o MESMO clientOrderId nas duas — base da dedup.
        enviados = [c["params"].get("clientOrderId") for c in b._ex.chamadas]
        assert enviados[0] == enviados[1] == r1["client_order_id"]


class TestRespostaPerdida:
    def test_attempt_is_journaled_before_the_network(self, tmp_path):
        erro = TimeoutError("resposta perdida")
        b = _bridge(tmp_path, erro=erro)
        with pytest.raises(TimeoutError):
            b.submit_order(_estado_seguro(), job_id="job-x", symbol=SYMBOL,
                           side="buy", amount=1.0)
        linhas = [json.loads(ln) for ln in
                  (tmp_path / "journal.jsonl").read_text().splitlines() if ln]
        eventos = {ln["event"] for ln in linhas}
        coid = CCXTBridge.client_order_id("job-x", SYMBOL, "buy")
        # A tentativa ficou registrada com o clientOrderId ANTES do erro:
        # recuperação possível mesmo sem a resposta da corretora.
        assert "submit_attempt" in eventos
        tentativa = next(ln for ln in linhas if ln["event"] == "submit_attempt")
        assert tentativa["client_order_id"] == coid
        assert "submit_error" in eventos      # erro também registrado (redigido)
        assert "submit_ok" not in eventos      # NUNCA reporta sucesso

    def test_lost_response_reraises_and_returns_no_success(self, tmp_path):
        erro = ConnectionResetError("conexão caiu após o envio")
        b = _bridge(tmp_path, erro=erro)
        with pytest.raises(ConnectionResetError):
            b.submit_order(_estado_seguro(), job_id="j", symbol=SYMBOL,
                           side="buy", amount=1.0)


class TestGateDeCapitalEhCamadaIndependente:
    """FIN-2: o gate de transporte roda antes da rede, inclusive em testnet,
    e é independente do modo/limites LIVE."""

    def test_empty_state_blocked_before_any_network_call(self, tmp_path):
        b = _bridge(tmp_path)
        with pytest.raises(InsecureTransportError):
            b.submit_order({}, job_id="j", symbol=SYMBOL, side="buy", amount=1.0)
        assert b._ex.chamadas == []        # nunca tocou a corretora

    def test_insecure_transport_mark_blocked_before_network(self, tmp_path):
        b = _bridge(tmp_path)
        estado = _estado_seguro()
        estado["transport_security"]["insecure"] = True
        estado["transport_security"]["insecure_targets"] = [
            {"provider": "ollama", "host": "10.0.0.5"}]
        with pytest.raises(InsecureTransportError):
            b.submit_order(estado, job_id="j", symbol=SYMBOL, side="buy", amount=1.0)
        assert b._ex.chamadas == []

    def test_valid_state_reaches_the_simulated_exchange(self, tmp_path):
        b = _bridge(tmp_path)
        out = b.submit_order(_estado_seguro(), job_id="j", symbol=SYMBOL,
                             side="buy", amount=1.0)
        assert out["order"]["id"] == "ex-123"
        assert len(b._ex.chamadas) == 1

    def test_testnet_never_builds_a_live_client(self, tmp_path):
        """Reforça FIN-1 no nível do bridge: sem autorização LIVE, o modo é
        testnet e os tetos de dinheiro real ficam zerados."""
        b = _bridge(tmp_path)
        assert b.mode == "testnet"
        assert b.max_order_value == 0
        with pytest.raises(BrokerSecurityError):
            CCXTBridge(env={"TRADINGAGENTS_EXCHANGE": "binance",
                            "TRADINGAGENTS_BROKER_MODE": "live"},
                       journal_path=str(tmp_path / "j2.jsonl"))

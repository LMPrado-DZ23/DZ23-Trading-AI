"""§3: contrato de identidade da reconciliação. Só o ID GENUÍNO da exchange
prova ausência (CONFIRMED_NOT_FOUND). O nosso clientOrderId 'dz23-...' e o id
interno NUNCA provam ausência — no máximo UNKNOWN, que mantém a reserva.

Exercita o ADAPTADOR REAL (CCXTDeskBroker em testnet); faz fake só do TRANSPORTE
(o objeto exchange do ccxt), não do adaptador. Requer ccxt (roda no CI Docker)."""
from __future__ import annotations

from tradingagents.desk.brokers import CCXTDeskBroker


class OrderNotFound(Exception):
    """Nome EXATO que o adaptador reconhece como 'a exchange nega o id'."""


class FakeExchange:
    id = "binance"

    def __init__(self, known=None, historico=None):
        self.known = known or {}
        self.historico = historico or []

    def fetch_order(self, id, symbol=None, params=None):
        if id in self.known:
            return self.known[id]
        raise OrderNotFound(f"no order {id}")

    def fetch_orders(self, symbol=None, since=None, limit=None):
        return list(self.historico)


def _broker():
    b = CCXTDeskBroker({"TRADINGAGENTS_EXCHANGE": "binance",
                        "TRADINGAGENTS_BROKER_MODE": "testnet"}, final_state={})
    return b


def test_id_interno_sem_id_da_exchange_e_unknown():
    b = _broker()
    b.bridge._ex = FakeExchange()                 # nada conhecido, histórico vazio
    r = b.fetch_order_by_client_id("audit-coid")  # id interno, SEM id da exchange
    assert r["resultado"] == "UNKNOWN"            # nunca CONFIRMED_NOT_FOUND -> reserva mantida


def test_id_genuino_da_exchange_prova_ausencia():
    b = _broker()
    b.bridge._ex = FakeExchange()                 # a exchange NÃO conhece 'ghost'
    r = b.fetch_order_by_client_id("audit-coid", exchange_order_id="ghost-999")
    assert r["resultado"] == "CONFIRMED_NOT_FOUND"


def test_id_genuino_encontra():
    b = _broker()
    b.bridge._ex = FakeExchange(
        known={"exchange-123": {"id": "exchange-123", "status": "open"}})
    r = b.fetch_order_by_client_id("audit-coid", exchange_order_id="exchange-123")
    assert r["resultado"] == "FOUND"


def test_nosso_clientorderid_nao_conta_como_id_da_exchange():
    # passar 'dz23-...' como se fosse id da exchange NÃO prova ausência
    b = _broker()
    b.bridge._ex = FakeExchange()
    r = b.fetch_order_by_client_id(
        "audit-coid", exchange_order_id="dz23-f045fe561d3125d3f206")
    assert r["resultado"] == "UNKNOWN"


def test_scan_encontra_pelo_clientorderid_enviado():
    b = _broker()
    b.submitted["audit-coid"] = {"exchange_id": "", "sent_coid": "dz23-abc"}
    b.bridge._ex = FakeExchange(historico=[
        {"id": "exchange-123", "clientOrderId": "dz23-abc", "status": "open"}])
    r = b.fetch_order_by_client_id("audit-coid")
    assert r["resultado"] == "FOUND"


def test_reproducao_da_auditoria_nao_libera_reserva():
    # reprodução exata: id interno audit-coid, enviado dz23-f045..., exchange
    # exchange-123 existe. Consultar pelo id interno NÃO pode dar CONFIRMED_NOT_FOUND.
    b = _broker()
    b.submitted["audit-coid"] = {"exchange_id": "exchange-123",
                                 "sent_coid": "dz23-f045fe561d3125d3f206"}
    b.bridge._ex = FakeExchange(
        known={"exchange-123": {"id": "exchange-123", "status": "open"}})
    r = b.fetch_order_by_client_id("audit-coid")   # sem passar id da exchange
    assert r["resultado"] == "FOUND"               # resolve o id genuíno do mapa

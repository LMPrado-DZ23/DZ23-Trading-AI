"""Marco broker CCXT: fail-closed, gate incontornável, idempotência, redação."""
import pytest

from tradingagents.brokers.ccxt_bridge import (
    EXCHANGE_ALIASES,
    BrokerSecurityError,
    CCXTBridge,
)
from tradingagents.gates import MARK_KEY, MARK_SCHEMA, InsecureTransportError


def _env(**kw):
    base = {"TRADINGAGENTS_EXCHANGE": "binance",
            "TRADINGAGENTS_BROKER_MODE": "testnet",
            "EXCHANGE_API_KEY": "k-test-000000",
            "EXCHANGE_API_SECRET": "s-test-000000"}
    base.update(kw)
    return base


# Estado VÁLIDO segundo o contrato selado (r6+r7): schema fechado, insecure
# explícito False, targets vazio, run-binding E identidade própria do estado
# batendo com o mark (estado anônimo é recusado — e há teste disso abaixo).
SECURE_STATE = {
    "company_of_interest": "BTC/USDT",
    "trade_date": "2026-09-04",
    MARK_KEY: {"schema": MARK_SCHEMA, "insecure": False,
               "insecure_targets": [],
               "policy": "backend_policy/rodada5",
               "run": {"ticker": "BTC/USDT", "trade_date": "2026-09-04"}},
}


class TestFailClosed:
    @pytest.mark.parametrize("mode", ["", "paper", "prod", "LIVE ", None])
    def test_invalid_modes_refuse_to_be_born(self, mode, tmp_path):
        env = _env()
        if mode is None:
            env.pop("TRADINGAGENTS_BROKER_MODE")
        else:
            env["TRADINGAGENTS_BROKER_MODE"] = mode
        with pytest.raises(BrokerSecurityError):
            CCXTBridge(env=env, journal_path=tmp_path / "j.jsonl")

    def test_unknown_exchange_refused(self, tmp_path):
        with pytest.raises(BrokerSecurityError):
            CCXTBridge(env=_env(TRADINGAGENTS_EXCHANGE="iqoption"),
                       journal_path=tmp_path / "j.jsonl")

    @pytest.mark.parametrize("ex", ["kraken", "kucoin", "mercadobitcoin", "bitget"])
    def test_exchange_without_testnet_refused_never_degrades(self, ex, tmp_path):
        with pytest.raises(BrokerSecurityError, match="testnet"):
            CCXTBridge(env=_env(TRADINGAGENTS_EXCHANGE=ex),
                       journal_path=tmp_path / "j.jsonl")

    def test_live_without_owner_phrase_refuses(self, tmp_path):
        with pytest.raises(BrokerSecurityError, match="LIVE_TRADING_CONFIRM"):
            CCXTBridge(env=_env(TRADINGAGENTS_BROKER_MODE="live"),
                       journal_path=tmp_path / "j.jsonl")
        with pytest.raises(BrokerSecurityError):
            CCXTBridge(env=_env(TRADINGAGENTS_BROKER_MODE="live",
                                LIVE_TRADING_CONFIRM="autorizo"),
                       journal_path=tmp_path / "j.jsonl")

    @pytest.mark.parametrize("ex", sorted(EXCHANGE_ALIASES))
    def test_live_with_owner_phrase_boots_ALL_nine_exchanges(self, ex, tmp_path):
        from tradingagents.brokers.ccxt_bridge import LIVE_CONFIRM_PHRASE
        b = CCXTBridge(env=_env(TRADINGAGENTS_EXCHANGE=ex,
                                TRADINGAGENTS_BROKER_MODE="live",
                                LIVE_TRADING_CONFIRM=LIVE_CONFIRM_PHRASE),
                       journal_path=tmp_path / "j.jsonl")
        st = b.status()
        assert st["mode"] == "live" and st["sandbox"] is False

    def test_gate_still_mandatory_in_live(self, tmp_path):
        from tradingagents.brokers.ccxt_bridge import LIVE_CONFIRM_PHRASE
        b = CCXTBridge(env=_env(TRADINGAGENTS_BROKER_MODE="live",
                                LIVE_TRADING_CONFIRM=LIVE_CONFIRM_PHRASE),
                       journal_path=tmp_path / "j.jsonl")
        called = {"n": 0}
        b._ex.create_order = lambda *a, **k: called.__setitem__("n", 1)
        with pytest.raises(InsecureTransportError):
            b.submit_order({}, job_id="j", symbol="BTC/USDT", side="buy",
                           amount=0.001)
        assert called["n"] == 0

    @pytest.mark.parametrize("ex", ["binance", "bybit", "okx", "gateio", "coinbase"])
    def test_testnet_capable_exchanges_boot_in_sandbox(self, ex, tmp_path):
        b = CCXTBridge(env=_env(TRADINGAGENTS_EXCHANGE=ex),
                       journal_path=tmp_path / "j.jsonl")
        st = b.status()
        assert st["mode"] == "testnet" and st["sandbox"] is True


class TestGateIsMandatory:
    def test_insecure_state_refused_before_any_network(self, tmp_path):
        b = CCXTBridge(env=_env(), journal_path=tmp_path / "j.jsonl")
        called = {"n": 0}
        b._ex.create_order = lambda *a, **k: called.__setitem__("n", called["n"] + 1)
        anon = {k: v for k, v in SECURE_STATE.items()
                if k not in ("company_of_interest",)}  # r7: anônimo recusa
        mismatch = dict(SECURE_STATE, company_of_interest="ETH/USDT")
        for bad in ({}, {MARK_KEY: {}},
                    {MARK_KEY: {"schema": MARK_SCHEMA, "insecure": True,
                                "insecure_targets": [{}]}},
                    anon, mismatch):
            with pytest.raises(InsecureTransportError):
                b.submit_order(bad, job_id="j1", symbol="BTC/USDT",
                               side="buy", amount=0.001)
        assert called["n"] == 0  # a rede NUNCA foi tocada

    def test_secure_state_reaches_submission(self, tmp_path):
        b = CCXTBridge(env=_env(), journal_path=tmp_path / "j.jsonl")
        sent = {}
        b._ex.create_order = lambda sym, typ, side, amt, price, params: (
            sent.update(symbol=sym, params=params) or {"id": "oid-1"})
        out = b.submit_order(SECURE_STATE, job_id="j1", symbol="BTC/USDT",
                             side="buy", amount=0.001)
        assert sent["symbol"] == "BTC/USDT"
        assert out["order"]["id"] == "oid-1"


class TestIdempotency:
    def test_client_order_id_deterministic(self):
        a = CCXTBridge.client_order_id("job-77", "BTC/USDT", "buy")
        b = CCXTBridge.client_order_id("job-77", "BTC/USDT", "buy")
        c = CCXTBridge.client_order_id("job-78", "BTC/USDT", "buy")
        assert a == b and a != c and a.startswith("dz23-")

    def test_retry_carries_same_client_order_id(self, tmp_path):
        b = CCXTBridge(env=_env(), journal_path=tmp_path / "j.jsonl")
        seen = []
        b._ex.create_order = lambda *a, **k: (
            seen.append(a[5]["clientOrderId"]) or {"id": "x"})
        for _ in range(3):
            b.submit_order(SECURE_STATE, job_id="jR", symbol="ETH/USDT",
                           side="sell", amount=0.01)
        assert len(set(seen)) == 1  # retry ⇒ MESMO clientOrderId


class TestJournalRedaction:
    def test_secrets_never_reach_journal(self, tmp_path):
        jp = tmp_path / "j.jsonl"
        b = CCXTBridge(env=_env(EXCHANGE_API_SECRET="s-SUPersecret-999999"),
                       journal_path=jp)
        def boom(*a, **k):
            raise RuntimeError('auth {"secret": "s-SUPersecret-999999"} rejected')
        b._ex.create_order = boom
        with pytest.raises(RuntimeError):
            b.submit_order(SECURE_STATE, job_id="jS", symbol="BTC/USDT",
                           side="buy", amount=0.001)
        content = jp.read_text()
        assert "s-SUPersecret-999999" not in content
        assert "submit_error" in content


class TestAliasesMatchDecision:
    def test_full_dz23_lineup_present(self):
        assert set(EXCHANGE_ALIASES) == {"binance", "bybit", "okx", "coinbase",
                                         "kraken", "gateio", "kucoin",
                                         "mercadobitcoin", "bitget"}

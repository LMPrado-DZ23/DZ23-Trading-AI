"""Marco broker CCXT: fail-closed, gate incontornável, idempotência, redação."""
from decimal import Decimal

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

    def test_live_without_the_owner_phrase_is_refused(self, tmp_path):
        with pytest.raises(BrokerSecurityError, match="LIVE_TRADING_CONFIRM"):
            CCXTBridge(env=_env(TRADINGAGENTS_BROKER_MODE="live"),
                       journal_path=tmp_path / "j.jsonl")
        with pytest.raises(BrokerSecurityError):
            CCXTBridge(env=_env(TRADINGAGENTS_BROKER_MODE="live",
                                LIVE_TRADING_CONFIRM="autorizo"),
                       journal_path=tmp_path / "j.jsonl")

    def test_live_requires_explicit_money_limits(self, tmp_path):
        """Sem teto, um erro de cálculo vira ordem do tamanho da conta."""
        from tradingagents.brokers.ccxt_bridge import LIVE_CONFIRM_PHRASE
        base = _env(TRADINGAGENTS_BROKER_MODE="live",
                    LIVE_TRADING_CONFIRM=LIVE_CONFIRM_PHRASE)
        with pytest.raises(BrokerSecurityError, match="LIVE_MAX_ORDER_VALUE"):
            CCXTBridge(env=base, journal_path=tmp_path / "j.jsonl")
        with pytest.raises(BrokerSecurityError, match="LIVE_MAX_DAILY_LOSS"):
            CCXTBridge(env={**base, "LIVE_MAX_ORDER_VALUE": "100"},
                       journal_path=tmp_path / "j.jsonl")

    def test_live_boots_with_phrase_and_limits(self, tmp_path):
        from tradingagents.brokers.ccxt_bridge import LIVE_CONFIRM_PHRASE
        b = CCXTBridge(env=_env(TRADINGAGENTS_BROKER_MODE="live",
                                LIVE_TRADING_CONFIRM=LIVE_CONFIRM_PHRASE,
                                LIVE_MAX_ORDER_VALUE="250",
                                LIVE_MAX_DAILY_LOSS="500"),
                       journal_path=tmp_path / "j.jsonl")
        st = b.status()
        assert st["mode"] == "live" and st["sandbox"] is False
        assert st["teto_por_ordem"] == "250" and st["parada_emergencia"] is False


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


class TestDinheiroRealTemFreios:
    """Live existe por decisão do dono — e vem com freios que ele controla."""

    def _live(self, tmp_path, **extra):
        from tradingagents.brokers.ccxt_bridge import LIVE_CONFIRM_PHRASE
        env = _env(TRADINGAGENTS_BROKER_MODE="live",
                   LIVE_TRADING_CONFIRM=LIVE_CONFIRM_PHRASE,
                   LIVE_MAX_ORDER_VALUE="1000", LIVE_MAX_DAILY_LOSS="300")
        env.update(extra)
        b = CCXTBridge(env=env, journal_path=tmp_path / "j.jsonl")
        b._ex.fetch_ticker = lambda s: {"last": 100}
        b._ex.create_order = lambda *a, **k: {"id": "ord-real", "status": "closed",
                                              "filled": a[3], "average": 100}
        return b

    def test_order_within_limit_goes_through(self, tmp_path):
        b = self._live(tmp_path)
        out = b.submit_order(SECURE_STATE, job_id="j1", symbol="BTC/USDT",
                             side="buy", amount=1)          # 1 x 100 = 100
        assert out["order"]["id"] == "ord-real"

    def test_order_above_the_cap_is_refused_before_the_network(self, tmp_path):
        b = self._live(tmp_path)
        chamou = {"n": 0}
        b._ex.create_order = lambda *a, **k: chamou.__setitem__("n", 1)
        with pytest.raises(BrokerSecurityError, match="teto por ordem"):
            b.submit_order(SECURE_STATE, job_id="j2", symbol="BTC/USDT",
                           side="buy", amount=50)            # 50 x 100 = 5000
        assert chamou["n"] == 0

    def test_no_price_means_no_order(self, tmp_path):
        b = self._live(tmp_path)

        def sem_preco(_):
            raise RuntimeError("exchange fora do ar")

        b._ex.fetch_ticker = sem_preco
        with pytest.raises(BrokerSecurityError, match="sem preço"):
            b.submit_order(SECURE_STATE, job_id="j3", symbol="BTC/USDT",
                           side="buy", amount=1)

    def test_daily_loss_limit_stops_real_money(self, tmp_path):
        b = self._live(tmp_path)
        from decimal import Decimal
        b.register_loss(Decimal("300"))
        with pytest.raises(BrokerSecurityError, match="perda do dia"):
            b.submit_order(SECURE_STATE, job_id="j4", symbol="BTC/USDT",
                           side="buy", amount=1)

    def test_emergency_stop_blocks_and_resume_needs_the_phrase(self, tmp_path):
        from tradingagents.brokers.ccxt_bridge import LIVE_CONFIRM_PHRASE
        b = self._live(tmp_path)
        b.emergency_stop("teste")
        with pytest.raises(BrokerSecurityError, match="PARADA DE EMERGÊNCIA"):
            b.submit_order(SECURE_STATE, job_id="j5", symbol="BTC/USDT",
                           side="buy", amount=1)
        with pytest.raises(BrokerSecurityError, match="repita a frase"):
            b.resume("qualquer coisa")
        b.resume(LIVE_CONFIRM_PHRASE)
        assert b.submit_order(SECURE_STATE, job_id="j6", symbol="BTC/USDT",
                              side="buy", amount=1)["order"]["id"]

    def test_capital_gate_still_mandatory_in_live(self, tmp_path):
        b = self._live(tmp_path)
        chamou = {"n": 0}
        b._ex.create_order = lambda *a, **k: chamou.__setitem__("n", 1)
        with pytest.raises(InsecureTransportError):
            b.submit_order({}, job_id="j7", symbol="BTC/USDT", side="buy", amount=1)
        assert chamou["n"] == 0

    def test_live_decisions_are_journaled(self, tmp_path):
        b = self._live(tmp_path)
        b.submit_order(SECURE_STATE, job_id="j8", symbol="BTC/USDT",
                       side="buy", amount=1)
        linhas = (tmp_path / "j.jsonl").read_text()
        assert "live_limits_ok" in linhas and "submit_ok" in linhas
        assert "s-test-000000" not in linhas          # segredo continua fora


class TestDocumentacaoDeRiscoExiste:
    def test_live_guide_states_the_four_locks(self):
        from pathlib import Path
        doc = Path("LIVE-DINHEIRO-REAL.md").read_text(encoding="utf-8")
        for item in ("EU AUTORIZO OPERAR COM DINHEIRO REAL", "LIVE_MAX_ORDER_VALUE",
                     "LIVE_MAX_DAILY_LOSS", "PARADA DE EMERGÊNCIA",
                     "não fecha posições sozinho", "irreversíveis"):
            assert item in doc, f"faltou no guia: {item}"


class TestProtectiveStop:
    """P0-1: stop de VENDA passa pelo mesmo gate e pelos mesmos tetos."""

    def test_stop_is_sell_with_stop_price(self, tmp_path):
        b = CCXTBridge(env=_env(), journal_path=tmp_path / "j.jsonl")
        sent = {}
        b._ex.create_order = lambda sym, typ, side, amt, price, params: (
            sent.update(symbol=sym, type=typ, side=side, amount=amt,
                        price=price, params=params)
            or {"id": "stop-1", "status": "open"})
        out = b.submit_protective_stop(
            SECURE_STATE, job_id="j-stop", symbol="BTC/USDT",
            amount=0.001, stop_price=Decimal("90"))
        assert sent["side"] == "sell"
        assert sent["type"] == "stop_loss_limit"
        assert sent["params"]["stopPrice"] == 90.0
        assert out["order"]["id"] == "stop-1"
        assert "stop_submit_ok" in (tmp_path / "j.jsonl").read_text()

    def test_stop_gate_is_mandatory(self, tmp_path):
        b = CCXTBridge(env=_env(), journal_path=tmp_path / "j.jsonl")
        chamou = {"n": 0}
        b._ex.create_order = lambda *a, **k: chamou.__setitem__("n", 1)
        with pytest.raises(InsecureTransportError):
            b.submit_protective_stop({}, job_id="j-stop", symbol="BTC/USDT",
                                      amount=0.001, stop_price=Decimal("90"))
        assert chamou["n"] == 0

    def test_stop_rejects_unknown_order_type(self, tmp_path):
        b = CCXTBridge(env=_env(), journal_path=tmp_path / "j.jsonl")
        with pytest.raises(BrokerSecurityError, match="STOP_ORDER_TYPE"):
            b.submit_protective_stop(
                SECURE_STATE, job_id="j-stop", symbol="BTC/USDT",
                amount=0.001, stop_price=Decimal("90"), order_type="market")

    def test_stop_honors_live_order_ceiling(self, tmp_path):
        from tradingagents.brokers.ccxt_bridge import LIVE_CONFIRM_PHRASE
        b = CCXTBridge(
            env=_env(TRADINGAGENTS_BROKER_MODE="live",
                     LIVE_TRADING_CONFIRM=LIVE_CONFIRM_PHRASE,
                     LIVE_MAX_ORDER_VALUE="50", LIVE_MAX_DAILY_LOSS="25"),
            journal_path=tmp_path / "j.jsonl")
        b._ex.fetch_ticker = lambda symbol: {"last": 100}
        chamou = {"n": 0}
        b._ex.create_order = lambda *a, **k: chamou.__setitem__("n", 1)
        with pytest.raises(BrokerSecurityError, match="teto"):
            b.submit_protective_stop(
                SECURE_STATE, job_id="j-stop", symbol="BTC/USDT",
                amount=1.0, stop_price=Decimal("90"))
        assert chamou["n"] == 0

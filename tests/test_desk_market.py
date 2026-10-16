"""Feed de mercado com cutoff e adaptador de corretora da mesa.

Cobre: vela em formação descartada, cutoff = fechamento (não relógio),
fonte fora do ar vira qualidade degradada (e não ordem), cache compartilhado,
e a escolha de corretora que nunca vaza para externo por acidente.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tradingagents.desk.brokers import BrokerAdapterError, build_desk_broker
from tradingagents.desk.market import MarketDataError, MarketFeedProviders, PublicMarketFeed
from tradingagents.desk.models import Action, AgentStage
from tradingagents.desk.traders import TrendTrader

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


class FakeExchange:
    """Velas horárias determinísticas; a última ainda está EM FORMAÇÃO."""

    def __init__(self, n=40, fail=False):
        self.n, self.fail, self.calls = n, fail, 0

    def fetch_ohlcv(self, symbol, timeframe="1h", limit=60):
        self.calls += 1
        if self.fail:
            raise RuntimeError("exchange fora do ar")
        rows = []
        for i in range(self.n):
            # vela i abre em NOW - (n-i) horas ⇒ a última abre às 12:00 (aberta)
            open_at = NOW - timedelta(hours=self.n - 1 - i)
            rows.append([int(open_at.timestamp() * 1000), 100, 101, 99,
                         100 + i * 1.5, 10])
        return rows


class FakePagedExchange:
    """Histórico maior que o limite de uma única resposta da corretora."""

    def __init__(self, n=2600):
        self.calls = 0
        self.rows = []
        for i in range(n):
            open_at = NOW - timedelta(hours=n - 1 - i)
            self.rows.append([
                int(open_at.timestamp() * 1000), 100, 101, 99,
                100 + i / 100, 10])

    def fetch_ohlcv(self, symbol, timeframe="1h", since=None, limit=1000):
        self.calls += 1
        candidatas = self.rows if since is None else [
            row for row in self.rows if row[0] >= since]
        return candidatas[:limit]


class TestCutoffEVelaEmFormacao:
    def test_open_candle_is_discarded_and_cutoff_is_close_time(self):
        feed = PublicMarketFeed("binance", client=FakeExchange(n=40))
        snap = feed.snapshot("BTC/USDT", "1h", now=NOW)
        assert len(snap.closes) == 39            # a 40ª ainda está em formação
        assert snap.cutoff == NOW                # fechamento da última fechada
        assert snap.cutoff <= NOW and snap.quality == "ok"

    def test_cutoff_is_not_wall_clock_when_data_is_late(self):
        """Com o relógio adiantado, a vela antes aberta já fechou e entra —
        mas o cutoff continua sendo o FECHAMENTO dela, muito atrás de 'agora'."""
        feed = PublicMarketFeed("binance", client=FakeExchange(n=40))
        later = NOW + timedelta(hours=10)
        snap = feed.snapshot("BTC/USDT", "1h", now=later)
        assert snap.cutoff == NOW + timedelta(hours=1)   # fechamento, não relógio
        assert later - snap.cutoff == timedelta(hours=9)
        assert snap.quality.startswith("degraded: dado atrasado")
        assert len(snap.closes) == 40                    # todas já fechadas

    def test_degraded_snapshot_never_becomes_a_proposal(self):
        feed = PublicMarketFeed("binance", client=FakeExchange(n=40))
        snap = feed.snapshot("BTC/USDT", "1h", now=NOW + timedelta(hours=10))
        t = TrendTrader("t", ("BTC/USDT",))
        for st in (AgentStage.VALIDATED, AgentStage.SHADOW, AgentStage.PAPER_ENABLED):
            t.spec.move_to(st)
        assert t.propose(snap).action is Action.DATA_INSUFFICIENT


class TestFonteForaDoAr:
    def test_source_down_yields_degraded_not_exception(self):
        feed = PublicMarketFeed("binance", client=FakeExchange(fail=True))
        snap = feed.snapshot("BTC/USDT", "1h", now=NOW)
        assert snap.closes == () and snap.quality.startswith("degraded")
        assert "indisponivel" in snap.source

    def test_price_lookup_refuses_to_invent(self):
        feed = PublicMarketFeed("binance", client=FakeExchange(fail=True))
        with pytest.raises(MarketDataError):
            feed.last_price("BTC/USDT")

    def test_providers_skip_instrument_without_price(self):
        feed = PublicMarketFeed("binance", client=FakeExchange(fail=True))
        prov = MarketFeedProviders(feed, ("BTC/USDT", "ETH/USDT"))
        assert prov.prices() == {}               # nenhum preço chutado
        assert all(s.quality.startswith("degraded") for s in prov.snapshots())


class TestCacheCompartilhado:
    def test_same_read_is_reused_across_agents(self):
        ex = FakeExchange(n=40)
        feed = PublicMarketFeed("binance", client=ex, cache_seconds=60)
        for _ in range(5):
            feed.snapshot("BTC/USDT", "1h", now=NOW)
        assert ex.calls == 1                     # uma leitura serve a mesa toda

    def test_different_instruments_are_separate_keys(self):
        ex = FakeExchange(n=40)
        feed = PublicMarketFeed("binance", client=ex, cache_seconds=60)
        feed.snapshot("BTC/USDT", "1h", now=NOW)
        feed.snapshot("ETH/USDT", "1h", now=NOW)
        assert ex.calls == 2


class TestHistoricoPaginado:
    def test_busca_varias_paginas_sem_duplicar_e_descarta_vela_aberta(self):
        ex = FakePagedExchange(n=2600)
        feed = PublicMarketFeed("binance", client=ex)
        snap = feed.historical_snapshot(
            "BTC/USDT", "1h", limit=2200, now=NOW, page_size=500)
        assert ex.calls >= 5
        assert len(snap.closes) == 2200
        assert snap.cutoff == NOW
        assert snap.quality == "ok"
        assert len(set(snap.closes)) == 2200

    def test_historico_incompleto_e_degradado_sem_inventar_velas(self):
        ex = FakePagedExchange(n=120)
        feed = PublicMarketFeed("binance", client=ex)
        snap = feed.historical_snapshot(
            "BTC/USDT", "1h", limit=500, now=NOW, page_size=100)
        assert len(snap.closes) == 119
        assert snap.quality == "degraded: apenas 119/500 velas fechadas"


class TestEscolhaDeCorretora:
    def test_default_is_paper_never_external(self):
        broker, mode = build_desk_broker({}, final_state={})
        assert mode == "paper" and type(broker).__name__ == "PaperBroker"

    def test_explicit_ccxt_without_mode_fails_closed(self):
        from tradingagents.brokers.ccxt_bridge import BrokerSecurityError
        with pytest.raises(BrokerSecurityError, match="testnet"):
            build_desk_broker({"TRADINGAGENTS_DESK_BROKER": "ccxt",
                               "TRADINGAGENTS_EXCHANGE": "binance",
                               "EXCHANGE_CREDENTIAL_EXCHANGE": "binance"},
                              final_state={})

    def test_ccxt_recusa_credencial_de_outra_corretora_antes_da_rede(self):
        from tradingagents.brokers.ccxt_bridge import BrokerSecurityError
        with pytest.raises(BrokerSecurityError, match="não está vinculada"):
            build_desk_broker({
                "TRADINGAGENTS_DESK_BROKER": "ccxt",
                "TRADINGAGENTS_EXCHANGE": "bybit",
                "EXCHANGE_CREDENTIAL_EXCHANGE": "binance",
                "TRADINGAGENTS_BROKER_MODE": "testnet",
                "EXCHANGE_API_KEY": "binance-key",
                "EXCHANGE_API_SECRET": "binance-secret",
            }, final_state={})

    def test_ccxt_testnet_builds_and_reports_mode(self, tmp_path):
        broker, mode = build_desk_broker(
            {"TRADINGAGENTS_DESK_BROKER": "ccxt", "TRADINGAGENTS_EXCHANGE": "binance",
             "TRADINGAGENTS_BROKER_MODE": "testnet", "EXCHANGE_API_KEY": "k",
             "EXCHANGE_API_SECRET": "s",
             "EXCHANGE_CREDENTIAL_EXCHANGE": "binance"},
            final_state={}, journal_path=str(tmp_path / "j.jsonl"))
        assert mode == "testnet" and broker.status()["sandbox"] is True

    def test_adapter_refuses_without_capital_gate(self, tmp_path):
        """Estado sem marca de transporte ⇒ a ponte recusa ANTES da rede."""
        broker, _ = build_desk_broker(
            {"TRADINGAGENTS_DESK_BROKER": "ccxt", "TRADINGAGENTS_EXCHANGE": "binance",
             "TRADINGAGENTS_BROKER_MODE": "testnet", "EXCHANGE_API_KEY": "k",
             "EXCHANGE_API_SECRET": "s",
             "EXCHANGE_CREDENTIAL_EXCHANGE": "binance"},
            final_state={}, journal_path=str(tmp_path / "j.jsonl"))
        called = {"n": 0}
        broker.bridge._ex.create_order = lambda *a, **k: called.__setitem__("n", 1)
        with pytest.raises(BrokerAdapterError):
            broker.submit("coid-1", "BTC/USDT", "buy", Decimal("0.001"))
        assert called["n"] == 0

    def test_adapter_retry_does_not_duplicate(self, tmp_path):
        broker, _ = build_desk_broker(
            {"TRADINGAGENTS_DESK_BROKER": "ccxt", "TRADINGAGENTS_EXCHANGE": "binance",
             "TRADINGAGENTS_BROKER_MODE": "testnet", "EXCHANGE_API_KEY": "k",
             "EXCHANGE_API_SECRET": "s",
             "EXCHANGE_CREDENTIAL_EXCHANGE": "binance"},
            final_state={}, journal_path=str(tmp_path / "j.jsonl"))
        sent = []
        broker.bridge.submit_order = lambda *a, **k: (
            sent.append(1) or {"client_order_id": "c1",
                               "order": {"id": "x1", "status": "closed",
                                         "filled": 0.001, "average": 100}})
        first = broker.submit("coid-2", "BTC/USDT", "buy", Decimal("0.001"))
        again = broker.submit("coid-2", "BTC/USDT", "buy", Decimal("0.001"))
        assert first == again and len(sent) == 1


class TestPaperUsaPrecoDoFeed:
    """Achado em teste vivo: com feed real, o paper preenchia a preço fixo e
    a contabilidade mentia."""

    def test_paper_fills_at_feed_price(self):
        from tradingagents.desk.execution import PaperBroker
        pb = PaperBroker(Decimal("100"),
                         price_provider=lambda: {"BTC/USDT": Decimal("115432.5")})
        out = pb.submit("c1", "BTC/USDT", "buy", Decimal("0.01"))
        assert out["average"] == Decimal("115432.5")
        assert out["fee"] > 0

    def test_falls_back_when_provider_fails(self):
        from tradingagents.desk.execution import PaperBroker

        def boom():
            raise RuntimeError("feed caiu")

        pb = PaperBroker(Decimal("100"), price_provider=boom)
        assert pb.submit("c2", "BTC/USDT", "buy", Decimal("1"))["average"] == Decimal("100")

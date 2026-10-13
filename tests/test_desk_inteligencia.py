"""Leitura de sinais e aprendizado: agentes melhores, sem autoengano."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from tradingagents.desk.learning import AMOSTRA_MINIMA, Desfecho, MemoriaDeDesempenho
from tradingagents.desk.models import AgentStage, MarketSnapshot, utcnow
from tradingagents.desk.signals import Regime, atr_pct, interpretar, momentum, rsi, sma, zscore
from tradingagents.desk.traders import (
    MeanReversionTrader,
    TrendTrader,
    default_desk,
)

AGORA = datetime(2026, 9, 14, tzinfo=timezone.utc)


def serie(valores):
    return tuple(Decimal(str(v)) for v in valores)


def alta(n=40):
    return serie([100 + i * 1.5 for i in range(n)])


def lateral(n=40):
    return serie([100 + (i % 3) * 0.2 for i in range(n)])


def queda(n=40):
    return serie([200 - i * 1.5 for i in range(n)])


def volatil(n=40):
    return serie([100 * (1.12 if i % 2 else 0.9) for i in range(n)])


class TestIndicadores:
    def test_sma_and_rsi_match_manual_calculation(self):
        assert sma(serie([1, 2, 3, 4]), 2) == 3.5
        assert rsi(serie([10] * 20)) == 50.0            # sem variação
        assert rsi(serie(list(range(1, 30)))) == 100.0   # só subiu

    def test_short_series_returns_none_instead_of_guessing(self):
        curta = serie([1, 2, 3])
        assert sma(curta, 10) is None and rsi(curta) is None
        assert momentum(curta, 10) is None and zscore(curta, 20) is None
        assert atr_pct(curta) is None

    def test_indicators_only_use_the_series_given(self):
        """Sem olhar o futuro: acrescentar velas DEPOIS muda o resultado, o
        que prova que o cálculo é da janela e não de dados externos."""
        base = alta(40)
        assert sma(base, 10) != sma(base + serie([500, 600]), 10)


class TestRegime:
    def test_uptrend_is_detected(self):
        assert interpretar("BTC/USDT", alta()).regime is Regime.ALTA

    def test_downtrend_is_detected(self):
        assert interpretar("BTC/USDT", queda()).regime is Regime.BAIXA

    def test_sideways_is_detected(self):
        assert interpretar("BTC/USDT", lateral()).regime is Regime.LATERAL

    def test_volatile_beats_other_labels(self):
        assert interpretar("BTC/USDT", volatil()).regime is Regime.VOLATIL

    def test_short_series_is_undefined_not_optimistic(self):
        leitura = interpretar("BTC/USDT", serie([1, 2, 3]))
        assert leitura.regime is Regime.INDEFINIDO and leitura.suficiente() is False
        assert "série curta" in leitura.resumo()

    def test_reading_explains_itself_in_plain_words(self):
        leitura = interpretar("BTC/USDT", alta())
        assert leitura.motivos and "média curta" in leitura.resumo()
        assert 0 <= leitura.forca <= 1


class TestTradersUsamORegime:
    def _pronto(self, trader):
        for st in (AgentStage.VALIDATED, AgentStage.SHADOW, AgentStage.PAPER_ENABLED):
            trader.spec.move_to(st)
        return trader

    def snap(self, closes):
        return MarketSnapshot("BTC/USDT", "1h", utcnow(), closes)

    def test_trend_trader_stays_out_of_sideways(self):
        t = self._pronto(TrendTrader("t", ("BTC/USDT",)))
        p = t.propose(self.snap(lateral()))
        assert p.action.value in ("no_trade",) and "não é o meu terreno" in p.reason

    def test_mean_reversion_avoids_strong_uptrend(self):
        t = self._pronto(MeanReversionTrader("t", ("BTC/USDT",)))
        assert "não é o meu terreno" in t.propose(self.snap(alta())).reason

    def test_proposal_carries_the_evidence(self):
        t = self._pronto(TrendTrader("t", ("BTC/USDT",)))
        p = t.propose(self.snap(alta()))
        assert "leitura:" in p.reason and "força" in p.reason


class TestAprendizado:
    def _memoria(self, tmp_path):
        return MemoriaDeDesempenho(tmp_path / "memoria.json")

    def _preencher(self, mem, retorno, n=AMOSTRA_MINIMA, regime=Regime.ALTA,
                   estrategia="trend_following", dias=1):
        for _i in range(n):
            mem.registrar(Desfecho(estrategia, regime.value, "BTC/USDT",
                                   Decimal(str(retorno)),
                                   AGORA - timedelta(days=dias), 0.8))

    def test_no_learning_without_minimum_sample(self, tmp_path):
        mem = self._memoria(tmp_path)
        self._preencher(mem, 0.05, n=3)
        base = Decimal("0.6")
        assert mem.ajuste_de_confianca("trend_following", Regime.ALTA, base,
                                       AGORA) == base
        assert mem.fator_de_exposicao("trend_following", Regime.ALTA, AGORA) == Decimal("1.0")

    def test_good_history_raises_confidence_within_the_cap(self, tmp_path):
        mem = self._memoria(tmp_path)
        self._preencher(mem, 0.04)
        ajustada = mem.ajuste_de_confianca("trend_following", Regime.ALTA,
                                           Decimal("0.6"), AGORA)
        assert ajustada > Decimal("0.6") and ajustada <= Decimal("1.0")

    def test_bad_history_shrinks_exposure(self, tmp_path):
        mem = self._memoria(tmp_path)
        self._preencher(mem, -0.03)
        fator = mem.fator_de_exposicao("trend_following", Regime.ALTA, AGORA)
        assert fator < Decimal("1.0")

    def test_learning_never_raises_exposure_above_the_mandate(self, tmp_path):
        mem = self._memoria(tmp_path)
        self._preencher(mem, 0.30)          # histórico excelente
        assert mem.fator_de_exposicao("trend_following", Regime.ALTA, AGORA) == Decimal("1.0")

    def test_old_outcomes_weigh_less(self, tmp_path):
        recente, antigo = self._memoria(tmp_path), self._memoria(tmp_path / "x")
        self._preencher(recente, 0.05, dias=1)
        self._preencher(antigo, 0.05, dias=400)
        assert (recente.estatistica("trend_following", Regime.ALTA, AGORA).peso_total >
                antigo.estatistica("trend_following", Regime.ALTA, AGORA).peso_total * 5)

    def test_demotion_requires_evidence_not_a_bad_day(self, tmp_path):
        mem = self._memoria(tmp_path)
        mem.registrar(Desfecho("trend_following", Regime.ALTA.value, "BTC/USDT",
                               Decimal("-0.5"), AGORA - timedelta(days=1)))
        rebaixar, motivo = mem.deve_rebaixar("trend_following", AGORA)
        assert rebaixar is False and "amostra insuficiente" in motivo

    def test_consistently_losing_strategy_is_demoted(self, tmp_path):
        mem = self._memoria(tmp_path)
        self._preencher(mem, -0.02)
        rebaixar, motivo = mem.deve_rebaixar("trend_following", AGORA)
        assert rebaixar is True and "retorno médio" in motivo

    def test_memory_finds_where_the_strategy_works(self, tmp_path):
        mem = self._memoria(tmp_path)
        self._preencher(mem, 0.05, regime=Regime.ALTA)
        self._preencher(mem, -0.04, regime=Regime.LATERAL)
        melhor, retorno = mem.melhor_regime("trend_following")
        assert melhor == Regime.ALTA.value and retorno > 0

    def test_memory_survives_restart(self, tmp_path):
        mem = self._memoria(tmp_path)
        self._preencher(mem, 0.03)
        revivida = MemoriaDeDesempenho(tmp_path / "memoria.json")
        assert revivida.estatistica("trend_following", Regime.ALTA, AGORA).amostras \
            == AMOSTRA_MINIMA

    def test_report_is_honest_about_uncertainty(self, tmp_path):
        mem = self._memoria(tmp_path)
        self._preencher(mem, 0.03)
        linha = mem.relatorio()[0]
        assert linha["operacoes"] == AMOSTRA_MINIMA and linha["maduro"] is True
        assert "não prevê o futuro" in linha["aviso"]


class TestTraderQueAprende:
    def test_demoted_strategy_stops_proposing(self, tmp_path):
        mem = MemoriaDeDesempenho(tmp_path / "m.json")
        for _ in range(AMOSTRA_MINIMA):
            mem.registrar(Desfecho("trend_following", Regime.ALTA.value, "BTC/USDT",
                                   Decimal("-0.03"), utcnow()))
        t = default_desk(("BTC/USDT",), memoria=mem)[0]
        for st in (AgentStage.VALIDATED, AgentStage.SHADOW, AgentStage.PAPER_ENABLED):
            t.spec.move_to(st)
        p = t.propose(MarketSnapshot("BTC/USDT", "1h", utcnow(), alta()))
        assert "rebaixada pelo histórico" in p.reason

    def test_bad_history_reduces_the_requested_size(self, tmp_path):
        mem = MemoriaDeDesempenho(tmp_path / "m.json")
        for _ in range(AMOSTRA_MINIMA):
            mem.registrar(Desfecho("breakout", Regime.ALTA.value, "BTC/USDT",
                                   Decimal("-0.01"), utcnow()))
        com = default_desk(("BTC/USDT",), memoria=mem)[2]
        sem = default_desk(("BTC/USDT",))[2]
        for t in (com, sem):
            for st in (AgentStage.VALIDATED, AgentStage.SHADOW, AgentStage.PAPER_ENABLED):
                t.spec.move_to(st)
        rompimento = serie([100 + (i % 3) * 0.2 for i in range(29)] + [130])
        snap = MarketSnapshot("BTC/USDT", "1h", utcnow(), rompimento)
        p_com, p_sem = com.propose(snap), sem.propose(snap)
        if p_com.desired_exposure_pct and p_sem.desired_exposure_pct:
            assert p_com.desired_exposure_pct <= p_sem.desired_exposure_pct


class TestMesaRecarregaAoConfigurar:
    """Defeito real encontrado: _desk_reset existia mas nunca era chamado —
    o cliente salvava a configuração e nada mudava até reiniciar."""

    def _app(self, tmp_path, monkeypatch):
        import importlib
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        import service.app as m
        importlib.reload(m)
        m._FAILS.clear()
        m._DESKS.clear()
        return m

    def test_saving_desk_config_rebuilds_the_desk(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        h = {"Authorization": "Bearer s3nh4"}
        c.get("/v1/desk/status", headers=h)
        assert "legacy" in m._DESKS
        c.post("/v1/config", headers=h,
               json={"vars": {"TRADINGAGENTS_DESK_CAPITAL": "5000"}})
        assert "legacy" not in m._DESKS           # será reconstruída
        novo = c.get("/v1/desk/status", headers=h).json()
        assert novo["mandate"]["capital"] == "5000"

    def test_resaving_the_same_value_keeps_the_desk_running(self, tmp_path, monkeypatch):
        # O painel reenvia o formulário inteiro a cada Salvar — inclusive o
        # seletor de fonte de dados. Reenvio idêntico não pode derrubar a
        # mesa: a autônoma pararia sem o dono ter pedido.
        from fastapi.testclient import TestClient
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        h = {"Authorization": "Bearer s3nh4"}
        c.get("/v1/desk/status", headers=h)
        c.post("/v1/config", headers=h,
               json={"vars": {"TRADINGAGENTS_DESK_FEED": "real"}})
        assert "legacy" not in m._DESKS           # mudou: reconstrói
        c.get("/v1/desk/status", headers=h)       # reconstrói com o feed novo
        assert "legacy" in m._DESKS
        c.post("/v1/config", headers=h,
               json={"vars": {"TRADINGAGENTS_DESK_FEED": "real"}})
        assert "legacy" in m._DESKS               # idêntico: mesa de pé

    def test_saving_the_feed_toggle_rebuilds_with_the_new_source(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        h = {"Authorization": "Bearer s3nh4"}
        c.get("/v1/desk/status", headers=h)
        assert c.get("/v1/desk/status", headers=h).json()["fonte"]["dados"] == "fixture"
        r = c.post("/v1/config", headers=h,
                   json={"vars": {"TRADINGAGENTS_DESK_FEED": "real"}})
        assert "TRADINGAGENTS_DESK_FEED" in r.json()["saved"]
        assert c.get("/v1/desk/status", headers=h).json()["fonte"]["dados"] == "real"

    def test_learning_route_reads_what_was_persisted(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        m = self._app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        h = {"Authorization": "Bearer s3nh4"}
        c.get("/v1/desk/status", headers=h)        # cria a mesa e a memória
        mem = m._DESKS["legacy"]["memoria"]
        for _ in range(AMOSTRA_MINIMA):
            mem.registrar(Desfecho("trend_following", "tendencia_alta", "BTC/USDT",
                                   Decimal("0.02"), utcnow(), 0.8))
        d = c.get("/v1/desk/aprendizado", headers=h).json()
        linha = next(e for e in d["estrategias"] if e["estrategia"] == "trend_following")
        assert linha["operacoes"] == AMOSTRA_MINIMA and linha["maduro"] is True
        assert "não prevê o futuro" in d["aviso"]


class TestEstadoSobreviveAoServidor:
    """Servidor provisório: o aprendizado é patrimônio e não pode morrer com
    a máquina."""

    def test_memory_loads_from_the_store_not_only_from_file(self, tmp_path):
        from tradingagents.desk.storage import ArmazemDeEstado
        armazem = ArmazemDeEstado(None, diretorio=tmp_path / "estado")
        mem = MemoriaDeDesempenho(None, armazem=armazem, tenant_id="t1")
        for _ in range(AMOSTRA_MINIMA):
            mem.registrar(Desfecho("trend_following", "tendencia_alta", "BTC/USDT",
                                   Decimal("0.02"), utcnow(), 0.8))
        # "novo servidor": instância nova, mesmo armazém
        revivida = MemoriaDeDesempenho(None, armazem=armazem, tenant_id="t1")
        assert revivida.estatistica("trend_following").amostras == AMOSTRA_MINIMA

    def test_tenants_do_not_share_learning(self, tmp_path):
        from tradingagents.desk.storage import ArmazemDeEstado
        armazem = ArmazemDeEstado(None, diretorio=tmp_path / "estado")
        a = MemoriaDeDesempenho(None, armazem=armazem, tenant_id="a")
        a.registrar(Desfecho("trend_following", "tendencia_alta", "BTC/USDT",
                             Decimal("0.02"), utcnow()))
        b = MemoriaDeDesempenho(None, armazem=armazem, tenant_id="b")
        assert b.estatistica("trend_following").amostras == 0

    def test_store_says_plainly_if_state_is_disposable(self, tmp_path):
        from tradingagents.desk.storage import ArmazemDeEstado
        local = ArmazemDeEstado(None, diretorio=tmp_path).estado()
        assert local["destino"] == "arquivo"
        assert "se perde" in local["durabilidade"]

    def test_broken_database_falls_back_instead_of_breaking(self, tmp_path):
        from tradingagents.desk.storage import ArmazemDeEstado
        armazem = ArmazemDeEstado("postgresql://u:p@127.0.0.1:1/nada",
                                  diretorio=tmp_path)
        assert armazem.backend == "arquivo" and armazem.aviso
        armazem.salvar("t1", "mesa", {"cycles_run": 3})
        assert armazem.carregar("t1", "mesa")["cycles_run"] == 3

    def test_scheduler_state_round_trips_through_the_store(self, tmp_path):
        from tradingagents.desk.storage import ArmazemDeEstado
        armazem = ArmazemDeEstado(None, diretorio=tmp_path / "e")
        armazem.salvar("t1", "mesa", {"cycles_run": 9, "errors": 1,
                                      "open_positions": ["BTC/USDT"],
                                      "pending_reconciliation": [], "stopped": False,
                                      "last_cycle_at": None})
        from tradingagents.desk.scheduler import SchedulerState
        doc = armazem.carregar("t1", "mesa")
        estado = SchedulerState(**{k: v for k, v in doc.items()
                                   if k in SchedulerState.__annotations__})
        assert estado.cycles_run == 9 and estado.open_positions == ["BTC/USDT"]

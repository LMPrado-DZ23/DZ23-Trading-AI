"""M01 — Mandato MAESTRO: dez traders, evidência estatística e painel.

O que o mandato QODER-TEN-TRADERS exige e este arquivo prova:
- identidade estável dos dez perfis e contrato completo por trader;
- supervisão por regime com abstenção EXPLÍCITA (não silêncio);
- trader sem evidência estatística NÃO é habilitado (gate determinístico);
- distinção aprovado (homologado) × experimental, persistida no reinício;
- ciclo PAPER de ponta a ponta com reinício e estado durável.
"""
from __future__ import annotations

import importlib
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from tradingagents.desk.learning import Desfecho, MemoriaDeDesempenho
from tradingagents.desk.models import (
    Action,
    AgentStage,
    MarketSnapshot,
)
from tradingagents.desk.traders import MarketRegimeSupervisor, default_desk

IDS_ESPERADOS = ("trader-trend", "trader-reversion", "trader-breakout",
                 "trader-scalping", "trader-swing", "trader-momentum",
                 "trader-volatility", "trader-arbitrage",
                 "trader-statistical", "trader-adaptive")

# Cutoff fixo nos testes do supervisor: a arbitragem decide pelo ESTADO DE
# MERCADO (instrumento + último fechamento), então a cena do fixture já é
# reprodutível por si — o cutoff é higiene do fixture, não variável de decisão.
CUTOFF_FIXO = datetime(2026, 1, 1, tzinfo=timezone.utc)


def alta(n=40):
    return tuple(Decimal(str(100 + i * 1.5)) for i in range(n))


def _memoria_com(tmp_path, estrategia, retornos):
    mem = MemoriaDeDesempenho(tmp_path / "aprendizado.json")
    for r in retornos:
        mem.registrar(Desfecho(
            estrategia=estrategia, regime="tendencia_alta",
            instrumento="BTC/USDT", retorno=Decimal(str(r)),
            fechado_em=datetime.now(timezone.utc)))
    return mem


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import service.app as m
    importlib.reload(m)
    m._FAILS.clear()
    m._DESKS.clear()
    from fastapi.testclient import TestClient
    return TestClient(m.app), m


H = {"Authorization": "Bearer s3nh4"}


class TestContratoDosDez:
    def test_ids_estaveis_exatos(self):
        traders = default_desk(("BTC/USDT",))
        assert tuple(t.spec.agent_id for t in traders) == IDS_ESPERADOS

    def test_contrato_completo_por_trader(self):
        for t in default_desk(("BTC/USDT",)):
            assert t.spec.version, f"{t.spec.agent_id} sem versão"
            assert t.strategy and t.timeframe, f"{t.spec.agent_id} sem contrato"
            assert t.needs >= 5 and t.movimento_alvo > 0
            assert isinstance(t.regimes_favoraveis, tuple)
            assert t.universe == ("BTC/USDT",)

    def test_tres_preservados_sao_homologados(self):
        por_id = {t.spec.agent_id: t for t in default_desk(("BTC/USDT",))}
        for ident in IDS_ESPERADOS[:3]:
            assert por_id[ident].homologado is True, ident
        for ident in IDS_ESPERADOS[3:]:
            assert por_id[ident].homologado is False, ident


class TestSupervisorDeRegime:
    def _aptos(self, universo=("BTC/USDT",)):
        traders = default_desk(universo)
        for t in traders:
            t.spec.move_to(AgentStage.VALIDATED)
            t.spec.move_to(AgentStage.SHADOW)
        return traders

    def test_elegivel_propoe_e_inelegivel_recebe_filtro_explicito(self):
        traders = self._aptos()
        sup = MarketRegimeSupervisor(traders, Decimal("1000"))
        snaps = [MarketSnapshot("BTC/USDT", "1h", CUTOFF_FIXO, alta(),
                                source="fixture")]
        props = sup.supervise(snaps, {})
        por_id = {p.agent_id: p for p in props}
        # quem vence a disputa de exposição é o sinal de MAIOR confiança
        # (momentum 0.7 > trend 0.6): o supervisor resolve conflito, não
        # empilha duas compras no mesmo instrumento.
        assert por_id["trader-momentum"].action is Action.BUY
        for perdedor in ("trader-trend", "trader-adaptive"):
            assert por_id[perdedor].action is Action.NO_TRADE
            assert "exposição duplicada" in por_id[perdedor].reason
        filtrado = por_id["trader-scalping"]  # só opera LATERAL/VOLATIL
        assert filtrado.action is Action.NO_TRADE
        assert "Filtro do Supervisor" in filtrado.reason

    def test_orcamento_dividido_entre_elegiveis(self):
        traders = self._aptos()
        sup = MarketRegimeSupervisor(traders, Decimal("1000"))
        snaps = [MarketSnapshot("BTC/USDT", "1h", CUTOFF_FIXO, alta(),
                                source="fixture")]
        sup.supervise(snaps, {})
        por_id = {t.spec.agent_id: t for t in traders}
        # 1000 entre os 6 elegíveis da ALTA (trend, breakout, swing, momentum,
        # arbitrage, adaptive) — e os inativos não consomem orçamento.
        assert por_id["trader-trend"].capital == Decimal("166.67")
        assert por_id["trader-scalping"].capital == Decimal("1000")


class TestGateDeEvidencia:
    def test_sem_amostra_nao_promove(self, tmp_path):
        ok, motivo = MemoriaDeDesempenho(
            tmp_path / "a.json").pode_promover("scalping")
        assert ok is False and "amostra insuficiente" in motivo

    def test_amostra_madura_positiva_promove(self, tmp_path):
        mem = _memoria_com(tmp_path, "scalping", [0.004] * 14)
        ok, motivo = mem.pode_promover("scalping")
        assert ok is True and "evidência suficiente" in motivo

    def test_retorno_medio_negativo_reprova(self, tmp_path):
        mem = _memoria_com(tmp_path, "scalping", [-0.004] * 14)
        ok, motivo = mem.pode_promover("scalping")
        assert ok is False and "não é positivo" in motivo

    def test_acerto_abaixo_de_45_reprova(self, tmp_path):
        # retorno médio positivo, mas ganhos concentrados: 5 de 12 = 41.7%
        mem = _memoria_com(tmp_path, "scalping",
                           [0.05] * 5 + [-0.005] * 7)
        ok, motivo = mem.pode_promover("scalping")
        assert ok is False and "abaixo de 45%" in motivo


class TestBootETiersDaMesa:
    def test_boot_tiers_homologados_e_experimentais(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        st = c.get("/v1/desk/status", headers=H).json()
        agents = st["agents"]
        assert tuple(a["id"] for a in agents) == IDS_ESPERADOS
        homologados = [a for a in agents if a["tier"] == "homologado"]
        experimentais = [a for a in agents if a["tier"] == "experimental"]
        assert [a["id"] for a in homologados] == list(IDS_ESPERADOS[:3])
        assert all(a["stage"] == "paper_enabled"
                   and a["stage_label"] == "aprovado (paper)"
                   for a in homologados)
        assert len(experimentais) == 7
        assert all(a["stage"] == "shadow"
                   and a["stage_label"] == "experimental (paper)"
                   for a in experimentais)
        # painel: contrato e evidência visíveis por trader
        amostra = experimentais[0]
        assert amostra["timeframe"] and amostra["needs"] > 0
        assert amostra["modelo_ia"]
        assert amostra["evidencia"]["elegivel"] is False
        assert "amostra insuficiente" in amostra["evidencia"]["motivo"]

    def test_promover_sem_evidencia_409_e_estado_intacto(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        r = c.post("/v1/desk/traders/trader-scalping/promover", headers=H)
        assert r.status_code == 409
        assert "amostra insuficiente" in r.json()["detail"]
        st = c.get("/v1/desk/status", headers=H).json()
        alvo = next(a for a in st["agents"] if a["id"] == "trader-scalping")
        assert alvo["stage"] == "shadow"          # evidência insuficiente não habilita

    def test_promover_com_evidencia_habilita_e_sobrevive_ao_reinicio(
            self, tmp_path, monkeypatch):
        c, m = _client(tmp_path, monkeypatch)
        c.get("/v1/desk/status", headers=H)
        d = m._DESKS["legacy"]
        for _ in range(14):
            d["memoria"].registrar(Desfecho(
                estrategia="scalping", regime="lateral",
                instrumento="BTC/USDT", retorno=Decimal("0.004"),
                fechado_em=datetime.now(timezone.utc)))
        r = c.post("/v1/desk/traders/trader-scalping/promover", headers=H)
        assert r.status_code == 200
        assert r.json()["stage"] == "paper_enabled"
        assert "evidência suficiente" in r.json()["motivo"]
        # REINÍCIO: mesa reconstruída do disco — a promoção persiste
        m._desk_reset("legacy")
        st = c.get("/v1/desk/status", headers=H).json()
        alvo = next(a for a in st["agents"] if a["id"] == "trader-scalping")
        assert alvo["stage"] == "paper_enabled"

    def test_pausar_retomar_preserva_estagio_anterior(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        r = c.post("/v1/desk/traders/trader-momentum/pausar", headers=H)
        assert r.status_code == 200 and r.json()["stage"] == "paused"
        # pausado não propõe — e o ciclo registra o motivo, não o silencia
        c.post("/v1/desk/cycle", headers=H)
        st = c.get("/v1/desk/status", headers=H).json()
        momentum = next(a for a in st["agents"] if a["id"] == "trader-momentum")
        assert momentum["stage_label"] == "pausado"
        assert "não propõe" in momentum["ultimo_no_trade"]
        r = c.post("/v1/desk/traders/trader-momentum/retomar", headers=H)
        assert r.status_code == 200 and r.json()["stage"] == "shadow"
        # homologado pausado volta para aprovado — sem pular etapas
        c.post("/v1/desk/traders/trader-trend/pausar", headers=H)
        r = c.post("/v1/desk/traders/trader-trend/retomar", headers=H)
        assert r.json()["stage"] == "paper_enabled"

    def test_trader_inexistente_404(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        r = c.post("/v1/desk/traders/trader-fantasma/pausar", headers=H)
        assert r.status_code == 404


class TestCicloPaperE2E:
    def test_ciclo_completo_com_reinicio_e_sem_pendencias(self, tmp_path, monkeypatch):
        c, m = _client(tmp_path, monkeypatch)
        r = c.post("/v1/desk/cycle", headers=H)
        assert r.status_code == 200
        out = r.json()
        assert out["resumo"]
        assert out["abstencoes"], "motivos de NO_TRADE precisam ser visíveis"
        st = c.get("/v1/desk/status", headers=H).json()
        assert st["contabilidade"]["balanceado"] is True
        assert st["mandate"]["mode"] == "paper"   # esta superfície não liga LIVE
        assert any(a["ultimo_no_trade"] for a in st["agents"])
        ordens = len(st["orders"])
        ciclos = st["agendador"]["ciclos"]
        # REINÍCIO: estado durável restaurado do disco, nada fica incerto
        m._desk_reset("legacy")
        st2 = c.get("/v1/desk/status", headers=H).json()
        assert st2["agendador"]["ciclos"] >= ciclos
        assert len(st2["orders"]) == ordens
        assert st2["pendencias_reconciliacao"] == []


class TestPainelMesa:
    def test_painel_tem_estado_evidencia_e_habilitacao(self):
        html = Path("service/static/index.html").read_text(encoding="utf-8")
        for marcador in ("Modelo IA", "Último motivo de não operar",
                         "deskTrader(", "Habilitar", "stage_label",
                         "ultimo_no_trade", "Traders (10 perfis)"):
            assert marcador in html, f"faltou no painel: {marcador}"

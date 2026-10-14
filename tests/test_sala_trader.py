"""TD-2 — Sala individual do trader (dashboard por trader, task #91).

Regras do dono que estes contratos pinam:
- gráfico de mercado (OHLCV/volume/indicadores/S-R/marcações/sinais/abstenções);
- gráfico de portfólio (curva, P&L, custos, drawdown, exposição, hit rate,
  expectativa, profit factor, Champion/Challenger);
- "Os gráficos financeiros devem derivar de ledger e eventos verificáveis,
  não de números simulados indevidamente apresentados como reais.";
- "Não misturar resultados PAPER, TESTNET e LIVE."

O retrato é uma função PURA (`retrato_do_trader`): roda sobre dados que a
rota coleta (velas/diário/livro/ciclos). Nenhum caminho de ordem.
"""
from __future__ import annotations

import importlib
import re
from datetime import datetime, timezone
from pathlib import Path

from tradingagents.desk.sala_trader import (
    curva_de_resultado,
    drawdown_maximo,
    experimentos_do_trader,
    exposicao_do_trader,
    indicadores,
    marcacoes_de_execucao,
    marcacoes_dos_ciclos,
    media_movel,
    metricas_portfolio,
    ordens_do_trader,
    retrato_do_trader,
    rsi,
    suportes_e_resistencias,
    velas_de_rows,
)

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "service" / "static" / "index.html"
MODULO = ROOT / "tradingagents" / "desk" / "sala_trader.py"

H = {"Authorization": "Bearer s3nh4"}


# ---------------------------------------------------------------- unidade


class TestIndicadores:
    def test_media_movel_nula_antes_da_janela(self):
        saida = media_movel([1, 2, 3, 4], 3)
        assert saida[:2] == [None, None]
        assert saida[2] == 2.0 and saida[3] == 3.0

    def test_rsi_dentro_dos_limites_e_nulo_no_inicio(self):
        closes = [100 + (i % 7) * 0.4 - (i % 3) * 0.3 for i in range(40)]
        saida = rsi(closes, 14)
        assert saida[:14] == [None] * 14
        assert all(v is None or 0.0 <= v <= 100.0 for v in saida)
        assert any(v is not None for v in saida)

    def test_serie_curta_nao_inventa_indicador(self):
        out = indicadores([10, 11, 12], rapida=9, lenta=21, rsi_periodo=14)
        assert out["sma_rapida"] == [None, None, None]
        assert out["sma_lenta"] == [None, None, None]
        assert out["rsi"] == [None, None, None]


class TestSuporteResistencia:
    def test_pivos_de_serie_conhecida(self):
        # pico claro em 30 (índice 4) e vale claro em 10 (índice 8)
        closes = [20, 22, 25, 28, 30, 27, 24, 18, 10, 14, 19, 23]
        sr = suportes_e_resistencias(closes, janela=2, maximo=3)
        assert any(abs(r - 30.0) < 1e-9 for r in sr["resistencias"])
        assert any(abs(s - 10.0) < 1e-9 for s in sr["suportes"])
        assert "fechamento" in sr["nota"]

    def test_sem_pivos_devolve_listas_vazias_com_nota(self):
        sr = suportes_e_resistencias([10, 11], janela=2, maximo=3)
        assert sr["resistencias"] == [] and sr["suportes"] == []
        assert sr["nota"]


class TestCurvaEMetricas:
    def _ops(self):
        return [
            {"quando": "2026-10-14T10:00:00+00:00", "retorno_liquido": "0.10",
             "custo": "0.5", "instrumento": "BTC/USDT", "causa": "alvo"},
            {"quando": "2026-10-14T11:00:00+00:00", "retorno_liquido": "-0.05",
             "custo": "0.4", "instrumento": "BTC/USDT", "causa": "stop"},
            {"quando": "2026-10-14T12:00:00+00:00", "retorno_liquido": "0.02",
             "custo": "0.3", "instrumento": "ETH/USDT", "causa": "tempo"},
        ]

    def test_curva_acumulada_soma_simples_e_ordem(self):
        curva = curva_de_resultado(list(reversed(self._ops())))
        assert [c["acumulado"] for c in curva] == [0.10, 0.05, 0.07]
        assert curva[0]["instrumento"] == "BTC/USDT"
        assert curva[-1]["causa"] == "tempo"

    def test_drawdown_do_pico_ao_vale(self):
        assert abs(drawdown_maximo([0.10, 0.05, 0.07]) - 0.05) < 1e-12
        assert drawdown_maximo([]) == 0.0

    def test_metricas_completas(self):
        m = metricas_portfolio(self._ops())
        assert m["operacoes"] == 3 and m["acertos"] == 2
        assert abs(m["taxa_acerto"] - 2 / 3) < 1e-9
        assert abs(m["expectativa"] - (0.10 - 0.05 + 0.02) / 3) < 1e-9
        assert abs(m["profit_factor"] - (0.12 / 0.05)) < 1e-9
        assert m["motivo_profit_factor"] == ""
        assert m["custos_total"] == "1.2"
        assert abs(m["drawdown"] - 0.05) < 1e-9

    def test_sem_operacao_nao_inventa_numero(self):
        m = metricas_portfolio([])
        assert m["operacoes"] == 0
        assert m["taxa_acerto"] is None and m["expectativa"] is None
        assert m["profit_factor"] is None
        assert "sem operações fechadas" in m["motivo_profit_factor"]

    def test_sem_perdas_profit_factor_indefinido_nao_infinito(self):
        m = metricas_portfolio([
            {"quando": "2026-10-14T10:00:00+00:00", "retorno_liquido": "0.05",
             "custo": "0", "instrumento": "X", "causa": ""}])
        assert m["profit_factor"] is None
        assert "sem perdas" in m["motivo_profit_factor"]
        assert "não é" in m["motivo_profit_factor"]


class TestMarcacoes:
    def test_execucoes_do_agente_com_preco_derivado(self):
        txs = [
            {"kind": "fill_buy", "at": "2026-10-14T10:00:00+00:00",
             "instrument": "BTC/USDT", "quantity": "10", "agent_id": "t1",
             "entries": [{"account": "POSITION:BTC/USDT", "amount": "100",
                          "memo": ""},
                         {"account": "FEES:USDT", "amount": "1", "memo": ""},
                         {"account": "RESERVED:r1", "amount": "-101",
                          "memo": ""}]},
            {"kind": "fill_sell", "at": "2026-10-14T11:00:00+00:00",
             "instrument": "BTC/USDT", "quantity": "5", "agent_id": "t1",
             "entries": [{"account": "CASH:USDT", "amount": "49.5", "memo": ""},
                         {"account": "FEES:USDT", "amount": "0.5", "memo": ""},
                         {"account": "POSITION:BTC/USDT", "amount": "-50",
                          "memo": ""},
                         {"account": "PNL:t1", "amount": "-0.5", "memo": ""}]},
            {"kind": "fill_buy", "at": "2026-10-14T12:00:00+00:00",
             "instrument": "ETH/USDT", "quantity": "1", "agent_id": "t2",
             "entries": [{"account": "POSITION:ETH/USDT", "amount": "9",
                          "memo": ""}]},
        ]
        m = marcacoes_de_execucao(txs, "t1")
        assert len(m) == 2
        assert m[0]["tipo"] == "compra" and abs(m[0]["preco"] - 10.0) < 1e-9
        assert m[1]["tipo"] == "venda" and abs(m[1]["preco"] - 10.0) < 1e-9

    def test_abstencoes_e_vetos_por_agente(self):
        ciclos = [
            {"em": "2026-10-14T10:00:00+00:00",
             "abstencoes": ["t1 · BTC/USDT: calmo demais",
                            "t2 · ETH/USDT: spread"],
             "vetos": ["t1: risco alto", "t3: sem regime"]},
            {"em": "2026-10-14T11:00:00+00:00",
             "abstencoes": [], "vetos": ["t1: sem sinal"]},
        ]
        m = marcacoes_dos_ciclos(ciclos, "t1")
        tipos = [x["tipo"] for x in m]
        assert tipos.count("abstencao") == 1 and tipos.count("veto") == 2
        assert m[0]["instrumento"] == "BTC/USDT"
        assert m[0]["motivo"] == "calmo demais"
        assert all(x["t"] for x in m)


class TestExperimentosEExtras:
    def test_experimentos_do_trader_com_veredito(self):
        registros = {
            "a1": {"id": "exp-1", "titulo": "MM curta vs longa", "trader": "t1",
                   "familia": "t1", "criado_em": "2026-10-13T10:00:00+00:00",
                   "champion": {"descricao": "MM9"}, "challenger":
                   {"descricao": "MM21"},
                   "assinatura_champion": "c", "assinatura_challenger": "l"},
            "a2": {"id": "exp-2", "titulo": "outro", "trader": "t2",
                   "familia": "t2", "criado_em": "x", "champion": {},
                   "challenger": {}, "assinatura_champion": "x",
                   "assinatura_challenger": "y"}}
        resultados = {"a1": {"aprovado": True, "motivo": "p=0.01",
                             "executada_em": "2026-10-13T11:00:00+00:00"}}
        e = experimentos_do_trader(registros, resultados, "t1")
        assert len(e) == 1
        assert e[0]["id"] == "exp-1" and e[0]["titulo"] == "MM curta vs longa"
        assert e[0]["resultado"]["aprovado"] is True

    def test_experimento_sem_veredito_e_declarado(self):
        registros = {"a1": {"id": "exp-1", "titulo": "x", "trader": "t1",
                            "familia": "t1", "criado_em": "x",
                            "champion": {}, "challenger": {},
                            "assinatura_champion": "c",
                            "assinatura_challenger": "l"}}
        e = experimentos_do_trader(registros, {}, "t1")
        assert e[0]["resultado"] is None

    def test_exposicao_e_ordens_filtram_por_agente(self):
        lotes = [
            {"agente": "t1", "instrumento": "BTC/USDT", "quantidade": "2",
             "custo_restante": "20"},
            {"agente": "t2", "instrumento": "ETH/USDT", "quantidade": "1",
             "custo_restante": "5"}]
        exp = exposicao_do_trader(lotes, "t1")
        assert exp["custo_total"] == "20"
        assert len(exp["posicoes"]) == 1
        ordens = [{"id": "o1", "agente": "t1", "aberta": True},
                  {"id": "o2", "agente": "t2", "aberta": True},
                  {"id": "o3", "agente": "t1", "aberta": False}]
        abertas = ordens_do_trader(ordens, "t1")
        assert [o["id"] for o in abertas] == ["o1"]


class TestVelas:
    def test_velas_descartam_a_formacao_e_convertem_para_iso(self):
        t0 = datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)
        ms = int(t0.timestamp() * 1000)
        rows = [
            [ms, 1, 2, 0.5, 1.5, 10],
            [ms + 3600_000, 1.5, 2.5, 1, 2, 20],       # fecha 10:00 → ok
            [ms + 7200_000, 2, 3, 1.9, 2.8, 5],        # fecha 11:00 → ok
            [ms + 10800_000, 2.8, 4, 2.5, 3.5, 1],     # fecha 12:00 → formação
        ]
        agora = datetime(2026, 10, 14, 11, 30, tzinfo=timezone.utc)
        velas = velas_de_rows(rows, "1h", agora=agora)
        assert len(velas) == 3
        assert velas[0]["t"] == "2026-10-14T09:00:00+00:00"
        assert velas[-1]["c"] == "2.8"
        assert velas[0]["v"] == "10"


class TestRetratoUnico:
    def _retrato(self, **over):
        base = {
            "agent_id": "t1",
            "trader": {"id": "t1", "strategy": "momento", "version": "1",
                       "stage": "shadow",
                       "stage_label": "experimental (paper)",
                       "tier": "experimental", "universe": ["BTC/USDT"],
                       "timeframe": "1h", "regimes": ["tendencia"]},
            "instrumento": "BTC/USDT", "timeframe": "1h",
            "velas": None,
            "fechamentos": ["100", "101", "102", "101", "99"],
            "fonte": "fixture",
            "aviso_fonte": "dados de FIXTURE — provam a mesa, não o mercado",
            "qualidade": "fixture",
            "diario": [
                {"quando": "2026-10-14T10:00:00+00:00",
                 "retorno_liquido": "0.10", "custo": "0.5",
                 "instrumento": "BTC/USDT", "causa": "alvo",
                 "regime": "tendencia", "versao": "1"},
                {"quando": "2026-10-14T11:00:00+00:00",
                 "retorno_liquido": "-0.05", "custo": "0.4",
                 "instrumento": "BTC/USDT", "causa": "stop",
                 "regime": "tendencia", "versao": "1"}],
            "transacoes": [{"kind": "fill_buy",
                            "at": "2026-10-14T10:30:00+00:00",
                            "instrument": "BTC/USDT", "quantity": "1",
                            "agent_id": "t1",
                            "entries": [{"account": "POSITION:BTC/USDT",
                                         "amount": "100", "memo": ""}]}],
            "lotes": [{"agente": "t1", "instrumento": "BTC/USDT",
                       "quantidade": "1", "custo_restante": "100"}],
            "ordens": [],
            "ciclos": [{"em": "2026-10-14T10:45:00+00:00",
                        "abstencoes": ["t1 · BTC/USDT: calmo demais"],
                        "vetos": ["t1: risco de sessão"]}],
            "experimentos": [],
            "atribuicao": {"t1": "0.5"},
            "moeda": "USDT", "modo": "paper"}
        base.update(over)
        return retrato_do_trader(**base)

    def test_estrutura_completa_e_segregacao_declarada(self):
        r = self._retrato()
        assert r["trader"]["id"] == "t1"
        assert r["modo"] == "paper" and r["moeda"] == "USDT"
        for chave in ("mercado", "portfolio", "champion_challenger", "avisos",
                      "trader"):
            assert chave in r

    def test_mercado_linha_no_fixture_com_aviso_e_indicadores(self):
        m = self._retrato()["mercado"]
        assert m["tipo"] == "linha"
        assert m["fonte"] == "fixture"
        assert "FIXTURE" in m["aviso"]
        assert set(m["indicadores"]) == {"sma_rapida", "sma_lenta", "rsi"}
        assert m["fechamentos"] == ["100", "101", "102", "101", "99"]
        assert "suportes" in m and "resistencias" in m

    def test_mercado_candles_quando_ha_ohlcv(self):
        velas = [{"t": f"2026-10-14T{i:02d}:00:00+00:00", "o": "1", "h": "2",
                  "l": "0.5", "c": str(1 + i * 0.1), "v": "10"}
                 for i in range(24)]
        m = self._retrato(velas=velas, fechamentos=None, fonte="real",
                          aviso_fonte="mercado real (leitura pública)",
                          qualidade="ok")["mercado"]
        assert m["tipo"] == "candles"
        assert len(m["velas"]) == 24
        assert m["velas"][0]["o"] == "1" and m["velas"][-1]["v"] == "10"

    def test_marcacoes_unem_execucao_e_ciclos_em_ordem_temporal(self):
        m = self._retrato()["mercado"]["marcacoes"]
        tipos = [x["tipo"] for x in m]
        assert "compra" in tipos and "abstencao" in tipos and "veto" in tipos
        ts = [x["t"] for x in m]
        assert ts == sorted(ts)

    def test_portfolio_deriva_de_diario_com_custos_e_livro(self):
        p = self._retrato()["portfolio"]
        assert p["operacoes"] == 2
        assert p["custos_total"] == "0.9"
        assert p["resultado_bruto_livro"] == "0.5"
        # líquido estimado = bruto do livro − custos do diário
        assert p["resultado_liquido_estimado"] == "-0.4"
        assert p["exposicao"]["custo_total"] == "100"
        assert p["curva"] and p["drawdown"] is not None

    def test_champion_challenger_vem_dos_registros(self):
        r = self._retrato(experimentos=[
            {"id": "exp-1", "titulo": "MM curta vs longa", "criado_em": "x",
             "champion": {"descricao": "MM9"},
             "challenger": {"descricao": "MM21"},
             "resultado": {"aprovado": False, "motivo": "p alto"}}])
        cc = r["champion_challenger"]
        assert cc[0]["id"] == "exp-1"
        assert cc[0]["resultado"]["aprovado"] is False

    def test_avisos_de_honestidade_sempre_presentes(self):
        r = self._retrato()
        texto = " ".join(r["avisos"])
        assert "PAPER" in texto and "simulad" in texto
        assert "promessa de lucro" in texto


class TestModuloPuro:
    def test_modulo_nao_tem_caminho_de_ordem_nem_rede(self):
        src = MODULO.read_text(encoding="utf-8")
        assert "create_order" not in src
        assert "ccxt" not in src
        assert "import requests" not in src
        assert "from .execution" not in src


# ------------------------------------------------------------------ HTTP


def _load_app(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import service.app as m
    importlib.reload(m)
    return m


def _client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    m = _load_app(tmp_path, monkeypatch)
    m._FAILS.clear()
    m._DESKS.clear()
    return TestClient(m.app), m


class TestRotaHttp:
    def test_rota_registrada_e_exige_auth(self, tmp_path, monkeypatch):
        c, m = _client(tmp_path, monkeypatch)
        paths = {r.path for r in m.app.routes if getattr(r, "path", None)}
        assert "/v1/desk/traders/{agent_id}/sala" in paths
        r = c.get("/v1/desk/traders/t1/sala")
        assert r.status_code == 401

    def test_agente_desconhecido_404(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        r = c.get("/v1/desk/traders/nao-existe/sala", headers=H)
        assert r.status_code == 404

    def test_timeframe_e_instrumento_invalidos_422(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        d = c.get("/v1/desk/status", headers=H).json()
        aid = d["agents"][0]["id"]
        r = c.get(f"/v1/desk/traders/{aid}/sala?timeframe=7h", headers=H)
        assert r.status_code == 422
        r = c.get(f"/v1/desk/traders/{aid}/sala?instrumento=FAKE/USDT",
                  headers=H)
        assert r.status_code == 422

    def test_sala_fixture_responde_honesta(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        d = c.get("/v1/desk/status", headers=H).json()
        aid = d["agents"][0]["id"]
        r = c.get(f"/v1/desk/traders/{aid}/sala", headers=H)
        assert r.status_code == 200, r.text
        sala = r.json()
        assert sala["trader"]["id"] == aid
        assert sala["modo"] == "paper" and sala["moeda"]
        m = sala["mercado"]
        assert m["fonte"] == "fixture" and m["tipo"] == "linha"
        assert "FIXTURE" in m["aviso"]
        assert sala["portfolio"]["operacoes"] == 0
        assert sala["portfolio"]["curva"] == []
        assert "sem operações fechadas" in \
            sala["portfolio"]["motivo_profit_factor"]
        assert isinstance(sala["champion_challenger"], list)
        # instrumento default é do universo do trader
        assert m["instrumento"] in d["agents"][0]["universe"]


# -------------------------------------------------------------------- DOM


class TestSalaNoDOM:
    def _html(self):
        return HTML.read_text(encoding="utf-8")

    def test_card_da_sala_na_pagina_mesa(self):
        html = self._html()
        assert re.search(r'id="salaTrader"', html)
        assert "Sala individual do trader" in html
        pagina = html[html.find('<section id="page-mesa"'):]
        pagina = pagina[:pagina.find("</section>")]
        assert 'id="salaTrader"' in pagina

    def test_botao_abrir_sala_por_trader(self):
        html = self._html()
        assert re.search(r"function openSala\(", html)
        assert re.search(r"openSala\('", html.replace("${id}", "ID"))
        assert "async function salaTraderLoad" in html

    def test_sala_le_a_rota_com_timeframe(self):
        html = self._html()
        assert re.search(r"/sala\?timeframe=", html)
        assert re.search(r"function svgLinhaMercado\(", html)
        assert re.search(r"function svgVelasMercado\(", html)
        assert re.search(r"function svgPortfolio\(", html)

    def test_rotulos_de_honestidade_visiveis_na_sala(self):
        html = self._html()
        trecho = html[html.find('id="salaTrader"'):]
        assert "PAPER" in trecho
        assert "promessa de lucro" in trecho

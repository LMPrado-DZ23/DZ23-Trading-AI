"""TD-1 — Painel Global do Maestro (consolidação multi-conta, task #90).

Regras verbatim do dono que estes contratos pinam:
- "Não misturar resultados PAPER, TESTNET e LIVE."
- "Não somar patrimônios ou resultados de moedas diferentes sem conversão
  identificada e verificável."
- LIVE permanece BLOQUEADO: o painel lê, nunca libera.

O retrato é uma função PURA (`retrato_global`): nenhum I/O, nenhum caminho
de ordem. A rota só coleta e repassa.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

from tradingagents.desk.painel_global import retrato_global

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "service" / "static" / "index.html"
MODULO = ROOT / "tradingagents" / "desk" / "painel_global.py"

H = {"Authorization": "Bearer s3nh4"}


def _agente(aid, *, stage="shadow", tier="experimental", ops=0, maduro=False,
            acerto=0.0, retorno=0.0):
    return {"id": aid, "strategy": "momento", "version": "1", "stage": stage,
            "stage_label": stage, "tier": tier, "universe": ["BTC/USDT"],
            "timeframe": "1h", "needs": [], "regimes": [],
            "modelo_ia": "desligado (núcleo determinístico)",
            "evidencia": {"operacoes": ops, "maduro": maduro,
                          "taxa_acerto": acerto, "retorno_medio": retorno,
                          "elegivel": False, "motivo": ""},
            "ultimo_no_trade": ""}


def _sala(chave="binance|conta-1|spot|paper", *, modo="paper", moeda="USDT",
          saldo="10", taxas="0.5", pos=None):
    partes = chave.split("|")
    return {"chave": chave, "corretora": partes[0],
            "conta": partes[1] if len(partes) > 1 else "",
            "mercado": partes[2] if len(partes) > 2 else "spot",
            "modo": modo, "moeda": moeda, "saldo_realizado": saldo,
            "taxas_acumuladas": taxas, "posicoes_abertas": pos or {},
            "laboratorio": None, "tem_ledger": True}


def _contabilidade(patrimonio="100", moeda="USDT"):
    return {"saldos": {moeda: patrimonio}, "patrimonio": patrimonio,
            "balanceado": True}


def _retrato(**over):
    base = {"agentes": [], "posicoes": [], "contabilidade": _contabilidade(),
            "salas": [], "ciclo": None, "fonte": None, "alertas": None,
            "maestro": None, "moeda_mesa": "USDT", "modo_mesa": "paper"}
    base.update(over)
    return retrato_global(**base)


class TestSegregacaoModosEMoedas:
    def test_modos_nunca_compartilham_numero(self):
        r = _retrato(salas=[
            _sala("binance|a|spot|paper", modo="paper", saldo="10"),
            _sala("binance|b|spot|testnet", modo="testnet", saldo="5")])
        assert r["patrimonio"]["por_modo"]["paper"]["USDT"][
            "saldo_realizado"] == "10"
        assert r["patrimonio"]["por_modo"]["testnet"]["USDT"][
            "saldo_realizado"] == "5"
        assert r["patrimonio"]["total"] is None
        motivo = r["patrimonio"]["motivo_sem_total"].lower()
        assert "modo" in motivo and "paper/testnet/live" in motivo

    def test_moedas_nunca_somadas_sem_conversao_identificada(self):
        r = _retrato(salas=[
            _sala("binance|a|spot|paper", moeda="USDT", saldo="10"),
            _sala("b3corp|b|acoes|paper", moeda="BRL", saldo="7")])
        modo = r["patrimonio"]["por_modo"]["paper"]
        assert modo["USDT"]["saldo_realizado"] == "10"
        assert modo["BRL"]["saldo_realizado"] == "7"
        assert r["patrimonio"]["total"] is None
        assert "conversão identificada e verificável" in \
            r["patrimonio"]["motivo_sem_total"]
        # cada subtotal é identificado: modo e moeda explícitos na entrada
        assert modo["USDT"]["moeda"] == "USDT" and modo["USDT"]["modo"] == "paper"

    def test_soma_so_com_mesmo_modo_e_mesma_moeda(self):
        r = _retrato(salas=[
            _sala("binance|a|spot|paper", saldo="10"),
            _sala("binance|b|spot|paper", saldo="5")])
        assert r["patrimonio"]["total"] == "115"
        assert r["patrimonio"]["total_moeda"] == "USDT"
        assert r["patrimonio"]["total_modo"] == "paper"
        assert r["patrimonio"]["motivo_sem_total"] == ""
        assert "mesma moeda" in r["patrimonio"]["total_resumo"]
        assert "saldos realizados" in r["patrimonio"]["total_resumo"]

    def test_moeda_nao_declarada_bloqueia_total(self):
        r = _retrato(salas=[_sala("x|a|spot|paper", moeda="", saldo="3")])
        assert r["patrimonio"]["total"] is None
        assert "declarada" in r["patrimonio"]["motivo_sem_total"]

    def test_live_sempre_bloqueado_e_declarado(self):
        r = _retrato(salas=[_sala("binance|a|spot|live", modo="live",
                                  saldo="999")])
        live = r["modos"]["live"]
        assert live["bloqueado"] is True
        assert "proprietário" in live["motivo"]
        # a sala live aparece declarada (leitura), jamais somada
        assert r["patrimonio"]["total"] is None
        assert "live" in r["modos"]["presentes"]

    def test_mesa_sempre_no_retrato_com_modo_e_moeda(self):
        r = _retrato()
        mesa = r["patrimonio"]["mesa"]
        assert mesa["modo"] == "paper" and mesa["moeda"] == "USDT"
        assert mesa["patrimonio"] == "100"
        assert r["patrimonio"]["total"] == "100"


class TestTradersEPosicoes:
    def test_traders_agregados_por_tier_estado_suspensos(self):
        r = _retrato(agentes=[
            _agente("t1", stage="paused", tier="homologado"),
            _agente("t2", stage="shadow", tier="homologado"),
            _agente("t3", stage="shadow")])
        t = r["traders"]
        assert t["total"] == 3
        assert t["por_tier"] == {"homologado": 2, "experimental": 1}
        assert t["por_estado"]["paused"] == 1
        assert t["suspensos"] == ["t1"]
        assert len(t["lista"]) == 3

    def test_posicoes_agregadas_por_agente_e_valor(self):
        pos = [
            {"instrumento": "BTC/USDT", "agente": "t1", "quantidade": "1",
             "preco_entrada": "10"},
            {"instrumento": "ETH/USDT", "agente": "t1", "quantidade": "2",
             "preco_entrada": "5"},
            {"instrumento": "SOL/USDT", "agente": "t2", "quantidade": "3",
             "preco_entrada": "2"}]
        r = _retrato(posicoes=pos)
        p = r["posicoes"]
        assert p["total"] == 3
        assert p["por_agente"] == {"t1": 2, "t2": 1}
        assert p["valor_total"] == "26"
        assert p["moeda"] == "USDT" and p["modo"] == "paper"
        assert len(p["lista"]) == 3


class TestOportunidadesEComparacoes:
    def test_oportunidades_do_ciclo_com_motivos_por_trader(self):
        ciclo = {"em": "2026-10-14T12:00:00Z", "origem": "manual",
                 "resumo": "5 avaliadas",
                 "vetos": ["t1: sem regime", "t2: risco alto"],
                 "abstencoes": ["t1 · e1: calmo demais", "t3 · e3: spread"],
                 "descartados": ["d: sem dados"],
                 "ordens": [{"id": "o1"}]}
        r = _retrato(ciclo=ciclo)
        o = r["oportunidades"]
        assert (o["vetos"], o["abstencoes"], o["descartados"],
                o["ordens"]) == (2, 2, 1, 1)
        assert o["por_trader"]["t1"] == {"vetos": 1, "abstencoes": 1,
                                         "ultimo_motivo": "calmo demais"}
        assert o["por_trader"]["t2"]["ultimo_motivo"] == "risco alto"
        assert o["resumo"] == "5 avaliadas"

    def test_comparacoes_so_com_evidencia_madura(self):
        r = _retrato(agentes=[
            _agente("h1", tier="homologado", ops=30, maduro=True,
                    acerto=0.6, retorno=0.01),
            _agente("h2", tier="homologado", ops=2, maduro=False,
                    acerto=0.9, retorno=0.05),
            _agente("x1", ops=1, maduro=False)])
        c = r["comparacoes"]
        h = c["por_tier"]["homologado"]
        assert h["traders"] == 2 and h["maduros"] == 1
        assert h["media_taxa_acerto"] == "0.6"
        assert h["media_retorno_medio"] == "0.01"
        x = c["por_tier"]["experimental"]
        assert x["maduros"] == 0 and x["media_taxa_acerto"] is None
        assert "madura" in x["motivo"]
        assert "madura" in c["aviso"]


class TestExposicaoEAlertas:
    def test_exposicao_por_modo_e_moeda(self):
        pos_mesa = [{"instrumento": "BTC/USDT", "agente": "t1",
                     "quantidade": "2", "preco_entrada": "3"}]
        r = _retrato(
            posicoes=pos_mesa,
            salas=[
                _sala("binance|a|spot|paper", moeda="USDT",
                      pos={"BTC/USDT": {"quantidade": "1",
                                        "preco_entrada": "10"}}),
                _sala("b3corp|b|acoes|paper", moeda="BRL",
                      pos={"PETR4": {"quantidade": "10",
                                     "preco_entrada": "2"}}),
                _sala("binance|c|spot|testnet", modo="testnet", moeda="USDT",
                      pos={"ETH/USDT": {"quantidade": "1",
                                        "preco_entrada": "5"}})])
        e = r["exposicao"]
        assert e["mesa"]["valor"] == "6" and e["mesa"]["posicoes"] == 1
        assert e["por_modo"]["paper"] == {"USDT": "16", "BRL": "20"}
        assert e["por_modo"]["testnet"] == {"USDT": "5"}
        assert e["mistura_modos"] is True and e["mistura_moedas"] is True
        assert e["aviso"]

    def test_alertas_e_fonte_passthrough(self):
        alertas = [{"codigo": "EDGE", "severidade": "alta", "titulo": "x",
                    "detalhe": "y", "acao": "z"}]
        fonte = {"dados": "fixture", "corretora": "paper",
                 "aviso": "dados de FIXTURE — provam a mesa, não o mercado"}
        r = _retrato(alertas=alertas, fonte=fonte)
        assert r["alertas"]["total"] == 1
        assert r["alertas"]["ativos"][0]["codigo"] == "EDGE"
        assert r["fonte"]["dados"] == "fixture"
        assert "FIXTURE" in r["fonte"]["aviso"]
        assert r["modos"]["live"]["bloqueado"] is True

    def test_maestro_passthrough_sem_conversao(self):
        maestro = {"pausa_global": {"ativo": False}, "exposicao":
                   {"por_sala": {}, "total": "0", "teto": None,
                    "excedido": False}, "prioridades": {}, "conflitos": [],
                   "aviso": "o Maestro Central supervisiona e bloqueia"}
        r = _retrato(maestro=maestro)
        assert r["maestro"]["pausa_global"]["ativo"] is False
        assert r["maestro"]["aviso"].startswith("o Maestro Central")
        assert "figura do Maestro" in r["exposicao"]["maestro_aviso"] or \
            r["exposicao"]["maestro"] is None


class TestModuloPuro:
    def test_modulo_nao_tem_caminho_de_ordem(self):
        src = MODULO.read_text(encoding="utf-8")
        assert "create_order" not in src
        assert "ccxt" not in src
        assert "import requests" not in src


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
        assert "/v1/maestro/painel" in paths
        assert c.get("/v1/maestro/painel").status_code == 401

    def test_painel_http_agrega_salas_abertas(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        for conta, modo in (("pa", "paper"), ("tb", "testnet")):
            r = c.post("/v1/escola/salas", headers=H,
                       json={"corretora": "binance", "conta": conta,
                             "mercado": "spot", "modo": modo})
            assert r.status_code in (200, 201), r.text
        r = c.get("/v1/maestro/painel", headers=H)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["titulo"] == "Painel Global do Maestro"
        chaves = {s["chave"] for s in d["patrimonio"]["contas"]}
        assert "binance|pa|spot|paper" in chaves
        assert "binance|tb|spot|testnet" in chaves
        # dois modos presentes ⇒ nenhum total; LIVE bloqueado sempre
        assert d["patrimonio"]["total"] is None
        assert d["modos"]["live"]["bloqueado"] is True
        assert d["traders"]["total"] >= 1
        assert "PAPER, TESTNET e LIVE" in d["modos"]["regra"]

    def test_painel_http_sem_desk_ainda_responde(self, tmp_path, monkeypatch):
        # parada/recusa da mesa não pode cegar a supervisão global
        c, _ = _client(tmp_path, monkeypatch)
        r = c.get("/v1/maestro/painel", headers=H)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "patrimonio" in d and d["modos"]["live"]["bloqueado"] is True


class TestPainelNoDOM:
    def _html(self):
        return HTML.read_text(encoding="utf-8")

    def test_secao_painel_global_no_painel_mesa(self):
        html = self._html()
        assert "Painel Global do Maestro" in html
        assert re.search(r'id="painelGlobal"', html)
        pagina = html[html.find('<section id="page-mesa"'):]
        pagina = pagina[:pagina.find("</section>")]
        assert 'id="painelGlobal"' in pagina, (
            "o Painel Global precisa viver na página Mesa")

    def test_funcao_e_chamada_no_mesa(self):
        html = self._html()
        assert "async function painelGlobalLoad" in html
        m = re.search(r"if\(id==='mesa'\)\{?deskLoad\(\);?painelGlobalLoad\(\)", html)
        assert m, "showPage('mesa') precisa chamar deskLoad + painelGlobalLoad"

    def test_rotulos_de_segregacao_visiveis(self):
        html = self._html()
        trecho = html[html.find('id="painelGlobal"'):]
        trecho = trecho[:trecho.find("deskOut")]
        assert "PAPER, TESTNET e LIVE" in trecho
        assert "conversão identificada e verificável" in trecho or \
            "moedas diferentes" in trecho

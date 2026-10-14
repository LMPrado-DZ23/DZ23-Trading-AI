"""TD-4 — Dashboard do Copiloto Manual: sinais + "Acompanhar esta operação"
(task #93).

Regras do dono que estes contratos pinam:
- "O Copiloto Manual jamais poderá criar ou cancelar ordens na corretora."
  (a análise é read-only; o módulo é puro e a rota não boota mesa);
- a análise apresenta oportunidade, riscos, custos, horizonte e condições
  de invalidação a partir de séries IDENTIFICADAS e verificáveis — fonte
  sem dado vira motivo declarado, nunca número inventado;
- "Não prometer previsão exata de preços nem lucros" — os avisos fixos
  ficam impressos na própria resposta e no painel;
- o botão "Acompanhar esta operação" fica visível e funcional no local
  apropriado (registro do que o dono JÁ executou), com custos/taxas.
"""
from __future__ import annotations

import ast
import importlib
import math
import re
from pathlib import Path

from tradingagents.desk.copiloto_sinais import analisar_mercado, horizonte_do_timeframe

ROOT = Path(__file__).resolve().parents[1]
MODULO = ROOT / "tradingagents" / "desk" / "copiloto_sinais.py"
APP = ROOT / "service" / "app.py"
HTML = ROOT / "service" / "static" / "index.html"

H = {"Authorization": "Bearer s3nh4"}


def _onda(slope=0.5, amp=2.0, periodo=6, base=100.0, n=30):
    """Série determinística com tendência + oscilação (gera pivôs)."""
    return [round(base + slope * i + amp * math.sin(2 * math.pi * i / periodo), 2)
            for i in range(n)]


# ------------------------------------------------------------ horizonte


class TestHorizonte:
    def test_catalogo_por_timeframe(self):
        assert "hora" in horizonte_do_timeframe("1h")
        assert "dia" in horizonte_do_timeframe("1d")
        assert "minuto" in horizonte_do_timeframe("1m")

    def test_timeframe_fora_do_catalogo_e_declarado(self):
        txt = horizonte_do_timeframe("42x")
        assert "42x" in txt and "não catalogado" in txt


# -------------------------------------------------------------- análise


class TestAnaliseUnidade:
    def _analise(self, **over):
        base = {"instrumento": "BTC/USDT", "timeframe": "1h",
                "fechamentos": _onda(), "fonte": "real", "qualidade": "ok",
                "aviso_fonte": "mercado real (leitura pública)",
                "preco_atual": _onda()[-1]}
        base.update(over)
        return analisar_mercado(**base)

    def test_serie_curta_declara_motivo_e_nao_inventa_numero(self):
        a = self._analise(fechamentos=[100 + i for i in range(10)])
        t, rs = a["tendencia"], a["rsi"]
        assert t["estado"] is None and t["sma_rapida"] is None
        assert "21" in t["motivo"] and "10" in t["motivo"]
        assert rs["valor"] is None and "15" in rs["motivo"]
        assert a["oportunidade"]["vies"] is None
        assert a["oportunidade"]["motivo"]
        assert a["amostra"] == 10

    def test_tendencia_de_alta_com_pullbacks_da_vies_comprador(self):
        s = _onda()
        a = self._analise(fechamentos=s)
        t, rs, op = a["tendencia"], a["rsi"], a["oportunidade"]
        assert t["estado"] == "alta" and t["motivo"] is None
        assert t["sma_rapida"] > t["sma_lenta"]
        assert 30 < rs["valor"] < 70 and rs["estado"] == "neutro"
        assert op["vies"] == "compra" and "alta" in op["texto"]

    def test_tendencia_de_baixa_espelhada_da_vies_vendedor(self):
        s = [round(200 - x, 2) for x in _onda()]
        a = self._analise(fechamentos=s, preco_atual=s[-1])
        assert a["tendencia"]["estado"] == "baixa"
        assert a["tendencia"]["sma_rapida"] < a["tendencia"]["sma_lenta"]
        assert 30 < a["rsi"]["valor"] < 70
        assert a["oportunidade"]["vies"] == "venda"

    def test_rsi_sobrecomprado_bloqueia_o_vies_com_motivo(self):
        a = self._analise(fechamentos=[100 + i for i in range(24)])
        assert a["rsi"]["estado"] == "sobrecomprado"
        assert a["oportunidade"]["vies"] is None
        assert "sobrecomprado" in a["oportunidade"]["motivo"]
        assert "aguarde" in a["oportunidade"]["motivo"]

    def test_rsi_sobrevendido_bloqueia_o_vies_com_motivo(self):
        a = self._analise(fechamentos=[200 - i for i in range(24)],
                          preco_atual=177)
        assert a["rsi"]["estado"] == "sobrevendido"
        assert a["oportunidade"]["vies"] is None
        assert "sobrevendido" in a["oportunidade"]["motivo"]

    def test_invalidacao_de_compra_usa_o_suporte_mais_proximo(self):
        s = _onda()
        a = self._analise(fechamentos=s)
        assert a["oportunidade"]["vies"] == "compra"
        texto = a["invalidacao"]["texto"]
        assert texto and "suporte" in texto and "109.27" in texto
        assert a["invalidacao"]["motivo"] is None

    def test_invalidacao_de_venda_usa_a_resistencia_mais_proxima(self):
        s = [round(200 - x, 2) for x in _onda()]
        a = self._analise(fechamentos=s, preco_atual=s[-1])
        assert a["oportunidade"]["vies"] == "venda"
        texto = a["invalidacao"]["texto"]
        assert texto and "resistência" in texto and "90.73" in texto

    def test_sem_pivos_declara_motivo_em_vez_de_nivel(self):
        a = self._analise(fechamentos=[100 + i for i in range(24)])
        assert a["invalidacao"]["texto"] is None
        assert a["invalidacao"]["motivo"]

    def test_fonte_fixture_entra_nos_riscos_com_rotulo(self):
        a = self._analise(fonte="fixture", qualidade="fixture",
                          aviso_fonte="dados de FIXTURE (rotulados)")
        riscos = " · ".join(a["riscos"])
        assert "fixture" in riscos and "rotulada" in riscos
        assert a["aviso_fonte"].startswith("dados de FIXTURE")

    def test_fonte_real_nao_ganha_risco_de_fixture(self):
        a = self._analise()
        assert "rotulada" not in " · ".join(a["riscos"])

    def test_custos_nunca_sao_estimados_pelo_copiloto(self):
        a = self._analise()
        nota = a["custos"]["nota"]
        assert "corretora" in nota and "NUNCA" in nota and "registre" in nota

    def test_avisos_fixos_de_honestidade_sempre_presentes(self):
        for a in (self._analise(), self._analise(fechamentos=[])):
            avisos = " · ".join(a["avisos"])
            assert "não é promessa de lucro nem previsão exata de preços" in avisos
            assert "não envia ordens" in avisos and "corretora" in avisos
            assert "executado por você" in avisos

    def test_riscos_da_amostra_e_do_stop(self):
        a = self._analise()
        riscos = " · ".join(a["riscos"])
        assert "volatilidade da amostra" in riscos
        assert "declare stop e invalidação no registro" in riscos

    def test_horizonte_e_rotulos_da_fonte_passthrough(self):
        a = self._analise(timeframe="1d")
        assert "dia" in a["horizonte"]
        assert a["fonte"] == "real" and a["qualidade"] == "ok"
        assert a["instrumento"] == "BTC/USDT" and a["timeframe"] == "1d"

    def test_determinismo_mesma_entrada_mesma_saida(self):
        assert self._analise() == self._analise()

    def test_serie_vazia_nao_explode_e_declara_motivos(self):
        a = self._analise(fechamentos=[], preco_atual=None)
        assert a["amostra"] == 0
        assert a["tendencia"]["estado"] is None
        assert a["rsi"]["valor"] is None
        assert a["niveis"]["suportes"] == [] and a["niveis"]["resistencias"] == []


# ------------------------------------------------------------ módulo puro


class TestModuloPuro:
    _PROIBIDOS = {"create_order", "cancel_order", "close_position",
                  "executar_ordem", "enviar_ordem", "submit"}

    def test_modulo_sem_caminho_de_ordem_nem_rede(self):
        src = MODULO.read_text(encoding="utf-8")
        assert "create_order" not in src and "cancel_order" not in src
        assert "ccxt" not in src and "import requests" not in src
        assert "from .execution" not in src and "subprocess" not in src
        assert "socket" not in src

    def test_rota_manual_sinais_nao_chama_execucao(self):
        arvore = ast.parse(APP.read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            if not (isinstance(no, ast.FunctionDef)
                    and no.name == "manual_sinais"):
                continue
            for filho in ast.walk(no):
                if isinstance(filho, ast.Call):
                    nome = ""
                    if isinstance(filho.func, ast.Attribute):
                        nome = filho.func.attr
                    elif isinstance(filho.func, ast.Name):
                        nome = filho.func.id
                    assert nome not in self._PROIBIDOS, nome
            return
        raise AssertionError("rota manual_sinais não encontrada em app.py")


# ----------------------------------------------------------------- HTTP


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


class TestRotaSinaisHttp:
    def test_rota_registrada_e_exige_auth(self, tmp_path, monkeypatch):
        c, m = _client(tmp_path, monkeypatch)
        paths = {r.path for r in m.app.routes if getattr(r, "path", None)}
        assert "/v1/manual/sinais" in paths
        r = c.get("/v1/manual/sinais?instrumento=BTC/USDT")
        assert r.status_code == 401

    def test_timeframe_invalido_422(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        r = c.get("/v1/manual/sinais?instrumento=BTC/USDT&timeframe=7h",
                  headers=H)
        assert r.status_code == 422

    def test_sem_instrumento_422(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        r = c.get("/v1/manual/sinais", headers=H)
        assert r.status_code == 422

    def test_sinais_nao_boota_a_mesa(self, tmp_path, monkeypatch):
        """Como o restante do copiloto: read-only e independente — a análise
        não constrói broker/execução para funcionar."""
        c, m = _client(tmp_path, monkeypatch)
        r = c.get("/v1/manual/sinais?instrumento=BTC/USDT&timeframe=1h",
                  headers=H)
        assert r.status_code == 200, r.text
        assert m._DESKS == {}
        a = r.json()
        assert a["fonte"] == "fixture"
        assert a["tendencia"]["estado"] is None
        assert "0 de 21" in a["tendencia"]["motivo"]

    def test_sinais_fixture_rotulados_e_honestos(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        d = c.get("/v1/desk/status", headers=H).json()
        instr = d["agents"][0]["universe"][0]
        r = c.get("/v1/manual/sinais?instrumento=" + instr + "&timeframe=1h",
                  headers=H)
        assert r.status_code == 200, r.text
        a = r.json()
        assert a["instrumento"] == instr and a["timeframe"] == "1h"
        assert a["fonte"] == "fixture" and "FIXTURE" in a["aviso_fonte"]
        assert "fixture" in " · ".join(a["riscos"])
        assert a["amostra"] > 0
        assert "não é promessa de lucro nem previsão exata de preços" in \
            " · ".join(a["avisos"])
        assert "hora" in a["horizonte"]


# -------------------------------------------------------------------- DOM


class TestSinaisNoDOM:
    def _html(self):
        return HTML.read_text(encoding="utf-8")

    def _pagina_manual(self):
        html = self._html()
        pagina = html[html.find('<section id="page-manual"'):]
        return pagina[:pagina.find("</section>")]

    def test_card_de_analise_na_pagina_manual(self):
        pagina = self._pagina_manual()
        assert re.search(r'id="sinaisCopiloto"', pagina)
        assert "Análise do copiloto" in pagina
        assert re.search(r'id="sigInstr"', pagina)
        assert re.search(r'id="sigTf"', pagina)
        assert re.search(r'id="sinaisOut"', pagina)
        assert 'onclick="manualAnalisar()"' in pagina

    def test_js_analisa_por_series_identificadas(self):
        html = self._html()
        assert re.search(r"function manualAnalisar\(", html)
        assert re.search(r"function manualUsarAnalise\(", html)
        assert "/v1/manual/sinais?instrumento=" in html
        assert "&timeframe=" in html

    def test_js_prefill_do_registro_e_scroll(self):
        html = self._html()
        trecho = html[html.find("function manualUsarAnalise("):]
        trecho = trecho[:trecho.find("\nfunction ")]
        assert "$('manInstr')" in trecho and "$('manDir')" in trecho
        assert "$('manInvalidacao')" in trecho and "$('manTese')" in trecho
        assert "scrollIntoView" in trecho

    def test_botao_exato_acompanhar_esta_operacao(self):
        pagina = self._pagina_manual()
        assert re.search(r'onclick="manualCriar\(\)">Acompanhar esta operação</button>',
                         pagina)
        assert "nunca cria ou cancela ordens" in pagina

    def test_custos_entram_no_registro(self):
        pagina = self._pagina_manual()
        assert re.search(r'id="manCustos"', pagina)
        html = self._html()
        trecho = html[html.find("async function manualCriar("):]
        trecho = trecho[:trecho.find("\nfunction ") if "\nfunction " in trecho
                        else len(trecho)]
        assert "custos_entrada:$('manCustos')" in trecho

    def test_promessas_no_painel(self):
        pagina = self._pagina_manual()
        assert "não é promessa de lucro nem previsão exata" in pagina
        assert "quem opera é você" in pagina

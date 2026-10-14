"""TD-3 — Gráficos profissionais e IA lendo gráficos (task #92).

Regras do dono que estes contratos pinam:
- biblioteca de gráficos vendorizada localmente (Lightweight Charts,
  Apache-2.0) — sem CDN e sem scraping: o painel segue offline/privado;
- gráficos ricos (candles/volume/SMA/linhas de S-R/marcações e curva de
  portfólio) com QUEDA para o SVG existente quando a biblioteca não
  carregar — nunca tela quebrada nem dado inventado;
- a IA lê o gráfico por séries ESTRUTURADAS (fechamentos, indicadores,
  marcações, curva) — não por imagem — com proibições explícitas no
  prompt: nunca inventar dado, nunca prometer lucro, nunca previsão
  exata de preços, nunca sugerir ordem;
- sem provedor de IA configurado, NADA é enviado: a rota responde
  honestamente que a leitura é opcional.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

from tradingagents.desk.sala_trader import prompt_de_leitura, retrato_do_trader

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "service" / "static" / "index.html"
VENDOR = ROOT / "service" / "static" / "vendor" / "lightweight-charts"
LIB_JS = VENDOR / "lightweight-charts.standalone.production.js"
LIB_ROTA = "/static/vendor/lightweight-charts/lightweight-charts.standalone.production.js"

H = {"Authorization": "Bearer s3nh4"}


# ------------------------------------------------------- biblioteca local


class TestBibliotecaVendorizada:
    def test_js_standalone_presente_e_identificado(self):
        assert LIB_JS.exists(), "biblioteca vendorizada ausente"
        src = LIB_JS.read_text(encoding="utf-8", errors="replace")
        assert len(src) > 100_000
        assert "LightweightCharts" in src

    def test_licenca_e_versao_pinadas(self):
        lic = (VENDOR / "LICENSE").read_text(encoding="utf-8")
        assert "Apache License" in lic and "Version 2.0" in lic
        ver = (VENDOR / "VERSION").read_text(encoding="utf-8")
        assert "5.2.1" in ver and "Apache-2.0" in ver

    def test_painel_carrega_a_biblioteca_local_sem_cdn(self):
        html = HTML.read_text(encoding="utf-8")
        assert f'<script src="{LIB_ROTA}"></script>' in html
        # a biblioteca entra ANTES do script inline que usa LightweightCharts
        assert html.find(LIB_ROTA) < html.find("<script>")
        # nada de CDN/scraping: nenhum script externo e nenhum domínio de
        # terceiros no arquivo
        assert re.search(r'<script[^>]+src="https?://', html) is None
        assert "cdn.jsdelivr" not in html and "unpkg.com" not in html
        assert "tradingview.com" not in html


class TestVendorServidoHttp:
    """A homologação viva pegou: o painel referenciava /static/vendor/... mas
    o app só servia arquivos por rotas explícitas — o JS 404ava e a biblioteca
    nunca carregava no navegador real (o fallback SVG mascarava). Este
    contrato pina a rota dedicada, com allowlist como a da marca."""

    def test_rota_registrada(self, tmp_path, monkeypatch):
        c, m = _client(tmp_path, monkeypatch)
        paths = {r.path for r in m.app.routes if getattr(r, "path", None)}
        assert "/static/vendor/lightweight-charts/{arquivo}" in paths

    def test_js_da_biblioteca_e_servido_com_o_conteudo_do_arquivo(self, tmp_path,
                                                                  monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        r = c.get(LIB_ROTA)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("application/javascript")
        assert len(r.content) == LIB_JS.stat().st_size

    def test_licenca_e_version_tambem_servidos(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        for nome in ("LICENSE", "VERSION"):
            r = c.get(f"/static/vendor/lightweight-charts/{nome}")
            assert r.status_code == 200, nome
            assert (VENDOR / nome).stat().st_size == len(r.content)

    def test_nome_fora_da_allowlist_e_404(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        for nome in ("../index.html", "..%2Findex.html", "segredo.js",
                     "package.json"):
            r = c.get(f"/static/vendor/lightweight-charts/{nome}")
            assert r.status_code == 404, nome


# ------------------------------------------------------- gráficos no DOM


class TestGraficosNoPainel:
    def _html(self):
        return HTML.read_text(encoding="utf-8")

    def test_renderizadores_lightweight_presentes(self):
        html = self._html()
        assert re.search(r"function lcGraficoMercado\(", html)
        assert re.search(r"function lcGraficoPortfolio\(", html)
        assert "LightweightCharts.createChart" in html

    def test_api_v5_series_e_marcadores(self):
        html = self._html()
        assert "addSeries(LightweightCharts.CandlestickSeries" in html
        assert "addSeries(LightweightCharts.AreaSeries" in html
        assert "LightweightCharts.createSeriesMarkers" in html
        # API antiga (v4) fora — foi removida na v5
        assert "addCandlestickSeries" not in html
        assert "addHistogramSeries" not in html

    def test_fallback_svg_preservado_e_escolhido_em_runtime(self):
        html = self._html()
        assert re.search(r"function svgLinhaMercado\(", html)
        assert re.search(r"function svgVelasMercado\(", html)
        assert re.search(r"function svgPortfolio\(", html)
        assert re.search(r"function graficoMercadoEscolha\(", html)
        assert re.search(r"function graficoPortfolioEscolha\(", html)
        assert "if(window.LightweightCharts" in html
        # a sala usa os escolhedores (LC com fallback), não o SVG direto
        assert "graficoMercadoEscolha(m,mq)" in html
        assert "graficoPortfolioEscolha(p.curva" in html

    def test_rotulos_de_honestidade_nos_dois_caminhos(self):
        html = self._html()
        # os avisos do caminho SVG continuam existindo (fallback honesto)
        assert "se o navegador não carregar a biblioteca" in html or \
            "linha dos fechamentos da fonte" in html
        assert "a vela em formação é descartada" in html
        assert "não é saldo de conta" in html


# --------------------------------------------- IA lê séries estruturadas


def _retrato_fixture(**over):
    base = {
        "agent_id": "t1",
        "trader": {"id": "t1", "strategy": "momento", "version": "1"},
        "instrumento": "BTC/USDT", "timeframe": "1h",
        "velas": None,
        "fechamentos": ["100", "101", "102", "101", "99"],
        "fonte": "fixture",
        "aviso_fonte": ("dados de FIXTURE (rotulados) — provam a mesa, não o "
                        "mercado; o gráfico mostra os fechamentos, sem OHLCV "
                        "inventado"),
        "qualidade": "fixture",
        "diario": [], "transacoes": [], "lotes": [], "ordens": [], "ciclos": [],
        "experimentos": [], "atribuicao": {}, "moeda": "USDT", "modo": "paper"}
    base.update(over)
    return retrato_do_trader(**base)


class TestPromptDeLeitura:
    def test_prompt_le_series_estruturadas_com_guardas(self):
        p = prompt_de_leitura(_retrato_fixture())
        assert "SÉRIES ESTRUTURADAS" in p
        assert "não é imagem" in p
        assert "NUNCA" in p
        assert "previsão exata" in p and "prometa lucro" in p
        assert "sugira ordem" in p

    def test_prompt_carrega_os_dados_reais_do_retrato(self):
        p = prompt_de_leitura(_retrato_fixture())
        assert "BTC/USDT" in p and "1h" in p
        assert "100, 101, 102, 101, 99" in p  # fechamentos, na ordem
        assert "SMA rápida" in p and "RSI" in p

    def test_prompt_rotula_fixume_e_metrica_vazia(self):
        p = prompt_de_leitura(_retrato_fixture())
        assert "FIXTURE" in p                       # fonte rotulada
        assert "sem operações fechadas" in p        # motivo do denominador zero

    def test_prompt_leva_marcacoes_do_livro(self):
        r = _retrato_fixture(transacoes=[
            {"kind": "fill_buy", "at": "2026-10-14T10:30:00+00:00",
             "instrument": "BTC/USDT", "quantity": "1", "agent_id": "t1",
             "entries": [{"account": "POSITION:BTC/USDT", "amount": "100",
                          "memo": ""}]}])
        p = prompt_de_leitura(r)
        assert "Marcações" in p and "compra" in p


# ----------------------------------------------------------------- HTTP


def _limpar_ambiente_ia(monkeypatch):
    for k in ("TRADINGAGENTS_LLM_PROVIDER", "TRADINGAGENTS_LLM_FALLBACK",
              "GROQ_API_KEY", "MISTRAL_API_KEY", "OPENROUTER_API_KEY",
              "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY",
              "ANTHROPIC_API_KEY", "OPENAI_COMPATIBLE_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def _client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    _limpar_ambiente_ia(monkeypatch)
    import service.app as m
    importlib.reload(m)
    m._FAILS.clear()
    m._DESKS.clear()
    return TestClient(m.app), m


class TestRotaLeituraHttp:
    def test_rota_registrada_e_exige_auth(self, tmp_path, monkeypatch):
        c, m = _client(tmp_path, monkeypatch)
        paths = {r.path for r in m.app.routes if getattr(r, "path", None)}
        assert "/v1/desk/traders/{agent_id}/sala/leitura" in paths
        r = c.post("/v1/desk/traders/t1/sala/leitura")
        assert r.status_code == 401

    def test_agente_desconhecido_404(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        r = c.post("/v1/desk/traders/nao-existe/sala/leitura", headers=H)
        assert r.status_code == 404

    def test_timeframe_invalido_422(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        d = c.get("/v1/desk/status", headers=H).json()
        aid = d["agents"][0]["id"]
        r = c.post(f"/v1/desk/traders/{aid}/sala/leitura?timeframe=7h",
                   headers=H)
        assert r.status_code == 422

    def test_sem_provedor_responde_honesto_sem_enviar_nada(self, tmp_path,
                                                          monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        d = c.get("/v1/desk/status", headers=H).json()
        aid = d["agents"][0]["id"]
        r = c.post(f"/v1/desk/traders/{aid}/sala/leitura", headers=H)
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["ok"] is False
        assert "provedor" in corpo["motivo"]
        assert "nenhum" not in corpo.get("leitura", "")


# -------------------------------------------------------------------- DOM


class TestLeituraNoDOM:
    def _html(self):
        return HTML.read_text(encoding="utf-8")

    def test_botao_pede_leitura_por_series_estruturadas(self):
        html = self._html()
        assert "Pedir leitura à IA" in html
        assert re.search(r"function salaLeituraIA\(", html)
        assert re.search(r"/sala/leitura\?timeframe=", html)
        assert "séries estruturadas" in html

    def test_aviso_de_nao_previsao_junto_do_botao(self):
        html = self._html()
        trecho = html[html.find('id="salaTrader"'):]
        assert "promessa de lucro" in trecho
        assert re.search(r"não é promessa de lucro nem previsão exata", html)

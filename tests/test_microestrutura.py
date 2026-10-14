"""MO-2-A — Microestrutura read-only (livro L2): análise pura + leitor do feed.

Prova o que a missão exige: adaptador de LEITURA por corretora homologada,
com validade temporal e testes de dados ausentes ou inconsistentes —
nunca número inventado, nunca caminho de ordem.

Regras codificadas testadas aqui:
- livro válido ⇒ bid/ask/spread bps/profundidade/desequilíbrio exatos;
- ausente/vazio/cruzado/lado vazio/nível não-positivo ⇒ estado honesto SEM números;
- validade temporal: leitura mais velha que `max_idade_s` ⇒ `vencida`
  (números da última leitura ficam, marcados; nada de fingir atualidade);
- corretora sem `fetchOrderBook` nativo ⇒ `nao_suportado` sem tocar a rede;
- falha de rede ⇒ `indisponivel`; com leitura anterior ⇒ `vencida`;
- slippage estimada POR LIVRO (caminhada de níveis); profundidade
  insuficiente ⇒ `None` (não se inventa preço);
- nenhum token de ordem no módulo (pin estrutural).
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from tradingagents.desk.market import PublicMarketFeed
from tradingagents.desk.microestrutura import (
    LeituraDeLivro,
    analisar_livro,
    slipagem_estimada,
)

AGORA = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)
H = {"Authorization": "Bearer s3nh4"}
RAIZ = Path(__file__).resolve().parents[1]


def _livro(bids, asks):
    return {"bids": [[str(p), str(q)] for p, q in bids],
            "asks": [[str(p), str(q)] for p, q in asks]}


class FakeExchange:
    """Corretora fake: só o que o leitor usa — fetch_order_book e `has`."""

    def __init__(self, bruto=None, erro=None, suporta=True):
        self.has = {"fetchOrderBook": suporta}
        self.bruto = bruto
        self.erro = erro
        self.chamadas = 0

    def fetch_order_book(self, instrumento, limit=None):
        self.chamadas += 1
        if self.erro:
            raise self.erro
        return self.bruto


def _feed(cliente, **kw):
    return PublicMarketFeed("binance", client=cliente, **kw)


class TestAnaliseDoLivro:
    def test_livro_valido_calcula_metricas_exatas(self):
        leitura = analisar_livro(
            "binance", "BTC/USDT",
            _livro([[100, 2], [99, 3]], [[101, 1], [102, 4]]),
            agora=AGORA)
        assert leitura.estado == "disponivel"
        assert leitura.bid == Decimal("100")
        assert leitura.ask == Decimal("101")
        # spread = 1 / mid 100.5 * 10000 = 99.5025 bps (4 casas)
        assert leitura.spread_bps == Decimal("99.5025")
        assert leitura.profundidade_bid == Decimal("5")
        assert leitura.profundidade_ask == Decimal("5")
        assert leitura.imbalance == Decimal("0")
        assert leitura.niveis_usados == 2
        assert leitura.obtido_em == AGORA.isoformat()
        assert leitura.idade_s == 0

    def test_desequilibrio_assimetrico(self):
        leitura = analisar_livro(
            "binance", "BTC/USDT",
            _livro([[100, 7], [99, 3]], [[101, 1]]),
            agora=AGORA)
        # (10 - 1) / (10 + 1) = 0.8182
        assert leitura.estado == "disponivel"
        assert leitura.imbalance == Decimal("0.8182")

    def test_livro_ausente_indisponivel_sem_numeros(self):
        leitura = analisar_livro("binance", "BTC/USDT", None, agora=AGORA)
        assert leitura.estado == "indisponivel"
        assert leitura.bid is None and leitura.ask is None
        assert leitura.spread_bps is None and leitura.imbalance is None
        assert leitura.motivo

    def test_livro_cruzado_inconsistente(self):
        leitura = analisar_livro(
            "binance", "BTC/USDT", _livro([[102, 1]], [[101, 1]]),
            agora=AGORA)
        assert leitura.estado == "inconsistente"
        assert "cruzado" in leitura.motivo
        assert leitura.bid is None and leitura.ask is None

    def test_lado_vazio_inconsistente(self):
        leitura = analisar_livro(
            "binance", "BTC/USDT", _livro([], [[101, 1]]), agora=AGORA)
        assert leitura.estado == "inconsistente"
        assert leitura.imbalance is None

    def test_nivel_nao_positivo_inconsistente(self):
        for malformado in (_livro([[0, 1]], [[101, 1]]),
                           _livro([[100, 0]], [[101, 1]]),
                           {"bids": [["x", "1"]], "asks": [[101, 1]]}):
            leitura = analisar_livro("binance", "BTC/USDT", malformado,
                                     agora=AGORA)
            assert leitura.estado == "inconsistente", malformado
            assert leitura.bid is None

    def test_validade_temporal_vencida_mantem_ultima_leitura(self):
        leitura = analisar_livro(
            "binance", "BTC/USDT",
            _livro([[100, 1]], [[101, 1]]),
            agora=AGORA, max_idade_s=15,
            obtido_em=AGORA - timedelta(seconds=30))
        assert leitura.estado == "vencida"
        assert leitura.idade_s == 30
        assert leitura.bid == Decimal("100")     # última leitura, marcada
        assert "30" in leitura.motivo and "15" in leitura.motivo

    def test_para_dict_json_seguro(self):
        leitura = analisar_livro(
            "binance", "BTC/USDT",
            _livro([[100, 1]], [[101, 1]]), agora=AGORA)
        d = leitura.para_dict()
        assert json.dumps(d)                     # sem Decimal/datetime soltos
        assert d["somente_leitura"] is True
        assert d["bid"] == "100"
        assert d["niveis_bid"] == [["100", "1"]]

    def test_estados_declarados(self):
        from tradingagents.desk import microestrutura as mo
        assert set(mo.ESTADOS) == {"disponivel", "indisponivel",
                                   "inconsistente", "vencida",
                                   "nao_suportado", "nao_aplicavel"}


class TestSlipagemPorLivro:
    def test_compra_caminha_niveis_e_calcula_bps(self):
        leitura = analisar_livro(
            "binance", "BTC/USDT",
            _livro([[99, 5]], [[101, 1], [102, 2]]), agora=AGORA)
        est = slipagem_estimada(leitura, "compra", Decimal("2.5"))
        assert est is not None
        # vwap = (101*1 + 102*1.5) / 2.5 = 101.6; slip = 0.6/101 * 10000
        assert est["preco_medio_estimado"] == "101.6"
        assert est["slipagem_bps"] == "59.4059"
        assert est["niveis_consumidos"] == 2

    def test_venda_caminha_o_lado_dos_bids(self):
        leitura = analisar_livro(
            "binance", "BTC/USDT",
            _livro([[100, 1], [99, 2]], [[101, 5]]), agora=AGORA)
        est = slipagem_estimada(leitura, "venda", Decimal("2.5"))
        assert est is not None
        # vwap = (100*1 + 99*1.5) / 2.5 = 99.4; slip = 0.6/100 * 10000
        assert est["preco_medio_estimado"] == "99.4"
        assert est["slipagem_bps"] == "60"

    def test_profundidade_insuficiente_none(self):
        leitura = analisar_livro(
            "binance", "BTC/USDT",
            _livro([[99, 5]], [[101, 1]]), agora=AGORA)
        assert slipagem_estimada(leitura, "compra", Decimal("5")) is None

    def test_leitura_inconsistente_ou_lado_invalido_none(self):
        ruim = analisar_livro("binance", "BTC/USDT", None, agora=AGORA)
        assert slipagem_estimada(ruim, "compra", Decimal("1")) is None
        boa = analisar_livro("binance", "BTC/USDT",
                             _livro([[99, 5]], [[101, 5]]), agora=AGORA)
        assert slipagem_estimada(boa, "compra", Decimal("0")) is None
        assert slipagem_estimada(boa, "compra", "x") is None
        assert slipagem_estimada(boa, "qualquer", Decimal("1")) is None


class TestLeitorDoFeed:
    def test_corretora_sem_livro_nativo_nao_suportado_sem_rede(self):
        fake = FakeExchange(suporta=False)
        leitura = _feed(fake).leitura_de_livro("BTC/USDT", agora=AGORA)
        assert leitura.estado == "nao_suportado"
        assert fake.chamadas == 0
        assert "fetchOrderBook" in leitura.motivo

    def test_emulado_nao_vale_como_nativo(self):
        fake = FakeExchange(suporta="emulated")
        leitura = _feed(fake).leitura_de_livro("BTC/USDT", agora=AGORA)
        assert leitura.estado == "nao_suportado"
        assert fake.chamadas == 0

    def test_leitura_valida_e_cache_curto(self):
        fake = FakeExchange(bruto=_livro([[100, 1]], [[101, 1]]))
        feed = _feed(fake)
        a = feed.leitura_de_livro("BTC/USDT", agora=AGORA)
        b = feed.leitura_de_livro("BTC/USDT", agora=AGORA)
        assert a.estado == "disponivel" and b.estado == "disponivel"
        assert fake.chamadas == 1               # cache dentro da validade

    def test_falha_de_rede_sem_leitura_anterior_indisponivel(self):
        fake = FakeExchange(erro=RuntimeError("sem rede"))
        leitura = _feed(fake).leitura_de_livro("BTC/USDT", agora=AGORA)
        assert leitura.estado == "indisponivel"
        assert leitura.bid is None
        assert "RuntimeError" in leitura.motivo

    def test_falha_de_rede_com_leitura_anterior_marca_vencida(self):
        fake = FakeExchange(bruto=_livro([[100, 1]], [[101, 1]]))
        feed = _feed(fake, book_cache_seconds=0)
        primeira = feed.leitura_de_livro("BTC/USDT", agora=AGORA)
        assert primeira.estado == "disponivel"
        fake.erro = RuntimeError("caiu")
        ultima = feed.leitura_de_livro(
            "BTC/USDT", agora=AGORA + timedelta(seconds=40))
        assert ultima.estado == "vencida"
        assert ultima.bid == Decimal("100")     # número da ÚLTIMA leitura
        assert "indisponível" in ultima.motivo
        assert ultima.idade_s == 40


class TestSemCaminhoDeOrdem:
    def test_modulo_nao_conhece_ordem(self):
        fonte = (RAIZ / "tradingagents" / "desk" / "microestrutura.py"
                 ).read_text(encoding="utf-8")
        for proibido in ("create_order", "cancel_order", "submit",
                         "withdraw", "transfer"):
            assert proibido not in fonte, proibido


class TestRetratoEContaRota:
    def test_sala_renderiza_livro_l2(self):
        html = (RAIZ / "service" / "static" / "index.html"
                ).read_text(encoding="utf-8")
        assert "Livro (L2)" in html
        assert "d.livro" in html

    def test_retrato_inclui_livro_ou_none_honesto(self):
        from tradingagents.desk.sala_trader import retrato_do_trader
        livro = {"estado": "nao_aplicavel",
                 "motivo": "modo dados de treino (fixture) — sem livro"}
        base = {"trader": {"id": "t1"}, "instrumento": "BTC/USDT",
                "timeframe": "1h", "velas": None, "fechamentos": ["100", "101"],
                "fonte": "fixture", "aviso_fonte": "fixture",
                "qualidade": "ok", "diario": [], "transacoes": [],
                "lotes": [], "ordens": [], "ciclos": [], "experimentos": [],
                "atribuicao": {}, "moeda": "USDT", "modo": "paper"}
        com = retrato_do_trader("t1", livro=livro, **base)
        sem = retrato_do_trader("t1", **base)
        assert com["livro"]["estado"] == "nao_aplicavel"
        assert sem["livro"] is None

    def test_rota_da_sala_em_fixture_declara_livro_nao_aplicavel(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        for k in ("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
                  "GOOGLE_API_KEY", "OPENAI_COMPATIBLE_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        import service.app as m
        importlib.reload(m)
        m._FAILS.clear()
        m._DESKS.clear()
        from fastapi.testclient import TestClient
        c = TestClient(m.app)
        agente = c.get("/v1/desk/status", headers=H).json()["agents"][0]["id"]
        r = c.get(f"/v1/desk/traders/{agente}/sala", headers=H)
        assert r.status_code == 200, r.text
        livro = r.json()["livro"]
        assert livro["estado"] == "nao_aplicavel"
        assert "treino" in livro["motivo"]
        assert livro["somente_leitura"] is True


class TestAnotacaoDoLivroRefletivel:
    """AC-1/A4: o contrato de retorno de `leitura_de_livro` precisa ser
    refletível — a anotação citada resolve no escopo do módulo (o import
    vivia só dentro do método; `get_type_hints` quebrava com NameError)."""

    def test_tipo_de_retorno_do_livro_resolve(self):
        import typing

        hints = typing.get_type_hints(PublicMarketFeed.leitura_de_livro)
        assert hints["return"] is LeituraDeLivro

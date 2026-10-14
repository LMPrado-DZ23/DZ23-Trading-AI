"""MO-2-B — Trilha da decisão na sala (transparência §2) e taxas REAIS
por execução, direto das entries `FEES:` do livro-razão.

A trilha NÃO cria backend paralelo: usa o payload existente da sala e a
rota existente do Painel Global do Maestro (/v1/maestro/painel).
"""
from __future__ import annotations

from pathlib import Path

from tradingagents.desk.sala_trader import marcacoes_de_execucao

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "service" / "static" / "index.html"


def _tx(kind, at, instrument, qtd, entries, agent="t1"):
    return {"kind": kind, "at": at, "instrument": instrument,
            "quantity": qtd, "agent_id": agent, "entries": entries}


class TestTaxasPorExecucao:
    def test_compra_traz_a_taxa_real_da_entry_fees(self):
        tx = _tx("fill_buy", "2026-10-14T10:00:00+00:00", "BTC/USDT", "10", [
            {"account": "POSITION:BTC/USDT", "amount": "100", "memo": ""},
            {"account": "CASH:USDT", "amount": "-101", "memo": ""},
            {"account": "FEES:USDT", "amount": "1", "memo": "corretagem"}])
        m = marcacoes_de_execucao([tx], "t1")
        assert m[0]["taxa"] == "1"
        assert m[0]["moeda_taxa"] == "USDT"
        assert m[0]["nota_taxa"] == ""

    def test_venda_traz_a_taxa_real(self):
        tx = _tx("fill_sell", "2026-10-14T11:00:00+00:00", "BTC/USDT", "5", [
            {"account": "CASH:USDT", "amount": "50", "memo": ""},
            {"account": "FEES:USDT", "amount": "0.5", "memo": ""},
            {"account": "POSITION:BTC/USDT", "amount": "-50", "memo": ""}])
        m = marcacoes_de_execucao([tx], "t1")
        assert m[0]["taxa"] == "0.5" and m[0]["moeda_taxa"] == "USDT"
        assert m[0]["nota_taxa"] == ""

    def test_sem_lancamento_de_taxa_e_ausencia_honesta(self):
        tx = _tx("fill_buy", "2026-10-14T10:00:00+00:00", "BTC/USDT", "1", [
            {"account": "POSITION:BTC/USDT", "amount": "10", "memo": ""}])
        m = marcacoes_de_execucao([tx], "t1")
        assert m[0]["taxa"] is None and m[0]["moeda_taxa"] == ""
        assert "sem lançamento de taxa" in m[0]["nota_taxa"]

    def test_moedas_diferentes_nao_somam(self):
        tx = _tx("fill_buy", "2026-10-14T10:00:00+00:00", "BTC/USDT", "1", [
            {"account": "POSITION:BTC/USDT", "amount": "10", "memo": ""},
            {"account": "FEES:USDT", "amount": "0.1", "memo": ""},
            {"account": "FEES:BNB", "amount": "0.001", "memo": ""}])
        m = marcacoes_de_execucao([tx], "t1")
        assert m[0]["taxa"] is None
        assert "moedas diferentes" in m[0]["nota_taxa"]


class TestTrilhaDaDecisaoNoDOM:
    def _html(self):
        return HTML.read_text(encoding="utf-8")

    def test_bloco_trilha_existe_na_sala(self):
        html = self._html()
        assert "Trilha da decisão" in html
        assert "function salaTrilhaMaestro" in html
        assert 'id="salaTrilhaMaestro"' in html

    def test_trilha_usa_rota_existente_do_maestro_sem_rota_nova(self):
        html = self._html()
        trecho = html[html.find("function salaTraderLoad"):]
        trecho = trecho[:trecho.find("function svgLinhaMercado")]
        assert "salaTrilhaMaestro(" in trecho      # a sala CHAMA a trilha
        inicio = html[html.find("async function salaTrilhaMaestro"):]
        inicio = inicio[:inicio.find("\nfunction ")]
        assert "/v1/maestro/painel" in inicio     # rota EXISTENTE
        assert "por_trader" in inicio

    def test_trilha_cobre_dados_motivo_maestro_risco_custos(self):
        html = self._html()
        trecho = html[html.find("Trilha da decisão"):]
        trecho = trecho[:trecho.find("Portfólio do trader")]
        for passo in ("Dados de entrada", "Motivo de operar ou esperar",
                      "Maestro", "Risco", "Custos"):
            assert passo in trecho, passo

    def test_marcacoes_mostram_taxa_quando_existir(self):
        html = self._html()
        assert "moeda_taxa" in html and "nota_taxa" in html
        assert "taxa por execução" in html

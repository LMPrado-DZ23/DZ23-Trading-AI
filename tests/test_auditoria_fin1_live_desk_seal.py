"""FIN-1 — Auditoria adversarial (achado ChatGPT, commit 4b83e16).

Achado: `build_desk_broker` é a única rota de PRODUÇÃO até `create_order`
(desk/brokers.py:266 → ccxt_bridge.submit_order → create_order). Com a
configuração salva pelo painel (TRADINGAGENTS_DESK_BROKER=ccxt +
TRADINGAGENTS_BROKER_MODE=live + frase + tetos + chaves), a mesa construía
corretora LIVE mesmo com mandato PAPER: faltava a trava de MESA — a última
condição para create_order dependia só de configuração salva.

Correção: a mesa recusa construir corretora LIVE sem autorização explícita
de CÓDIGO (`live_authorized=True`, nunca decidida pelo painel). Os freios
da ponte (frase exata, chaves, teto por ordem, teto diário) seguem valendo
por cima. LIVE continua bloqueado por política (ADR-002/ADR-006).
"""
from __future__ import annotations

import importlib

import pytest

from tradingagents.brokers.ccxt_bridge import BrokerSecurityError
from tradingagents.desk.brokers import build_desk_broker

H = {"Authorization": "Bearer s3nh4"}

# Exatamente o que o painel permite salvar (ALLOWED_KEYS) com todas as
# fechaduras do dono satisfeitas. Nada aqui é inventado: é o estado máximo
# que a interface consegue construir sozinha.
CFG_LIVE_COMPLETO = {
    "TRADINGAGENTS_DESK_BROKER": "ccxt",
    "TRADINGAGENTS_EXCHANGE": "binance",
    "TRADINGAGENTS_BROKER_MODE": "live",
    "EXCHANGE_API_KEY": "k",
    "EXCHANGE_API_SECRET": "s",
    "EXCHANGE_CREDENTIAL_EXCHANGE": "binance",
    "LIVE_TRADING_CONFIRM": "EU AUTORIZO OPERAR COM DINHEIRO REAL",
    "LIVE_MAX_ORDER_VALUE": "50",
    "LIVE_MAX_DAILY_LOSS": "25",
}


class TestSeloDaMesa:
    def test_cfg_completa_do_painel_nao_constroi_corretora_live(self, tmp_path):
        """PROVA DO ACHADO: antes da correção, este teste falhava porque a
        corretora LIVE era construída — a mesa ficava a um submit de uma
        ordem real, com mandato PAPER."""
        with pytest.raises(BrokerSecurityError, match="live_authorized"):
            build_desk_broker(CFG_LIVE_COMPLETO, final_state={},
                              journal_path=str(tmp_path / "j.jsonl"))

    def test_autorizacao_de_codigo_explicita_permite_live(self, tmp_path):
        """O selo é um PORTÃO, não uma remoção: com liberação explícita de
        código a ponte LIVE continua construível — e os freios do dono
        (frase/tetos) seguem valendo por cima."""
        broker, mode = build_desk_broker(
            CFG_LIVE_COMPLETO, final_state={},
            journal_path=str(tmp_path / "j.jsonl"), live_authorized=True)
        assert mode == "live" and broker.mode == "live"

    def test_testnet_continua_intacto(self, tmp_path):
        cfg = {**CFG_LIVE_COMPLETO, "TRADINGAGENTS_BROKER_MODE": "testnet"}
        broker, mode = build_desk_broker(
            cfg, final_state={}, journal_path=str(tmp_path / "j.jsonl"))
        assert mode == "testnet" and broker.status()["sandbox"] is True

    def test_selo_nao_substitui_os_freios_da_ponte(self, tmp_path):
        """live_authorized NÃO afrouxa nenhuma fechadura: sem teto por ordem
        a ponte continua recusando."""
        cfg = {k: v for k, v in CFG_LIVE_COMPLETO.items()
               if k != "LIVE_MAX_ORDER_VALUE"}
        with pytest.raises(BrokerSecurityError, match="LIVE_MAX_ORDER_VALUE"):
            build_desk_broker(cfg, final_state={},
                              journal_path=str(tmp_path / "j.jsonl"),
                              live_authorized=True)

    def test_paper_continua_o_padrao(self):
        broker, mode = build_desk_broker({}, final_state={})
        assert mode == "paper" and type(broker).__name__ == "PaperBroker"


class TestPontaAPontaNoServico:
    """O caminho REAL do painel: salvar a configuração e usar a mesa."""

    def _app(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.delenv("DATABASE_URL", raising=False)
        import service.app as m
        importlib.reload(m)
        m._FAILS.clear()
        m._DESKS.clear()
        from fastapi.testclient import TestClient
        return m, TestClient(m.app)

    def test_mesa_recusa_arrancar_em_live_mesmo_com_cfg_completa(self, tmp_path, monkeypatch):
        m, c = self._app(tmp_path, monkeypatch)
        r = c.post("/v1/config", headers=H, json={"vars": CFG_LIVE_COMPLETO})
        assert r.status_code == 200, r.text
        # o boot da mesa RECUSA — e a recusa é verbosa, não um 500 mudo
        r = c.get("/v1/desk/status", headers=H)
        assert r.status_code == 409, (
            f"mesa arrancou com corretora LIVE ({r.status_code}): {r.text[:300]}")
        assert "LIVE" in r.json()["detail"] or "live" in r.json()["detail"]
        # e nenhuma mesa com corretora live ficou viva na memória
        assert "legacy" not in m._DESKS

    def test_checklist_mostra_o_selo_de_codigo_e_nunca_promete_real(self, tmp_path, monkeypatch):
        m, c = self._app(tmp_path, monkeypatch)
        c.post("/v1/config", headers=H, json={"vars": CFG_LIVE_COMPLETO})
        d = c.get("/v1/live/status", headers=H).json()
        assert d["pode_operar_real"] is False
        selos = [i for i in d["checklist"] if "código" in i["item"].lower()]
        assert selos, "o checklist precisa declarar o selo de código da mesa"
        assert selos[0]["pronto"] is False and selos[0]["acao"]

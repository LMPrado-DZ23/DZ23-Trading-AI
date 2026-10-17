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
from types import SimpleNamespace

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
    "LIVE_EXCHANGE_API_KEY": "live-k",
    "LIVE_EXCHANGE_API_SECRET": "live-s",
    "LIVE_EXECUTION_EXCHANGE": "binance",
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

    def test_mandato_live_exige_selo_e_ativacao_vinculada(self, tmp_path,
                                                          monkeypatch):
        m, _ = TestPontaAPontaNoServico()._app(tmp_path, monkeypatch)
        from tradingagents.desk.models import Mode

        with pytest.raises(BrokerSecurityError, match="selo de código"):
            m._construir_mandato_da_mesa(
                "legacy", CFG_LIVE_COMPLETO, ("BTC/USDT",),
                m.Decimal("1000"), ativacao_live={
                    "schema": "dz23.ativacao-live/1", "tenant_id": "legacy",
                    "exchange": "binance", "conta": "principal",
                    "agent_id": "trader-momentum",
                    "authorization_id": "AUT-1", "valida": True,
                })

        monkeypatch.setattr(m, "MESA_LIVE_LIBERADA", True)
        with pytest.raises(BrokerSecurityError, match="ativação explícita"):
            m._construir_mandato_da_mesa(
                "legacy", CFG_LIVE_COMPLETO, ("BTC/USDT",),
                m.Decimal("1000"), ativacao_live=None)

        mandato = m._construir_mandato_da_mesa(
            "legacy", CFG_LIVE_COMPLETO, ("BTC/USDT",),
            m.Decimal("1000"), ativacao_live={
                "schema": "dz23.ativacao-live/1", "tenant_id": "legacy",
                "exchange": "binance", "conta": "principal",
                "agent_id": "trader-momentum",
                "authorization_id": "AUT-1", "valida": True,
            })
        assert mandato.mode is Mode.LIVE
        assert mandato.account == "principal"
        assert mandato.live_authorized is True

    def test_testnet_recebe_mandato_testnet(self, tmp_path, monkeypatch):
        m, _ = TestPontaAPontaNoServico()._app(tmp_path, monkeypatch)
        from tradingagents.desk.models import Mode

        cfg = {**CFG_LIVE_COMPLETO,
               "TRADINGAGENTS_BROKER_MODE": "testnet"}
        mandato = m._construir_mandato_da_mesa(
            "legacy", cfg, ("BTC/USDT",), m.Decimal("1000"))
        assert mandato.mode is Mode.TESTNET
        assert mandato.live_authorized is False
        assert mandato.account == "testnet-legacy"

    def test_boot_live_isola_estado_e_nasce_bloqueado_para_reconciliar(
            self, tmp_path, monkeypatch):
        m, _ = TestPontaAPontaNoServico()._app(tmp_path, monkeypatch)
        from tradingagents.desk.models import Mode

        monkeypatch.setattr(m, "MESA_LIVE_LIBERADA", True)
        ativacao = {
            "schema": "dz23.ativacao-live/1", "tenant_id": "legacy",
            "exchange": "binance", "conta": "principal",
            "agent_id": "trader-momentum",
            "authorization_id": "AUT-1", "valida": True,
        }
        desk = m._desk_boot(
            "legacy", cfg_override=CFG_LIVE_COMPLETO,
            live_activation=ativacao)

        mandato = desk["orchestrator"].mandate
        assert mandato.mode is Mode.LIVE and mandato.live_authorized is True
        assert desk["broker_mode"] == "live"
        assert desk["coordinator"].reconciliacao_pendente is True
        assert "reconcilie" in desk["coordinator"].motivo_bloqueio.lower()
        assert desk["chave_oms"].startswith("oms_live_")
        assert desk["chave_livro"].startswith("livro_live_")
        assert desk["chave_mesa"].startswith("mesa_live_")
        assert desk["scheduler"].running is False


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
        cfg_sem_consentimento = {
            k: v for k, v in CFG_LIVE_COMPLETO.items()
            if k != "LIVE_TRADING_CONFIRM"
        }
        r = c.post(
            "/v1/config", headers=H,
            json={"vars": cfg_sem_consentimento})
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

    def test_rota_de_ativacao_nao_contorna_selo_fechado(self, tmp_path,
                                                        monkeypatch):
        m, c = self._app(tmp_path, monkeypatch)
        r = c.post(
            "/v1/live/ativar/trader-momentum", headers=H,
            json={"frase": "EU AUTORIZO OPERAR COM DINHEIRO REAL",
                  "conta": "principal"})
        assert r.status_code == 409
        assert "MESA_LIVE_LIBERADA=False" in str(r.json()["detail"])
        assert m.MESA_LIVE_LIBERADA is False
        assert m._DESKS == {}

    def test_rota_futura_vincula_agente_conta_e_nao_envia_ordem(
            self, tmp_path, monkeypatch):
        m, c = self._app(tmp_path, monkeypatch)
        cfg = {**CFG_LIVE_COMPLETO,
               "TRADINGAGENTS_BROKER_MODE": "testnet"}
        piloto = SimpleNamespace(
            authorization_id="AUT-1", conta="principal")
        salvos = {}

        class Armazem:
            def salvar(self, tenant, chave, valor):
                salvos[(tenant, chave)] = valor

        mesa = {"broker_mode": "live",
                "scheduler": SimpleNamespace(running=False)}
        boot = {}
        monkeypatch.setattr(m, "MESA_LIVE_LIBERADA", True)
        monkeypatch.setattr(m, "load_cfg", lambda _tid: dict(cfg))
        monkeypatch.setattr(m, "_live_status_payload", lambda _req: {
            "estado_de_liberacao": {
                "pre_requisitos_sem_selo_concluidos": True},
            "certificacoes_estrategia_conta": [{
                "agent_id": "trader-momentum", "conta": "principal",
                "risco_compativel": True}],
        })
        monkeypatch.setattr(m, "_escola_of", lambda _req: {
            "armazem": Armazem(), "tenant_id": "legacy"})
        monkeypatch.setattr(
            m, "_supervisor_of",
            lambda _req: SimpleNamespace(de=lambda _agent: piloto))
        monkeypatch.setattr(
            m, "_prova_leitura_real", lambda _req, _cfg: ({}, True))
        monkeypatch.setattr(
            m, "_prova_execucao_live", lambda _req, _cfg: ({
                "conta": "principal",
                "verificada_em": "2026-10-16T00:00:00+00:00",
            }, True))
        monkeypatch.setattr(
            m, "_prova_real_serve_piloto", lambda *_args: True)
        def fake_boot(*_args, **kwargs):
            boot.update(kwargs)
            return mesa

        monkeypatch.setattr(m, "_desk_boot", fake_boot)
        monkeypatch.setattr(
            m, "save_cfg", lambda value, tenant: salvos.__setitem__(
                (tenant, "config"), value))

        r = c.post(
            "/v1/live/ativar/trader-momentum", headers=H,
            json={"frase": "EU AUTORIZO OPERAR COM DINHEIRO REAL",
                  "conta": "principal"})

        assert r.status_code == 200, r.text
        assert r.json()["envio_real_habilitado"] is False
        assert r.json()["reconciliacao_obrigatoria"] is True
        assert salvos[("legacy", "config")][
            "TRADINGAGENTS_BROKER_MODE"] == "live"
        assert "LIVE_TRADING_CONFIRM" not in salvos[("legacy", "config")]
        assert "LIVE_TRADING_CONFIRM" not in boot["cfg_override"]
        registro = salvos[("legacy", m._CHAVE_ATIVACAO_LIVE)]
        assert registro["agent_id"] == "trader-momentum"
        assert registro["conta"] == "principal"
        assert registro["execution_credential_fingerprint"] == (
            m.live_execution_credential_fingerprint(
                m._env_live_execution(cfg)))
        assert "frase" not in registro
        assert m._DESKS["legacy"] is mesa

"""V8.1 §4 — Decisão do ciclo da mesa (abstenção inteligente, task #102).

O proprietário nomeou QUATRO estados e nenhum deles é "sempre operar":

- OPERAR   — somente quando todos os gates forem aprovados;
- AGUARDAR — oportunidade insuficiente ou condições desfavoráveis;
- BLOQUEAR — risco, dados ou condições operacionais inadequadas;
- SUSPENDER — deterioração comprovada ou falha crítica.

Contratos pinados aqui:
- o módulo é PURO (sem I/O, sem caminho de ordem — LLM nenhum contorna);
- a ausência de operação é apresentada como decisão ("esperar"), nunca
  como falha nem como evidência de rentabilidade;
- a decisão chega ao painel via /v1/desk/status (`decisao`) e é renderizada
  na Início — o dono lê POR QUE a mesa não operou.
"""
from __future__ import annotations

import importlib
from pathlib import Path

from tradingagents.desk.decisao import (
    AGUARDAR,
    BLOQUEAR,
    OPERAR,
    SUSPENDER,
    decidir_do_ciclo,
)

ROOT = Path(__file__).resolve().parents[1]
MODULO = ROOT / "tradingagents" / "desk" / "decisao.py"
APP = ROOT / "service" / "app.py"
HTML = ROOT / "service" / "static" / "index.html"

H = {"Authorization": "Bearer s3nh4"}

ESTADOS = ("OPERAR", "AGUARDAR", "BLOQUEAR", "SUSPENDER")


def _ciclo(**extra):
    base = {"em": "2026-10-14T12:00:00+00:00", "origem": "fixture",
            "resumo": "", "propostas": 0, "vetos": [], "abstencoes": [],
            "erros": [], "descartados": [], "ordens": [], "encerradas": []}
    base.update(extra)
    return base


# ------------------------------------------------------------- módulo puro


class TestDecisaoUnidade:
    def test_operar_quando_ultimo_ciclo_executou_entrada(self):
        d = decidir_do_ciclo(mesa_ativa=True,
                             ultimo_ciclo=_ciclo(propostas=3,
                                                 ordens=[{"ordem": "o-1"}]))
        assert d["estado"] == OPERAR
        assert "1" in d["motivo"] and "entrada" in d["motivo"]

    def test_operar_quando_ultimo_ciclo_so_executou_saida(self):
        d = decidir_do_ciclo(
            mesa_ativa=True,
            ultimo_ciclo=_ciclo(encerradas=[("BTC/USDT", "stop", "x")]))
        assert d["estado"] == OPERAR
        assert "saída" in d["motivo"]

    def test_aguardar_sem_ciclo_avaliado(self):
        d = decidir_do_ciclo(mesa_ativa=True, ultimo_ciclo=None)
        assert d["estado"] == AGUARDAR
        assert d["motivo"].strip()

    def test_aguardar_sem_oportunidade_conta_abstencoes_e_vetos(self):
        d = decidir_do_ciclo(
            mesa_ativa=True,
            ultimo_ciclo=_ciclo(propostas=4,
                                abstencoes=[("a", "critério x"),
                                            ("b", "critério y")],
                                vetos=[("c", "risco")]))
        assert d["estado"] == AGUARDAR
        assert "4" in d["motivo"]
        assert "2" in d["motivo"]
        assert "1" in d["motivo"]
        # não operar é DECISÃO declarada — nunca falha, nunca prova de lucro
        assert "esperar" in d["motivo"]

    def test_suspender_contabilidade_desbalanceada_tem_precedencia(self):
        d = decidir_do_ciclo(mesa_ativa=False, motivo_do_modo="mesa pausada",
                             contabilidade_balanceada=False,
                             ultimo_ciclo=_ciclo(ordens=[{"ordem": "o-1"}]))
        assert d["estado"] == SUSPENDER
        assert "contabilidade" in d["motivo"]

    def test_bloquear_mesa_pausada_explica_o_motivo(self):
        d = decidir_do_ciclo(mesa_ativa=False,
                             motivo_do_modo="mesa pausada pelo dono")
        assert d["estado"] == BLOQUEAR
        assert "pausada" in d["motivo"]

    def test_bloquear_posse_de_outro_processo(self):
        d = decidir_do_ciclo(mesa_ativa=True, posse_sou_eu=False,
                             posse_dono="pc-do-dono-1234")
        assert d["estado"] == BLOQUEAR
        assert "pc-do-dono-1234" in d["motivo"]

    def test_posse_livre_nao_bloqueia(self):
        # Posse vencida/livre: o próximo ciclo assume — bloquear aqui seria
        # alarme falso para o dono.
        d = decidir_do_ciclo(mesa_ativa=True, posse_sou_eu=False,
                             posse_dono="", ultimo_ciclo=None)
        assert d["estado"] == AGUARDAR

    def test_bloquear_perda_do_dia_atingida(self):
        d = decidir_do_ciclo(mesa_ativa=True, perda_do_dia_atingida=True)
        assert d["estado"] == BLOQUEAR
        assert "perda" in d["motivo"]

    def test_bloquear_pendencias_de_reconciliacao(self):
        d = decidir_do_ciclo(mesa_ativa=True,
                             pendencias_de_reconciliacao=2)
        assert d["estado"] == BLOQUEAR
        assert "2" in d["motivo"]

    def test_bloquear_apos_ciclo_com_erro(self):
        d = decidir_do_ciclo(mesa_ativa=True,
                             ultimo_ciclo=_ciclo(erros=["saída BTC: erro"]))
        assert d["estado"] == BLOQUEAR
        assert "erro" in d["motivo"]

    def test_saida_enxuta_e_estados_exatos(self):
        assert (OPERAR, AGUARDAR, BLOQUEAR, SUSPENDER) == ESTADOS
        for kwargs, esperado in [
            ({}, AGUARDAR),
            ({"contabilidade_balanceada": False}, SUSPENDER),
            ({"mesa_ativa": False}, BLOQUEAR),
        ]:
            d = decidir_do_ciclo(**{"mesa_ativa": True, **kwargs})
            assert d["estado"] == esperado
            assert set(d) == {"estado", "motivo"}
            assert str(d["motivo"]).strip()

    def test_modulo_puro_sem_caminho_de_ordem_nem_rede(self):
        src = MODULO.read_text(encoding="utf-8")
        assert "create_order" not in src and "cancel_order" not in src
        assert "ccxt" not in src and "import requests" not in src
        assert "subprocess" not in src and "socket" not in src
        assert "from .execution" not in src


# ------------------------------------------------------------------- HTTP


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


class TestDecisaoHttp:
    def test_status_traz_decisao_de_mesa_sem_ciclos(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        r = c.get("/v1/desk/status", headers=H)
        assert r.status_code == 200
        dec = r.json()["decisao"]
        assert dec["estado"] in ESTADOS
        assert dec["motivo"].strip()

    def test_mesa_pausada_vira_bloquear_com_motivo(self, tmp_path, monkeypatch):
        c, _ = _client(tmp_path, monkeypatch)
        r = c.post("/v1/desk/modo",
                   json={"estado": "pausada", "motivo": "parada do teste"},
                   headers=H)
        assert r.status_code == 200
        dec = c.get("/v1/desk/status", headers=H).json()["decisao"]
        assert dec["estado"] == "BLOQUEAR"
        assert "pausada" in dec["motivo"]

    def test_ciclo_gravado_alimenta_a_decisao(self, tmp_path, monkeypatch):
        # Propostas com critérios não atendidos NÃO viram operação — e a
        # decisão apresenta isso como AGUARDAR, com os números do ciclo.
        c, m = _client(tmp_path, monkeypatch)
        c.get("/v1/desk/status", headers=H)          # monta a mesa
        tid = next(iter(m._DESKS))
        m._DESKS[tid]["cycles"].append(
            _ciclo(propostas=2, abstencoes=["algum-trader · critério x"]))
        dec = c.get("/v1/desk/status", headers=H).json()["decisao"]
        assert dec["estado"] == "AGUARDAR"
        assert "2" in dec["motivo"]


# -------------------------------------------------------------------- DOM


class TestPainelMostraDecisao:
    def test_inicio_renderiza_decisao_do_ciclo(self):
        html = HTML.read_text(encoding="utf-8")
        assert "Decisão do ciclo" in html
        assert "rd.data.decisao||{}" in html

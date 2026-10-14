"""Copiloto Manual (E3) no serviço: rotas, guardas e contrato read-only.

Prova o que o desenho promete:
- auth obrigatória e nenhuma rota manual executa ordem (AST + rotas);
- CRUD de posições/eventos/política com idempotência e versão otimista;
- modo ligado = mesa autônoma bloqueada (guarda do laço e do pedido);
- monitor integrado à fila de alertas E2, sem fechar posição;
- o copiloto funciona SEM bootar a mesa (nenhum broker no caminho).
"""
from __future__ import annotations

import ast
import importlib
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import service.app as m
    importlib.reload(m)
    m._FAILS.clear()
    m._DESKS.clear()
    m._COPILOTOS.clear()
    m._FILAS_ALERTAS.clear()
    return m, TestClient(m.app), {"Authorization": "Bearer s3nh4"}


def corpo_posicao(**kw):
    base = {"instrumento": "BTC/USDT", "direcao": "long", "quantidade": 10,
            "preco_entrada": 100, "stop": 90, "alvos": [120],
            "intervalo_s": 60}
    base.update(kw)
    return base


def criar(m, c, h, **kw):
    return c.post("/v1/manual/positions", headers=h, json=corpo_posicao(**kw))


class _CopilotoFalso:
    ativo = True


class TestAutenticacao:
    def test_rotas_manuais_exigem_token(self, tmp_path, monkeypatch):
        m, c, h = _app(tmp_path, monkeypatch)
        for metodo, rota in [
                ("get", "/v1/manual/modo"),
                ("put", "/v1/manual/modo"),
                ("get", "/v1/manual/positions"),
                ("post", "/v1/manual/positions"),
                ("get", "/v1/manual/positions/mp_x"),
                ("post", "/v1/manual/positions/mp_x/events"),
                ("get", "/v1/manual/monitor/health")]:
            r = getattr(c, metodo)(rota)
            assert r.status_code in (401, 403), f"{metodo} {rota} liberou sem token"


class TestModo:
    def test_default_desligado_e_ligar_persiste(self, tmp_path, monkeypatch):
        m, c, h = _app(tmp_path, monkeypatch)
        d = c.get("/v1/manual/modo", headers=h).json()
        assert d["ativo"] is False
        r = c.put("/v1/manual/modo", headers=h, json={"ativo": True})
        assert r.status_code == 200 and r.json()["ativo"] is True
        # recarrega do armazém (novo processo em miniatura): o modo sobrevive
        m._COPILOTOS.clear()
        d2 = c.get("/v1/manual/modo", headers=h).json()
        assert d2["ativo"] is True
        assert any("mesa autônoma não executa" in a for a in d2["avisos"])

    def test_ativo_precisa_ser_booleano(self, tmp_path, monkeypatch):
        m, c, h = _app(tmp_path, monkeypatch)
        assert c.put("/v1/manual/modo", headers=h,
                     json={"ativo": "sim"}).status_code == 422
        assert c.put("/v1/manual/modo", headers=h, json={}).status_code == 422

    def test_desligar_devolve_anterior(self, tmp_path, monkeypatch):
        m, c, h = _app(tmp_path, monkeypatch)
        c.put("/v1/manual/modo", headers=h, json={"ativo": True})
        r = c.put("/v1/manual/modo", headers=h, json={"ativo": False})
        assert r.json()["anterior"] is True and r.json()["ativo"] is False


class TestPosicoesCRUD:
    def test_criar_listar_e_obter(self, tmp_path, monkeypatch):
        m, c, h = _app(tmp_path, monkeypatch)
        r = criar(m, c, h).json()
        assert r["criada"] is True
        pid = r["posicao"]["posicao_id"]
        assert r["posicao"]["estado_financeiro"] == "ABERTA"
        lista = c.get("/v1/manual/positions", headers=h).json()
        assert lista["resumo"]["abertas"] == 1
        detalhe = c.get(f"/v1/manual/positions/{pid}", headers=h).json()
        assert detalhe["posicao"]["posicao_id"] == pid
        assert detalhe["eventos"] == []

    def test_idempotencia_de_criacao(self, tmp_path, monkeypatch):
        m, c, h = _app(tmp_path, monkeypatch)
        r1 = criar(m, c, h, idempotency_key="k1").json()
        r2 = criar(m, c, h, idempotency_key="k1").json()
        assert r2["criada"] is False
        assert r2["posicao"]["posicao_id"] == r1["posicao"]["posicao_id"]
        assert c.get("/v1/manual/positions", headers=h).json()["resumo"]["total"] == 1

    def test_sem_stop_valido_para_long_e_recusado(self, tmp_path, monkeypatch):
        m, c, h = _app(tmp_path, monkeypatch)
        assert criar(m, c, h, stop=110).status_code == 422   # stop acima da entrada
        assert criar(m, c, h, preco_entrada=None).status_code == 422

    def test_encerramento_fecha_posicao_inteira(self, tmp_path, monkeypatch):
        m, c, h = _app(tmp_path, monkeypatch)
        pid = criar(m, c, h).json()["posicao"]["posicao_id"]
        # parcial via encerramento é recusado — a saída correta é 'reducao'
        r = c.post(f"/v1/manual/positions/{pid}/events", headers=h,
                   json={"tipo": "encerramento", "quantidade": 4, "preco": 118})
        assert r.status_code == 422
        r = c.post(f"/v1/manual/positions/{pid}/events", headers=h,
                   json={"tipo": "encerramento", "preco": 118})
        assert r.status_code == 200
        d = r.json()
        assert d["posicao"]["estado_financeiro"] == "ENCERRADA"
        assert Decimal(d["posicao"]["lucro_realizado_liquido"]) == Decimal("180")
        assert c.get("/v1/manual/positions", headers=h,
                     params={"estado": "ABERTA"}).json()["resumo"]["total"] == 0

    def test_preco_executado_e_obrigatorio(self, tmp_path, monkeypatch):
        m, c, h = _app(tmp_path, monkeypatch)
        pid = criar(m, c, h).json()["posicao"]["posicao_id"]
        r = c.post(f"/v1/manual/positions/{pid}/events", headers=h,
                   json={"tipo": "reducao", "quantidade": 2})
        assert r.status_code == 422
        assert "preço executado" in r.json()["detail"]

    def test_conflito_de_versao_e_409(self, tmp_path, monkeypatch):
        m, c, h = _app(tmp_path, monkeypatch)
        pid = criar(m, c, h).json()["posicao"]["posicao_id"]
        c.post(f"/v1/manual/positions/{pid}/events", headers=h,
               json={"tipo": "reducao", "quantidade": 2, "preco": 110})
        r = c.post(f"/v1/manual/positions/{pid}/events", headers=h,
                   json={"tipo": "reducao", "quantidade": 2, "preco": 112,
                         "expected_version": 1})
        assert r.status_code == 409

    def test_idempotencia_de_evento(self, tmp_path, monkeypatch):
        m, c, h = _app(tmp_path, monkeypatch)
        pid = criar(m, c, h).json()["posicao"]["posicao_id"]
        ev = {"tipo": "reducao", "quantidade": 2, "preco": 110,
              "idempotency_key": "ev-1"}
        r1 = c.post(f"/v1/manual/positions/{pid}/events", headers=h, json=ev).json()
        r2 = c.post(f"/v1/manual/positions/{pid}/events", headers=h, json=ev).json()
        assert r2["aplicado"] is False
        assert r2["evento"]["evento_id"] == r1["evento"]["evento_id"]
        assert Decimal(r2["posicao"]["quantidade_aberta"]) == Decimal("8")

    def test_pausa_e_retomada(self, tmp_path, monkeypatch):
        m, c, h = _app(tmp_path, monkeypatch)
        pid = criar(m, c, h).json()["posicao"]["posicao_id"]
        d = c.post(f"/v1/manual/positions/{pid}/pause", headers=h,
                   json={"motivo": "viagem"}).json()
        assert d["posicao"]["estado_monitor"] == "PAUSED"
        d = c.post(f"/v1/manual/positions/{pid}/resume", headers=h).json()
        assert d["posicao"]["estado_monitor"] == "MONITORING"

    def test_politica_nova_e_recusa_invalida(self, tmp_path, monkeypatch):
        m, c, h = _app(tmp_path, monkeypatch)
        pid = criar(m, c, h).json()["posicao"]["posicao_id"]
        ok = c.put(f"/v1/manual/positions/{pid}/monitoring-policy", headers=h,
                   json={"stop": 95, "alvos": [130], "trailing_pct": 5,
                         "intervalo_s": 30})
        assert ok.status_code == 200
        assert ok.json()["posicao"]["politica"]["stop"] == "95"
        ruim = c.put(f"/v1/manual/positions/{pid}/monitoring-policy", headers=h,
                     json={"stop": 150})
        assert ruim.status_code == 422

    def test_revisao_sem_ia_registra_fatos_sem_inventar(self, tmp_path, monkeypatch):
        m, c, h = _app(tmp_path, monkeypatch)
        pid = criar(m, c, h).json()["posicao"]["posicao_id"]
        d = c.post(f"/v1/manual/positions/{pid}/revisao", headers=h,
                   json={"observacao": "estou calmo"}).json()
        assert d["ia_configurada"] is False
        assert d["entrada"]["revisao"] is None
        assert d["entrada"]["fatos"]["preco_entrada"] == "100"
        detalhe = c.get(f"/v1/manual/positions/{pid}", headers=h).json()
        assert any(r["fatos"]["observacao"] == "estou calmo"
                   for r in detalhe["revisoes"])

    def test_ids_desconhecidos_dao_404(self, tmp_path, monkeypatch):
        m, c, h = _app(tmp_path, monkeypatch)
        assert c.get("/v1/manual/positions/mp_nao_existe",
                     headers=h).status_code == 404
        for rota, metodo in [("events", "post"), ("pause", "post"),
                             ("resume", "post"), ("alerts", "get")]:
            chamada = getattr(c, metodo)
            extra = {"json": {}} if metodo == "post" else {}
            r = chamada(f"/v1/manual/positions/mp_x/{rota}",
                        headers=h, **extra)
            assert r.status_code == 404


class TestMonitorIntegrado:
    def _com_posicao(self, tmp_path, monkeypatch):
        m, c, h = _app(tmp_path, monkeypatch)
        pid = criar(m, c, h, alvos=[120]).json()["posicao"]["posicao_id"]
        return m, c, h, pid

    def test_tique_enfileira_alerta_e_nao_fecha_posicao(self, tmp_path, monkeypatch):
        from tradingagents.desk.models import MarketSnapshot, utcnow
        m, c, h, pid = self._com_posicao(tmp_path, monkeypatch)
        cop = m._copiloto("legacy")
        snap = MarketSnapshot("BTC/USDT", "1h", utcnow(),
                              (Decimal("121"),), source="fixture")
        resumo = m._tique_do_copiloto("legacy", cop, snaps_fn=lambda: [snap])
        assert resumo["avaliadas"] == 1
        tipos = [a["tipo"] for a in resumo["alertas"]]
        assert "manual_alvo_atingido" in tipos
        # alvo atingido vira ALERTA — posição permanece aberta (jamais fechada
        # pelo monitor; encerramento só por evento do dono).
        assert cop.obter(pid).estado_financeiro == "ABERTA"
        fila = m._fila_alertas("legacy")
        assert any(a.fonte == "copiloto_manual" and a.tipo == "manual_alvo_atingido"
                   for a in fila.alertas)

    def test_alerta_fica_visivel_na_rota_da_posicao_e_ack_funciona(
            self, tmp_path, monkeypatch):
        from tradingagents.desk.models import MarketSnapshot, utcnow
        m, c, h, pid = self._com_posicao(tmp_path, monkeypatch)
        cop = m._copiloto("legacy")
        snap = MarketSnapshot("BTC/USDT", "1h", utcnow(),
                              (Decimal("121"),), source="fixture")
        m._tique_do_copiloto("legacy", cop, snaps_fn=lambda: [snap])
        d = c.get(f"/v1/manual/positions/{pid}/alerts", headers=h).json()
        assert d["total"] == 1
        alerta = d["alertas"][0]
        ack = c.post(f"/v1/manual/alerts/{alerta['id']}/ack", headers=h)
        assert ack.status_code == 200 and ack.json()["lido_em"]
        assert c.post("/v1/manual/alerts/nao_existe/ack",
                      headers=h).status_code == 404

    def test_ack_nao_aceita_alerta_de_outro_modo(self, tmp_path, monkeypatch):
        m, c, h = _app(tmp_path, monkeypatch)
        fila = m._fila_alertas("legacy")
        alerta, _ = fila.enfileirar(tipo="mesa_parada", severidade="atencao",
                                    titulo="t", detalhe="d", acao="a",
                                    fonte="mesa", dedupe_key="mesa:x")
        assert c.post(f"/v1/manual/alerts/{alerta.id}/ack",
                      headers=h).status_code == 404

    def test_health_mostra_estado_do_monitor(self, tmp_path, monkeypatch):
        m, c, h, pid = self._com_posicao(tmp_path, monkeypatch)
        d = c.get("/v1/manual/monitor/health", headers=h).json()
        assert d["monitor"]["posicoes_abertas"] == 1
        assert d["monitor"]["estados"].get("MONITORING", 0) == 1
        assert "nunca executa ordem" in d["aviso"].lower()


class TestGuardasDaMesa:
    def test_copiloto_ligado_faz_o_laco_so_observar(self, tmp_path, monkeypatch):
        m, c, h = _app(tmp_path, monkeypatch)
        assert m._guarda_do_laco(None, None, copiloto=_CopilotoFalso()) == \
            ("observar", "")

    def test_copiloto_ligado_bloqueia_pedido_sob_demanda(self, tmp_path, monkeypatch):
        m, c, h = _app(tmp_path, monkeypatch)
        with pytest.raises(HTTPException) as e:
            m._guarda_do_pedido({"manual": _CopilotoFalso(),
                                 "modo": None, "posse": None})
        assert e.value.status_code == 409
        assert "Copiloto Manual ligado" in e.value.detail

    def test_copiloto_desligado_nao_altera_a_guarda(self, tmp_path, monkeypatch):
        m, c, h = _app(tmp_path, monkeypatch)

        class Desligado:
            ativo = False

        class ModoOk:
            class estado:  # noqa: N801 — espelha o enum real
                pass

            @staticmethod
            def pode_executar():
                return True, ""

        from tradingagents.desk.modo import EstadoDaMesa
        modo = ModoOk()
        modo.estado = EstadoDaMesa.ATIVA

        class PosseOk:
            @staticmethod
            def assumir():
                return True, ""

        acao, motivo = m._guarda_do_laco(modo, PosseOk(), copiloto=Desligado())
        assert acao == "executar" and motivo == ""

    def test_sem_feed_o_tique_nao_explode(self, tmp_path, monkeypatch):
        m, c, h = _app(tmp_path, monkeypatch)
        cop = m._copiloto("legacy")
        assert m._tique_do_copiloto("legacy", cop) == {}

    def test_rotas_manuais_nao_bootam_a_mesa(self, tmp_path, monkeypatch):
        """O copiloto é read-only e independente: nenhuma rota manual pode
        construir a mesa (broker/execução) para funcionar."""
        m, c, h = _app(tmp_path, monkeypatch)
        criar(m, c, h)
        c.get("/v1/manual/positions", headers=h)
        c.get("/v1/manual/monitor/health", headers=h)
        assert m._DESKS == {}


class TestContratoReadOnly:
    _FUNCOES_MANUAIS = {
        "_armazem_do_tenant", "_copiloto", "_persistir_copiloto",
        "_tique_do_copiloto", "_copiloto_para_painel", "_revisor_de_ia",
        "_manual_http", "manual_modo_obter", "manual_modo_definir",
        "manual_criar_posicao", "manual_listar_posicoes",
        "manual_obter_posicao", "manual_registrar_evento",
        "manual_definir_politica", "manual_pausar", "manual_retomar",
        "manual_revisao", "manual_alertas_da_posicao", "manual_ack",
        "manual_monitor_health",
    }
    _PROIBIDOS = {"create_order", "cancel_order", "close_position",
                  "executar_ordem", "submit"}

    def test_nenhuma_funcao_manual_chama_execucao(self):
        fonte = (Path(__file__).resolve().parents[1] / "service" / "app.py")
        arvore = ast.parse(fonte.read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            if not isinstance(no, ast.FunctionDef):
                continue
            if no.name not in self._FUNCOES_MANUAIS:
                continue
            for filho in ast.walk(no):
                if isinstance(filho, ast.Call):
                    nome = ""
                    if isinstance(filho.func, ast.Attribute):
                        nome = filho.func.attr
                    elif isinstance(filho.func, ast.Name):
                        nome = filho.func.id
                    assert nome not in self._PROIBIDOS, \
                        f"{no.name} chama {nome} — viola o contrato read-only"

    def test_todas_as_rotas_do_spec_estao_registradas(self, tmp_path, monkeypatch):
        m, c, h = _app(tmp_path, monkeypatch)
        rotas = {r.path for r in m.app.routes if hasattr(r, "path")}
        for esperada in [
                "/v1/manual/modo", "/v1/manual/positions",
                "/v1/manual/positions/{posicao_id}",
                "/v1/manual/positions/{posicao_id}/events",
                "/v1/manual/positions/{posicao_id}/monitoring-policy",
                "/v1/manual/positions/{posicao_id}/pause",
                "/v1/manual/positions/{posicao_id}/resume",
                "/v1/manual/positions/{posicao_id}/alerts",
                "/v1/manual/alerts/{alerta_id}/ack",
                "/v1/manual/monitor/health"]:
            assert esperada in rotas, f"rota {esperada} não registrada"

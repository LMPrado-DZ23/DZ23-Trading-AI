"""Relato canônico da preparação para dinheiro real, sem ativar LIVE."""

import importlib

H = {"Authorization": "Bearer s3nh4"}


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import service.app as m
    importlib.reload(m)
    m._FAILS.clear()
    m._DESKS.clear()
    from fastapi.testclient import TestClient
    return m, TestClient(m.app)


def test_status_separa_pendencias_por_responsabilidade(tmp_path, monkeypatch):
    m, client = _app(tmp_path, monkeypatch)

    response = client.get("/v1/live/status", headers=H)

    assert response.status_code == 200, response.text
    body = response.json()
    readiness = body["estado_de_liberacao"]
    assert readiness["schema"] == "dz23.liberacao-real/1"
    assert readiness["estado"] == "preparacao_pendente"
    assert readiness["pre_requisitos_sem_selo_concluidos"] is False
    assert readiness["exige_confirmacao_no_momento_da_acao"] is True
    assert sum(readiness["quantidade_por_categoria"].values()) == body[
        "pendencias"]
    assert readiness["bloqueios_por_categoria"]["liberacao_de_codigo"] == [
        "Ativação do modo LIVE somente na etapa final",
        "Liberação de código para operar dinheiro real na mesa",
    ]
    assert m.MESA_LIVE_LIBERADA is False
    assert body["pode_operar_real"] is False
    assert all(item["categoria"] in {
        "acao_do_proprietario", "prova_externa", "controle_operacional",
        "liberacao_de_codigo",
    } for item in body["checklist"])


def test_modo_live_so_muda_na_fechadura_final_nao_durante_preparacao(
        tmp_path, monkeypatch):
    m, client = _app(tmp_path, monkeypatch)

    body = client.get("/v1/live/status", headers=H).json()
    modo = next(
        item for item in body["checklist"]
        if item["item"] == "Ativação do modo LIVE somente na etapa final"
    )

    assert modo["pronto"] is False
    assert modo["categoria"] == "liberacao_de_codigo"
    assert "mantenha PAPER/TESTNET" in modo["acao"]
    assert m.MESA_LIVE_LIBERADA is False


def test_preparacao_completa_e_alcancavel_sem_trocar_testnet_para_live(
        tmp_path, monkeypatch):
    m, client = _app(tmp_path, monkeypatch)
    cfg = {
        "TRADINGAGENTS_EXCHANGE": "binance",
        "TRADINGAGENTS_BROKER_MODE": "testnet",
        "LIVE_TRADING_CONFIRM": m.LIVE_CONFIRM_PHRASE,
        "LIVE_MAX_ORDER_VALUE": "25",
        "LIVE_MAX_DAILY_LOSS": "10",
    }
    prova = {
        "authenticated_read": True,
        "broker_identity_verified": True,
        "sem_trade_declarado": True,
        "sem_saque_declarado": True,
    }
    certificacao = {
        "agent_id": "agente-formado",
        "authorization_id": "auth-1",
        "estrategia": "momentum",
        "versao": "v1",
        "corretora": "binance",
        "conta": "real-principal",
        "contexto_certificacao": {},
        "valido_ate": "2099-01-01T00:00:00+00:00",
        "risco_compativel": True,
        "pendencias_risco": [],
    }
    monkeypatch.setattr(m, "load_cfg", lambda _tenant: cfg)
    monkeypatch.setattr(
        m, "_credenciais_execucao_prontas", lambda _cfg: (True, []))
    monkeypatch.setattr(
        m, "_prova_leitura_real", lambda _request, _cfg: (prova, True))
    monkeypatch.setattr(
        m, "_prova_execucao_live", lambda _request, _cfg: ({
            "conta": "real-principal",
            "authenticated_read": True,
            "broker_identity_verified": True,
            "trade_habilitado_declarado": True,
            "sem_saque_declarado": True,
        }, True))
    monkeypatch.setattr(
        m, "_prova_testnet", lambda _request, _cfg: (
            {"valida": True, "exchange": "binance"}, True))
    monkeypatch.setattr(
        m, "_certificacoes_real",
        lambda _request, _cfg, _prova, _valida: [certificacao])
    monkeypatch.setattr(
        m, "_reconciliacao_para_prontidao",
        lambda _tenant: {"seguro": True, "pendencias": []})
    monkeypatch.setattr(m, "_parada_desarmada_para_prontidao", lambda _: True)
    monkeypatch.setattr(
        m, "_recuperacao_local_para_prontidao", lambda: {"valida": True})
    monkeypatch.setattr(
        m, "_recuperacao_postgres_para_prontidao",
        lambda: {"necessaria": False, "valida": True})
    monkeypatch.setattr(
        m, "_observabilidade_para_prontidao", lambda: {"valida": True})

    body = client.get("/v1/live/status", headers=H).json()

    assert body["pode_operar_real"] is False
    assert body["estado_de_liberacao"]["estado"] == (
        "aguardando_liberacao_de_codigo")
    assert body["estado_de_liberacao"][
        "pre_requisitos_sem_selo_concluidos"] is True
    assert [item["item"] for item in body["checklist"] if not item["pronto"]] == [
        "Ativação do modo LIVE somente na etapa final",
        "Liberação de código para operar dinheiro real na mesa",
    ]
    assert cfg["TRADINGAGENTS_BROKER_MODE"] == "testnet"
    assert m.MESA_LIVE_LIBERADA is False


def test_resumo_puro_distingue_preparado_de_liberado(tmp_path, monkeypatch):
    m, _ = _app(tmp_path, monkeypatch)
    checklist = [
        {"item": "prova externa", "pronto": True,
         "categoria": "prova_externa"},
        {"item": "selo", "pronto": False,
         "categoria": "liberacao_de_codigo"},
    ]

    readiness = m._estado_de_liberacao_real(checklist)

    assert readiness["estado"] == "aguardando_liberacao_de_codigo"
    assert readiness["pre_requisitos_sem_selo_concluidos"] is True
    assert readiness["bloqueios_por_categoria"] == {
        "acao_do_proprietario": [],
        "prova_externa": [],
        "controle_operacional": [],
        "liberacao_de_codigo": ["selo"],
    }


def test_status_expoe_fila_da_sala_real_sem_capacidade_de_ordem(
        tmp_path, monkeypatch):
    _, client = _app(tmp_path, monkeypatch)

    body = client.get("/v1/live/status", headers=H).json()

    sala = body["sala_real"]
    assert sala["disponivel"] is True
    assert sala["modo_do_piloto"] == "paper (simulado)"
    assert sala["envio_real_habilitado"] is False
    assert sala["encaminhamentos_total"] == 0
    assert sala["autorizacoes_total"] == 0
    assert sala["estados_encaminhamento"] == {}
    assert sala["estados_autorizacao"] == {}


def test_resumo_falha_fechado_se_sala_real_ficar_indisponivel(
        tmp_path, monkeypatch):
    m, client = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(
        m, "_supervisor_of",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("falha")))

    body = client.get("/v1/live/status", headers=H).json()

    assert body["sala_real"] == {
        "disponivel": False,
        "modo_do_piloto": "paper (simulado)",
        "envio_real_habilitado": False,
        "encaminhamentos_total": 0,
        "autorizacoes_total": 0,
        "estados_encaminhamento": {},
        "estados_autorizacao": {},
        "aviso": "estado da sala REAL indisponível; prontidão permanece bloqueada",
    }
    assert body["pode_operar_real"] is False

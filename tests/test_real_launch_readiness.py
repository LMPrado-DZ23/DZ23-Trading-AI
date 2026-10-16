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
        "Liberação de código para operar dinheiro real na mesa"]
    assert m.MESA_LIVE_LIBERADA is False
    assert body["pode_operar_real"] is False
    assert all(item["categoria"] in {
        "acao_do_proprietario", "prova_externa", "controle_operacional",
        "liberacao_de_codigo",
    } for item in body["checklist"])


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

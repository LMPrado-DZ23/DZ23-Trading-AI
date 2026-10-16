"""Prontidão operacional autenticada e preflight sem ativação financeira."""

import importlib
import json

from scripts import real_readiness_check as preflight
from scripts.real_readiness_check import avaliar

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


def test_operational_readiness_exige_autenticacao_e_falha_fechado(
        tmp_path, monkeypatch):
    m, client = _app(tmp_path, monkeypatch)

    assert client.get("/v1/operational-readiness").status_code == 401
    response = client.get("/v1/operational-readiness", headers=H)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema"] == "dz23.operational-readiness/1"
    assert body["escopo"] == "INQUILINO_AUTENTICADO"
    assert body["status"] == "blocked"
    assert body["modo_atual"] == "PAPER"
    assert body["invariantes"] == {
        "consulta_envia_ordens": False,
        "selo_de_codigo_live": False,
        "envio_real_habilitado": False,
        "exige_confirmacao_no_momento_da_acao": True,
    }
    assert body["dinheiro_real"]["pode_operar_real"] is False
    assert body["dinheiro_real"]["recuperacao_local"]["valida"] is False
    assert any(
        item["item"] == "Backup local restaurado e verificado recentemente"
        and item["pronto"] is False
        for item in body["dinheiro_real"]["checklist"])
    assert body["dinheiro_real"]["estado_de_liberacao"]["estado"] == (
        "preparacao_pendente")
    assert m.MESA_LIVE_LIBERADA is False


def test_operational_readiness_nao_expoe_segredos(tmp_path, monkeypatch):
    _, client = _app(tmp_path, monkeypatch)
    secrets = {
        "TRADINGAGENTS_EXCHANGE": "binance",
        "EXCHANGE_API_KEY": "chave-super-secreta",
        "EXCHANGE_API_SECRET": "segredo-super-secreto",
        "REAL_READ_ONLY_API_KEY": "leitura-super-secreta",
        "REAL_READ_ONLY_API_SECRET": "leitura-segredo-super-secreto",
    }
    assert client.post("/v1/config", headers=H, json={"vars": secrets}).status_code == 200

    text = client.get("/v1/operational-readiness", headers=H).text

    for value in secrets.values():
        if value != "binance":
            assert value not in text


def test_operational_readiness_recognizes_verified_local_restore(
        tmp_path, monkeypatch):
    _, client = _app(tmp_path, monkeypatch)
    from tradingagents.desk.backup import create_backup
    from tradingagents.desk.recovery import rehearse_local_restore
    archive = tmp_path / "backups" / "readiness.tar.gz"
    create_backup(tmp_path, archive)
    rehearse_local_restore(tmp_path, archive)

    body = client.get("/v1/operational-readiness", headers=H).json()

    assert body["dinheiro_real"]["recuperacao_local"]["valida"] is True
    item = next(
        item for item in body["dinheiro_real"]["checklist"]
        if item["item"] == "Backup local restaurado e verificado recentemente")
    assert item["pronto"] is True
    assert body["invariantes"]["envio_real_habilitado"] is False


def test_status_publico_nao_anuncia_mais_rota_ausente(tmp_path, monkeypatch):
    _, client = _app(tmp_path, monkeypatch)

    body = client.get("/v1/status/operacao").json()

    assert "/v1/operational-readiness" in json.dumps(body)
    assert "ainda não implementado" not in json.dumps(body, ensure_ascii=False)


def test_preflight_distingue_preparacao_de_live():
    payload = {
        "status": "blocked",
        "invariantes": {"selo_de_codigo_live": False,
                         "envio_real_habilitado": False},
        "dinheiro_real": {
            "pode_operar_real": False,
            "estado_de_liberacao": {
                "estado": "aguardando_liberacao_de_codigo",
                "pre_requisitos_sem_selo_concluidos": True,
            },
        },
    }

    prepared = avaliar(payload, "preparacao")
    live = avaliar(payload, "live")

    assert prepared["ok"] is True
    assert prepared["resultado"] == "preparado_sem_live"
    assert live["ok"] is False
    assert live["resultado"] == "live_bloqueado"


def test_preflight_recusa_payload_incompleto_ou_inseguro():
    assert avaliar({}, "preparacao")["ok"] is False
    assert avaliar({
        "invariantes": {"selo_de_codigo_live": True,
                         "envio_real_habilitado": True},
        "dinheiro_real": {
            "pode_operar_real": True,
            "estado_de_liberacao": {
                "estado": "liberado",
                "pre_requisitos_sem_selo_concluidos": True,
            },
        },
    }, "preparacao")["ok"] is False


def test_preflight_exige_token_apenas_no_ambiente(monkeypatch, capsys):
    monkeypatch.delenv("TOKEN_TESTE_DZ23", raising=False)

    code = preflight.main(["--token-env", "TOKEN_TESTE_DZ23"])

    assert code == 2
    assert "TOKEN_TESTE_DZ23" in capsys.readouterr().err


def test_preflight_salva_relatorio_sem_token(tmp_path, monkeypatch, capsys):
    token = "token-que-nao-pode-vazar"
    monkeypatch.setenv("TOKEN_TESTE_DZ23", token)
    payload = {
        "status": "blocked",
        "invariantes": {"selo_de_codigo_live": False,
                         "envio_real_habilitado": False},
        "dinheiro_real": {
            "pode_operar_real": False,
            "estado_de_liberacao": {
                "estado": "aguardando_liberacao_de_codigo",
                "pre_requisitos_sem_selo_concluidos": True,
            },
        },
    }

    def fake_get(url, recebido, _timeout):
        if url.endswith("/healthz"):
            assert recebido is None
            return {"status": "ok"}
        assert recebido == token
        return payload

    monkeypatch.setattr(preflight, "_get_json", fake_get)
    output = tmp_path / "evidence" / "real-readiness.json"

    code = preflight.main([
        "--base-url", "http://127.0.0.1:9999/",
        "--token-env", "TOKEN_TESTE_DZ23",
        "--target", "preparacao", "--output", str(output),
    ])

    stdout = capsys.readouterr().out
    persisted = output.read_text(encoding="utf-8")
    assert code == 0
    assert '"resultado": "preparado_sem_live"' in stdout
    assert token not in stdout and token not in persisted


"""AC-1/A3 — caderno global de experimentos consultável (débito FQ-3 #3).

O registro por trader já é exposto na sala individual
(`/v1/desk/traders/{id}/sala` → `experimentos`); faltava o caderno INTEIRO
numa só leitura: pré-registros, vereditos executados e o que mudou — o
relatório global da evolução como artefato consultável (§3/§13.7).

Rota `GET /v1/desk/evolucao`: LEITURA PURA (mesmo armazém da mesa, sem
novo estado), autenticada como o resto do produto, sem caminho de ordem.
"""
from __future__ import annotations

import importlib

H = {"Authorization": "Bearer s3nh4"}


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    for k in ("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
              "GOOGLE_API_KEY", "OPENAI_COMPATIBLE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    import service.app as m
    importlib.reload(m)
    m._FAILS.clear()
    m._DESKS.clear()
    return m


def test_caderno_global_e_rota_read_only(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    m = _app(tmp_path, monkeypatch)
    c = TestClient(m.app)
    r = c.get("/v1/desk/evolucao", headers=H)
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert set(corpo) >= {"traders", "experimentos", "aviso"}
    # o aviso da evolução viaja junto: triagem não é promoção
    assert "não" in corpo["aviso"].lower()


def test_sem_token_e_recusado(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    m = _app(tmp_path, monkeypatch)
    c = TestClient(m.app)
    assert c.get("/v1/desk/evolucao").status_code == 401


def test_caderno_recem_nascido_e_vazio_honesto(tmp_path, monkeypatch):
    """DATA_DIR novo ⇒ mesa nova ⇒ nenhum experimento pré-registrado. O
    zero é reportado como zero — experimento sem veredito não vira
    'aprovado' por omissão (o mesmo contrato da EvolucaoAutonoma)."""
    from fastapi.testclient import TestClient
    m = _app(tmp_path, monkeypatch)
    m._DESKS.clear()
    c = TestClient(m.app)
    r = c.get("/v1/desk/evolucao", headers=H)
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["experimentos"] == []

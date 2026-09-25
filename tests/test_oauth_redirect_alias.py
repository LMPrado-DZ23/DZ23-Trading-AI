"""Regressão: o redirect_uri anunciado ao provedor social é
/api/oauth/{provider}/callback, mas a rota canônica é /v1/auth/oauth/...
Sem o alias, o retorno do Google caía em 404 ('Not Found') e o login social
nunca completava. Estes testes garantem que o caminho anunciado existe e
espelha EXATAMENTE a handler canônica (mesma lógica, mesma segurança)."""
from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import service.app as m
    importlib.reload(m)
    return m, TestClient(m.app)


def test_api_callback_existe_e_espelha_a_canonica(tmp_path, monkeypatch):
    _, c = _app(tmp_path, monkeypatch)
    r_api = c.get("/api/oauth/google/callback")     # onde o Google retorna
    r_v1 = c.get("/v1/auth/oauth/google/callback")  # rota canônica
    # nunca o 404 de ROTA inexistente (era exatamente o bug)
    assert not (r_api.status_code == 404 and r_api.json().get("detail") == "Not Found")
    # paridade total com a canônica
    assert r_api.status_code == r_v1.status_code
    assert r_api.json() == r_v1.json()


def test_api_start_espelha_a_canonica(tmp_path, monkeypatch):
    _, c = _app(tmp_path, monkeypatch)
    assert (c.get("/api/oauth/google/start").status_code
            == c.get("/v1/auth/oauth/google/start").status_code)


def test_api_provedor_desconhecido_e_404_de_provedor(tmp_path, monkeypatch):
    _, c = _app(tmp_path, monkeypatch)
    r = c.get("/api/oauth/naoexiste/callback")
    assert r.status_code == 404
    assert "provedor" in r.json().get("detail", "").lower()

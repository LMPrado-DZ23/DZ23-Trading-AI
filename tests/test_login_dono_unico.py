"""Login do dono único: primeira configuração, hash Argon2id e recuperação.

A senha do dono deixa de ser comparada contra texto puro e passa a viver como
hash Argon2id em owner_credentials.json. Estes testes cobrem o contrato HTTP:
assistente de primeira execução (setup), login com hash, convivência com o
ambiente legado, recuperação por código e a trava de força-bruta compartilhada
entre login e recuperação.
"""
from __future__ import annotations

import importlib

from fastapi.testclient import TestClient

SENHA = "senha-do-dono-2026"
CURTA = "curta"


def _app(tmp_path, monkeypatch, env_password: str | None = None):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    if env_password is None:
        monkeypatch.delenv("ACCESS_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("ACCESS_PASSWORD", env_password)
    import service.app as m
    importlib.reload(m)
    m._FAILS.clear()
    return m


def _bearer(senha: str) -> dict:
    return {"Authorization": f"Bearer {senha}"}


class TestPrimeiraConfiguracao:
    def test_sem_senha_configurada_status_pede_configuracao(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch)
        d = TestClient(m.app).get("/v1/auth/status").json()
        assert d["setup_required"] is True
        assert d["multi_tenant"] is False
        assert d["live_trading"] is False

    def test_rota_protegida_sem_configuracao_responde_409(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch)
        r = TestClient(m.app).get("/v1/auth/me", headers=_bearer("qualquer"))
        assert r.status_code == 409

    def test_setup_cria_senha_e_devolve_codigo_uma_unica_vez(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        r = c.post("/v1/auth/setup", json={"senha": SENHA})
        assert r.status_code == 200
        codigo = r.json()["codigo_recuperacao"]
        assert len(codigo.split("-")) == 6
        assert c.get("/v1/auth/status").json()["setup_required"] is False
        # Repetir o assistente com credenciais no lugar: 409, nada muda.
        assert c.post("/v1/auth/setup", json={"senha": "outra-senha-2026"}).status_code == 409
        assert c.get("/v1/auth/me", headers=_bearer(SENHA)).status_code == 200
        assert c.get("/v1/auth/me", headers=_bearer("outra-senha-2026")).status_code == 401

    def test_setup_recusa_senha_curta(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch)
        r = TestClient(m.app).post("/v1/auth/setup", json={"senha": CURTA})
        assert r.status_code == 422
        assert "8" in r.json()["detail"]

    def test_setup_bloqueado_quando_ambiente_tem_senha(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch, env_password="senha-do-ambiente")
        r = TestClient(m.app).post("/v1/auth/setup", json={"senha": SENHA})
        assert r.status_code == 409


class TestLoginComHash:
    def test_login_com_senha_certa_e_errada(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        c.post("/v1/auth/setup", json={"senha": SENHA})
        assert c.get("/v1/auth/me", headers=_bearer(SENHA)).status_code == 200
        assert c.get("/v1/auth/me", headers=_bearer("senha-errada-2026")).status_code == 401

    def test_cinco_erros_trancam_o_ip_por_15_minutos(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        c.post("/v1/auth/setup", json={"senha": SENHA})
        for _ in range(m.MAX_FAILS):
            assert c.get("/v1/auth/me", headers=_bearer("senha-errada-2026")).status_code == 401
        # Certa também fica de fora durante a espera — a trava não dá bônus.
        assert c.get("/v1/auth/me", headers=_bearer(SENHA)).status_code == 429

    def test_hash_vence_ambiente_legado(self, tmp_path, monkeypatch):
        # Migrado: hash no disco E ACCESS_PASSWORD (legado) ainda no ar. Vale
        # o hash — o texto puro do ambiente não reabre a porta.
        m = _app(tmp_path, monkeypatch, env_password="senha-legada-do-ambiente")
        c = TestClient(m.app)
        from service.api.owner_credentials import CredenciaisDoDono
        CredenciaisDoDono(m.DATA / "owner_credentials.json").criar(SENHA)
        assert c.get("/v1/auth/me", headers=_bearer(SENHA)).status_code == 200
        assert c.get("/v1/auth/me", headers=_bearer("senha-legada-do-ambiente")).status_code == 401

    def test_ambiente_legado_continua_logando_sem_arquivo(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch, env_password="senha-do-ambiente")
        c = TestClient(m.app)
        assert c.get("/v1/auth/me", headers=_bearer("senha-do-ambiente")).status_code == 200
        assert c.get("/v1/auth/status").json()["setup_required"] is False


class TestRecuperacao:
    def _com_credenciais(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch)
        c = TestClient(m.app)
        codigo = c.post("/v1/auth/setup", json={"senha": SENHA}).json()["codigo_recuperacao"]
        return m, c, codigo

    def test_recuperar_troca_senha_e_devolve_codigo_novo(self, tmp_path, monkeypatch):
        m, c, codigo = self._com_credenciais(tmp_path, monkeypatch)
        r = c.post("/v1/auth/recover", json={"codigo": codigo, "nova_senha": "senha-nova-2026"})
        assert r.status_code == 200
        novo = r.json()["codigo_recuperacao"]
        assert novo and novo != codigo
        assert c.get("/v1/auth/me", headers=_bearer("senha-nova-2026")).status_code == 200
        assert c.get("/v1/auth/me", headers=_bearer(SENHA)).status_code == 401
        # Código antigo morreu; o novo vale.
        assert c.post("/v1/auth/recover",
                      json={"codigo": codigo, "nova_senha": "outra-senha-2027"}).status_code == 401
        assert c.post("/v1/auth/recover",
                      json={"codigo": novo, "nova_senha": "outra-senha-2027"}).status_code == 200

    def test_codigo_errado_alimenta_a_trava(self, tmp_path, monkeypatch):
        m, c, _ = self._com_credenciais(tmp_path, monkeypatch)
        errado = "AAAAA-BBBBB-CCCCC-DDDDD-EEEEE-FFFFF"
        for _ in range(m.MAX_FAILS):
            assert c.post("/v1/auth/recover",
                          json={"codigo": errado, "nova_senha": "senha-nova-2026"}).status_code == 401
        assert c.post("/v1/auth/recover",
                      json={"codigo": errado, "nova_senha": "senha-nova-2026"}).status_code == 429
        # A trava também segura o LOGIN: mesmo poço para os dois caminhos.
        assert c.get("/v1/auth/me", headers=_bearer(SENHA)).status_code == 429

    def test_recuperar_com_senha_curta_da_422_sem_gastar_tentativa(self, tmp_path, monkeypatch):
        m, c, codigo = self._com_credenciais(tmp_path, monkeypatch)
        r = c.post("/v1/auth/recover", json={"codigo": codigo, "nova_senha": CURTA})
        assert r.status_code == 422
        assert c.get("/v1/auth/me", headers=_bearer(SENHA)).status_code == 200

    def test_recuperar_sem_credenciais_da_409(self, tmp_path, monkeypatch):
        m = _app(tmp_path, monkeypatch)
        r = TestClient(m.app).post("/v1/auth/recover",
                                   json={"codigo": "x", "nova_senha": "senha-nova-2026"})
        assert r.status_code == 409

    def test_gerar_novo_codigo_exige_sessao_e_invalida_o_antigo(self, tmp_path, monkeypatch):
        m, c, codigo = self._com_credenciais(tmp_path, monkeypatch)
        assert c.post("/v1/auth/recovery-code").status_code == 401
        r = c.post("/v1/auth/recovery-code", headers=_bearer(SENHA))
        assert r.status_code == 200
        novo = r.json()["codigo_recuperacao"]
        assert novo != codigo
        assert c.post("/v1/auth/recover",
                      json={"codigo": codigo, "nova_senha": "senha-nova-2026"}).status_code == 401
        assert c.post("/v1/auth/recover",
                      json={"codigo": novo, "nova_senha": "senha-nova-2026"}).status_code == 200

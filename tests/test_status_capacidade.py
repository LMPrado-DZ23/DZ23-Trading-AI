"""Status público de capacidade: parsing estrito e sem dado de inquilino.

`bool(os.environ.get("DEMO_TRADING"))` liga o modo com QUALQUER texto —
"false", "0" e "nao" incluídos. Num campo que decide se ordens vão a uma
corretora, o erro cai do lado errado.

E este endpoint é PÚBLICO: ele responde o que a plataforma oferece, não o
que um inquilino pode fazer. Confundir os dois vaza estado de cliente para
quem não fez login.
"""
from __future__ import annotations

import importlib
import json

import pytest
from fastapi.testclient import TestClient


def app_com(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("ACCESS_PASSWORD", "s3nh4-de-teste-1234")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    import service.app as m
    importlib.reload(m)
    return m


def status(m):
    return TestClient(m.app).get("/v1/status/operacao").json()


class TestParsingEstrito:
    @pytest.mark.parametrize("valor", ["false", "False", "FALSE", "0", "no",
                                       "nao", "não", "off", "", "  "])
    def test_negatives_keep_demo_disabled(self, valor, monkeypatch, tmp_path):
        m = app_com(monkeypatch, tmp_path, DEMO_TRADING=valor)
        assert status(m)["modos"]["DEMO"]["habilitado"] is False, (
            f"{valor!r} ligou o modo DEMO")

    @pytest.mark.parametrize("valor", ["true", "True", "TRUE", "1", "yes",
                                       "sim", "on"])
    def test_positives_enable_demo(self, valor, monkeypatch, tmp_path):
        m = app_com(monkeypatch, tmp_path, DEMO_TRADING=valor)
        assert status(m)["modos"]["DEMO"]["habilitado"] is True, (
            f"{valor!r} não ligou o modo DEMO")

    @pytest.mark.parametrize("valor", ["talvez", "2", "sim!", "ligado?",
                                       "TRUEE", "enable"])
    def test_garbage_fails_closed(self, valor, monkeypatch, tmp_path):
        """Valor que ninguém previu não vira 'sim'."""
        m = app_com(monkeypatch, tmp_path, DEMO_TRADING=valor)
        assert status(m)["modos"]["DEMO"]["habilitado"] is False, (
            f"{valor!r} foi interpretado como verdadeiro")

    def test_absent_is_disabled(self, monkeypatch, tmp_path):
        m = app_com(monkeypatch, tmp_path, DEMO_TRADING=None)
        assert status(m)["modos"]["DEMO"]["habilitado"] is False

    def test_the_parser_is_not_bool_of_string(self):
        import inspect

        import service.app as m
        fonte = inspect.getsource(m.status_operacao)
        assert 'bool(os.environ.get("DEMO_TRADING"))' not in fonte
        assert "flag_ligada" in fonte or "_flag" in fonte


class TestEscopoPublico:
    PROIBIDOS = ("tenant", "account", "saldo", "balance", "order", "ordem",
                 "secret", "token", "password", "api_key", "reservation",
                 "position", "posicao", "incident")

    @staticmethod
    def _chaves(no, prefixo=""):
        """Todas as CHAVES do documento, recursivamente.

        Checar o texto inteiro daria falso positivo em prosa explicativa
        ('sem ordem na corretora'); o que não pode existir é CAMPO de dado
        de inquilino.
        """
        if isinstance(no, dict):
            for k, v in no.items():
                yield f"{prefixo}{k}"
                yield from TestEscopoPublico._chaves(v, f"{prefixo}{k}.")
        elif isinstance(no, list):
            for item in no:
                yield from TestEscopoPublico._chaves(item, prefixo)

    def test_the_payload_has_no_tenant_or_money_fields(self, monkeypatch,
                                                       tmp_path):
        m = app_com(monkeypatch, tmp_path)
        d = status(m)
        chaves = {c.lower() for c in self._chaves(d)}
        for termo in self.PROIBIDOS:
            vazou = [c for c in chaves if termo in c]
            assert not vazou, (
                f"o status público expõe o campo {vazou}: ele responde o que "
                "a PLATAFORMA oferece, não o que um inquilino tem")

    def test_no_value_looks_like_an_identifier_or_secret(self, monkeypatch,
                                                         tmp_path):
        """Valores também não podem carregar id de inquilino nem segredo."""
        m = app_com(monkeypatch, tmp_path)
        texto = json.dumps(status(m), ensure_ascii=False)
        for suspeito in ("sb_secret", "eyJ", "Bearer ", "postgresql://",
                         "conta-", "tenant-"):
            assert suspeito not in texto, f"valor suspeito: {suspeito}"

    def test_it_answers_without_authentication(self, monkeypatch, tmp_path):
        m = app_com(monkeypatch, tmp_path)
        r = TestClient(m.app).get("/v1/status/operacao")
        assert r.status_code == 200

    def test_it_declares_what_it_is_not(self, monkeypatch, tmp_path):
        """Quem lê precisa saber que isto NÃO é prova de prontidão."""
        m = app_com(monkeypatch, tmp_path)
        d = status(m)
        assert d["escopo"] == "PLATAFORMA"
        assert "nao_e" in d or "limitacao" in d
        alvo = json.dumps(d, ensure_ascii=False).lower()
        assert "readiness" in alvo or "prontidão" in alvo

    def test_the_future_authenticated_endpoint_is_named(self, monkeypatch,
                                                        tmp_path):
        m = app_com(monkeypatch, tmp_path)
        assert "/v1/operational-readiness" in json.dumps(status(m))


class TestModoAtualEhPolitica:
    def test_it_says_the_mode_comes_from_policy_not_detection(self, monkeypatch,
                                                              tmp_path):
        m = app_com(monkeypatch, tmp_path)
        d = status(m)
        assert d["modo_atual"] == "PAPER"
        assert d["modo_atual_origem"] == "POLITICA_ATUAL", (
            "sem isto, alguém lê 'PAPER' como detecção automática do mandato")


class TestPainelCarregaAposLogin:
    def test_enter_app_loads_the_state(self):
        from pathlib import Path
        html = Path("service/static/index.html").read_text(encoding="utf-8")
        i = html.index("function enterApp")
        corpo = html[i:i + 700]
        assert "carregarEstadoOperacional" in corpo, (
            "quem entra pelo login normal nunca via o estado operacional")

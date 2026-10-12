"""O produto não pode afirmar mais do que sustenta.

"Paper Ready", "Auditado" e "Seguro" são promessas verificáveis. Enquanto a
auditoria estiver aberta, escrevê-las na tela é criar uma dívida com o
cliente que o sistema não paga. E o estado operacional precisa vir do
servidor: texto fixo envelhece na direção perigosa — convidando a operar
algo que está fechado.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

APP = Path("service/static/index.html").read_text(encoding="utf-8")
SITE = Path("service/static/site.html").read_text(encoding="utf-8")


def cliente():
    import service.app as m
    return TestClient(m.app)


class TestIdentidadeDoProduto:
    def test_mt5_is_not_the_product_identity(self):
        """MT5 é um adaptador entre vários, não o que a plataforma é."""
        assert "MT5" not in APP, "MT5 voltou a descrever o produto"

    def test_the_subtitle_describes_the_real_scope(self):
        for termo in ("multiagente", "Paper Trading", "Shadow Trading",
                      "execução governada", "corretoras"):
            assert termo in APP, f"falta {termo} na descrição"

    def test_the_positioning_line_is_kept(self):
        assert "Inteligência coordenada" in APP
        assert "Risco governado" in APP


class TestBadges:
    def test_the_new_badges_are_there(self):
        for badge in ("PAPER MODE", "MULTI-TENANT", "EXECUÇÃO GOVERNADA"):
            assert badge in APP, f"falta a badge {badge}"

    def test_the_old_badges_are_gone(self):
        for badge in ("PAPER READY", "LIVE BLOCKED"):
            assert badge not in APP, f"{badge} continua na tela"


class TestAfirmacoesProibidas:
    PROIBIDAS = ("paper ready", "production ready", "live ready",
                 "sistema seguro", "plataforma segura", "auditado",
                 "totalmente auditado", "pronto para produção")

    def test_the_app_never_claims_what_is_not_proven(self):
        baixo = APP.lower()
        for frase in self.PROIBIDAS:
            assert frase not in baixo, f"afirmação sem lastro: {frase}"

    def test_the_public_site_never_claims_it_either(self):
        baixo = SITE.lower()
        for frase in self.PROIBIDAS:
            assert frase not in baixo, f"afirmação sem lastro: {frase}"


class TestEstadoVemDoBackend:
    def test_the_panel_asks_the_server(self):
        assert "/v1/status/operacao" in APP
        assert "carregarEstadoOperacional" in APP

    def test_the_state_is_not_hardcoded_in_the_panel(self):
        assert "Dinheiro real bloqueado" not in APP, (
            "o estado voltou a ser texto fixo")

    def test_the_endpoint_reports_the_real_state(self):
        d = cliente().get("/v1/status/operacao").json()
        assert d["operacao_real"]["disponivel"] is False
        assert d["operacao_real"]["estado"] == "BLOQUEADA"
        assert "auditoria" in d["operacao_real"]["motivo"]

    def test_the_reason_is_specific_not_generic(self):
        motivo = cliente().get("/v1/status/operacao").json()["operacao_real"]["motivo"]
        assert motivo.strip() and motivo != "indisponível", (
            "motivo genérico não ajuda ninguém a decidir")


class TestArquiteturaDeModos:
    def test_the_four_modes_exist(self):
        modos = cliente().get("/v1/status/operacao").json()["modos"]
        assert set(modos) == {"PAPER", "SHADOW", "DEMO", "LIVE"}

    def test_live_remains_a_declared_capability(self):
        """Sumir com LIVE esconderia o roteiro; dizer que está pronto seria
        mentir. Ele fica declarado e indisponível."""
        modos = cliente().get("/v1/status/operacao").json()["modos"]
        assert "LIVE" in modos
        assert modos["LIVE"]["habilitado"] is False
        assert modos["LIVE"]["descricao"]

    def test_paper_and_shadow_are_available(self):
        modos = cliente().get("/v1/status/operacao").json()["modos"]
        assert modos["PAPER"]["habilitado"] is True
        assert modos["SHADOW"]["habilitado"] is True

    def test_the_gates_say_what_is_missing(self):
        gates = cliente().get("/v1/status/operacao").json()["gates"]
        assert gates, "sem gates, ninguém sabe o que falta"
        for g in gates:
            assert g["status"] == "EM_AUDITORIA"
            assert g["detalhe"], "gate sem detalhe não informa nada"

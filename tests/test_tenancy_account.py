"""Criação de conta ATÔMICA: tenant + usuário + vínculo, ou NADA.

Um cadastro que cria o tenant e falha ao criar o usuário deixa lixo órfão e
dados meio-criados. Aqui a criação é uma transação: falhou, desfez tudo.
"""
from __future__ import annotations

import pytest

from tradingagents.tenancy import AuthError, TenantStore

CHAVE = "x" * 40  # >= 32 exigido pelo store


def _store(tmp_path) -> TenantStore:
    return TenantStore(tmp_path / "t.db", signing_key=CHAVE)


class TestCriacaoAtomica:
    def test_cria_tenant_usuario_e_vinculo_de_uma_vez(self, tmp_path):
        st = _store(tmp_path)
        tid, uid = st.create_account("Agência A", "dono@a.com", "senhabemgrande")
        assert tid.startswith("ten_") and uid.startswith("usr_")
        assert st.tenant_count() == 1
        # o vínculo existe e permite login imediato
        token = st.login("dono@a.com", "senhabemgrande", tid)
        assert st.authenticate(token).tenant_id == tid

    def test_email_repetido_nao_cria_tenant_orfao(self, tmp_path):
        st = _store(tmp_path)
        st.create_account("Agência A", "dono@a.com", "senhabemgrande")
        assert st.tenant_count() == 1
        # segundo cadastro com o MESMO e-mail: falha e NÃO deixa tenant novo
        with pytest.raises(AuthError):
            st.create_account("Agência B", "dono@a.com", "outrasenhaforte")
        assert st.tenant_count() == 1                # rollback provado

    def test_mensagem_e_generica_anti_enumeracao(self, tmp_path):
        st = _store(tmp_path)
        st.create_account("Agência A", "dono@a.com", "senhabemgrande")
        with pytest.raises(AuthError) as exc:
            st.create_account("Agência B", "dono@a.com", "outrasenhaforte")
        # não confirma "e-mail já existe": mensagem neutra
        assert "existe" not in str(exc.value).lower()
        assert "já tem" not in str(exc.value).lower()

    def test_senha_curta_barra_antes_de_qualquer_insert(self, tmp_path):
        st = _store(tmp_path)
        with pytest.raises(AuthError):
            st.create_account("Agência A", "dono@a.com", "curta")
        assert st.tenant_count() == 0               # nada foi criado

    def test_papel_invalido_nao_cria_nada(self, tmp_path):
        st = _store(tmp_path)
        with pytest.raises(AuthError):
            st.create_account("Agência A", "dono@a.com", "senhabemgrande",
                              role="deus")
        assert st.tenant_count() == 0

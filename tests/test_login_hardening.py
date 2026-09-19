"""Rate limit de login PERSISTENTE (§4): sobrevive a restart e é compartilhado
entre instâncias (está no banco de identidade, não em RAM)."""
from __future__ import annotations

from tradingagents.tenancy import LOGIN_MAX_FAILS, TenantStore

CHAVE = "x" * 40


def _store(path) -> TenantStore:
    return TenantStore(path, signing_key=CHAVE)


class TestRateLimitPersistente:
    def test_bloqueia_apos_o_limite(self, tmp_path):
        db = tmp_path / "id.db"
        st = _store(db)
        assert st.login_lock_seconds("1.2.3.4", "a@a.com") == 0
        for _ in range(LOGIN_MAX_FAILS):
            st.record_login_failure("1.2.3.4", "a@a.com")
        assert st.login_lock_seconds("1.2.3.4", "a@a.com") > 0

    def test_lock_sobrevive_a_restart(self, tmp_path):
        db = tmp_path / "id.db"
        st = _store(db)
        for _ in range(LOGIN_MAX_FAILS):
            st.record_login_failure("1.2.3.4", "a@a.com")
        # "restart": nova instância apontando pro MESMO arquivo ainda vê o lock.
        st2 = _store(db)
        assert st2.login_lock_seconds("1.2.3.4", "a@a.com") > 0

    def test_sucesso_limpa_o_contador(self, tmp_path):
        db = tmp_path / "id.db"
        st = _store(db)
        for _ in range(LOGIN_MAX_FAILS):
            st.record_login_failure("1.2.3.4", "a@a.com")
        st.clear_login_failures("1.2.3.4", "a@a.com")
        assert st.login_lock_seconds("1.2.3.4", "a@a.com") == 0

    def test_chave_normaliza_email_e_separa_ip(self, tmp_path):
        st = _store(tmp_path / "id.db")
        # o mesmo e-mail (case-insensitive) e IP => mesma chave; IP diferente separa
        assert st._login_key("1.1.1.1", "A@A.com") == st._login_key("1.1.1.1", "a@a.com")
        assert st._login_key("1.1.1.1", "a@a.com") != st._login_key("2.2.2.2", "a@a.com")

    def test_contas_diferentes_nao_compartilham_bloqueio(self, tmp_path):
        st = _store(tmp_path / "id.db")
        for _ in range(LOGIN_MAX_FAILS):
            st.record_login_failure("1.2.3.4", "vitima@a.com")
        # outra identidade no mesmo IP não é bloqueada pelo ataque à vítima
        assert st.login_lock_seconds("1.2.3.4", "outro@a.com") == 0


class TestStepUpTokens:
    """§1: token de step-up aleatório, escopado, curto, single-use, hasheado."""

    def test_valido_consumido_uma_vez(self, tmp_path):
        st = _store(tmp_path / "id.db")
        tok = st.create_step_up("t", "u", "export")
        assert st.consume_step_up(tok, "t", "u", "export") is True
        assert st.consume_step_up(tok, "t", "u", "export") is False   # single-use

    def test_escopo_errado_falha(self, tmp_path):
        st = _store(tmp_path / "id.db")
        tok = st.create_step_up("t", "u", "export")
        assert st.consume_step_up(tok, "t", "u", "import") is False
        assert st.consume_step_up(tok, "outro", "u", "export") is False
        assert st.consume_step_up(tok, "t", "outro", "export") is False
        # não foi consumido pelos escopos errados: o correto ainda passa uma vez
        assert st.consume_step_up(tok, "t", "u", "export") is True

    def test_expirado_falha(self, tmp_path):
        st = _store(tmp_path / "id.db")
        tok = st.create_step_up("t", "u", "export", ttl_seconds=-1)   # já expirado
        assert st.consume_step_up(tok, "t", "u", "export") is False

    def test_token_guardado_hasheado_nao_em_claro(self, tmp_path):
        import sqlite3
        db = tmp_path / "id.db"
        st = _store(db)
        tok = st.create_step_up("t", "u", "export")
        with sqlite3.connect(db) as con:
            linhas = con.execute("SELECT token_hash FROM step_up_tokens").fetchall()
        assert linhas and all(tok not in row[0] for row in linhas)   # nunca em claro

    def test_vinculado_a_sessao(self, tmp_path):
        # §5: token emitido para a sessão A NÃO vale em OUTRA sessão (session binding).
        st = _store(tmp_path / "id.db")
        tok = st.create_step_up("t", "u", "export", session_id="sesA")
        assert st.consume_step_up(tok, "t", "u", "export", session_id="sesB") is False
        assert st.consume_step_up(tok, "t", "u", "export", session_id="sesA") is True

    def test_revogacao_por_sessao(self, tmp_path):
        # §5: logout/reset revoga os tokens daquela sessão.
        st = _store(tmp_path / "id.db")
        tok = st.create_step_up("t", "u", "export", session_id="sesA")
        st.revoke_step_up_for_session("sesA")
        assert st.consume_step_up(tok, "t", "u", "export", session_id="sesA") is False

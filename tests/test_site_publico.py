"""Site público e ver senha.

A raiz era o formulário de login: quem chegava pelo domínio via um campo de
senha sem saber o que o produto faz. O painel pessoal tem um único campo de
senha — a do dono — e nenhuma superfície de cadastro ou login social.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

SITE = Path("service/static/site.html").read_text(encoding="utf-8")
APP = Path("service/static/index.html").read_text(encoding="utf-8")


def cliente():
    import service.app as m
    return TestClient(m.app)


class TestRotas:
    def test_the_root_serves_the_public_page(self):
        html = cliente().get("/").text
        assert "Como funciona" in html
        assert 'id="loginForm"' not in html, "a raiz virou formulário de login"

    def test_the_app_is_served_at_app(self):
        html = cliente().get("/app").text
        assert 'id="authGate"' in html

    def test_the_public_page_points_to_the_app(self):
        assert 'href="/app"' in SITE

    def test_both_carry_the_brand(self):
        for html in (SITE, APP):
            assert "/static/marca/favicon" in html
            assert "dz23-logo-192.png" in html


class TestVerSenha:
    def test_every_password_field_has_a_toggle(self):
        import re
        campos = re.findall(r'<input[^>]*type="password"[^>]*id="(\w+)"', APP)
        campos += re.findall(r'<input[^>]*id="(\w+)"[^>]*type="password"', APP)
        do_acesso = [c for c in campos if c in ("loginPassword",)]
        assert do_acesso, "o cenário precisa encontrar os campos"
        for campo in do_acesso:
            assert f'data-alvo="{campo}"' in APP, f"{campo} sem botão de ver"

    def test_the_toggle_switches_the_type_and_the_label(self):
        assert "function verSenha" in APP
        assert "campo.type=oculto?'text':'password'" in APP
        assert "aria-label" in APP, "sem rótulo o botão some para leitor de tela"

    def test_each_field_has_exactly_one_toggle(self):
        """Dois botões para o mesmo campo apareceram na tela como olhos
        soltos — a regex de inserção rodou duas vezes."""
        import re
        alvos = re.findall(r'data-alvo="(\w+)"', APP)
        duplicados = {a for a in alvos if alvos.count(a) > 1}
        assert not duplicados, f"campos com olho duplicado: {duplicados}"

    def test_no_toggle_floats_outside_its_field(self):
        assert '</div><button type="button" class="pwd-eye"' not in APP, (
            "há botão de olho fora do bloco do campo: ele aparece solto")

    def test_the_only_auth_field_left_is_the_owner_password(self):
        """No app do dono único sobrou um campo: a senha do dono. Campos de
        bootstrap/cadastro/usuário eram do SaaS e não existem mais."""
        assert 'id="loginPassword"' in APP, "o campo da senha do dono sumiu"
        assert 'data-alvo="loginPassword"' in APP, "loginPassword sem botão de ver"
        for campo in ("bootPassword", "bootToken", "suSenha", "newUserPassword"):
            assert f'id="{campo}"' not in APP, f"{campo} reapareceu na tela"

    def test_the_toggle_is_a_button_not_a_submit(self):
        assert 'type="button" class="pwd-eye"' in APP, (
            "sem type=button o clique enviaria o formulário")


class TestLoginSocialRemovido:
    """O login social era superfície de cadastro do SaaS. No app do dono
    único ele saiu do DOM por completo — não basta esconder o botão."""

    def test_the_social_login_dom_is_gone(self):
        for needle in ("carregarProvedores", "socialButtons", 'id="socialBox"',
                       "MARCAS", "/v1/auth/oauth"):
            assert needle not in APP, f"{needle} continua no painel"

    def test_the_start_endpoint_is_disabled_in_the_personal_app(self):
        assert cliente().get("/v1/auth/oauth/google/start").status_code == 404

    def test_the_providers_endpoint_is_disabled_in_the_personal_app(self):
        assert cliente().get("/v1/auth/oauth/providers").status_code == 404

    def test_google_is_a_known_provider_in_the_backend(self):
        from tradingagents.identity.oauth import PROVIDERS
        assert "google" in PROVIDERS
        assert PROVIDERS["google"].client_id_env == "GOOGLE_CLIENT_ID"


class TestConteudoQueVende:
    def test_it_says_what_the_product_does(self):
        for termo in ("Como funciona", "Planos", "Paper", "risco"):
            assert termo in SITE, f"falta {termo}"

    def test_it_states_the_limits_instead_of_hiding_them(self):
        """Prometer rentabilidade seria o caminho fácil — e desonesto."""
        assert "Não prometemos rentabilidade" in SITE
        assert "Dinheiro real está bloqueado" in SITE or \
               "DINHEIRO REAL BLOQUEADO" in SITE

    def test_it_never_promises_returns(self):
        proibidos = ("lucro garantido", "rentabilidade garantida",
                     "ganhos garantidos", "sem risco")
        baixo = SITE.lower()
        for frase in proibidos:
            assert frase not in baixo, f"promessa indevida: {frase}"

    def test_the_risk_warning_is_present(self):
        assert "risco de perda" in SITE.lower()
        assert "não é recomendação de investimento" in SITE.lower()

    def test_it_works_on_a_phone(self):
        assert "width=device-width" in SITE
        assert "@media(max-width:760px)" in SITE

    def test_it_has_sharing_metadata(self):
        for tag in ('property="og:title"', 'property="og:image"',
                    'name="description"'):
            assert tag in SITE, f"falta {tag}"

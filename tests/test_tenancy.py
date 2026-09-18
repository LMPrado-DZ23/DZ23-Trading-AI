import pytest

from tradingagents.tenancy import AuthError, TenantStore


def test_tenant_login_and_role_permissions(tmp_path):
    store = TenantStore(tmp_path / "identity.db", signing_key="x" * 48)
    tenant_a = store.create_tenant("A")
    tenant_b = store.create_tenant("B")
    user = store.create_user("user@example.com", "correct horse battery staple")
    store.add_member(tenant_a, user, "operator")
    store.add_member(tenant_b, user, "viewer")

    operator = store.authenticate(store.login("user@example.com", "correct horse battery staple", tenant_a))
    viewer = store.authenticate(store.login("user@example.com", "correct horse battery staple", tenant_b))
    assert operator.tenant_id == tenant_a
    assert operator.can("paper") and operator.can("demo")
    assert viewer.tenant_id == tenant_b
    assert viewer.can("read") and not viewer.can("paper")


def test_invalid_tenant_and_revoked_session_are_rejected(tmp_path):
    store = TenantStore(tmp_path / "identity.db", signing_key="y" * 48)
    tenant = store.create_tenant("A")
    store.create_tenant("B")
    user = store.create_user("user@example.com", "correct horse battery staple")
    store.add_member(tenant, user, "admin")
    with pytest.raises(AuthError):
        store.login("user@example.com", "wrong password", tenant)
    token = store.login("user@example.com", "correct horse battery staple", tenant)
    store.revoke(token)
    with pytest.raises(AuthError):
        store.authenticate(token)


def test_short_signing_key_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="32"):
        TenantStore(tmp_path / "identity.db", signing_key="short")


def test_login_by_email_sem_tenant_um_workspace(tmp_path):
    """Login sem Tenant ID: um único workspace → token direto."""
    store = TenantStore(tmp_path / "identity.db", signing_key="z" * 48)
    t = store.create_tenant("Acme")
    u = store.create_user("user@example.com", "correct horse battery staple")
    store.add_member(t, u, "admin")
    r = store.login_by_email("user@example.com", "correct horse battery staple")
    assert r["tenant_id"] == t
    p = store.authenticate(r["token"])
    assert p.tenant_id == t and p.email == "user@example.com"


def test_login_by_email_multiplos_workspaces_pede_selecao(tmp_path):
    """Vários workspaces → devolve a seleção (após senha correta, sem vazar)."""
    store = TenantStore(tmp_path / "identity.db", signing_key="z" * 48)
    a = store.create_tenant("Acme")
    b = store.create_tenant("Beta")
    u = store.create_user("user@example.com", "correct horse battery staple")
    store.add_member(a, u, "admin")
    store.add_member(b, u, "viewer")
    r = store.login_by_email("user@example.com", "correct horse battery staple")
    assert "token" not in r
    ids = {w["tenant_id"] for w in r["select"]}
    assert ids == {a, b}


def test_login_by_email_anti_enumeracao(tmp_path):
    """E-mail inexistente e senha errada dão o MESMO erro genérico."""
    store = TenantStore(tmp_path / "identity.db", signing_key="z" * 48)
    t = store.create_tenant("Acme")
    u = store.create_user("real@example.com", "correct horse battery staple")
    store.add_member(t, u, "admin")
    with pytest.raises(AuthError, match="credenciais inválidas"):
        store.login_by_email("nao-existe@example.com", "qualquer coisa 123456")
    with pytest.raises(AuthError, match="credenciais inválidas"):
        store.login_by_email("real@example.com", "senha errada aqui 123456")

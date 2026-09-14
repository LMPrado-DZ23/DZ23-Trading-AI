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

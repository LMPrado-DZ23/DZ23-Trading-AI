from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable

from tradingagents.platform.identity import (
    AuditEvent,
    Base,
    EmailVerificationToken,
    IdentityService,
    Membership,
    MembershipRole,
    PasswordManager,
    PasswordResetToken,
    TokenHasher,
    User,
    UserSession,
)
from tradingagents.platform.identity.service import (
    AuthenticationFailed,
    AuthorizationDenied,
    Conflict,
    InvalidToken,
)

PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "new correct horse battery staple"


@pytest.fixture
def identity():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    service = IdentityService(factory, token_hasher=TokenHasher(b"p" * 32))
    return service, factory


def register(identity, *, slug: str = "acme", email: str = "owner@example.com"):
    service, _factory = identity
    return service.register_tenant(
        tenant_name=f"Tenant {slug}",
        tenant_slug=slug,
        owner_email=email,
        display_name="Test Owner",
        password=PASSWORD,
        client_ip="127.0.0.1",
    )


def test_registration_persists_argon2id_and_tenant_scoped_owner(identity):
    service, factory = identity
    result = register(identity)

    with factory() as db:
        user = db.get(User, result.user_id)
        membership = db.get(Membership, result.membership_id)
        audit = db.scalar(select(AuditEvent))

    assert user is not None
    assert user.password_hash.startswith("$argon2id$")
    assert PASSWORD not in user.password_hash
    assert membership is not None
    assert membership.tenant_id == result.tenant_id
    assert membership.role == MembershipRole.OWNER.value
    assert audit is not None
    assert audit.tenant_id == result.tenant_id
    assert audit.event_type == "tenant.registered"

    issued = service.authenticate(
        tenant_id=result.tenant_id,
        email=" OWNER@EXAMPLE.COM ",
        password=PASSWORD,
    )
    assert issued.tenant_id == result.tenant_id


def test_account_lock_survives_ip_rotation_and_resets_after_expiry(identity) -> None:
    service, factory = identity
    result = register(identity)
    for index in range(5):
        with pytest.raises(AuthenticationFailed):
            service.authenticate(
                tenant_id=result.tenant_id,
                email="owner@example.com",
                password="wrong-password",
                client_ip=f"198.51.100.{index}",
            )

    with pytest.raises(AuthenticationFailed):
        service.authenticate(
            tenant_id=result.tenant_id,
            email="owner@example.com",
            password=PASSWORD,
            client_ip="203.0.113.50",
        )

    with factory() as db, db.begin():
        user = db.get(User, result.user_id)
        assert user is not None
        assert user.failed_login_attempts == 5
        assert user.auth_locked_until is not None
        user.auth_locked_until = datetime.now(UTC) - timedelta(seconds=1)

    service.authenticate(
        tenant_id=result.tenant_id,
        email="owner@example.com",
        password=PASSWORD,
    )
    with factory() as db:
        user = db.get(User, result.user_id)
        assert user is not None
        assert user.failed_login_attempts == 0
        assert user.auth_locked_until is None


def test_session_is_opaque_hashed_tenant_bound_and_revocable(identity):
    service, factory = identity
    first = register(identity, slug="first", email="first@example.com")
    second = register(identity, slug="second", email="second@example.com")
    issued = service.authenticate(
        tenant_id=first.tenant_id,
        email="first@example.com",
        password=PASSWORD,
    )
    assert str(first.tenant_id) == issued.token.partition(".")[0]

    assert issued.token not in repr(issued)
    with factory() as db:
        stored = db.get(UserSession, issued.session_id)
    assert stored is not None
    assert stored.token_hash != issued.token
    assert issued.token not in stored.token_hash
    assert len(stored.token_hash) == 64

    principal = service.authenticate_session(tenant_id=first.tenant_id, token=issued.token)
    assert principal.role is MembershipRole.OWNER
    with pytest.raises(AuthenticationFailed):
        service.authenticate_session(tenant_id=second.tenant_id, token=issued.token)

    service.revoke_session(principal=principal, session_id=issued.session_id)
    with pytest.raises(AuthenticationFailed):
        service.authenticate_session(tenant_id=first.tenant_id, token=issued.token)


def test_password_reset_is_one_time_hashed_and_revokes_sessions(identity):
    service, factory = identity
    result = register(identity)
    session = service.authenticate(
        tenant_id=result.tenant_id,
        email="owner@example.com",
        password=PASSWORD,
    )
    reset = service.issue_password_reset(
        tenant_id=result.tenant_id,
        email="owner@example.com",
    )
    assert reset is not None
    assert reset.token not in repr(reset)

    with factory() as db:
        stored = db.get(PasswordResetToken, reset.reset_id)
    assert stored is not None
    assert stored.token_hash != reset.token

    service.consume_password_reset(
        tenant_id=result.tenant_id,
        token=reset.token,
        new_password=NEW_PASSWORD,
    )
    with pytest.raises(InvalidToken):
        service.consume_password_reset(
            tenant_id=result.tenant_id,
            token=reset.token,
            new_password="another secure password",
        )
    with pytest.raises(AuthenticationFailed):
        service.authenticate_session(tenant_id=result.tenant_id, token=session.token)
    with pytest.raises(AuthenticationFailed):
        service.authenticate(
            tenant_id=result.tenant_id,
            email="owner@example.com",
            password=PASSWORD,
        )

    replacement = service.authenticate(
        tenant_id=result.tenant_id,
        email="owner@example.com",
        password=NEW_PASSWORD,
    )
    assert replacement.user_id == result.user_id


def test_expired_reset_is_rejected_without_changing_password(identity):
    service, _factory = identity
    result = register(identity)
    reset = service.issue_password_reset(
        tenant_id=result.tenant_id,
        email="owner@example.com",
        ttl=timedelta(microseconds=1),
    )
    assert reset is not None
    with pytest.raises(InvalidToken):
        service.consume_password_reset(
            tenant_id=result.tenant_id,
            token=reset.token,
            new_password=NEW_PASSWORD,
        )

    issued = service.authenticate(
        tenant_id=result.tenant_id,
        email="owner@example.com",
        password=PASSWORD,
    )
    assert issued.user_id == result.user_id


def test_unknown_account_has_generic_authentication_failure(identity):
    service, _factory = identity
    result = register(identity)
    with pytest.raises(AuthenticationFailed, match="invalid credentials"):
        service.authenticate(
            tenant_id=result.tenant_id,
            email="missing@example.com",
            password=PASSWORD,
        )


def test_rbac_and_cross_tenant_session_revocation_fail_closed(identity):
    service, _factory = identity
    first = register(identity, slug="first", email="first@example.com")
    second = register(identity, slug="second", email="second@example.com")
    first_session = service.authenticate(
        tenant_id=first.tenant_id,
        email="first@example.com",
        password=PASSWORD,
    )
    second_session = service.authenticate(
        tenant_id=second.tenant_id,
        email="second@example.com",
        password=PASSWORD,
    )
    first_principal = service.authenticate_session(
        tenant_id=first.tenant_id, token=first_session.token
    )

    with pytest.raises(AuthorizationDenied):
        service.revoke_session(principal=first_principal, session_id=second_session.session_id)
    assert (
        service.authenticate_session(tenant_id=second.tenant_id, token=second_session.token).user_id
        == second.user_id
    )


def test_owner_can_grant_and_change_membership_but_last_owner_is_protected(identity):
    service, factory = identity
    first = register(identity, slug="first", email="first@example.com")
    second = register(identity, slug="second", email="second@example.com")
    issued = service.authenticate(
        tenant_id=first.tenant_id,
        email="first@example.com",
        password=PASSWORD,
    )
    owner = service.authenticate_session(tenant_id=first.tenant_id, token=issued.token)
    membership_id = service.grant_membership(
        principal=owner,
        user_id=second.user_id,
        role=MembershipRole.OPERATOR,
    )
    service.change_membership_role(
        principal=owner,
        membership_id=membership_id,
        role=MembershipRole.VIEWER,
    )

    with factory() as db:
        membership = db.get(Membership, membership_id)
    assert membership is not None
    assert membership.tenant_id == first.tenant_id
    assert membership.role == MembershipRole.VIEWER.value

    with pytest.raises(Conflict, match="last active owner"):
        service.change_membership_role(
            principal=owner,
            membership_id=first.membership_id,
            role=MembershipRole.ADMIN,
        )


def test_forged_principal_role_is_reloaded_from_server_authoritative_state(identity):
    service, _factory = identity
    first = register(identity, slug="first", email="first@example.com")
    second = register(identity, slug="second", email="second@example.com")
    owner_session = service.authenticate(
        tenant_id=first.tenant_id,
        email="first@example.com",
        password=PASSWORD,
    )
    owner = service.authenticate_session(tenant_id=first.tenant_id, token=owner_session.token)
    service.grant_membership(
        principal=owner,
        user_id=second.user_id,
        role=MembershipRole.OPERATOR,
    )
    operator_session = service.authenticate(
        tenant_id=first.tenant_id,
        email="second@example.com",
        password=PASSWORD,
    )
    operator = service.authenticate_session(tenant_id=first.tenant_id, token=operator_session.token)
    forged = replace(operator, role=MembershipRole.OWNER)

    with pytest.raises(AuthorizationDenied):
        service.list_audit_events(principal=forged)
    with pytest.raises(AuthorizationDenied):
        service.change_membership_role(
            principal=forged,
            membership_id=first.membership_id,
            role=MembershipRole.VIEWER,
        )


def test_audit_events_are_append_only_and_tenant_filtered(identity):
    service, factory = identity
    first = register(identity, slug="first", email="first@example.com")
    register(identity, slug="second", email="second@example.com")
    session = service.authenticate(
        tenant_id=first.tenant_id,
        email="first@example.com",
        password=PASSWORD,
    )
    principal = service.authenticate_session(tenant_id=first.tenant_id, token=session.token)
    events = service.list_audit_events(principal=principal)
    assert events
    assert {item.tenant_id for item in events} == {first.tenant_id}

    with pytest.raises(ValueError, match="append-only"), factory() as db, db.begin():
        event_row = db.scalar(select(AuditEvent).where(AuditEvent.tenant_id == first.tenant_id))
        assert event_row is not None
        event_row.event_type = "tampered"


def test_tenant_owned_tables_require_tenant_id():
    for table_name in (
        "identity_memberships",
        "identity_sessions",
        "identity_password_reset_tokens",
        "identity_email_verification_tokens",
        "identity_audit_events",
    ):
        table = Base.metadata.tables[table_name]
        assert table.c.tenant_id.nullable is False


def test_email_verification_token_is_hashed_one_time_and_updates_profile(identity):
    service, factory = identity
    registration = register(identity, slug="verified", email="verify@example.com")
    session = service.authenticate(
        tenant_id=registration.tenant_id,
        email="verify@example.com",
        password=PASSWORD,
    )
    profile = service.authenticate_session_token(token=session.token)
    assert profile.email_verified is False

    issued = service.issue_email_verification(
        tenant_id=registration.tenant_id,
        user_id=registration.user_id,
    )
    assert issued is not None
    with factory() as db:
        stored = db.scalar(
            select(EmailVerificationToken).where(
                EmailVerificationToken.id == issued.verification_id
            )
        )
        assert stored is not None
        assert issued.token not in stored.token_hash

    assert service.consume_email_verification_token(token=issued.token) == registration.user_id
    assert service.authenticate_session_token(token=session.token).email_verified is True
    with pytest.raises(InvalidToken):
        service.consume_email_verification_token(token=issued.token)


def test_external_identity_provisions_and_authenticates_without_local_password(identity):
    service, _factory = identity
    subject = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    issued = service.register_external_tenant(
        auth_subject=subject,
        auth_provider="supabase",
        verified_email="social@example.com",
        display_name="Social User",
        tenant_name="Social Organization",
        tenant_slug="social-org",
    )
    profile = service.authenticate_session_token(token=issued.token)
    assert profile.email == "social@example.com"
    assert profile.email_verified is True

    with pytest.raises(AuthenticationFailed):
        service.authenticate(
            tenant_id=issued.tenant_id,
            email="social@example.com",
            password=PASSWORD,
        )

    next_session = service.authenticate_external(
        auth_subject=subject,
        auth_provider="supabase",
        organization_slug="social-org",
    )
    assert next_session.user_id == issued.user_id
    with pytest.raises(AuthenticationFailed):
        service.authenticate_external(
            auth_subject=uuid4(),
            auth_provider="supabase",
            organization_slug="social-org",
        )


def test_schema_compiles_for_postgresql():
    dialect = postgresql.dialect()
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert f"CREATE TABLE {table.name}" in ddl
        assert "UUID" in ddl


def test_alembic_upgrade_runs_on_sqlite_and_installs_audit_guards(tmp_path):
    migration_path = (
        Path(__file__).parents[1] / "migrations" / "versions" / "20260908_0001_identity_tenancy.py"
    )
    spec = spec_from_file_location("identity_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'identity.db'}")
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        table_names = set(connection.dialect.get_table_names(connection))
    assert {
        "identity_tenants",
        "identity_users",
        "identity_memberships",
        "identity_sessions",
        "identity_email_verification_tokens",
        "identity_password_reset_tokens",
        "identity_audit_events",
    } <= table_names


def test_password_and_pepper_limits_are_fail_closed():
    manager = PasswordManager()
    with pytest.raises(ValueError, match="at least 12"):
        manager.hash("too-short")
    with pytest.raises(ValueError, match="at least 32"):
        TokenHasher(b"short")

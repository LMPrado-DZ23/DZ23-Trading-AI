from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from tradingagents.platform.identity import (
    AuditEvent,
    IdentityService,
    Membership,
    Tenant,
    TokenHasher,
    User,
)

POSTGRES_URL = os.environ.get("TRADINGAGENTS_TEST_POSTGRES_URL")
pytestmark = pytest.mark.integration


@pytest.mark.skipif(not POSTGRES_URL, reason="PostgreSQL RLS test URL not configured")
def test_postgres_rls_rejects_cross_tenant_reads_and_writes() -> None:
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    service = IdentityService(factory, token_hasher=TokenHasher(b"postgres-rls-test-token-pepper-32"))
    suffix = uuid4().hex[:10]
    first = service.register_tenant(
        tenant_name="Tenant RLS A",
        tenant_slug=f"rls-a-{suffix}",
        owner_email=f"rls-a-{suffix}@example.test",
        display_name="Owner A",
        password="correct horse battery staple",
    )
    second = service.register_tenant(
        tenant_name="Tenant RLS B",
        tenant_slug=f"rls-b-{suffix}",
        owner_email=f"rls-b-{suffix}@example.test",
        display_name="Owner B",
        password="correct horse battery staple",
    )

    with factory() as db, db.begin():
        assert db.scalar(select(Membership).where(Membership.id == first.membership_id)) is None
        assert tuple(db.scalars(select(Tenant))) == ()
        assert tuple(db.scalars(select(User))) == ()

    with factory() as db, db.begin():
        db.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(first.tenant_id)},
        )
        visible = tuple(db.scalars(select(Membership)))
        assert {item.tenant_id for item in visible} == {first.tenant_id}
        assert db.scalar(select(Membership).where(Membership.id == second.membership_id)) is None
        assert {item.id for item in db.scalars(select(Tenant))} == {first.tenant_id}
        assert {item.id for item in db.scalars(select(User))} == {first.user_id}

    assert service.tenant_id_by_slug(f"rls-a-{suffix}") == first.tenant_id

    with pytest.raises(DBAPIError), factory() as db, db.begin():
        db.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(first.tenant_id)},
        )
        db.add(
            AuditEvent(
                tenant_id=second.tenant_id,
                actor_user_id=second.user_id,
                event_type="cross_tenant.attempt",
                target_type="tenant",
                target_id=str(second.tenant_id),
                details={},
            )
        )
        db.flush()

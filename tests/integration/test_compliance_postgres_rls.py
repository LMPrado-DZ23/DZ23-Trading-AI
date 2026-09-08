from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from tradingagents.platform.compliance import (
    ComplianceService,
    PrivacyRequest,
    PrivacyRequestKind,
)
from tradingagents.platform.identity import IdentityService, TokenHasher

POSTGRES_URL = os.environ.get("TRADINGAGENTS_TEST_POSTGRES_URL")
pytestmark = pytest.mark.integration


@pytest.mark.skipif(not POSTGRES_URL, reason="PostgreSQL RLS test URL not configured")
def test_compliance_rls_rejects_cross_tenant_reads_and_writes() -> None:
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    identity = IdentityService(
        factory,
        token_hasher=TokenHasher(b"postgres-compliance-test-pepper-32"),
    )
    compliance = ComplianceService(factory)
    suffix = uuid4().hex[:10]

    principals = []
    for label in ("a", "b"):
        registered = identity.register_tenant(
            tenant_name=f"Compliance {label.upper()}",
            tenant_slug=f"compliance-{label}-{suffix}",
            owner_email=f"compliance-{label}-{suffix}@example.test",
            display_name=f"Owner {label.upper()}",
            password="correct horse battery staple",
        )
        issued = identity.authenticate(
            tenant_id=registered.tenant_id,
            email=f"compliance-{label}-{suffix}@example.test",
            password="correct horse battery staple",
        )
        principals.append(
            identity.authenticate_session(
                tenant_id=registered.tenant_id,
                token=issued.token,
            )
        )

    first, second = principals
    first_request = compliance.request_privacy_action(
        principal=first,
        kind=PrivacyRequestKind.EXPORT,
    )
    compliance.request_privacy_action(principal=second, kind=PrivacyRequestKind.DELETE)

    with factory() as db, db.begin():
        assert tuple(db.scalars(select(PrivacyRequest))) == ()

    with factory() as db, db.begin():
        db.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(first.tenant_id)},
        )
        visible = tuple(db.scalars(select(PrivacyRequest)))
        assert {row.tenant_id for row in visible} == {first.tenant_id}
        assert {row.id for row in visible} == {first_request}

    with pytest.raises(DBAPIError), factory() as db, db.begin():
        db.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(first.tenant_id)},
        )
        db.add(
            PrivacyRequest(
                tenant_id=second.tenant_id,
                user_id=second.user_id,
                kind=PrivacyRequestKind.EXPORT.value,
                status="requested",
                due_at=ComplianceService._now(),
            )
        )
        db.flush()

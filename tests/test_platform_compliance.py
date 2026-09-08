from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable

from tradingagents.platform.compliance import (
    AesGcmSecretCrypto,
    ComplianceAuthorizationDenied,
    ComplianceConflict,
    ComplianceService,
    ConsentDecision,
    ConsentRecord,
    InvalidComplianceTransition,
    MfaCredential,
    MfaService,
    MfaVerificationFailed,
    PrivacyRequest,
    PrivacyRequestEvent,
    PrivacyRequestKind,
    PrivacyRequestStatus,
    RecoveryCode,
    RecoveryCodeHasher,
    TermsDocument,
    TotpEngine,
)
from tradingagents.platform.identity import Base, IdentityService, TokenHasher

PASSWORD = "correct horse battery staple"
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


@pytest.fixture
def platform():
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
    identity = IdentityService(
        factory, token_hasher=TokenHasher(b"identity-pepper-32-bytes-long!!!")
    )
    compliance = ComplianceService(factory)
    mfa = MfaService(
        factory,
        crypto=AesGcmSecretCrypto(key=b"k" * 32, key_id="local-test-key-v1"),
        recovery_hasher=RecoveryCodeHasher(b"recovery-pepper-32-bytes-long!!!"),
    )
    return identity, compliance, mfa, factory


def register(platform, *, slug: str, email: str):
    identity, _compliance, _mfa, _factory = platform
    registered = identity.register_tenant(
        tenant_name=slug.title(),
        tenant_slug=slug,
        owner_email=email,
        display_name="Test Owner",
        password=PASSWORD,
    )
    session = identity.authenticate(
        tenant_id=registered.tenant_id,
        email=email,
        password=PASSWORD,
    )
    principal = identity.authenticate_session(
        tenant_id=registered.tenant_id,
        token=session.token,
    )
    return registered, principal


def publish_terms(compliance: ComplianceService, version: str = "2026-09"):
    return compliance.publish_terms(
        document_type="terms-of-service",
        version=version,
        content_sha256="a" * 64,
        publication_uri=f"https://legal.example/terms/{version}",
        published_at=NOW,
    )


def test_terms_and_consents_are_versioned_append_only_and_tenant_scoped(platform):
    _identity, compliance, _mfa, factory = platform
    _first, first = register(platform, slug="first", email="first@example.com")
    _second, second = register(platform, slug="second", email="second@example.com")
    first_doc = publish_terms(compliance)
    second_doc = publish_terms(compliance, "2026-10")

    accepted = compliance.record_consent(
        principal=first,
        terms_document_id=first_doc,
        decision=ConsentDecision.ACCEPTED,
    )
    withdrawn = compliance.record_consent(
        principal=first,
        terms_document_id=first_doc,
        decision=ConsentDecision.WITHDRAWN,
    )
    compliance.record_consent(
        principal=second,
        terms_document_id=second_doc,
        decision=ConsentDecision.ACCEPTED,
    )

    history = compliance.list_consents(principal=first)
    assert [row.id for row in history] == [accepted, withdrawn]
    assert {row.tenant_id for row in history} == {first.tenant_id}
    assert [row.decision for row in history] == ["accepted", "withdrawn"]

    with pytest.raises(ComplianceConflict, match="already exists"):
        publish_terms(compliance)
    with pytest.raises(ComplianceConflict, match="already current"):
        compliance.record_consent(
            principal=first,
            terms_document_id=first_doc,
            decision=ConsentDecision.WITHDRAWN,
        )
    with pytest.raises(ValueError, match="append-only"), factory() as db, db.begin():
        document = db.get(TermsDocument, first_doc)
        assert document is not None
        document.version = "tampered"
    with pytest.raises(ValueError, match="append-only"), factory() as db, db.begin():
        record = db.get(ConsentRecord, accepted)
        assert record is not None
        db.delete(record)


def test_consent_cannot_be_withdrawn_without_acceptance(platform):
    _identity, compliance, _mfa, _factory = platform
    _registered, owner = register(platform, slug="consent", email="consent@example.com")
    document_id = publish_terms(compliance)
    with pytest.raises(ComplianceConflict, match="before acceptance"):
        compliance.record_consent(
            principal=owner,
            terms_document_id=document_id,
            decision=ConsentDecision.WITHDRAWN,
        )


def test_lgpd_export_and_delete_have_explicit_state_retention_and_event_history(platform):
    _identity, compliance, _mfa, factory = platform
    _registered, owner = register(platform, slug="privacy", email="privacy@example.com")
    export_id = compliance.request_privacy_action(principal=owner, kind=PrivacyRequestKind.EXPORT)
    compliance.transition_privacy_request(
        principal=owner,
        request_id=export_id,
        expected_status=PrivacyRequestStatus.REQUESTED,
        target_status=PrivacyRequestStatus.IDENTITY_VERIFIED,
    )
    compliance.transition_privacy_request(
        principal=owner,
        request_id=export_id,
        expected_status=PrivacyRequestStatus.IDENTITY_VERIFIED,
        target_status=PrivacyRequestStatus.PROCESSING,
    )
    expires_at = datetime.now(UTC) + timedelta(days=2)
    compliance.transition_privacy_request(
        principal=owner,
        request_id=export_id,
        expected_status=PrivacyRequestStatus.PROCESSING,
        target_status=PrivacyRequestStatus.EXPORT_READY,
        artifact_expires_at=expires_at,
    )
    export = compliance.get_privacy_request(principal=owner, request_id=export_id)
    assert export.status == PrivacyRequestStatus.EXPORT_READY.value
    assert export.artifact_expires_at == expires_at
    assert export.due_at - export.requested_at == timedelta(days=15)

    delete_id = compliance.request_privacy_action(principal=owner, kind=PrivacyRequestKind.DELETE)
    compliance.transition_privacy_request(
        principal=owner,
        request_id=delete_id,
        expected_status=PrivacyRequestStatus.REQUESTED,
        target_status=PrivacyRequestStatus.IDENTITY_VERIFIED,
    )
    compliance.transition_privacy_request(
        principal=owner,
        request_id=delete_id,
        expected_status=PrivacyRequestStatus.IDENTITY_VERIFIED,
        target_status=PrivacyRequestStatus.PROCESSING,
    )
    retention_until = datetime.now(UTC) + timedelta(days=365)
    compliance.transition_privacy_request(
        principal=owner,
        request_id=delete_id,
        expected_status=PrivacyRequestStatus.PROCESSING,
        target_status=PrivacyRequestStatus.DELETION_SCHEDULED,
        retention_until=retention_until,
    )
    deletion = compliance.get_privacy_request(principal=owner, request_id=delete_id)
    assert deletion.retention_until == retention_until

    with factory() as db:
        events = list(
            db.scalars(
                select(PrivacyRequestEvent)
                .where(PrivacyRequestEvent.request_id == export_id)
                .order_by(PrivacyRequestEvent.occurred_at, PrivacyRequestEvent.id)
            )
        )
    assert [row.to_status for row in events] == [
        "requested",
        "identity_verified",
        "processing",
        "export_ready",
    ]
    with pytest.raises(ValueError, match="append-only"), factory() as db, db.begin():
        events[0] = db.merge(events[0])
        events[0].to_status = "tampered"


def test_lgpd_state_machine_rejects_wrong_kind_expiry_and_concurrent_state(platform):
    _identity, compliance, _mfa, _factory = platform
    _registered, owner = register(platform, slug="invalid", email="invalid@example.com")
    request_id = compliance.request_privacy_action(principal=owner, kind=PrivacyRequestKind.DELETE)
    with pytest.raises(InvalidComplianceTransition):
        compliance.transition_privacy_request(
            principal=owner,
            request_id=request_id,
            expected_status=PrivacyRequestStatus.REQUESTED,
            target_status=PrivacyRequestStatus.EXPORT_READY,
            artifact_expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    with pytest.raises(ComplianceConflict, match="concurrently"):
        compliance.transition_privacy_request(
            principal=owner,
            request_id=request_id,
            expected_status=PrivacyRequestStatus.PROCESSING,
            target_status=PrivacyRequestStatus.REJECTED,
            reason_code="LEGAL_HOLD",
        )


def test_cross_tenant_principal_cannot_read_or_transition_privacy_request(platform):
    _identity, compliance, _mfa, _factory = platform
    _first, first = register(platform, slug="tenant-a", email="a@example.com")
    _second, second = register(platform, slug="tenant-b", email="b@example.com")
    request_id = compliance.request_privacy_action(principal=first, kind=PrivacyRequestKind.EXPORT)
    with pytest.raises(ComplianceAuthorizationDenied):
        compliance.get_privacy_request(principal=second, request_id=request_id)
    forged = replace(first, tenant_id=second.tenant_id)
    with pytest.raises(ComplianceAuthorizationDenied):
        compliance.get_privacy_request(principal=forged, request_id=request_id)


def test_mfa_secret_is_encrypted_and_recovery_codes_are_hashed_one_time(platform):
    _identity, _compliance, mfa, factory = platform
    _registered, owner = register(platform, slug="mfa", email="mfa@example.com")
    enrollment = mfa.enroll(principal=owner, account_label="mfa@example.com")
    assert enrollment.secret not in repr(enrollment)
    assert enrollment.provisioning_uri not in repr(enrollment)

    with factory() as db:
        stored = db.get(MfaCredential, enrollment.credential_id)
    assert stored is not None
    assert enrollment.secret.encode("ascii") not in stored.secret_ciphertext
    assert stored.secret_key_id == "local-test-key-v1"

    engine = TotpEngine()
    activation = mfa.activate(
        principal=owner,
        code=engine.code_at(enrollment.secret, NOW),
        now=NOW,
    )
    assert all(item not in repr(activation) for item in activation.recovery_codes)
    selected = activation.recovery_codes[0]
    with factory() as db:
        stored_codes = list(
            db.scalars(
                select(RecoveryCode).where(RecoveryCode.credential_id == activation.credential_id)
            )
        )
    assert len(stored_codes) == 10
    assert all(selected not in row.code_hash for row in stored_codes)

    verified = mfa.verify_step_up(
        principal=owner,
        recovery_code=selected,
        now=NOW + timedelta(seconds=1),
    )
    assert verified.method == "recovery_code"
    assert verified.session_id == owner.session_id
    assert verified.valid_until - verified.verified_at == timedelta(minutes=5)
    with pytest.raises(MfaVerificationFailed):
        mfa.verify_step_up(
            principal=owner,
            recovery_code=selected,
            now=NOW + timedelta(seconds=2),
        )


def test_totp_step_up_is_session_bound_and_replay_resistant(platform):
    identity, _compliance, mfa, _factory = platform
    registered, owner = register(platform, slug="totp", email="totp@example.com")
    enrollment = mfa.enroll(principal=owner, account_label="totp@example.com")
    engine = TotpEngine()
    mfa.activate(
        principal=owner,
        code=engine.code_at(enrollment.secret, NOW),
        now=NOW,
    )
    next_period = NOW + timedelta(seconds=30)
    code = engine.code_at(enrollment.secret, next_period)
    verified = mfa.verify_step_up(principal=owner, code=code, now=next_period)
    assert verified.tenant_id == owner.tenant_id
    assert verified.user_id == owner.user_id
    persisted = mfa.assert_recent_step_up(
        principal=owner, now=next_period + timedelta(minutes=4)
    )
    assert persisted.session_id == owner.session_id
    assert persisted.method == "totp"
    with pytest.raises(MfaVerificationFailed):
        mfa.assert_recent_step_up(
            principal=owner, now=next_period + timedelta(minutes=5, seconds=1)
        )
    with pytest.raises(MfaVerificationFailed):
        mfa.verify_step_up(principal=owner, code=code, now=next_period)

    session = identity.authenticate(
        tenant_id=registered.tenant_id,
        email="totp@example.com",
        password=PASSWORD,
    )
    other_session = identity.authenticate_session(
        tenant_id=registered.tenant_id, token=session.token
    )
    later = next_period + timedelta(seconds=30)
    with pytest.raises(MfaVerificationFailed):
        mfa.assert_recent_step_up(principal=other_session, now=later)
    other = mfa.verify_step_up(
        principal=other_session,
        code=engine.code_at(enrollment.secret, later),
        now=later,
    )
    assert other.session_id == other_session.session_id
    assert other.session_id != verified.session_id
    assert mfa.assert_recent_step_up(principal=other_session, now=later).session_id == (
        other_session.session_id
    )


def test_mfa_lookup_is_tenant_bound_and_schema_has_no_plaintext_secret_column(platform):
    _identity, _compliance, mfa, _factory = platform
    _first, first = register(platform, slug="mfa-a", email="mfa-a@example.com")
    _second, second = register(platform, slug="mfa-b", email="mfa-b@example.com")
    enrollment = mfa.enroll(principal=first, account_label="mfa-a@example.com")
    code = TotpEngine().code_at(enrollment.secret, NOW)

    with pytest.raises(MfaVerificationFailed):
        mfa.activate(principal=second, code=code, now=NOW)
    assert "secret" not in MfaCredential.__table__.c
    assert "secret_plaintext" not in MfaCredential.__table__.c
    assert {"secret_key_id", "secret_nonce", "secret_ciphertext"} <= set(
        MfaCredential.__table__.c.keys()
    )


def test_compliance_schema_compiles_for_postgresql_and_is_tenant_owned():
    dialect = postgresql.dialect()
    tables = [
        ConsentRecord.__table__,
        PrivacyRequest.__table__,
        PrivacyRequestEvent.__table__,
        MfaCredential.__table__,
        RecoveryCode.__table__,
    ]
    for table in tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert f"CREATE TABLE {table.name}" in ddl
        assert "UUID" in ddl
        assert table.c.tenant_id.nullable is False


def test_compliance_migration_runs_after_identity_and_installs_append_only_guards(tmp_path):
    versions = Path(__file__).parents[1] / "migrations" / "versions"

    def load_migration(filename: str, module_name: str):
        spec = spec_from_file_location(module_name, versions / filename)
        assert spec is not None and spec.loader is not None
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    identity_migration = load_migration(
        "20260908_0001_identity_tenancy.py", "identity_migration_for_compliance"
    )
    compliance_migration = load_migration("20260908_0002_compliance_mfa.py", "compliance_migration")
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'compliance.db'}")
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        identity_migration.op = operations
        identity_migration.upgrade()
        compliance_migration.op = operations
        compliance_migration.upgrade()
        table_names = set(connection.dialect.get_table_names(connection))
        connection.exec_driver_sql(
            "INSERT INTO compliance_terms_documents "
            "(id, document_type, version, content_sha256, publication_uri, published_at) "
            "VALUES (?, 'terms', 'v1', ?, 'https://legal.example/v1', CURRENT_TIMESTAMP)",
            (uuid4().hex, "a" * 64),
        )
        with pytest.raises(DBAPIError, match="append-only"):
            connection.exec_driver_sql("UPDATE compliance_terms_documents SET version = 'tampered'")
    assert {
        "compliance_terms_documents",
        "compliance_consent_records",
        "compliance_privacy_requests",
        "compliance_privacy_request_events",
        "compliance_mfa_credentials",
        "compliance_recovery_codes",
    } <= table_names


def test_compliance_postgresql_migration_forces_fail_closed_rls():
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    spec = spec_from_file_location(
        "compliance_migration_rls", versions / "20260908_0002_compliance_mfa.py"
    )
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)

    class Dialect:
        name = "postgresql"

    class Bind:
        dialect = Dialect()

    class OperationsRecorder:
        statements: list[str]

        def __init__(self):
            self.statements = []

        @staticmethod
        def get_bind():
            return Bind()

        def execute(self, statement):
            self.statements.append(str(statement))

    recorder = OperationsRecorder()
    migration.op = recorder
    migration._install_database_guards()
    rendered = "\n".join(recorder.statements)
    for table in migration._TENANT_TABLES:
        assert f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY' in rendered
        assert f'CREATE POLICY "{table}_tenant_isolation"' in rendered
    assert "current_setting('app.tenant_id', true)" in rendered
    assert "compliance_reject_evidence_mutation" in rendered

"""Tenant-safe encrypted exchange connection application service."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tradingagents.platform.connections.catalog import (
    BrokerEnvironment,
    capability_digest,
    exchange_capability,
)
from tradingagents.platform.connections.crypto import CredentialCryptoPort, CredentialEnvelope
from tradingagents.platform.connections.diagnostics import (
    CCXTReadOnlyDiagnostics,
    DiagnosticResult,
    DiagnosticSafetyError,
)
from tradingagents.platform.connections.models import (
    BrokerConnection,
    BrokerCredential,
    BrokerCredentialAccessEvent,
    ConnectionStatus,
)
from tradingagents.platform.identity.service import IdentityPrincipal


class ConnectionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BrokerCredentialInput:
    api_key: str = field(repr=False)
    secret: str = field(repr=False)
    passphrase: str | None = field(default=None, repr=False)
    uid: str | None = field(default=None, repr=False)

    def as_mapping(self) -> dict[str, str]:
        values = {
            "api_key": self.api_key,
            "secret": self.secret,
            "passphrase": self.passphrase,
            "uid": self.uid,
        }
        return {key: value for key, value in values.items() if value is not None}


@dataclass(frozen=True, slots=True)
class ConnectionView:
    id: UUID
    label: str
    exchange_slug: str
    display_name: str
    environment: str
    status: str
    credential_version: int
    capability_digest: str
    ccxt_version: str | None
    permission_status: str
    last_diagnostic_code: str | None
    last_checked_at: datetime | None


class ConnectionService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        keyring: CredentialCryptoPort,
        diagnostics: CCXTReadOnlyDiagnostics,
        idempotency_digest_key: bytes = b"development-only-idempotency-key",
    ) -> None:
        if len(idempotency_digest_key) < 32:
            raise ValueError("idempotency digest key must contain at least 32 bytes")
        self._sessions = session_factory
        self._keyring = keyring
        self._diagnostics = diagnostics
        self._idempotency_digest_key = idempotency_digest_key

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _set_tenant_scope(db: Session, tenant_id: UUID) -> None:
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )

    @staticmethod
    def _authorize(principal: IdentityPrincipal, *, write: bool) -> None:
        allowed = {"owner", "admin"} if write else {"owner", "admin", "operator", "auditor"}
        if principal.role.value not in allowed:
            raise ConnectionError("CONNECTION_FORBIDDEN")

    @staticmethod
    def _validate_credentials(
        exchange_slug: str, credentials: BrokerCredentialInput
    ) -> dict[str, str]:
        capability = exchange_capability(exchange_slug)
        values = credentials.as_mapping()
        required = set(capability.credential_fields)
        if required - values.keys():
            raise ConnectionError("CREDENTIAL_FIELDS_MISSING")
        if values.keys() - required:
            raise ConnectionError("CREDENTIAL_FIELDS_INVALID")
        for value in values.values():
            if not value or len(value) > 4096 or any(ord(character) < 32 for character in value):
                raise ConnectionError("CREDENTIAL_VALUE_INVALID")
        return values

    @staticmethod
    def _normalize_label(label: str) -> tuple[str, str]:
        cleaned = " ".join(label.strip().split())
        if not 2 <= len(cleaned) <= 80 or any(ord(character) < 32 for character in cleaned):
            raise ConnectionError("CONNECTION_LABEL_INVALID")
        return cleaned, cleaned.casefold()

    @staticmethod
    def _context(
        tenant_id: UUID,
        connection_id: UUID,
        exchange_slug: str,
        environment: str,
        version: int,
    ) -> bytes:
        return (
            f"broker-credential/1:{tenant_id}:{connection_id}:"
            f"{exchange_slug}:{environment}:{version}"
        ).encode("ascii")

    def upsert_connection(
        self,
        *,
        principal: IdentityPrincipal,
        idempotency_key: str,
        label: str,
        exchange_slug: str,
        environment: BrokerEnvironment,
        credentials: BrokerCredentialInput,
    ) -> UUID:
        self._authorize(principal, write=True)
        capability = exchange_capability(exchange_slug)
        if environment is BrokerEnvironment.TESTNET and not capability.has_real_testnet:
            raise ConnectionError("TESTNET_UNAVAILABLE")
        values = self._validate_credentials(capability.slug, credentials)
        display_label, normalized_label = self._normalize_label(label)
        if not 16 <= len(idempotency_key) <= 128 or any(
            ord(character) < 33 or ord(character) > 126 for character in idempotency_key
        ):
            raise ConnectionError("IDEMPOTENCY_KEY_INVALID")
        idempotency_hash = hashlib.sha256(
            f"{principal.tenant_id}:{idempotency_key}".encode()
        ).hexdigest()
        request_digest = hmac.new(
            self._idempotency_digest_key,
            json.dumps(
                {
                    "tenant_id": str(principal.tenant_id),
                    "label": normalized_label,
                    "exchange": capability.slug,
                    "environment": environment.value,
                    "credentials": values,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        try:
            with self._sessions() as db, db.begin():
                self._set_tenant_scope(db, principal.tenant_id)
                replay = db.scalar(
                    select(BrokerCredential).where(
                        BrokerCredential.tenant_id == principal.tenant_id,
                        BrokerCredential.idempotency_key_hash == idempotency_hash,
                    )
                )
                if replay is not None:
                    if not hmac.compare_digest(replay.request_digest, request_digest):
                        raise ConnectionError("IDEMPOTENCY_PAYLOAD_MISMATCH")
                    return replay.connection_id
                connection = db.scalar(
                    select(BrokerConnection)
                    .where(
                        BrokerConnection.tenant_id == principal.tenant_id,
                        BrokerConnection.label_normalized == normalized_label,
                    )
                    .with_for_update()
                )
                if connection is None:
                    connection = BrokerConnection(
                        id=uuid4(),
                        tenant_id=principal.tenant_id,
                        created_by_user_id=principal.user_id,
                        label=display_label,
                        label_normalized=normalized_label,
                        exchange_slug=capability.slug,
                        environment=environment.value,
                        active_credential_version=1,
                        capability_digest=capability_digest(capability),
                        permission_status="unverifiable",
                        status=ConnectionStatus.PENDING_DIAGNOSTIC.value,
                    )
                    db.add(connection)
                    db.flush()
                else:
                    if (
                        connection.exchange_slug != capability.slug
                        or connection.environment != environment.value
                    ):
                        raise ConnectionError("CONNECTION_LABEL_CONFLICT")
                    connection.label = display_label
                    connection.active_credential_version += 1
                    connection.capability_digest = capability_digest(capability)
                    connection.ccxt_version = None
                    connection.permission_status = "unverifiable"
                    connection.status = ConnectionStatus.PENDING_DIAGNOSTIC.value
                    connection.last_diagnostic_code = None
                    connection.disabled_at = None
                    connection.diagnostic_lease_id = None
                    connection.diagnostic_lease_expires_at = None

                version = connection.active_credential_version
                context = self._context(
                    principal.tenant_id,
                    connection.id,
                    capability.slug,
                    environment.value,
                    version,
                )
                plaintext = json.dumps(
                    values, sort_keys=True, separators=(",", ":"), ensure_ascii=True
                ).encode("utf-8")
                envelope = self._keyring.encrypt(plaintext, context=context)
                db.add(
                    BrokerCredential(
                        tenant_id=principal.tenant_id,
                        connection_id=connection.id,
                        version=version,
                        key_id=envelope.key_id,
                        wrapped_key_nonce=envelope.wrapped_key_nonce,
                        wrapped_key=envelope.wrapped_key,
                        payload_nonce=envelope.payload_nonce,
                        ciphertext=envelope.ciphertext,
                        idempotency_key_hash=idempotency_hash,
                        request_digest=request_digest,
                        created_by_user_id=principal.user_id,
                    )
                )
                db.add(
                    BrokerCredentialAccessEvent(
                        tenant_id=principal.tenant_id,
                        connection_id=connection.id,
                        credential_version=version,
                        actor_user_id=principal.user_id,
                        purpose="rotation" if version > 1 else "diagnostic",
                        outcome="allowed",
                        details={"event": "credential_stored"},
                    )
                )
                return connection.id
        except IntegrityError:
            with self._sessions() as db, db.begin():
                self._set_tenant_scope(db, principal.tenant_id)
                replay = db.scalar(
                    select(BrokerCredential).where(
                        BrokerCredential.tenant_id == principal.tenant_id,
                        BrokerCredential.idempotency_key_hash == idempotency_hash,
                    )
                )
                if replay is not None:
                    if not hmac.compare_digest(replay.request_digest, request_digest):
                        raise ConnectionError("IDEMPOTENCY_PAYLOAD_MISMATCH") from None
                    return replay.connection_id
            raise ConnectionError("CONNECTION_CONFLICT") from None

    def rewrap_tenant_credentials(
        self, *, principal: IdentityPrincipal, batch_size: int = 100
    ) -> int:
        """Rewrap every tenant DEK under the active external key without payload decrypt."""

        self._authorize(principal, write=True)
        if not 1 <= batch_size <= 500:
            raise ConnectionError("REWRAP_BATCH_SIZE_INVALID")
        changed = 0
        cursor: tuple[datetime, UUID] | None = None
        while True:
            with self._sessions() as db, db.begin():
                self._set_tenant_scope(db, principal.tenant_id)
                query = (
                    select(BrokerCredential, BrokerConnection)
                    .join(
                        BrokerConnection,
                        (BrokerConnection.id == BrokerCredential.connection_id)
                        & (BrokerConnection.tenant_id == BrokerCredential.tenant_id),
                    )
                    .where(BrokerCredential.tenant_id == principal.tenant_id)
                    .order_by(BrokerCredential.created_at, BrokerCredential.id)
                    .limit(batch_size)
                )
                if cursor is not None:
                    created_at, row_id = cursor
                    query = query.where(
                        or_(
                            BrokerCredential.created_at > created_at,
                            and_(
                                BrokerCredential.created_at == created_at,
                                BrokerCredential.id > row_id,
                            ),
                        )
                    )
                rows = tuple(db.execute(query))
                snapshots = tuple(
                    (
                        credential.id,
                        credential.created_at,
                        connection.id,
                        connection.exchange_slug,
                        connection.environment,
                        credential.version,
                        CredentialEnvelope(
                            credential.key_id,
                            bytes(credential.wrapped_key_nonce),
                            bytes(credential.wrapped_key),
                            bytes(credential.payload_nonce),
                            bytes(credential.ciphertext),
                        ),
                    )
                    for credential, connection in rows
                )
            if not snapshots:
                return changed
            cursor = (snapshots[-1][1], snapshots[-1][0])

            for (
                credential_id,
                _created_at,
                connection_id,
                exchange_slug,
                environment,
                version,
                envelope,
            ) in snapshots:
                if not self._keyring.needs_rewrap(envelope):
                    continue
                context = self._context(
                    principal.tenant_id,
                    connection_id,
                    exchange_slug,
                    environment,
                    version,
                )
                rotated = self._keyring.rotate(envelope, context=context)
                with self._sessions() as db, db.begin():
                    self._set_tenant_scope(db, principal.tenant_id)
                    credential = db.scalar(
                        select(BrokerCredential)
                        .where(
                            BrokerCredential.id == credential_id,
                            BrokerCredential.tenant_id == principal.tenant_id,
                        )
                        .with_for_update()
                    )
                    if credential is None:
                        raise ConnectionError("CREDENTIAL_CHANGED_DURING_REWRAP")
                    current = CredentialEnvelope(
                        credential.key_id,
                        bytes(credential.wrapped_key_nonce),
                        bytes(credential.wrapped_key),
                        bytes(credential.payload_nonce),
                        bytes(credential.ciphertext),
                    )
                    if current != envelope:
                        raise ConnectionError("CREDENTIAL_CHANGED_DURING_REWRAP")
                    credential.key_id = rotated.key_id
                    credential.wrapped_key_nonce = rotated.wrapped_key_nonce
                    credential.wrapped_key = rotated.wrapped_key
                    db.add(
                        BrokerCredentialAccessEvent(
                            tenant_id=principal.tenant_id,
                            connection_id=connection_id,
                            credential_version=credential.version,
                            actor_user_id=principal.user_id,
                            purpose="rotation",
                            outcome="allowed",
                            details={"event": "dek_rewrapped"},
                        )
                    )
                    changed += 1

    def list_connections(
        self, *, principal: IdentityPrincipal
    ) -> tuple[ConnectionView, ...]:
        self._authorize(principal, write=False)
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, principal.tenant_id)
            rows = tuple(
                db.scalars(
                    select(BrokerConnection)
                    .where(BrokerConnection.tenant_id == principal.tenant_id)
                    .order_by(BrokerConnection.created_at)
                )
            )
            return tuple(self._view(row) for row in rows)

    def diagnose(
        self, *, principal: IdentityPrincipal, connection_id: UUID
    ) -> DiagnosticResult:
        self._authorize(principal, write=True)
        snapshot: tuple[
            str, str, int, str, bytes, bytes, bytes, bytes
        ]
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, principal.tenant_id)
            connection = db.scalar(
                select(BrokerConnection)
                .where(
                    BrokerConnection.id == connection_id,
                    BrokerConnection.tenant_id == principal.tenant_id,
                    BrokerConnection.disabled_at.is_(None),
                )
                .with_for_update()
            )
            if connection is None:
                raise ConnectionError("CONNECTION_NOT_FOUND")
            credential = db.scalar(
                select(BrokerCredential).where(
                    BrokerCredential.tenant_id == principal.tenant_id,
                    BrokerCredential.connection_id == connection.id,
                    BrokerCredential.version == connection.active_credential_version,
                )
            )
            if credential is None:
                raise ConnectionError("CREDENTIAL_NOT_FOUND")
            now = self._now()
            if (
                connection.diagnostic_lease_id is not None
                and connection.diagnostic_lease_expires_at is not None
                and connection.diagnostic_lease_expires_at > now
            ):
                raise ConnectionError("DIAGNOSTIC_ALREADY_RUNNING")
            lease_id = uuid4()
            connection.diagnostic_lease_id = lease_id
            connection.diagnostic_lease_expires_at = now + timedelta(seconds=45)
            snapshot = (
                connection.exchange_slug,
                connection.environment,
                credential.version,
                credential.key_id,
                bytes(credential.wrapped_key_nonce),
                bytes(credential.wrapped_key),
                bytes(credential.payload_nonce),
                bytes(credential.ciphertext),
            )

        exchange_slug, environment, version, key_id, key_nonce, wrapped_key, payload_nonce, ciphertext = snapshot
        context = self._context(
            principal.tenant_id,
            connection_id,
            exchange_slug,
            environment,
            version,
        )
        try:
            plaintext = self._keyring.decrypt(
                CredentialEnvelope(
                    key_id,
                    key_nonce,
                    wrapped_key,
                    payload_nonce,
                    ciphertext,
                ),
                context=context,
            )
            decoded = json.loads(plaintext)
            if not isinstance(decoded, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in decoded.items()
            ):
                raise ValueError("credential payload is invalid")
            result = self._diagnostics.run(
                capability=exchange_capability(exchange_slug),
                environment=BrokerEnvironment(environment),
                credentials=decoded,
            )
        except DiagnosticSafetyError as exc:
            result = DiagnosticResult(False, str(exc))
        except Exception:
            result = DiagnosticResult(False, "CREDENTIAL_DECRYPTION_FAILED")

        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, principal.tenant_id)
            connection = db.scalar(
                select(BrokerConnection)
                .where(
                    BrokerConnection.id == connection_id,
                    BrokerConnection.tenant_id == principal.tenant_id,
                    BrokerConnection.disabled_at.is_(None),
                )
                .with_for_update()
            )
            if connection is None:
                raise ConnectionError("CONNECTION_NOT_FOUND")
            if connection.active_credential_version != version:
                raise ConnectionError("CREDENTIAL_CHANGED_DURING_DIAGNOSTIC")
            if connection.diagnostic_lease_id != lease_id:
                raise ConnectionError("DIAGNOSTIC_LEASE_LOST")
            connection.last_checked_at = self._now()
            connection.diagnostic_lease_id = None
            connection.diagnostic_lease_expires_at = None
            connection.last_diagnostic_code = result.code
            connection.ccxt_version = result.adapter_version
            connection.permission_status = result.permission_status
            connection.status = (
                ConnectionStatus.READY_READONLY.value
                if result.success
                else ConnectionStatus.ATTENTION_REQUIRED.value
            )
            db.add(
                BrokerCredentialAccessEvent(
                    tenant_id=principal.tenant_id,
                    connection_id=connection.id,
                    credential_version=version,
                    actor_user_id=principal.user_id,
                    purpose="diagnostic",
                    outcome="allowed" if result.success else "failed",
                    details={"code": result.code},
                )
            )
            return result

    def remove_connection(
        self, *, principal: IdentityPrincipal, connection_id: UUID
    ) -> None:
        self._authorize(principal, write=True)
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, principal.tenant_id)
            connection = db.scalar(
                select(BrokerConnection)
                .where(
                    BrokerConnection.id == connection_id,
                    BrokerConnection.tenant_id == principal.tenant_id,
                )
                .with_for_update()
            )
            if connection is None:
                raise ConnectionError("CONNECTION_NOT_FOUND")
            version = connection.active_credential_version
            db.execute(
                delete(BrokerCredential).where(
                    BrokerCredential.tenant_id == principal.tenant_id,
                    BrokerCredential.connection_id == connection.id,
                )
            )
            connection.status = ConnectionStatus.DISABLED.value
            connection.disabled_at = self._now()
            connection.last_diagnostic_code = "CREDENTIAL_REMOVED"
            db.add(
                BrokerCredentialAccessEvent(
                    tenant_id=principal.tenant_id,
                    connection_id=connection.id,
                    credential_version=version,
                    actor_user_id=principal.user_id,
                    purpose="rotation",
                    outcome="allowed",
                    details={"event": "credential_removed"},
                )
            )

    @staticmethod
    def _view(row: BrokerConnection) -> ConnectionView:
        capability = exchange_capability(row.exchange_slug)
        return ConnectionView(
            id=row.id,
            label=row.label,
            exchange_slug=row.exchange_slug,
            display_name=capability.display_name,
            environment=row.environment,
            status=row.status,
            credential_version=row.active_credential_version,
            capability_digest=row.capability_digest,
            ccxt_version=row.ccxt_version,
            permission_status=row.permission_status,
            last_diagnostic_code=row.last_diagnostic_code,
            last_checked_at=row.last_checked_at,
        )

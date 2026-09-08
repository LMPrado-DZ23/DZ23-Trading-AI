"""Tenant-safe encrypted LLM connection service and worker resolver."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tradingagents.llm_clients.backend_policy import validate_backend_url
from tradingagents.platform.connections.crypto import CredentialCryptoPort, CredentialEnvelope
from tradingagents.platform.identity.service import IdentityPrincipal
from tradingagents.platform.llm_connections.models import (
    LlmConnection,
    LlmCredential,
    LlmCredentialAccessEvent,
)
from tradingagents.platform.llm_provenance import (
    LlmReferenceSnapshot,
    build_llm_reference_snapshot,
)


class LlmConnectionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LlmProviderCapability:
    slug: str
    display_name: str
    requires_endpoint: bool = False


_PROVIDERS = tuple(
    LlmProviderCapability(slug, label, custom)
    for slug, label, custom in (
        ("openai", "OpenAI", False),
        ("anthropic", "Anthropic", False),
        ("google", "Google Gemini", False),
        ("deepseek", "DeepSeek", False),
        ("groq", "Groq", False),
        ("openrouter", "OpenRouter", False),
        ("xai", "xAI", False),
        ("mistral", "Mistral", False),
        ("qwen", "Qwen Global", False),
        ("glm", "Z.AI GLM", False),
        ("minimax", "MiniMax Global", False),
        ("kimi", "Moonshot Kimi", False),
        ("nvidia", "NVIDIA NIM", False),
        ("azure", "Azure OpenAI", True),
    )
)
_PROVIDER_BY_SLUG = {item.slug: item for item in _PROVIDERS}


@dataclass(frozen=True, slots=True)
class LlmConnectionView:
    id: UUID
    label: str
    provider: str
    display_name: str
    base_url: str | None
    status: str
    credential_version: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ResolvedLlmCredential:
    connection_id: UUID
    provider: str
    base_url: str | None
    version: int
    api_key: str = field(repr=False)

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        if self.base_url:
            parsed = urlparse(self.base_url)
            port = f":{parsed.port}" if parsed.port not in {None, 443} else ""
            return (f"{parsed.scheme}://{parsed.hostname}{port}",)
        return ()

    def provenance(self, *, model: str) -> LlmReferenceSnapshot:
        return build_llm_reference_snapshot(
            provider=self.provider,
            model=model,
            connection_id=self.connection_id,
            credential_version=self.version,
            base_url=self.base_url,
        )


def llm_provider_catalog() -> tuple[LlmProviderCapability, ...]:
    return _PROVIDERS


class LlmConnectionService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        keyring: CredentialCryptoPort,
        idempotency_digest_key: bytes | None,
    ) -> None:
        if idempotency_digest_key is not None and len(idempotency_digest_key) < 32:
            raise ValueError("LLM idempotency digest key must contain at least 32 bytes")
        self._sessions = session_factory
        self._keyring = keyring
        self._digest_key = idempotency_digest_key

    @staticmethod
    def _set_tenant_scope(db: Session, tenant_id: UUID) -> None:
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )

    @staticmethod
    def _set_worker_job_scope(
        db: Session,
        *,
        job_id: UUID,
        tenant_id: UUID,
        lease_id: UUID,
        lease_owner: str,
    ) -> None:
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            for setting, value in (
                ("app.worker_job_id", job_id),
                ("app.worker_tenant_id", tenant_id),
                ("app.worker_lease_id", lease_id),
                ("app.worker_lease_owner", lease_owner),
            ):
                db.execute(
                    text(f"SELECT set_config('{setting}', :value, true)"),
                    {"value": str(value)},
                )

    @staticmethod
    def _authorize(principal: IdentityPrincipal, *, write: bool) -> None:
        allowed = {"owner", "admin"} if write else {"owner", "admin", "operator", "auditor"}
        if principal.role.value not in allowed:
            raise LlmConnectionError("LLM_CONNECTION_FORBIDDEN")

    @staticmethod
    def _label(label: str) -> tuple[str, str]:
        cleaned = " ".join(label.strip().split())
        if not 2 <= len(cleaned) <= 80 or any(ord(char) < 32 for char in cleaned):
            raise LlmConnectionError("LLM_CONNECTION_LABEL_INVALID")
        return cleaned, cleaned.casefold()

    @staticmethod
    def _endpoint(provider: str, base_url: str | None) -> str | None:
        capability = _PROVIDER_BY_SLUG.get(provider)
        if capability is None:
            raise LlmConnectionError("LLM_PROVIDER_UNSUPPORTED")
        if capability.requires_endpoint and not base_url:
            raise LlmConnectionError("LLM_ENDPOINT_REQUIRED")
        if not capability.requires_endpoint and base_url:
            raise LlmConnectionError("LLM_ENDPOINT_FORBIDDEN")
        if not base_url:
            return None
        if len(base_url) > 2048:
            raise LlmConnectionError("LLM_ENDPOINT_INVALID")
        parsed = urlparse(base_url.strip())
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise LlmConnectionError("LLM_ENDPOINT_INVALID")
        try:
            validate_backend_url(provider, base_url)
        except ValueError as exc:
            raise LlmConnectionError("LLM_ENDPOINT_INVALID") from exc
        return base_url.rstrip("/")

    @staticmethod
    def _context(tenant_id: UUID, connection_id: UUID, provider: str, version: int) -> bytes:
        return f"llm-credential/1:{tenant_id}:{connection_id}:{provider}:{version}".encode("ascii")

    def upsert(
        self,
        *,
        principal: IdentityPrincipal,
        idempotency_key: str,
        label: str,
        provider: str,
        api_key: str,
        base_url: str | None = None,
    ) -> UUID:
        if self._digest_key is None:
            raise LlmConnectionError("LLM_CONNECTION_WRITE_CAPABILITY_UNAVAILABLE")
        self._authorize(principal, write=True)
        provider = provider.strip().lower()
        endpoint = self._endpoint(provider, base_url)
        display_label, normalized_label = self._label(label)
        if not 8 <= len(api_key) <= 4096 or any(ord(char) < 33 for char in api_key):
            raise LlmConnectionError("LLM_CREDENTIAL_INVALID")
        if not 16 <= len(idempotency_key) <= 128 or any(
            ord(char) < 33 or ord(char) > 126 for char in idempotency_key
        ):
            raise LlmConnectionError("IDEMPOTENCY_KEY_INVALID")
        idem_hash = hashlib.sha256(f"{principal.tenant_id}:{idempotency_key}".encode()).hexdigest()
        request_digest = hmac.new(
            self._digest_key,
            json.dumps(
                {
                    "tenant": str(principal.tenant_id),
                    "label": normalized_label,
                    "provider": provider,
                    "base_url": endpoint,
                    "api_key": api_key,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
            hashlib.sha256,
        ).hexdigest()
        # Snapshot database identity first, then call the external Transit/KMS
        # service with no database transaction or row lock held.
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, principal.tenant_id)
            replay = db.scalar(
                select(LlmCredential).where(
                    LlmCredential.tenant_id == principal.tenant_id,
                    LlmCredential.idempotency_key_hash == idem_hash,
                )
            )
            if replay is not None:
                if not hmac.compare_digest(replay.request_digest, request_digest):
                    raise LlmConnectionError("IDEMPOTENCY_PAYLOAD_MISMATCH")
                return replay.connection_id
            current = db.scalar(
                select(LlmConnection).where(
                    LlmConnection.tenant_id == principal.tenant_id,
                    LlmConnection.label_normalized == normalized_label,
                )
            )
            if current is not None and (
                current.provider != provider or current.base_url != endpoint
            ):
                raise LlmConnectionError("LLM_CONNECTION_IDENTITY_IMMUTABLE")
            connection_id = current.id if current is not None else uuid4()
            prior_version = current.active_credential_version if current is not None else 0
        version = prior_version + 1
        plaintext = json.dumps({"api_key": api_key}, separators=(",", ":")).encode()
        envelope = self._keyring.encrypt(
            plaintext,
            context=self._context(principal.tenant_id, connection_id, provider, version),
        )
        try:
            with self._sessions() as db, db.begin():
                self._set_tenant_scope(db, principal.tenant_id)
                replay = db.scalar(
                    select(LlmCredential).where(
                        LlmCredential.tenant_id == principal.tenant_id,
                        LlmCredential.idempotency_key_hash == idem_hash,
                    )
                )
                if replay is not None:
                    if not hmac.compare_digest(replay.request_digest, request_digest):
                        raise LlmConnectionError("IDEMPOTENCY_PAYLOAD_MISMATCH")
                    return replay.connection_id
                connection = db.scalar(
                    select(LlmConnection)
                    .where(
                        LlmConnection.tenant_id == principal.tenant_id,
                        LlmConnection.label_normalized == normalized_label,
                    )
                    .with_for_update()
                )
                if connection is None:
                    if prior_version != 0:
                        raise LlmConnectionError("LLM_CONNECTION_CONFLICT")
                    connection = LlmConnection(
                        id=connection_id,
                        tenant_id=principal.tenant_id,
                        created_by_user_id=principal.user_id,
                        label=display_label,
                        label_normalized=normalized_label,
                        provider=provider,
                        base_url=endpoint,
                        status="ready",
                        active_credential_version=version,
                    )
                    db.add(connection)
                    db.flush()
                else:
                    if (
                        connection.id != connection_id
                        or connection.provider != provider
                        or connection.base_url != endpoint
                        or connection.active_credential_version != prior_version
                    ):
                        raise LlmConnectionError("LLM_CONNECTION_CONFLICT")
                    connection.status = "ready"
                    connection.disabled_at = None
                    connection.active_credential_version = version
                db.add(
                    LlmCredential(
                        tenant_id=principal.tenant_id,
                        connection_id=connection.id,
                        version=version,
                        key_id=envelope.key_id,
                        wrapped_key_nonce=envelope.wrapped_key_nonce,
                        wrapped_key=envelope.wrapped_key,
                        payload_nonce=envelope.payload_nonce,
                        ciphertext=envelope.ciphertext,
                        idempotency_key_hash=idem_hash,
                        request_digest=request_digest,
                        created_by_user_id=principal.user_id,
                    )
                )
                return connection.id
        except IntegrityError:
            with self._sessions() as db, db.begin():
                self._set_tenant_scope(db, principal.tenant_id)
                replay = db.scalar(
                    select(LlmCredential).where(
                        LlmCredential.tenant_id == principal.tenant_id,
                        LlmCredential.idempotency_key_hash == idem_hash,
                    )
                )
                if replay is not None and hmac.compare_digest(
                    replay.request_digest, request_digest
                ):
                    return replay.connection_id
            raise LlmConnectionError("LLM_CONNECTION_CONFLICT") from None

    def list_connections(self, *, principal: IdentityPrincipal) -> tuple[LlmConnectionView, ...]:
        self._authorize(principal, write=False)
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, principal.tenant_id)
            rows = tuple(
                db.scalars(
                    select(LlmConnection)
                    .where(LlmConnection.tenant_id == principal.tenant_id)
                    .order_by(LlmConnection.created_at)
                )
            )
            return tuple(
                LlmConnectionView(
                    id=row.id,
                    label=row.label,
                    provider=row.provider,
                    display_name=_PROVIDER_BY_SLUG[row.provider].display_name,
                    base_url=row.base_url,
                    status=row.status,
                    credential_version=row.active_credential_version,
                    created_at=row.created_at,
                )
                for row in rows
            )

    def assert_reference(
        self,
        *,
        tenant_id: UUID,
        connection_id: UUID,
        credential_version: int,
        provider: str,
        model: str,
    ) -> LlmReferenceSnapshot:
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, tenant_id)
            reference = db.execute(
                select(LlmCredential.id, LlmConnection.base_url)
                .join(
                    LlmConnection,
                    (LlmConnection.id == LlmCredential.connection_id)
                    & (LlmConnection.tenant_id == LlmCredential.tenant_id),
                )
                .where(
                    LlmCredential.tenant_id == tenant_id,
                    LlmCredential.connection_id == connection_id,
                    LlmCredential.version == credential_version,
                    LlmConnection.provider == provider,
                    LlmConnection.status == "ready",
                    LlmConnection.active_credential_version == credential_version,
                )
            ).one_or_none()
            if reference is None:
                raise LlmConnectionError("LLM_CREDENTIAL_NOT_FOUND")
            try:
                return build_llm_reference_snapshot(
                    provider=provider,
                    model=model,
                    connection_id=connection_id,
                    credential_version=credential_version,
                    base_url=reference.base_url,
                )
            except ValueError as exc:
                raise LlmConnectionError("LLM_PROVENANCE_UNAVAILABLE") from exc

    def resolve_for_worker(
        self,
        *,
        job_id: UUID,
        tenant_id: UUID,
        lease_id: UUID,
        lease_owner: str,
        attempt_number: int,
        connection_id: UUID,
        credential_version: int,
        expected_provider: str,
        actor_user_id: UUID,
    ) -> ResolvedLlmCredential:
        denied = False
        active_version: int | None = None
        with self._sessions() as db, db.begin():
            self._set_worker_job_scope(
                db,
                job_id=job_id,
                tenant_id=tenant_id,
                lease_id=lease_id,
                lease_owner=lease_owner,
            )
            connection = db.scalar(
                select(LlmConnection).where(
                    LlmConnection.id == connection_id,
                    LlmConnection.tenant_id == tenant_id,
                )
            )
            credential = db.scalar(
                select(LlmCredential).where(
                    LlmCredential.connection_id == connection_id,
                    LlmCredential.tenant_id == tenant_id,
                    LlmCredential.version == credential_version,
                )
            )
            if (
                connection is None
                or credential is None
                or connection.provider != expected_provider
                or connection.status != "ready"
                or connection.active_credential_version != credential_version
            ):
                if connection is not None:
                    denied = True
                    active_version = connection.active_credential_version
            else:
                envelope = CredentialEnvelope(
                    credential.key_id,
                    credential.wrapped_key_nonce,
                    credential.wrapped_key,
                    credential.payload_nonce,
                    credential.ciphertext,
                )
                resolved_provider = connection.provider
                resolved_base_url = connection.base_url
        if denied:
            self._record_access(
                tenant_id=tenant_id,
                job_id=job_id,
                lease_id=lease_id,
                lease_owner=lease_owner,
                attempt_number=attempt_number,
                connection_id=connection_id,
                credential_version=credential_version,
                actor_user_id=actor_user_id,
                outcome="denied",
                provider=expected_provider,
                extra_details={"active_credential_version": active_version},
            )
            raise LlmConnectionError("LLM_CREDENTIAL_NOT_FOUND")
        if connection is None or credential is None:
            raise LlmConnectionError("LLM_CREDENTIAL_NOT_FOUND")
        context = self._context(tenant_id, connection_id, expected_provider, credential_version)
        try:
            payload = json.loads(self._keyring.decrypt(envelope, context=context))
            api_key = payload["api_key"]
            if not isinstance(api_key, str) or not api_key:
                raise ValueError
        except (InvalidTag, ValueError, KeyError, TypeError, json.JSONDecodeError):
            self._record_access(
                tenant_id=tenant_id,
                job_id=job_id,
                lease_id=lease_id,
                lease_owner=lease_owner,
                attempt_number=attempt_number,
                connection_id=connection_id,
                credential_version=credential_version,
                actor_user_id=actor_user_id,
                outcome="failed",
                provider=expected_provider,
            )
            raise LlmConnectionError("LLM_CREDENTIAL_DECRYPTION_FAILED") from None
        self._record_access(
            tenant_id=tenant_id,
            job_id=job_id,
            lease_id=lease_id,
            lease_owner=lease_owner,
            attempt_number=attempt_number,
            connection_id=connection_id,
            credential_version=credential_version,
            actor_user_id=actor_user_id,
            outcome="allowed",
            provider=expected_provider,
        )
        return ResolvedLlmCredential(
            connection_id=connection_id,
            provider=resolved_provider,
            base_url=resolved_base_url,
            version=credential_version,
            api_key=api_key,
        )

    def _record_access(
        self,
        *,
        tenant_id: UUID,
        job_id: UUID,
        lease_id: UUID,
        lease_owner: str,
        attempt_number: int,
        connection_id: UUID,
        credential_version: int,
        actor_user_id: UUID,
        outcome: str,
        provider: str,
        extra_details: dict[str, object] | None = None,
    ) -> None:
        details: dict[str, object] = {"provider": provider}
        details.update(extra_details or {})
        with self._sessions() as db, db.begin():
            self._set_worker_job_scope(
                db,
                job_id=job_id,
                tenant_id=tenant_id,
                lease_id=lease_id,
                lease_owner=lease_owner,
            )
            db.add(
                LlmCredentialAccessEvent(
                    tenant_id=tenant_id,
                    connection_id=connection_id,
                    credential_version=credential_version,
                    job_id=job_id,
                    lease_id=lease_id,
                    attempt_number=attempt_number,
                    actor_user_id=actor_user_id,
                    purpose="analysis",
                    outcome=outcome,
                    details=details,
                    occurred_at=datetime.now(UTC),
                )
            )

    def disable(self, *, principal: IdentityPrincipal, connection_id: UUID) -> None:
        if self._digest_key is None:
            raise LlmConnectionError("LLM_CONNECTION_WRITE_CAPABILITY_UNAVAILABLE")
        self._authorize(principal, write=True)
        with self._sessions() as db, db.begin():
            self._set_tenant_scope(db, principal.tenant_id)
            row = db.scalar(
                select(LlmConnection)
                .where(
                    LlmConnection.id == connection_id,
                    LlmConnection.tenant_id == principal.tenant_id,
                )
                .with_for_update()
            )
            if row is None:
                raise LlmConnectionError("LLM_CONNECTION_NOT_FOUND")
            row.status = "disabled"
            row.disabled_at = datetime.now(UTC)

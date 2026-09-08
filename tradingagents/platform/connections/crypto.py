"""Envelope encryption for tenant-bound exchange credentials."""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol

import httpx


@dataclass(frozen=True, slots=True)
class CredentialEnvelope:
    key_id: str
    wrapped_key_nonce: bytes = field(repr=False)
    wrapped_key: bytes = field(repr=False)
    payload_nonce: bytes = field(repr=False)
    ciphertext: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not self.key_id or len(self.key_id) > 160:
            raise ValueError("credential key id is invalid")
        if (
            len(self.wrapped_key_nonce) not in {0, 12}
            or not 1 <= len(self.wrapped_key) <= 8192
            or len(self.payload_nonce) != 12
            or len(self.ciphertext) < 17
        ):
            raise ValueError("credential envelope is malformed")


class CredentialCryptoPort(Protocol):
    """Narrow cryptographic boundary used by the broker application service."""

    @property
    def active_key_id(self) -> str: ...

    def encrypt(self, plaintext: bytes, *, context: bytes) -> CredentialEnvelope: ...

    def decrypt(self, envelope: CredentialEnvelope, *, context: bytes) -> bytes: ...

    def rotate(self, envelope: CredentialEnvelope, *, context: bytes) -> CredentialEnvelope: ...

    def needs_rewrap(self, envelope: CredentialEnvelope) -> bool: ...


class CredentialKeyring:
    """AES-256-GCM keyring with an explicit active key for safe rotation."""

    def __init__(self, *, keys: Mapping[str, bytes], active_key_id: str) -> None:
        normalized = dict(keys)
        if not normalized or active_key_id not in normalized:
            raise ValueError("active credential key must exist in the keyring")
        for key_id, key in normalized.items():
            if not key_id or len(key_id) > 160 or len(key) != 32:
                raise ValueError("every credential key must be AES-256 and have a safe id")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        self._aes = MappingProxyType({key_id: AESGCM(key) for key_id, key in normalized.items()})
        self.active_key_id = active_key_id

    def encrypt(self, plaintext: bytes, *, context: bytes) -> CredentialEnvelope:
        if not plaintext or not context:
            raise ValueError("credential plaintext and context are required")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        data_key = secrets.token_bytes(32)
        payload_nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(data_key).encrypt(payload_nonce, plaintext, context + b":payload")
        wrapped_key_nonce = secrets.token_bytes(12)
        wrapped_key = self._aes[self.active_key_id].encrypt(
            wrapped_key_nonce, data_key, context + b":dek"
        )
        return CredentialEnvelope(
            self.active_key_id,
            wrapped_key_nonce,
            wrapped_key,
            payload_nonce,
            ciphertext,
        )

    def decrypt(self, envelope: CredentialEnvelope, *, context: bytes) -> bytes:
        if not context:
            raise ValueError("credential context is required")
        try:
            cipher = self._aes[envelope.key_id]
        except KeyError as exc:
            raise ValueError("credential references an unavailable key") from exc
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        data_key = cipher.decrypt(
            envelope.wrapped_key_nonce,
            envelope.wrapped_key,
            context + b":dek",
        )
        return AESGCM(data_key).decrypt(
            envelope.payload_nonce,
            envelope.ciphertext,
            context + b":payload",
        )

    def rotate(self, envelope: CredentialEnvelope, *, context: bytes) -> CredentialEnvelope:
        try:
            old_cipher = self._aes[envelope.key_id]
        except KeyError as exc:
            raise ValueError("credential references an unavailable key") from exc
        data_key = old_cipher.decrypt(
            envelope.wrapped_key_nonce,
            envelope.wrapped_key,
            context + b":dek",
        )
        wrapped_key_nonce = secrets.token_bytes(12)
        wrapped_key = self._aes[self.active_key_id].encrypt(
            wrapped_key_nonce, data_key, context + b":dek"
        )
        return CredentialEnvelope(
            self.active_key_id,
            wrapped_key_nonce,
            wrapped_key,
            envelope.payload_nonce,
            envelope.ciphertext,
        )

    def needs_rewrap(self, envelope: CredentialEnvelope) -> bool:
        return envelope.key_id != self.active_key_id


class TransitCredentialKeyring:
    """Envelope encryption whose data-encryption keys are wrapped by Vault Transit."""

    def __init__(
        self,
        *,
        base_url: str,
        key_name: str,
        token: str,
        namespace: str | None = None,
        timeout_seconds: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("transit base URL must be an HTTPS origin")
        if not key_name or len(key_name) > 120 or not key_name.replace("-", "").isalnum():
            raise ValueError("transit key name is invalid")
        if not token or len(token) > 4096 or any(ord(char) < 33 for char in token):
            raise ValueError("transit token is invalid")
        if namespace is not None and (
            not namespace or len(namespace) > 200 or any(ord(char) < 33 for char in namespace)
        ):
            raise ValueError("transit namespace is invalid")
        if not 1.0 <= timeout_seconds <= 30.0:
            raise ValueError("transit timeout must be between 1 and 30 seconds")
        self._base_url = f"{parsed.scheme}://{parsed.hostname.lower()}"
        if parsed.port not in {None, 443}:
            self._base_url += f":{parsed.port}"
        self._key_name = key_name
        self._token = token
        self._namespace = namespace
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

    @property
    def active_key_id(self) -> str:
        return f"transit:{self._key_name}"

    def encrypt(self, plaintext: bytes, *, context: bytes) -> CredentialEnvelope:
        if not plaintext or not context:
            raise ValueError("credential plaintext and context are required")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        data_key = secrets.token_bytes(32)
        payload_nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(data_key).encrypt(payload_nonce, plaintext, context + b":payload")
        binding = hashlib.sha256(context + b":dek").digest() + data_key
        wrapped_key = self._transit("encrypt", plaintext=binding).encode("ascii")
        return CredentialEnvelope(
            self.active_key_id,
            b"",
            wrapped_key,
            payload_nonce,
            ciphertext,
        )

    def decrypt(self, envelope: CredentialEnvelope, *, context: bytes) -> bytes:
        if envelope.key_id != self.active_key_id or envelope.wrapped_key_nonce:
            raise ValueError("credential references an unavailable transit key")
        wrapped = envelope.wrapped_key.decode("ascii")
        decoded = self._transit("decrypt", ciphertext=wrapped)
        binding = base64.b64decode(decoded, validate=True)
        expected = hashlib.sha256(context + b":dek").digest()
        if len(binding) != 64 or not secrets.compare_digest(binding[:32], expected):
            raise ValueError("credential transit context does not match")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        return AESGCM(binding[32:]).decrypt(
            envelope.payload_nonce,
            envelope.ciphertext,
            context + b":payload",
        )

    def rotate(self, envelope: CredentialEnvelope, *, context: bytes) -> CredentialEnvelope:
        if envelope.key_id != self.active_key_id or envelope.wrapped_key_nonce:
            raise ValueError("credential references an unavailable transit key")
        # Rewrap updates the Transit key version without exposing the DEK.
        wrapped = self._transit("rewrap", ciphertext=envelope.wrapped_key.decode("ascii")).encode(
            "ascii"
        )
        return CredentialEnvelope(
            self.active_key_id,
            b"",
            wrapped,
            envelope.payload_nonce,
            envelope.ciphertext,
        )

    @staticmethod
    def needs_rewrap(_envelope: CredentialEnvelope) -> bool:
        # Transit embeds the master-key version inside its opaque ciphertext.
        # A requested maintenance pass therefore rewraps every selected DEK.
        return True

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _transit(
        self,
        operation: str,
        *,
        plaintext: bytes | None = None,
        ciphertext: str | None = None,
    ) -> str:
        url = f"{self._base_url}/v1/transit/{operation}/{self._key_name}"
        payload = (
            {"plaintext": base64.b64encode(plaintext).decode("ascii")}
            if plaintext is not None
            else {"ciphertext": ciphertext}
        )
        headers = {"X-Vault-Token": self._token, "Content-Type": "application/json"}
        if self._namespace:
            headers["X-Vault-Namespace"] = self._namespace
        try:
            response = self._client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            if len(response.content) > 32_768:
                raise ValueError("transit response is too large")
            data = response.json().get("data", {})
            field = "plaintext" if operation == "decrypt" else "ciphertext"
            value = data.get(field)
            if not isinstance(value, str) or not value or len(value) > 8192:
                raise ValueError("transit response is malformed")
            return value
        except (httpx.HTTPError, ValueError, AttributeError, TypeError):
            raise ValueError("credential transit operation failed") from None

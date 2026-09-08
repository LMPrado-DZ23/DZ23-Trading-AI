"""Cryptographic ports and primitives for MFA and recovery credentials."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class EncryptedSecret:
    """Opaque envelope persisted by the compliance store."""

    key_id: str
    nonce: bytes = field(repr=False)
    ciphertext: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not self.key_id or len(self.key_id) > 160:
            raise ValueError("key_id must contain 1-160 characters")
        if len(self.nonce) < 12 or not self.ciphertext:
            raise ValueError("encrypted secret envelope is malformed")


@runtime_checkable
class SecretCryptoPort(Protocol):
    """Only allowed boundary for persisting or recovering an MFA secret."""

    def encrypt(self, plaintext: bytes, *, context: bytes) -> EncryptedSecret: ...

    def decrypt(self, encrypted: EncryptedSecret, *, context: bytes) -> bytes: ...


class AesGcmSecretCrypto:
    """AES-256-GCM adapter suitable for a key supplied by a secret manager.

    ``cryptography`` is deliberately imported at construction time so the
    compliance domain and migrations remain importable without the adapter's
    optional runtime dependency.
    """

    def __init__(self, *, key: bytes, key_id: str) -> None:
        if len(key) != 32:
            raise ValueError("AES-GCM key must contain exactly 32 bytes")
        if not key_id or len(key_id) > 160:
            raise ValueError("key_id must contain 1-160 characters")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        self._cipher = AESGCM(key)
        self._key_id = key_id

    def encrypt(self, plaintext: bytes, *, context: bytes) -> EncryptedSecret:
        if not plaintext or not context:
            raise ValueError("plaintext and authenticated context are required")
        nonce = secrets.token_bytes(12)
        ciphertext = self._cipher.encrypt(nonce, plaintext, context)
        return EncryptedSecret(key_id=self._key_id, nonce=nonce, ciphertext=ciphertext)

    def decrypt(self, encrypted: EncryptedSecret, *, context: bytes) -> bytes:
        if encrypted.key_id != self._key_id:
            raise ValueError("encrypted secret references an unavailable key")
        if not context:
            raise ValueError("authenticated context is required")
        return self._cipher.decrypt(encrypted.nonce, encrypted.ciphertext, context)


@dataclass(frozen=True, slots=True)
class RecoveryCodeHasher:
    """Issue user-facing recovery codes and persist only keyed digests."""

    pepper: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if len(self.pepper) < 32:
            raise ValueError("recovery-code pepper must contain at least 32 bytes")

    @staticmethod
    def issue() -> str:
        raw = base64.b32encode(secrets.token_bytes(10)).decode("ascii").rstrip("=")
        return f"{raw[:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:]}"

    def digest(self, code: str, *, tenant_id: str, user_id: str) -> str:
        normalized = code.strip().replace("-", "").upper()
        if len(normalized) != 16 or not normalized.isascii() or not normalized.isalnum():
            raise ValueError("recovery code is malformed")
        context = f"mfa-recovery:{tenant_id}:{user_id}".encode("ascii")
        return hmac.new(
            self.pepper,
            context + b"\0" + normalized.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def matches(left: str, right: str) -> bool:
        return hmac.compare_digest(left, right)


class TotpEngine:
    """Small RFC 6238 SHA-1 implementation with replay-aware counters."""

    def __init__(self, *, digits: int = 6, period_seconds: int = 30) -> None:
        if digits not in {6, 8}:
            raise ValueError("TOTP digits must be 6 or 8")
        if period_seconds < 15 or period_seconds > 120:
            raise ValueError("TOTP period must be between 15 and 120 seconds")
        self.digits = digits
        self.period_seconds = period_seconds

    @staticmethod
    def issue_secret() -> str:
        return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_secret(secret: str) -> bytes:
        if not secret or len(secret) > 256:
            raise ValueError("TOTP secret is malformed")
        normalized = secret.strip().replace(" ", "").upper()
        padding = "=" * ((8 - len(normalized) % 8) % 8)
        try:
            return base64.b32decode(normalized + padding, casefold=False)
        except (ValueError, base64.binascii.Error) as exc:
            raise ValueError("TOTP secret is malformed") from exc

    def code_at_counter(self, secret: str, counter: int) -> str:
        if counter < 0:
            raise ValueError("TOTP counter cannot be negative")
        digest = hmac.new(
            self._decode_secret(secret), struct.pack(">Q", counter), hashlib.sha1
        ).digest()
        offset = digest[-1] & 0x0F
        binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
        return f"{binary % (10**self.digits):0{self.digits}d}"

    def code_at(self, secret: str, timestamp: datetime) -> str:
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        counter = int(timestamp.timestamp()) // self.period_seconds
        return self.code_at_counter(secret, counter)

    def accepted_counter(
        self,
        secret: str,
        code: str,
        timestamp: datetime,
        *,
        window: int = 1,
        after_counter: int | None = None,
    ) -> int | None:
        if not code.isascii() or not code.isdigit() or len(code) != self.digits:
            return None
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        if window < 0 or window > 2:
            raise ValueError("TOTP window must be between zero and two periods")
        current = int(timestamp.timestamp()) // self.period_seconds
        for counter in range(max(0, current - window), current + window + 1):
            if after_counter is not None and counter <= after_counter:
                continue
            if hmac.compare_digest(self.code_at_counter(secret, counter), code):
                return counter
        return None

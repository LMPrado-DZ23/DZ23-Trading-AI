"""Password and opaque-token primitives.

Raw bearer/reset tokens are deliberately returned only at issuance time.  The
database stores a domain-separated HMAC digest, so a database-only compromise
does not provide a usable token or an offline token-verification oracle.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type


class PasswordManager:
    """Argon2id password hashing with OWASP-aligned memory-hard defaults."""

    def __init__(self) -> None:
        self._hasher = PasswordHasher(
            time_cost=3,
            memory_cost=64 * 1024,
            parallelism=2,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )
        # Used to keep the missing-user authentication path computationally
        # equivalent without ever depending on a real user's password hash.
        self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(32))

    def hash(self, password: str) -> str:
        if len(password) < 12:
            raise ValueError("password must contain at least 12 characters")
        if len(password.encode("utf-8")) > 1024:
            raise ValueError("password is too long")
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        if len(password.encode("utf-8")) > 1024:
            return False
        try:
            return self._hasher.verify(password_hash, password)
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            return False

    def verify_missing_user(self, password: str) -> None:
        """Consume the normal verification cost for an unknown account."""

        self.verify(self._dummy_hash, password)

    def needs_rehash(self, password_hash: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except InvalidHashError:
            return True


@dataclass(frozen=True)
class TokenHasher:
    """Issue high-entropy tokens and persist only keyed SHA-256 digests."""

    pepper: bytes

    def __post_init__(self) -> None:
        if len(self.pepper) < 32:
            raise ValueError("token pepper must contain at least 32 bytes")

    def issue(self) -> str:
        return secrets.token_urlsafe(32)

    def digest(self, token: str, *, purpose: str) -> str:
        if not token or not purpose:
            raise ValueError("token and purpose are required")
        if len(token.encode("utf-8")) > 4096:
            raise ValueError("token is too long")
        message = purpose.encode("ascii") + b"\0" + token.encode("utf-8")
        return hmac.new(self.pepper, message, hashlib.sha256).hexdigest()

    @staticmethod
    def matches(left: str, right: str) -> bool:
        return hmac.compare_digest(left, right)

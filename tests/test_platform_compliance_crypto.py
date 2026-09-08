from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from cryptography.exceptions import InvalidTag

from tradingagents.platform.compliance import (
    AesGcmSecretCrypto,
    RecoveryCodeHasher,
    TotpEngine,
)


def test_aes_gcm_requires_context_and_rejects_cross_context_decryption():
    crypto = AesGcmSecretCrypto(key=b"x" * 32, key_id="test-key")
    encrypted = crypto.encrypt(b"sensitive", context=b"tenant-a:user-a")
    assert b"sensitive" not in encrypted.ciphertext
    assert "sensitive" not in repr(encrypted)
    assert crypto.decrypt(encrypted, context=b"tenant-a:user-a") == b"sensitive"
    with pytest.raises(InvalidTag):
        crypto.decrypt(encrypted, context=b"tenant-b:user-a")


def test_recovery_code_digest_is_domain_separated_and_secret_not_in_repr():
    hasher = RecoveryCodeHasher(b"p" * 32)
    code = hasher.issue()
    first = hasher.digest(code, tenant_id="tenant-a", user_id="user-a")
    second = hasher.digest(code, tenant_id="tenant-b", user_id="user-a")
    assert first != second
    assert code.replace("-", "") not in first
    assert b"p" * 32 not in repr(hasher).encode()


def test_totp_known_vector_window_and_replay_floor():
    engine = TotpEngine(digits=8)
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    timestamp = datetime.fromtimestamp(59, tz=UTC)
    assert engine.code_at(secret, timestamp) == "94287082"
    counter = engine.accepted_counter(secret, "94287082", timestamp, window=0)
    assert counter == 1
    assert (
        engine.accepted_counter(
            secret,
            "94287082",
            timestamp + timedelta(seconds=1),
            window=1,
            after_counter=counter,
        )
        is None
    )


@pytest.mark.parametrize("bad_code", ["", "12345", "1234567", "abcdef", "１２３４５６"])
def test_totp_rejects_malformed_codes_without_exception(bad_code):
    engine = TotpEngine()
    assert engine.accepted_counter(engine.issue_secret(), bad_code, datetime.now(UTC)) is None

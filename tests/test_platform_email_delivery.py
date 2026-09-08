from __future__ import annotations

import asyncio
import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage

import pytest

from tradingagents.platform.api.email_delivery import (
    IdentityDeliveryUnavailable,
    IdentityEmailKind,
    SmtpIdentityTokenDelivery,
    SmtpSettings,
)


def _delivery(kind: IdentityEmailKind = IdentityEmailKind.VERIFY) -> SmtpIdentityTokenDelivery:
    return SmtpIdentityTokenDelivery(
        settings=SmtpSettings(
            host="smtp.example.test",
            port=465,
            username="mailer",
            password="not-a-production-secret",
            sender="TradingAgents <noreply@example.test>",
        ),
        public_url="https://trading.example.test",
        kind=kind,
    )


def test_message_uses_https_and_percent_encodes_opaque_token() -> None:
    token = "tenant.secret+with/slash?and=equals-12345678901234567890"
    message = _delivery()._message(
        recipient="person@example.test",
        token=token,
        expires_at=datetime(2026, 9, 9, tzinfo=UTC),
    )
    body = message.get_content()
    assert "https://trading.example.test/verificar-email?token=" in body
    assert "secret%2Bwith%2Fslash%3Fand%3Dequals" in body
    assert token not in body


def test_delivery_never_places_token_in_headers_or_repr() -> None:
    delivery = _delivery(IdentityEmailKind.RECOVER)
    token = "opaque-token-that-must-remain-private-1234567890"
    message = delivery._message(
        recipient="person@example.test",
        token=token,
        expires_at=datetime(2026, 9, 9, tzinfo=UTC),
    )
    assert token not in repr(delivery)
    assert token not in "\n".join(f"{key}: {value}" for key, value in message.items())
    assert isinstance(message, EmailMessage)


@pytest.mark.parametrize("field", ["recipient", "sender"])
def test_header_injection_is_rejected(field: str) -> None:
    if field == "sender":
        with pytest.raises(ValueError, match="remetente"):
            SmtpSettings(
                host="smtp.example.test",
                port=465,
                username="mailer",
                password="secret",
                sender="safe@example.test\r\nBcc: attacker@example.test",
            )
        return
    with pytest.raises(ValueError, match="destinatário"):
        _delivery()._message(
            recipient="safe@example.test\r\nBcc: attacker@example.test",
            token="opaque-token-that-must-remain-private-1234567890",
            expires_at=datetime(2026, 9, 9, tzinfo=UTC),
        )


def test_plain_http_identity_links_are_forbidden() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        SmtpIdentityTokenDelivery(
            settings=_delivery().settings,
            public_url="http://trading.example.test",
            kind=IdentityEmailKind.VERIFY,
        )


def test_smtp_recipient_failure_is_sanitized_without_pii_or_token(monkeypatch) -> None:
    delivery = _delivery()
    recipient = "private-person@example.test"
    token = "opaque-token-that-must-remain-private-1234567890"

    def fail(_self, _message) -> None:
        raise smtplib.SMTPRecipientsRefused({recipient: (550, b"mailbox rejected")})

    monkeypatch.setattr(SmtpIdentityTokenDelivery, "_send", fail)
    with pytest.raises(IdentityDeliveryUnavailable) as caught:
        asyncio.run(
            delivery(
                recipient=recipient,
                token=token,
                expires_at=datetime(2026, 9, 9, tzinfo=UTC),
            )
        )
    assert recipient not in str(caught.value)
    assert token not in str(caught.value)
    assert caught.value.__cause__ is None

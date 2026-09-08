"""Transactional SMTP delivery for identity links.

Raw tokens exist only in memory long enough to build the HTTPS link and are
never logged or returned through the public API.
"""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from enum import StrEnum
from functools import partial
from urllib.parse import quote

from anyio import to_thread


class IdentityEmailKind(StrEnum):
    VERIFY = "verificar-email"
    RECOVER = "recuperar-senha"


class IdentityDeliveryUnavailable(RuntimeError):
    """Sanitized delivery failure that never exposes recipient or SMTP detail."""


@dataclass(frozen=True, slots=True)
class SmtpSettings:
    host: str
    port: int
    username: str
    password: str = field(repr=False)
    sender: str
    use_implicit_tls: bool = True
    timeout_seconds: float = 15.0

    def __post_init__(self) -> None:
        if not self.host.strip() or any(character.isspace() for character in self.host):
            raise ValueError("SMTP host inválido")
        if self.port < 1 or self.port > 65_535:
            raise ValueError("SMTP port inválida")
        if not self.username or not self.password:
            raise ValueError("credenciais SMTP são obrigatórias")
        if "\n" in self.sender or "\r" in self.sender or "@" not in self.sender:
            raise ValueError("remetente SMTP inválido")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 60:
            raise ValueError("timeout SMTP deve estar entre zero e 60 segundos")


@dataclass(frozen=True, slots=True)
class SmtpIdentityTokenDelivery:
    settings: SmtpSettings
    public_url: str
    kind: IdentityEmailKind

    def __post_init__(self) -> None:
        if not self.public_url.startswith("https://"):
            raise ValueError("links de identidade exigem URL pública HTTPS")

    async def __call__(self, *, recipient: str, token: str, expires_at: datetime) -> None:
        message = self._message(recipient=recipient, token=token, expires_at=expires_at)
        try:
            await to_thread.run_sync(partial(self._send, message))
        except (smtplib.SMTPException, OSError, ssl.SSLError):
            raise IdentityDeliveryUnavailable("identity email delivery unavailable") from None

    def _message(self, *, recipient: str, token: str, expires_at: datetime) -> EmailMessage:
        if "\n" in recipient or "\r" in recipient or "@" not in recipient:
            raise ValueError("destinatário inválido")
        if len(token) < 32 or expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("material de identidade inválido")
        url = f"{self.public_url.rstrip('/')}/{self.kind.value}?token={quote(token, safe='')}"
        if self.kind is IdentityEmailKind.VERIFY:
            subject = "Confirme seu e-mail — TradingAgents DZ23"
            intro = "Confirme seu e-mail para concluir a configuração da sua conta."
        else:
            subject = "Recupere sua senha — TradingAgents DZ23"
            intro = "Use este link para definir uma nova senha."
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.settings.sender
        message["To"] = recipient
        message.set_content(
            f"{intro}\n\n{url}\n\n"
            f"O link expira em {expires_at.isoformat()}. Se não foi você, ignore esta mensagem."
        )
        return message

    def _send(self, message: EmailMessage) -> None:
        context = ssl.create_default_context()
        if self.settings.use_implicit_tls:
            with smtplib.SMTP_SSL(
                self.settings.host,
                self.settings.port,
                timeout=self.settings.timeout_seconds,
                context=context,
            ) as client:
                client.login(self.settings.username, self.settings.password)
                client.send_message(message)
            return
        with smtplib.SMTP(
            self.settings.host,
            self.settings.port,
            timeout=self.settings.timeout_seconds,
        ) as client:
            client.starttls(context=context)
            client.login(self.settings.username, self.settings.password)
            client.send_message(message)

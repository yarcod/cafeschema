"""SMTP delivery.

Stdlib only, STARTTLS on port 587 — the same shape as the sender already in
production in ../charge-amps, minus the attachment handling.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from .config import EmailConfig
from .models import Message


class SendError(Exception):
    """Raised when a message could not be delivered."""


def send(message: Message, cfg: EmailConfig, *, password: str) -> None:
    """Send one message to its whole group in a single SMTP transaction."""
    if not password:
        raise SendError(
            "SMTP-lösenord saknas: sätt miljövariabeln SMTP_PASSWORD."
        )

    email = EmailMessage()
    email["From"] = cfg.from_address
    # A parent's address is not ours to show other parents, so the group only
    # travels in the SMTP envelope. To is the empty RFC 5322 group: it shows
    # the mail wasn't addressed to anyone visible, and delivers nowhere —
    # pointing it at our own address would copy every reminder to that inbox.
    email["To"] = "undisclosed-recipients:;"
    email["Subject"] = message.subject
    if cfg.reply_to:
        email["Reply-To"] = cfg.reply_to
    email.set_content(message.body)
    if message.html_body:
        email.add_alternative(message.html_body, subtype="html")

    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port) as server:
            server.starttls()
            server.login(cfg.smtp_user, password)
            server.send_message(email, to_addrs=list(message.to))
    except (smtplib.SMTPException, OSError) as exc:
        # Never str(exc): SMTPRecipientsRefused is the dict of refused
        # addresses and server replies often echo <addr>. Actions logs are public.
        code = getattr(exc, "smtp_code", None) or getattr(exc, "errno", None) or ""
        reason = " ".join(filter(None, (type(exc).__name__, str(code))))
        raise SendError(
            f"Kunde inte skicka \"{message.subject}\" till "
            f"{len(message.to)} mottagare: {reason}"
        ) from exc

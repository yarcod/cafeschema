"""Swedish email copy for account/swap events. SMTP delivery mirrors
duty_mailer's email_sender.py: stdlib smtplib, STARTTLS.
"""

from __future__ import annotations

import smtplib
from collections.abc import Sequence
from email.message import EmailMessage

_smtp_config: dict[str, str | int] = {}


def configure(*, host: str, port: int, user: str, password: str, from_address: str) -> None:
    _smtp_config.update(
        host=host, port=port, user=user, password=password, from_address=from_address
    )


def _send(to: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = _smtp_config["from_address"]
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(_smtp_config["host"], _smtp_config["port"]) as server:
        server.starttls()
        server.login(_smtp_config["user"], _smtp_config["password"])
        server.send_message(message)


def _send_each(emails: Sequence[str], subject: str, body: str) -> None:
    """One message per recipient, never a shared To:.

    A slot's events now concern both of a player's parents, but each still
    gets a mail addressed to them alone — as before — rather than a joint
    thread neither of them asked to be on.
    """
    for email in emails:
        _send(email, subject, body)


def send_login_link(email: str, link: str) -> None:
    _send(
        email,
        "Din inloggningslänk till Caféschema",
        f"Klicka på länken för att logga in (giltig i 20 minuter):\n\n{link}\n\n"
        "Om du inte bad om den kan du ignorera det här mailet.",
    )


def send_swap_proposed(
    emails: Sequence[str], *, proposer_name: str, proposer_slot: str, target_slot: str,
    link: str | None = None,
) -> None:
    body = (
        f"{proposer_name} föreslår att byta sitt pass ({proposer_slot}) mot ert "
        f"({target_slot})."
    )
    if link:
        body += f"\n\nSvara ja eller nej här:\n{link}"
    else:
        body += " Logga in för att acceptera eller avböja."
    _send_each(emails, f"{proposer_name} vill byta pass med er", body)


def send_swap_accepted(
    emails: Sequence[str], *, accepter_name: str, your_old_slot: str, your_new_slot: str,
    link: str | None = None,
) -> None:
    body = (
        f"{accepter_name} accepterade bytet. Ni har nu {your_new_slot} istället för "
        f"{your_old_slot}."
    )
    if link:
        body += f"\n\nErt schema:\n{link}"
    _send_each(emails, "Ert bytesförslag accepterades", body)


def send_swap_declined(
    emails: Sequence[str], *, decliner_name: str, your_slot: str,
    link: str | None = None,
) -> None:
    body = f"{decliner_name} avböjde bytet. Ni behåller {your_slot}."
    if link:
        body += f"\n\nErt schema:\n{link}"
    _send_each(emails, "Ert bytesförslag avböjdes", body)

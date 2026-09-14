"""Swedish email copy for account/swap events. SMTP delivery mirrors
duty_mailer's email_sender.py: stdlib smtplib, STARTTLS.
"""

from __future__ import annotations

import smtplib
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


def send_login_link(email: str, link: str) -> None:
    _send(
        email,
        "Din inloggningslänk till Caféschema",
        f"Klicka på länken för att logga in (giltig i 20 minuter):\n\n{link}\n\n"
        "Om du inte bad om den kan du ignorera det här mailet.",
    )


def send_swap_proposed(
    email: str, *, proposer_name: str, proposer_slot: str, target_slot: str,
    link: str | None = None,
) -> None:
    body = (
        f"{proposer_name} föreslår att byta sitt pass ({proposer_slot}) mot ditt "
        f"({target_slot})."
    )
    if link:
        body += f"\n\nSvara ja eller nej här:\n{link}"
    else:
        body += " Logga in för att acceptera eller avböja."
    _send(email, f"{proposer_name} vill byta pass med dig", body)


def send_swap_accepted(
    email: str, *, accepter_name: str, your_old_slot: str, your_new_slot: str,
    link: str | None = None,
) -> None:
    body = (
        f"{accepter_name} accepterade bytet. Du har nu {your_new_slot} istället för "
        f"{your_old_slot}."
    )
    if link:
        body += f"\n\nDitt schema:\n{link}"
    _send(email, "Ditt bytesförslag accepterades", body)


def send_swap_declined(
    email: str, *, decliner_name: str, your_slot: str, link: str | None = None,
) -> None:
    body = f"{decliner_name} avböjde bytet. Du behåller {your_slot}."
    if link:
        body += f"\n\nDitt schema:\n{link}"
    _send(email, "Ditt bytesförslag avböjdes", body)

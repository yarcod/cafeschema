from __future__ import annotations

import smtplib

import pytest

from duty_mailer.config import EmailConfig
from duty_mailer.email_sender import SendError, send
from duty_mailer.models import Message

CFG = EmailConfig(
    smtp_host="smtp.example.com",
    smtp_port=587,
    smtp_user="robot@example.com",
    from_address="robot@example.com",
    reply_to="styrelsen@example.com",
)

MSG = Message(
    to=("a@x.se", "b@x.se"), subject="Påminnelse — matchvärd i morgon", body="Hej!"
)


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host, port):
        self.host, self.port = host, port
        self.started_tls = False
        self.login_args = None
        self.sent = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, user, password):
        self.login_args = (user, password)

    def send_message(self, msg):
        self.sent.append(msg)


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


def test_connects_with_starttls_and_logs_in(smtp):
    send(MSG, CFG, password="hemligt")
    server = smtp.instances[0]
    assert (server.host, server.port) == ("smtp.example.com", 587)
    assert server.started_tls is True
    assert server.login_args == ("robot@example.com", "hemligt")


def test_sends_to_the_whole_group(smtp):
    send(MSG, CFG, password="hemligt")
    sent = smtp.instances[0].sent[0]
    assert sent["To"] == "a@x.se, b@x.se"
    assert sent["From"] == "robot@example.com"
    assert sent["Subject"] == "Påminnelse — matchvärd i morgon"
    assert sent["Reply-To"] == "styrelsen@example.com"
    assert sent.get_content().strip() == "Hej!"


def test_omits_reply_to_when_not_configured(smtp):
    cfg = EmailConfig(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="robot@example.com",
        from_address="robot@example.com",
    )
    send(MSG, cfg, password="hemligt")
    assert smtp.instances[0].sent[0]["Reply-To"] is None


def test_rejects_an_empty_password():
    with pytest.raises(SendError, match="SMTP_PASSWORD"):
        send(MSG, CFG, password="")


def test_smtp_failures_are_wrapped(monkeypatch):
    def boom(host, port):
        raise smtplib.SMTPAuthenticationError(535, b"nope")

    monkeypatch.setattr(smtplib, "SMTP", boom)
    with pytest.raises(SendError, match="Kunde inte skicka"):
        send(MSG, CFG, password="hemligt")

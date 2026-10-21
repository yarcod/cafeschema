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
        self.envelopes = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, user, password):
        self.login_args = (user, password)

    def send_message(self, msg, from_addr=None, to_addrs=None):
        self.sent.append(msg)
        self.envelopes.append(to_addrs)


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
    assert smtp.instances[0].envelopes[0] == ["a@x.se", "b@x.se"]
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


def test_recipients_are_hidden_from_each_other(smtp):
    """A parent's address is not ours to show other parents. The group is
    only in the envelope; To is the empty undisclosed-recipients group, so no
    mailbox of ours gets a copy either."""
    send(MSG, CFG, password="hemligt")
    assert len(smtp.instances) == 1
    assert len(smtp.instances[0].sent) == 1
    sent = smtp.instances[0].sent[0]
    assert sent["To"] == "undisclosed-recipients:;"
    assert sent["Cc"] is None
    assert sent["Bcc"] is None
    assert smtp.instances[0].envelopes[0] == list(MSG.to)


def test_send_error_does_not_leak_addresses(monkeypatch):
    def boom(host, port):
        raise smtplib.SMTPException("nope")

    monkeypatch.setattr(smtplib, "SMTP", boom)
    with pytest.raises(SendError) as info:
        send(MSG, CFG, password="hemligt")
    assert "a@x.se" not in str(info.value)
    assert "2 mottagare" in str(info.value)


@pytest.mark.parametrize(
    "exc",
    [
        smtplib.SMTPRecipientsRefused({"a@x.se": (550, b"no such user <a@x.se>")}),
        smtplib.SMTPDataError(550, b"5.7.1 <a@x.se>: rejected"),
    ],
)
def test_send_error_hides_addresses_echoed_by_smtp_exceptions(monkeypatch, exc):
    def boom(host, port):
        raise exc

    monkeypatch.setattr(smtplib, "SMTP", boom)
    with pytest.raises(SendError) as info:
        send(MSG, CFG, password="hemligt")
    text = str(info.value)
    assert "a@x.se" not in text and "b@x.se" not in text
    assert type(exc).__name__ in text
    assert "2 mottagare" in text


def test_recipients_are_off_the_wire_but_still_delivered(monkeypatch):
    """Let the real send_message flatten the message; capture what would be
    handed to the server."""
    wire = {}

    class WireSMTP(smtplib.SMTP):
        def __init__(self, host, port):
            self.local_hostname = "test"
            self.esmtp_features = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            pass

        def login(self, user, password):
            pass

        def ehlo_or_helo_if_needed(self):
            pass

        def sendmail(self, from_addr, to_addrs, msg, *args, **kwargs):
            wire["args"] = (from_addr, list(to_addrs), msg)

    monkeypatch.setattr(smtplib, "SMTP", WireSMTP)

    send(MSG, CFG, password="hemligt")

    from_addr, to_addrs, raw = wire["args"]
    assert from_addr == CFG.from_address
    # Exactly the shift's parents: the sender's own mailbox gets no copy.
    assert to_addrs == ["a@x.se", "b@x.se"]
    text = raw.decode() if isinstance(raw, bytes) else raw
    assert "a@x.se" not in text and "b@x.se" not in text
    assert "Bcc" not in text

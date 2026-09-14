"""The HTML alternative of the reminder mail."""

from datetime import date

from duty_mailer.email_sender import send
from duty_mailer.config import EmailConfig
from duty_mailer.models import Occurrence, Person, Role
from duty_mailer.templates import render


def _occurrence(**kwargs):
    return Occurrence(
        due=date(2026, 9, 18),
        people=(Person(email="a@exempel.se", name="Anna Andersson"),),
        **kwargs,
    )


def _render(role=Role.PAMINNELSE, previous=None, documents_link="https://x.example/dokument"):
    return render(
        _occurrence(), role, previous,
        chore="Arena värdskap",
        schedule_link="https://x.example/",
        documents_link=documents_link,
    )


def test_message_carries_both_a_text_and_an_html_body():
    message = _render()

    assert "Anna Andersson" in message.body
    assert message.html_body.startswith("<!doctype html>")


def test_html_links_to_the_documents_page():
    message = _render()

    assert 'href="https://x.example/dokument"' in message.html_body
    assert "Läs instruktionerna" in message.html_body


def test_documents_link_also_appears_in_the_plain_text_body():
    """A client showing only the text part must not lose the link."""
    message = _render()

    assert "Instruktioner: https://x.example/dokument" in message.body


def test_no_documents_button_when_no_link_is_configured():
    message = _render(documents_link=None)

    assert "Läs instruktionerna" not in message.html_body
    assert "Instruktioner:" not in message.body


def test_names_are_html_escaped():
    """Names come from a spreadsheet other people maintain."""
    occ = Occurrence(
        due=date(2026, 9, 18),
        people=(Person(email="a@exempel.se", name="<script>alert(1)</script>"),),
    )

    message = render(
        occ, Role.PAMINNELSE, None,
        chore="Arena värdskap", schedule_link=None, documents_link=None,
    )

    assert "<script>" not in message.html_body
    assert "&lt;script&gt;" in message.html_body


def test_handover_details_reach_the_html_version():
    previous = Occurrence(
        due=date(2026, 9, 11),
        people=(Person(email="b@exempel.se", name="Bo Bengtsson"),),
        key_location="Hämta nyckel helgen innan",
    )

    message = _render(role=Role.FORHANDSBESKED, previous=previous)

    assert "Hämta nyckel helgen innan" in message.html_body
    assert "Bo Bengtsson" in message.html_body


def test_sent_mail_is_multipart_with_both_parts(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, *args): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def starttls(self): pass
        def login(self, *args): pass
        def send_message(self, email): sent["email"] = email

    monkeypatch.setattr("duty_mailer.email_sender.smtplib.SMTP", FakeSMTP)
    cfg = EmailConfig(
        smtp_host="h", smtp_port=587, smtp_user="u", from_address="f@exempel.se"
    )

    send(_render(), cfg, password="p")

    email = sent["email"]
    assert email.get_content_type() == "multipart/alternative"
    types = {part.get_content_type() for part in email.iter_parts()}
    assert types == {"text/plain", "text/html"}

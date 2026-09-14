"""Per-parent settings, and what the e-mail opt-out does and does not stop."""

from datetime import date, time

from duty_web.models import Person, Slot


def _login(client, person):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(person.id)
        sess["_fresh"] = True


def test_settings_page_shows_the_logged_in_parent(client, seeded):
    _login(client, seeded["person"])

    body = client.get("/installningar").get_data(as_text=True)

    assert "tova@exempel.se" in body
    assert "checked" in body  # notifications are on by default


def test_settings_requires_login(client):
    response = client.get("/installningar")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_turning_off_notifications_persists(client, seeded, session_factory, csrf_token):
    _login(client, seeded["person"])

    client.post("/installningar", data={"csrf_token": csrf_token()})

    session = session_factory()
    assert session.get(Person, seeded["person"].id).email_notifications is False


def test_turning_notifications_back_on_persists(client, seeded, session_factory, csrf_token):
    session = session_factory()
    session.get(Person, seeded["person"].id).email_notifications = False
    session.commit()
    _login(client, seeded["person"])

    client.post("/installningar", data={"email_notifications": "on", "csrf_token": csrf_token()})

    assert session.get(Person, seeded["person"].id).email_notifications is True


def test_opted_out_parent_gets_no_swap_proposal_mail(
    client, seeded, session_factory, monkeypatch, csrf_token
):
    """The opt-out must actually stop the mail, not just the preference."""
    session = session_factory()
    other = Person(name="Bo Bengtsson", email="bo@exempel.se", email_notifications=False)
    session.add(other)
    session.flush()
    theirs = Slot(
        team_id=seeded["team"].id, date=date(2026, 12, 5), start_time=time(9, 0),
        end_time=time(12, 0), station="", duty_name="Bästkustcupen", venue="",
        person_id=other.id,
    )
    session.add(theirs)
    session.commit()

    sent = []
    monkeypatch.setattr(
        "duty_web.routes.swap_routes.send_swap_proposed",
        lambda *args, **kwargs: sent.append(args),
    )
    _login(client, seeded["person"])

    client.post("/swaps", data={
        "proposer_slot_id": str(seeded["future_slot"].id),
        "target_slot_ids": [str(theirs.id)],
        "csrf_token": csrf_token(),
    })

    assert sent == []


def test_login_link_is_sent_even_when_notifications_are_off(
    client, seeded, session_factory, monkeypatch
):
    """Opting out must never lock a parent out of the app."""
    session = session_factory()
    session.get(Person, seeded["person"].id).email_notifications = False
    session.commit()

    sent = []
    monkeypatch.setattr(
        "duty_web.routes.auth_routes.send_login_link",
        lambda email, link: sent.append(email),
    )

    client.post("/login", data={"email": "tova@exempel.se"})

    assert sent == ["tova@exempel.se"]


def test_opted_in_parent_does_get_the_swap_proposal_mail(
    client, seeded, session_factory, monkeypatch, csrf_token
):
    """Positive control for the opt-out test above: same flow, notifications
    left on, so a silent failure elsewhere can't make the opt-out look like
    it works."""
    session = session_factory()
    other = Person(name="Bo Bengtsson", email="bo@exempel.se")
    session.add(other)
    session.flush()
    theirs = Slot(
        team_id=seeded["team"].id, date=date(2026, 12, 5), start_time=time(9, 0),
        end_time=time(12, 0), station="", duty_name="Bästkustcupen", venue="",
        person_id=other.id,
    )
    session.add(theirs)
    session.commit()

    sent = []
    monkeypatch.setattr(
        "duty_web.routes.swap_routes.send_swap_proposed",
        lambda email, **kwargs: sent.append(email),
    )
    _login(client, seeded["person"])

    client.post("/swaps", data={
        "proposer_slot_id": str(seeded["future_slot"].id),
        "target_slot_ids": [str(theirs.id)],
        "csrf_token": csrf_token(),
    })

    assert sent == ["bo@exempel.se"]


def test_swap_proposal_mail_links_to_the_page_where_you_answer(
    client, seeded, session_factory, monkeypatch, csrf_token
):
    """Without a link the mail just says "log in" and leaves the recipient
    to find the right page on their own."""
    session = session_factory()
    other = Person(name="Bo Bengtsson", email="bo@exempel.se")
    session.add(other)
    session.flush()
    theirs = Slot(
        team_id=seeded["team"].id, date=date(2026, 12, 5), start_time=time(9, 0),
        end_time=time(12, 0), station="", duty_name="Bästkustcupen", venue="",
        person_id=other.id,
    )
    session.add(theirs)
    session.commit()

    sent = {}
    monkeypatch.setattr(
        "duty_web.routes.swap_routes.send_swap_proposed",
        lambda email, **kwargs: sent.update(kwargs),
    )
    _login(client, seeded["person"])

    client.post("/swaps", data={
        "proposer_slot_id": str(seeded["future_slot"].id),
        "target_slot_ids": [str(theirs.id)],
        "csrf_token": csrf_token(),
    })

    assert sent["link"].endswith("/byten")

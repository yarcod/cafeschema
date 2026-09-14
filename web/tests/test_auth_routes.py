from datetime import datetime, timedelta

from duty_web.models import LoginToken, Person


def test_login_post_creates_a_token_for_a_registered_person(
    client, session_factory, seeded, monkeypatch
):
    sent = {}
    monkeypatch.setattr(
        "duty_web.routes.auth_routes.send_login_link",
        lambda email, link: sent.update(email=email, link=link),
    )

    response = client.post("/login", data={"email": "tova@exempel.se"})

    assert response.status_code == 200
    assert sent["email"] == "tova@exempel.se"
    assert "token=" in sent["link"]

    session = session_factory()
    assert session.query(LoginToken).filter_by(email="tova@exempel.se").count() == 1


def test_login_post_does_not_send_mail_or_issue_a_token_for_an_unregistered_email(
    client, session_factory, monkeypatch
):
    """Privacy/abuse fix: the login form must never be usable to spam an
    arbitrary inbox with real mail from our SMTP account, and the page must
    not reveal whether an address is a registered parent (no enumeration).
    So an unregistered email gets the exact same response, but no token and
    no email are ever produced for it."""
    sent = {}
    monkeypatch.setattr(
        "duty_web.routes.auth_routes.send_login_link",
        lambda email, link: sent.update(email=email, link=link),
    )

    response = client.post("/login", data={"email": "stranger@exempel.se"})

    assert response.status_code == 200
    assert sent == {}
    session = session_factory()
    assert session.query(LoginToken).filter_by(email="stranger@exempel.se").count() == 0
    assert "Länk skickad" in response.get_data(as_text=True)


def test_login_post_response_is_identical_for_registered_and_unregistered_email(
    client, seeded, monkeypatch
):
    monkeypatch.setattr(
        "duty_web.routes.auth_routes.send_login_link", lambda email, link: None
    )

    registered = client.post("/login", data={"email": "tova@exempel.se"})
    unregistered = client.post("/login", data={"email": "stranger@exempel.se"})

    registered_text = registered.get_data(as_text=True).replace("tova@exempel.se", "EMAIL")
    unregistered_text = unregistered.get_data(as_text=True).replace("stranger@exempel.se", "EMAIL")
    assert registered.status_code == unregistered.status_code == 200
    assert registered_text == unregistered_text


def test_login_post_is_rate_limited_within_60_seconds(
    client, session_factory, seeded, monkeypatch
):
    monkeypatch.setattr(
        "duty_web.routes.auth_routes.send_login_link", lambda email, link: None
    )
    client.post("/login", data={"email": "tova@exempel.se"})

    response = client.post("/login", data={"email": "tova@exempel.se"})

    assert response.status_code == 200
    session = session_factory()
    assert session.query(LoginToken).filter_by(email="tova@exempel.se").count() == 1


def test_login_post_issues_a_new_token_once_the_rate_limit_window_has_passed(
    client, session_factory, seeded, monkeypatch
):
    monkeypatch.setattr(
        "duty_web.routes.auth_routes.send_login_link", lambda email, link: None
    )
    client.post("/login", data={"email": "tova@exempel.se"})

    # Simulate the first token having been issued long enough ago that the
    # 60-second rate-limit window has elapsed, by backdating its expires_at
    # (which the route derives issued_at from) rather than sleeping.
    session = session_factory()
    first = session.query(LoginToken).filter_by(email="tova@exempel.se").one()
    first.expires_at = datetime.utcnow() - timedelta(minutes=19)
    session.commit()

    response = client.post("/login", data={"email": "tova@exempel.se"})

    assert response.status_code == 200
    session = session_factory()
    assert session.query(LoginToken).filter_by(email="tova@exempel.se").count() == 2


def test_verify_logs_in_an_existing_person_and_redirects_home(
    client, session_factory, seeded
):
    from duty_web.auth import issue_login_token

    session = session_factory()
    token = issue_login_token(session, "tova@exempel.se", secret_key="test-secret")

    response = client.get(f"/login/verify?token={token}", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"] == "/"


def test_verify_refuses_to_log_in_an_email_with_no_existing_person(client, session_factory):
    """No open self-registration: a magic link for an email that was never
    seeded as a Person must not create one or log anyone in (I1) — otherwise
    a stranger could request a link for their own address and, via /team's
    fallback to the first Team, read the whole roster and season calendar."""
    from duty_web.auth import issue_login_token

    session = session_factory()
    token = issue_login_token(session, "ny@exempel.se", secret_key="test-secret")

    response = client.get(f"/login/verify?token={token}")

    assert response.status_code == 200
    assert "finns inte i laglistan" in response.get_data(as_text=True)
    assert session.query(Person).filter_by(email="ny@exempel.se").count() == 0
    with client.session_transaction() as sess:
        assert "_user_id" not in sess


def test_verify_rejects_a_bad_token(client):
    response = client.get("/login/verify?token=not-real")

    assert response.status_code == 200
    assert "ogiltig".encode() in response.data or "ogiltig" in response.get_data(as_text=True)


def test_login_link_uses_https_when_behind_the_tls_proxy(client, seeded, monkeypatch):
    """Fly terminates TLS and forwards plain HTTP. Without ProxyFix the
    emailed link would be http://, sending the login token over the wire
    in cleartext on the first request."""
    links = []
    monkeypatch.setattr(
        "duty_web.routes.auth_routes.send_login_link",
        lambda email, link: links.append(link),
    )

    client.post(
        "/login",
        data={"email": "tova@exempel.se"},
        headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "cafeschema.example"},
    )

    assert links and links[0].startswith("https://cafeschema.example/login/verify")
